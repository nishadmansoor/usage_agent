"""Flexible, READ-ONLY access to the whole semantic model — without free-form DAX.

The agent needs to reach every table, column and measure in the model to answer
dashboard questions, but must never author a query: hand-written DAX is how figures
drifted away from the dashboard in the first place. This module squares that circle.

How it works
------------
``query_model`` is a generic builder. The agent supplies only STRUCTURED choices:

    measures  = ["Total Spend"]                      -> validated against the model
    group_by  = ["ai_dim_date[week_start]"]           -> validated against the model
    period    = "2026-06" | "last_week" | "last_8_weeks" | ...
    filters   = [{"column": "...", "values": [...]}]

Every one of those is checked against the model's OWN metadata (``INFO.VIEW.*``,
fetched once and cached), and only names that really exist are accepted. The DAX
text is composed here, so:

* the agent cannot inject DAX — identifiers never come from its text, they come from
  the metadata dict after a membership test;
* every figure is one of the model's own measures, read back unmodified;
* nothing is computed here — no arithmetic, no derived ratios.

Read-only by construction: the only statement ever emitted is a single
``EVALUATE`` (optionally preceded by ``DEFINE VAR``), and
``validation.ensure_read_only_dax`` re-checks that at execution time.

Coverage: because the allowlist IS the model, this reaches everything the model
exposes — including the ``otter_*`` tables and dimensions like ``region`` and
``connector_name`` that the older fixed tools never surfaced.
"""

from __future__ import annotations

import re
from functools import lru_cache

import pandas as pd

from ..clients.powerbi import PowerBIClient, get_powerbi_client
from ..logging_config import get_logger
from .usage_metrics import _BLOCKED_LOOKUP, _SELF_SCOPED, _clean, _df
from .validation import ensure_read_only_dax

logger = get_logger(__name__)

_DATE = "'ai_dim_date'"
_FACT = "'ai_usage_report'"

# "Table[Column]" / "'Table'[Column]" -> (table, column). Anything else is rejected
# before it gets near the metadata lookup.
_COLUMN_REF = re.compile(r"^'?(?P<table>[^'\[\]]+?)'?\s*\[(?P<column>[^\[\]]+)\]$")

_MAX_ROWS = 5000


# -- model metadata (the allowlist) ----------------------------------------
@lru_cache(maxsize=4)
def _metadata(dataset_id: str | None = None) -> tuple[frozenset[str], frozenset[str]]:
    """Return (measure names, "Table[Column]" refs) that ACTUALLY exist in the model.

    Cached per process: this is two metadata queries and the schema does not change
    mid-conversation. ``dataset_id`` is part of the cache key so pointing at a
    different model doesn't reuse a stale allowlist.
    """
    pbi = get_powerbi_client()
    measures: set[str] = set()
    columns: set[str] = set()

    for row in pbi.execute_dax("EVALUATE INFO.VIEW.MEASURES()"):
        name = row.get("[Name]")
        if name:
            measures.add(str(name))

    for row in pbi.execute_dax("EVALUATE INFO.VIEW.COLUMNS()"):
        table, name = row.get("[Table]"), row.get("[Name]")
        if not table or not name or str(name).startswith("RowNumber-"):
            continue
        if str(table).startswith(("LocalDateTable_", "DateTableTemplate_")):
            continue  # Power BI's auto-generated date tables: noise
        columns.add(f"{table}[{name}]")

    logger.info("Model metadata: %d measures, %d columns", len(measures), len(columns))
    return frozenset(measures), frozenset(columns)


def _catalog(client: PowerBIClient | None) -> tuple[frozenset[str], frozenset[str]]:
    """Metadata for the configured model. Injected clients skip the cache."""
    if client is not None:
        return _metadata_from(client)
    from ..config import get_settings

    return _metadata(get_settings().powerbi_dataset_id)


def _metadata_from(pbi: PowerBIClient) -> tuple[frozenset[str], frozenset[str]]:
    """Uncached metadata read through an explicit client (used by tests)."""
    measures = {
        str(r["[Name]"])
        for r in pbi.execute_dax("EVALUATE INFO.VIEW.MEASURES()")
        if r.get("[Name]")
    }
    columns = {
        f"{r['[Table]']}[{r['[Name]']}]"
        for r in pbi.execute_dax("EVALUATE INFO.VIEW.COLUMNS()")
        if r.get("[Table]") and r.get("[Name]")
    }
    return frozenset(measures), frozenset(columns)


def _resolve_measure(name: str, allowed: frozenset[str]) -> str:
    """Canonical measure name, or raise. Blocked measures keep their explanation."""
    raw = (name or "").strip().strip("[]")
    if raw.lower() in _BLOCKED_LOOKUP:
        raise ValueError(f"Measure '{name}' is blocked: {_BLOCKED_LOOKUP[raw.lower()]}")
    lookup = {m.lower(): m for m in allowed}
    if raw.lower() not in lookup:
        raise ValueError(
            f"Measure '{name}' does not exist in this model. Call describe_model to "
            f"see the real measure names."
        )
    return lookup[raw.lower()]


def _resolve_column(ref: str, allowed: frozenset[str]) -> str:
    """Canonical ``'Table'[Column]`` reference, or raise.

    The returned string is rebuilt from the metadata entry, never from the agent's
    input, so no agent-supplied character reaches the DAX.
    """
    m = _COLUMN_REF.match((ref or "").strip())
    if not m:
        raise ValueError(
            f"Column '{ref}' is malformed. Use Table[Column], e.g. "
            f"'ai_dim_user_dept[department]'."
        )
    want = f"{m.group('table').strip()}[{m.group('column').strip()}]".lower()
    lookup = {c.lower(): c for c in allowed}
    if want not in lookup:
        raise ValueError(
            f"Column '{ref}' does not exist in this model. Call describe_model to see "
            f"the real table/column names."
        )
    table, column = lookup[want].split("[", 1)
    return f"'{table}'[{column.rstrip(']')}]"


def _literal(value) -> str:
    """Render a filter value as a DAX literal (strings get their quotes doubled)."""
    if isinstance(value, bool):
        return "TRUE()" if value else "FALSE()"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace('"', '""') + '"'


# -- period scoping --------------------------------------------------------
# Named windows are anchored to the DATA (the model's is_complete_week /
# is_complete_month and the last actual usage date), never to this machine's clock.
_NAMED = ("all_time", "last_week", "prior_week", "last_month", "this_month")

_LW = (
    f"VAR LW = CALCULATE(MAX({_DATE}[week_start]), "
    f"FILTER(ALL({_DATE}), {_DATE}[is_complete_week] = TRUE()))"
)
_LM = (
    f"VAR LM = CALCULATE(MAX({_DATE}[month_start]), "
    f"FILTER(ALL({_DATE}), {_DATE}[is_complete_month] = TRUE()))"
)
_AD = f"VAR AD = CALCULATE(MAX({_FACT}[usage_date]), ALL({_DATE}))"

_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_YEAR_RE = re.compile(r"^(\d{4})$")
_LAST_N_RE = re.compile(r"^last[_ ](\d{1,3})[_ ](day|days|week|weeks|month|months)$")


def resolve_period(period: str) -> tuple[str, str]:
    """Return (DEFINE block, filter fragment WITHOUT trailing comma) for *period*.

    Accepts a named window, an exact month ``"2026-06"``, a year ``"2026"``, or a
    rolling window ``"last_8_weeks"`` / ``"last_30_days"`` / ``"last_6_months"``.
    """
    p = (period or "all_time").strip().lower().replace(" ", "_")
    aliases = {
        "last_complete_week": "last_week", "lastweek": "last_week",
        "week_before_last": "prior_week", "priorweek": "prior_week",
        "previous_month": "last_month", "lastmonth": "last_month",
        "month_to_date": "this_month", "mtd": "this_month",
        "all": "all_time", "overall": "all_time", "total": "all_time",
    }
    p = aliases.get(p, p)

    if p == "all_time":
        return "", ""
    if p == "last_week":
        return f"DEFINE {_LW}", f"FILTER(ALL({_DATE}), {_DATE}[week_start] = LW)"
    if p == "prior_week":
        return (f"DEFINE {_LW} VAR PW = LW - 7",
                f"FILTER(ALL({_DATE}), {_DATE}[week_start] = PW)")
    if p == "last_month":
        return f"DEFINE {_LM}", f"FILTER(ALL({_DATE}), {_DATE}[month_start] = LM)"
    if p == "this_month":
        return (f"DEFINE {_AD} VAR TM = DATE(YEAR(AD), MONTH(AD), 1)",
                f"FILTER(ALL({_DATE}), {_DATE}[month_start] = TM)")

    if m := _MONTH_RE.match(p):
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            raise ValueError(f"'{period}' is not a valid month (use YYYY-MM).")
        return "", f'FILTER(ALL({_DATE}), {_DATE}[month] = "{y:04d}-{mo:02d}")'
    if m := _YEAR_RE.match(p):
        return "", f"FILTER(ALL({_DATE}), {_DATE}[year] = {int(m.group(1))})"

    if m := _LAST_N_RE.match(p):
        n, unit = int(m.group(1)), m.group(2).rstrip("s")
        if n < 1:
            raise ValueError(f"'{period}' must cover at least 1 {unit}.")
        if unit == "day":
            # Rolling window ending on the last day of ACTUAL usage.
            return (f"DEFINE {_AD}",
                    f"FILTER(ALL({_DATE}), {_DATE}[date] > AD - {n} "
                    f"&& {_DATE}[date] <= AD)")
        if unit == "week":
            # N COMPLETE weeks, ending with the last complete one.
            return (f"DEFINE {_LW}",
                    f"FILTER(ALL({_DATE}), {_DATE}[week_start] > LW - {7 * n} "
                    f"&& {_DATE}[week_start] <= LW)")
        # N COMPLETE months, ending with the last complete one.
        return (f"DEFINE {_LM}",
                f"FILTER(ALL({_DATE}), {_DATE}[month_start] > EDATE(LM, -{n}) "
                f"&& {_DATE}[month_start] <= LM)")

    raise ValueError(
        f"Unknown period '{period}'. Use one of {_NAMED}, an exact month "
        f"('2026-06'), a year ('2026'), or a rolling window "
        f"('last_8_weeks', 'last_30_days', 'last_6_months')."
    )


# -- the generic query -----------------------------------------------------
def query_model(
    measures: list[str] | str,
    group_by: list[str] | str | None = None,
    period: str = "all_time",
    *,
    filters: list[dict] | None = None,
    top_n: int | None = None,
    order_by: str | None = None,
    client: PowerBIClient | None = None,
) -> pd.DataFrame:
    """Read the model's own measures, optionally grouped and filtered. READ-ONLY.

    Parameters
    ----------
    measures:
        Measure names that exist in the model (see describe_model). Reported as-is.
    group_by:
        Zero or more ``Table[Column]`` refs to break the measures down by. Omit for
        a single total row. Use ``ai_dim_date[week_start]`` / ``[month]`` for trends.
    period:
        Named window, ``"YYYY-MM"``, ``"YYYY"``, or ``"last_N_weeks|days|months"``.
    filters:
        ``[{"column": "ai_usage_report[provider]", "values": ["anthropic"]}]`` — an
        IN test per entry, ANDed together. Values are rendered as DAX literals.
    top_n / order_by:
        Keep only the top N rows by ``order_by`` (defaults to the first measure).
    """
    names = [measures] if isinstance(measures, str) else list(measures or [])
    if not names:
        raise ValueError("query_model requires at least one measure.")
    groups = [group_by] if isinstance(group_by, str) else list(group_by or [])

    allowed_measures, allowed_columns = _catalog(client)
    resolved_m = [_resolve_measure(n, allowed_measures) for n in names]
    resolved_g = [_resolve_column(c, allowed_columns) for c in groups]

    define, period_filter = resolve_period(period)

    self_scoped = [m for m in resolved_m if m in _SELF_SCOPED]
    if self_scoped and period_filter:
        raise ValueError(
            f"{self_scoped} already carry their own time window; request them with "
            f"period='all_time'. Their names state the period they cover."
        )

    filter_frags: list[str] = [period_filter] if period_filter else []
    for entry in filters or []:
        col = _resolve_column(entry.get("column", ""), allowed_columns)
        values = entry.get("values")
        values = [values] if not isinstance(values, list) else values
        if not values:
            raise ValueError(f"Filter on {col} has no values.")
        rendered = ", ".join(_literal(v) for v in values)
        filter_frags.append(f"FILTER(ALL({col}), {col} IN {{{rendered}}})")

    body = ", ".join(f'"{_alias(m)}", [{m}]' for m in resolved_m)

    if resolved_g:
        parts = list(resolved_g) + filter_frags + [body]
        inner = "SUMMARIZECOLUMNS(\n        " + ",\n        ".join(parts) + "\n    )"
        rank_key = _alias(_resolve_measure(order_by, allowed_measures)) if order_by else _alias(resolved_m[0])
        # TOPN always ranks by a MEASURE — "top 10" means biggest, never earliest.
        if top_n:
            inner = f"TOPN({int(top_n)}, {inner}, [{rank_key}], DESC)"
        # ...but a TREND must read chronologically, so when the breakdown is by a
        # time column, sort by that column ascending instead of by the measure.
        # Sorting a time series by size makes it unreadable and invites the model to
        # misreport direction (this bit us: weeks came back 07-13, 06-22, 07-06).
        temporal = next((c for c in resolved_g if _is_temporal(c)), None)
        if temporal and not order_by:
            dax = f"{define}\nEVALUATE\n    {inner}\nORDER BY {temporal} ASC"
        else:
            dax = f"{define}\nEVALUATE\n    {inner}\nORDER BY [{rank_key}] DESC"
    else:
        # No grouping: one row of scalars, each measure scoped by the period.
        scoped = ",\n        ".join(
            f'"{_alias(m)}", ' + (f"CALCULATE([{m}], {_and(filter_frags)})" if filter_frags else f"[{m}]")
            for m in resolved_m
        )
        dax = f"{define}\nEVALUATE\n    ROW(\n        {scoped}\n    )"

    ensure_read_only_dax(dax)
    pbi = client or get_powerbi_client()
    logger.info(
        "query_model(measures=%s, group_by=%s, period=%s, filters=%d)",
        resolved_m, resolved_g, period, len(filters or []),
    )
    df = _df(pbi.execute_dax(dax))
    if len(df) > _MAX_ROWS:
        logger.warning("query_model returned %d rows; truncating.", len(df))
        df = df.head(_MAX_ROWS)
    return df


# Column names that mean "a point in time". Matched on the COLUMN part only, so a
# dimension like [day_name] ('Mon') is excluded -- sorting that alphabetically would
# be worse than sorting by measure.
_TEMPORAL = ("date", "week", "week_start", "month", "month_start", "year", "quarter")


def _is_temporal(column_ref: str) -> bool:
    """True if a ``'Table'[Column]`` ref names a time column."""
    col = column_ref.split("[", 1)[1].rstrip("]").lower() if "[" in column_ref else ""
    return col in _TEMPORAL or col.endswith(("_date", "_week", "_month"))


def _alias(measure: str) -> str:
    """Result column name for a measure. Quotes are stripped, not escaped, because
    this lands inside a DAX string literal and a stray quote would break it."""
    return measure.replace('"', "")


def _and(frags: list[str]) -> str:
    return ", ".join(frags)


# -- forecast --------------------------------------------------------------
def forecast_outlook(
    months: int = 3,
    *,
    provider: str | None = None,
    client: PowerBIClient | None = None,
) -> pd.DataFrame:
    """Projected spend/users for the next *months* COMPLETE calendar months.

    ``ai_forecast`` is WEEKLY, so a naive month bucket under-reports any month the
    weeks only partly cover — the "partial months" bug the system prompt warns
    about. This returns weekly rows and does the completeness test IN CODE: a month
    is emitted only when every Monday belonging to it is present, and only future
    weeks (after the last complete week) are counted.

    Columns: ``month``, ``weeks``, ``spend``, ``spend_low``, ``spend_high``,
    ``active_users_avg``. ``months_requested`` vs the row count tells the caller how
    many complete months were actually available.
    """
    if months < 1:
        raise ValueError("months must be at least 1.")

    filters = [{"column": "ai_forecast[kind]", "values": ["trend"]}]
    if provider:
        filters.append({"column": "ai_forecast[provider]", "values": [provider.lower()]})

    weekly = query_model(
        ["Trend Spend", "Trend Spend Low", "Trend Spend High", "Trend Users"],
        group_by=["ai_forecast[week]"],
        period="all_time",
        filters=filters,
        client=client,
    )
    if weekly.empty:
        return weekly

    weekly = weekly.rename(columns={
        "Trend Spend": "spend", "Trend Spend Low": "spend_low",
        "Trend Spend High": "spend_high", "Trend Users": "active_users",
    })
    weekly["week"] = pd.to_datetime(weekly["week"])
    weekly = weekly.sort_values("week")

    # Only weeks strictly after the last complete week are projection.
    anchor = query_model(["Last Complete Week"], period="all_time", client=client)
    cutoff = pd.to_datetime(anchor.iloc[0]["Last Complete Week"])
    future = weekly[weekly["week"] > cutoff].copy()
    if future.empty:
        return future

    future["month"] = future["week"].dt.strftime("%Y-%m")

    # A month is COMPLETE when every Monday falling in it is present.
    out = []
    for month, grp in future.groupby("month", sort=True):
        first = pd.Timestamp(month + "-01")
        mondays = pd.date_range(
            first + pd.Timedelta(days=(7 - first.dayofweek) % 7),
            first + pd.offsets.MonthEnd(0),
            freq="7D",
        )
        if len(grp) < len(mondays):
            continue  # partial month -- never reported
        out.append({
            "month": month,
            "weeks": len(grp),
            "spend": float(grp["spend"].sum()),
            "spend_low": float(grp["spend_low"].sum()),
            "spend_high": float(grp["spend_high"].sum()),
            "active_users_avg": float(grp["active_users"].mean()),
        })

    df = pd.DataFrame(out).head(months)
    df.attrs["months_requested"] = months
    logger.info("forecast_outlook(months=%d) -> %d complete month(s)", months, len(df))
    return df
