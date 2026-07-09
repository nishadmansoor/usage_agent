"""DAX tools over the Fabric Power BI semantic model — the PRIMARY query path.

- ``run_dax_query``  : run a DAX ``EVALUATE`` and return rows (answers reconcile
                       with the dashboard because they use the model's measures).
- ``describe_model`` : list the model's tables, columns, and measures so Claude
                       references real measure names instead of inventing SQL.

Both go through the single ``PowerBIClient`` choke point.
"""

from __future__ import annotations

import pandas as pd

from ..clients.powerbi import PowerBIClient, get_powerbi_client
from ..config import get_settings
from ..logging_config import get_logger

logger = get_logger(__name__)


def run_dax_query(
    dax: str,
    *,
    client: PowerBIClient | None = None,
    row_limit: int | None = None,
) -> pd.DataFrame:
    """Run a DAX query against the semantic model and return a DataFrame.

    Column names are as Power BI returns them (e.g. ``DimUser[Department]`` or
    ``[Total Cost USD]``). Rows are capped at ``QUERY_ROW_LIMIT``.

    Parameters
    ----------
    dax:
        A DAX query — typically ``EVALUATE SUMMARIZECOLUMNS(...)`` referencing the
        model's own measures.
    client:
        Optional injected client (tests pass a stub).
    """
    if not dax or not dax.strip():
        raise ValueError("run_dax_query requires a non-empty 'dax' query.")
    limit = row_limit or get_settings().query_row_limit
    pbi = client or get_powerbi_client()

    rows = pbi.execute_dax(dax)
    df = pd.DataFrame(rows)
    if len(df) > limit:
        logger.warning("DAX returned %d rows; truncating to %d.", len(df), limit)
        df = df.head(limit)
    return df


# Model metadata via the DAX INFO.VIEW.* functions. (Plain INFO.TABLES() etc.
# are rejected by this engine; the INFO.VIEW.* variants return friendly columns:
# [Name], [Table] (table NAME, not id), [IsHidden], [DataType].)
_TABLES_DAX = "EVALUATE INFO.VIEW.TABLES()"
_COLUMNS_DAX = "EVALUATE INFO.VIEW.COLUMNS()"
_MEASURES_DAX = "EVALUATE INFO.VIEW.MEASURES()"
_RELATIONSHIPS_DAX = "EVALUATE INFO.VIEW.RELATIONSHIPS()"


def _visible(row: dict) -> bool:
    return not bool(row.get("[IsHidden]"))


def _rel_field(row: dict, *names: str):
    """Fetch a relationship field tolerantly (column names vary by engine)."""
    lower = {k.lower(): k for k in row}
    for name in names:
        for low, orig in lower.items():
            if name in low:
                return row[orig]
    return None


def _is_auto_date(name) -> bool:
    """Power BI's auto-generated date tables — noise, so we hide them."""
    n = str(name or "")
    return n.startswith("LocalDateTable_") or n.startswith("DateTableTemplate_")


def describe_model(*, client: PowerBIClient | None = None) -> str:
    """Return a compact text description of the semantic model.

    Lists visible tables, their columns, and — most importantly — the model's
    measure names, so the agent can reference existing measures. Degrades
    gracefully: if a metadata query isn't supported, that section is noted rather
    than raising.
    """
    pbi = client or get_powerbi_client()

    tables = _safe_eval(pbi, _TABLES_DAX)
    columns = _safe_eval(pbi, _COLUMNS_DAX)
    measures = _safe_eval(pbi, _MEASURES_DAX)
    relationships = _safe_eval(pbi, _RELATIONSHIPS_DAX)

    lines: list[str] = ["# Semantic model"]

    lines.append("\n## Tables & columns")
    if tables or columns:
        by_table: dict[str, list[str]] = {
            r.get("[Name]"): []
            for r in tables
            if _visible(r) and not _is_auto_date(r.get("[Name]"))
        }
        for r in columns:
            tname = r.get("[Table]")
            col = r.get("[Name]")
            if not _visible(r) or _is_auto_date(tname) or not col:
                continue
            if str(col).startswith("RowNumber-"):  # internal key column
                continue
            by_table.setdefault(tname, []).append(str(col))
        for tname in sorted(k for k in by_table if k):
            cols = ", ".join(by_table[tname]) or "(no visible columns)"
            lines.append(f"- {tname}: {cols}")
    else:
        lines.append("- (column metadata unavailable)")

    lines.append("\n## Measures (prefer these — they match the dashboard)")
    # Include hidden measures too: they're not shown in the report's field list
    # but are still valid to reference in a DAX query, and are often the useful
    # time-intelligence measures (e.g. week-to-date, WoW).
    if measures:
        for r in sorted(
            measures,
            key=lambda r: (str(r.get("[Table]") or ""), str(r.get("[Name]") or "")),
        ):
            tname = r.get("[Table]")
            prefix = f"{tname}: " if tname else ""
            hidden = "  (hidden)" if not _visible(r) else ""
            lines.append(f"- {prefix}[{r.get('[Name]')}]{hidden}")
    else:
        lines.append("- (measure metadata unavailable on this model)")

    lines.append(
        "\n## Relationships (which tables are date/dimension-connected — "
        "only related tables respect a filter on a dimension)"
    )
    if relationships:
        for r in relationships:
            ft = _rel_field(r, "fromtable")
            fc = _rel_field(r, "fromcolumn")
            tt = _rel_field(r, "totable")
            tc = _rel_field(r, "tocolumn")
            active = _rel_field(r, "isactive", "active")
            suffix = "" if active in (True, "True", 1) else "  (inactive)"
            lines.append(f"- {ft}[{fc}] -> {tt}[{tc}]{suffix}")
    else:
        lines.append("- (relationship metadata unavailable)")

    return "\n".join(lines)


def _safe_eval(pbi: PowerBIClient, dax: str) -> list[dict]:
    """Run a metadata DAX query; return [] (logged) if the model rejects it."""
    try:
        return pbi.execute_dax(dax)
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("Model metadata query failed: %s", exc)
        return []
