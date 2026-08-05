"""Deterministic metric tools — FIXED queries, the agent's ONLY way to get numbers.

The agent cannot write DAX or SQL. Every query in this module is built here from a
fixed template; the agent chooses only *which* allowlisted measure, dimension, or
period it wants. That is what makes the bot's figures equal the dashboard's: they
ARE the dashboard's measures, read back unmodified, with no agent-authored query and
no recomputation from raw columns. The same module backs the CLI and the bot, so the
two surfaces cannot diverge.

Coverage:
- ``measure_values``       — read any allowlisted MEASURE for a period (the
                             general-purpose replacement for hand-written DAX).
- ``weekly_spend_summary`` — last COMPLETE week vs the prior week: total spend,
                             WoW $/%%, active users. (The weekly-briefing headline.)
- ``spend_breakdown``      — spend + active users by provider / model / product.
- ``department_spend``     — spend + active users by department.
- ``top_users``            — highest-spend users, with department and top product.
- ``org_adoption``         — % of the ORG using these tools, against the TRUE HR
                             headcount rather than the model's hardcoded 4750.

Period-scoping is anchored to the DATA (the model's ``is_complete_week`` /
``is_complete_month`` and the last usage date), never to this machine's clock.
"""

from __future__ import annotations

import pandas as pd

from ..clients.powerbi import PowerBIClient, get_powerbi_client
from ..logging_config import get_logger

logger = get_logger(__name__)

_DATE = "'ai_dim_date'"
_FACT = "'ai_usage_report'"

_DIMENSION_COLUMN = {
    "provider": "'ai_dim_provider'[provider]",
    "model": "'ai_usage_report'[model]",
    "product": "'ai_usage_report'[product_group]",
}

_PERIODS = ("last_week", "prior_week", "last_month", "this_month", "all_time")


# -- helpers ---------------------------------------------------------------
def _clean(col: str) -> str:
    """'DimProvider'[provider] -> provider ; [Spend] -> Spend."""
    return col.split("[")[-1].rstrip("]")


def _df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if not df.empty:
        df.columns = [_clean(c) for c in df.columns]
    return df


def _norm_period(period: str) -> str:
    p = (period or "").lower().strip().replace(" ", "_")
    aliases = {
        "lastweek": "last_week", "last_complete_week": "last_week",
        "week_before_last": "prior_week", "priorweek": "prior_week",
        "previous_month": "last_month", "lastmonth": "last_month",
        "month_to_date": "this_month", "mtd": "this_month", "thismonth": "this_month",
        "all": "all_time", "overall": "all_time", "total": "all_time",
    }
    p = aliases.get(p, p)
    if p not in _PERIODS:
        raise ValueError(f"Unknown period '{period}'. Use one of {_PERIODS}.")
    return p


# Every period is anchored to the DATA, never to this machine's clock: weeks off the
# model's [is_complete_week], months off [is_complete_month] and the last usage date.
# A clock-derived "YYYY-MM" string silently disagrees with the dashboard whenever the
# pipeline hasn't landed the current day yet (and around month boundaries), which is
# exactly the class of mismatch these tools exist to prevent.
_LW_VAR = (
    f"VAR LW = CALCULATE(MAX({_DATE}[week_start]), "
    f"FILTER(ALL({_DATE}), {_DATE}[is_complete_week] = TRUE()))"
)
# Last COMPLETE month — identical logic to the model's [Last Complete Month].
_LM_VAR = (
    f"VAR LM = CALCULATE(MAX({_DATE}[month_start]), "
    f"FILTER(ALL({_DATE}), {_DATE}[is_complete_month] = TRUE()))"
)
# The month containing the last day of actual usage — the model's [Anchor Date].
_TM_VAR = (
    f"VAR AD = CALCULATE(MAX({_FACT}[usage_date]), ALL({_DATE})) "
    "VAR TM = DATE(YEAR(AD), MONTH(AD), 1)"
)


def _period_dax(period: str) -> tuple[str, str]:
    """Return (DEFINE block, SUMMARIZECOLUMNS filter fragment) for a period.

    The filter fragment carries a trailing comma so it can be dropped straight into
    a SUMMARIZECOLUMNS argument list; ``_calc`` strips it for CALCULATE.
    """
    p = _norm_period(period)
    if p == "last_week":
        return f"DEFINE {_LW_VAR}", f"FILTER(ALL({_DATE}), {_DATE}[week_start] = LW),"
    if p == "prior_week":
        return f"DEFINE {_LW_VAR} VAR PW = LW - 7", f"FILTER(ALL({_DATE}), {_DATE}[week_start] = PW),"
    if p == "last_month":
        return f"DEFINE {_LM_VAR}", f"FILTER(ALL({_DATE}), {_DATE}[month_start] = LM),"
    if p == "this_month":
        return f"DEFINE {_TM_VAR}", f"FILTER(ALL({_DATE}), {_DATE}[month_start] = TM),"
    return "", ""  # all_time


def _calc(measure: str, filt: str) -> str:
    """Wrap a measure reference in the period filter (or leave it bare for all_time)."""
    if not filt:
        return f"[{measure}]"
    return f"CALCULATE([{measure}], {filt.rstrip(',')})"


# -- measure catalog -------------------------------------------------------
# The measures the agent is allowed to READ, grouped only for readability. This is
# an allowlist, not a hint: ``measure_values`` resolves a requested name to the
# canonical string below and refuses anything else, so the agent can never invent a
# measure, redefine one, or smuggle DAX in through a measure name.
_MEASURES: tuple[str, ...] = (
    # spend
    "Total Spend", "Anthropic Spend", "OpenAI Spend", "List Cost",
    "Discount Dollar", "Discount Pct", "Pct Anthropic",
    "Cost per 1M Tokens", "Cost per Request", "Avg Spend per User",
    "Annualized Run-Rate",
    # users / adoption
    "Active Users", "Active Accounts", "Active Users - OpenAI",
    "Active Users - Anthropic", "Active Users - Copilot", "Active Claude Users",
    "Unique People", "People on 2+ Providers", "% People Multi-Tool",
    "Cumulative Users", "New Users",
    # tokens / requests
    "Uncached Input Tokens", "Cache Read Tokens", "Cache Creation Tokens",
    "Output Tokens", "Total Tokens", "Total Input Tokens", "Cache Read Pct",
    "Cache Creation Pct", "Output Token Pct", "Total Requests",
    # connectors / skills
    "Connector Users", "Connector User-Days", "Connector Invocations",
    "Avg Invocations per User", "% Claude Users Using Connectors",
    "Avg Distinct Connectors per User-Day", "Skill Invocations",
    "Plugin Invocations", "Skill Users", "Connectors In Use", "Connector Calls",
    "Connector Read Calls", "Connector Write Calls",
    # data currency
    "Anchor Date", "Data As Of", "Last Complete Week", "Last Complete Month",
    "Days in Period", "Top Product",
)

# Measures that carry their OWN time window internally (last complete week, last
# 7/30 days, the forecast horizon, weekday-average DAU...). Filtering them again by
# a period would either double-scope them or blank them out, so they are only
# readable with period="all_time" — their name already states the window.
_SELF_SCOPED: tuple[str, ...] = (
    "Spend Last Week", "Spend Prior Week", "WoW Dollar", "WoW Pct",
    "Spend Last Month", "Spend Prior Month", "MoM Pct", "Spend This Week",
    "Spend Last 7d", "Spend Prev 7d", "Spend 7d Delta Pct",
    "Spend Last 30d", "Spend Prev 30d", "Spend 30d Delta Pct",
    "Users Last 7d", "Users Prev 7d", "Users 7d Delta Pct",
    "Users Last 30d", "Users Prev 30d", "Users 30d Delta Pct",
    "Weekly Active Users", "WAU Prior Week", "WAU WoW Pct",
    "Monthly Active Users",
    "OpenAI DAU", "Anthropic DAU", "Copilot DAU", "Total Weekday Avg DAU",
    "DAU Last Week (Anthropic)", "DAU Last Week (OpenAI)",
    "DAU Last Week (Copilot)", "DAU Last Week (Total)",
    "DAU Last Week (Copilot Chat)", "DAU Last Week (Teams)",
    "DAU Last Week (Outlook)", "DAU Last Week (Word)", "DAU Last Week (Excel)",
    "DAU Last Week (PowerPoint)", "DAU Last Week (OneNote)",
    "DAU Last Week (Loop)",
    "Copilot Chat", "Excel", "Loop", "Teams", "Onenote", "Outlook",
    "Powerpoint", "Word",
    "Actual Spend", "Trend Spend", "Trend Spend Low", "Trend Spend High",
    "Actual Users", "Trend Users", "Users Low", "Users High",
    "Peak Weekly Active Users", "Projected Spend", "Projected Spend Low",
    "Projected Spend High", "Projected Spend 13w", "Projected Year-End Spend",
    "Actual vs Trend %", "Avg Weekly Diff %", "Trend MAPE %",
    "Forecast Generated",
)

# Measures the agent must NEVER report, with the reason surfaced in the error so a
# blocked request turns into a correct answer instead of a silent fallback.
_BLOCKED: dict[str, str] = {
    "Org Headcount": (
        "[Org Headcount] is the hardcoded literal 4750, not a headcount — it is the "
        "fallback constant the pipelines use when the HR feed is unreadable. Call "
        "org_headcount (or org_adoption) for the true active-employee count."
    ),
    "% Org Adopted": (
        "[% Org Adopted] divides by the hardcoded 4750, so it is wrong whenever real "
        "headcount differs. Call org_adoption, which divides [Active Users] by the "
        "true HR headcount."
    ),
    "Spend LW": "[Spend LW] returns the ALL-TIME total (model-side bug). Use period='last_week'.",
    "Spend LM": "[Spend LM] returns the ALL-TIME total (model-side bug). Use period='last_month'.",
    "WoW $": "Built on the broken [Spend LW]. Use weekly_spend_summary.",
    "WoW %": "Built on the broken [Spend LW]. Use weekly_spend_summary.",
    "MoM $": "Built on the broken [Spend LM]. Use measure_values with period='last_month'.",
    "MoM %": "Built on the broken [Spend LM]. Use measure_values with period='last_month'.",
}

# Advertised to the model as the tool-schema enum.
ALLOWED_MEASURE_NAMES: list[str] = sorted(_MEASURES + _SELF_SCOPED)

_MEASURE_LOOKUP: dict[str, str] = {m.lower(): m for m in _MEASURES + _SELF_SCOPED}
_BLOCKED_LOOKUP: dict[str, str] = {m.lower(): why for m, why in _BLOCKED.items()}


def _resolve_measure(name: str) -> str:
    """Map an agent-supplied measure name to its canonical form, or raise.

    Because the returned string always comes from the catalog above, nothing the
    agent writes ever reaches the DAX text.
    """
    key = (name or "").strip().strip("[]").lower()
    if key in _BLOCKED_LOOKUP:
        raise ValueError(f"Measure '{name}' is blocked: {_BLOCKED_LOOKUP[key]}")
    if key in _MEASURE_LOOKUP:
        return _MEASURE_LOOKUP[key]
    raise ValueError(
        f"Measure '{name}' is not in the allowlist. Choose from: "
        f"{', '.join(ALLOWED_MEASURE_NAMES)}"
    )


# -- tools -----------------------------------------------------------------
def weekly_spend_summary(*, client: PowerBIClient | None = None) -> pd.DataFrame:
    """Last complete week vs the prior week: total spend, WoW $/%, active users.

    Fixed query — the weekly-briefing headline, identical every run.
    """
    pbi = client or get_powerbi_client()
    dax = f"""
DEFINE
    VAR LW = CALCULATE(MAX({_DATE}[week_start]),
        FILTER(ALL({_DATE}), {_DATE}[is_complete_week] = TRUE()))
    VAR PW = LW - 7
    VAR SpendLW = CALCULATE([Total Spend], FILTER(ALL({_DATE}), {_DATE}[week_start] = LW))
    VAR SpendPW = CALCULATE([Total Spend], FILTER(ALL({_DATE}), {_DATE}[week_start] = PW))
    VAR UsersLW = CALCULATE([Active Users], FILTER(ALL({_DATE}), {_DATE}[week_start] = LW))
    VAR UsersPW = CALCULATE([Active Users], FILTER(ALL({_DATE}), {_DATE}[week_start] = PW))
EVALUATE
    ROW(
        "week_start", LW,
        "prior_week_start", PW,
        "spend_last_week", SpendLW,
        "spend_prior_week", SpendPW,
        "wow_dollar", SpendLW - SpendPW,
        "wow_pct", DIVIDE(SpendLW - SpendPW, SpendPW),
        "active_users_last_week", UsersLW,
        "active_users_prior_week", UsersPW
    )
"""
    logger.info("weekly_spend_summary()")
    return _df(pbi.execute_dax(dax))


def spend_breakdown(
    dimension: str = "provider",
    period: str = "last_week",
    *,
    top_n: int = 20,
    client: PowerBIClient | None = None,
) -> pd.DataFrame:
    """Spend + active users by *dimension* (provider/model/product) for *period*.

    Uses the [Total Spend] measure so figures reconcile with the dashboard.
    """
    dim = (dimension or "").lower().strip()
    if dim not in _DIMENSION_COLUMN:
        raise ValueError(f"dimension must be one of {list(_DIMENSION_COLUMN)}")
    col = _DIMENSION_COLUMN[dim]
    define, filt = _period_dax(period)
    pbi = client or get_powerbi_client()
    dax = f"""
{define}
EVALUATE
    TOPN({int(top_n)},
        SUMMARIZECOLUMNS(
            {col},
            {filt}
            "Spend", [Total Spend],
            "ActiveUsers", [Active Users]
        ),
        [Spend], DESC)
ORDER BY [Spend] DESC
"""
    logger.info("spend_breakdown(dimension=%s, period=%s)", dim, _norm_period(period))
    return _df(pbi.execute_dax(dax))


_DEPT = "'ai_dim_user_dept'[department]"
_PROVIDER = "'ai_dim_provider'[provider]"


def department_spend(
    period: str = "last_week",
    *,
    provider: str | None = None,
    top_n: int = 15,
    client: PowerBIClient | None = None,
) -> pd.DataFrame:
    """Spend + active users by department for *period* — entirely within the
    AI Usage semantic model.

    Department lives in 'ai_dim_user_dept' (related to the fact by
    user_key), so this is a DAX aggregate over [Total Spend] / [Active Users] and
    reconciles with the dashboard. No SQL, no external directory.
    """
    p = _norm_period(period)
    if provider and provider.lower() not in ("anthropic", "openai"):
        raise ValueError("provider must be 'anthropic' or 'openai'")
    define, filt = _period_dax(p)
    prov_filter = (
        f'FILTER(ALL({_PROVIDER}), {_PROVIDER} = "{provider.lower()}"),'
        if provider
        else ""
    )
    pbi = client or get_powerbi_client()
    dax = f"""
{define}
EVALUATE
    TOPN({int(top_n)},
        FILTER(
            SUMMARIZECOLUMNS(
                {_DEPT},
                {filt}
                {prov_filter}
                "Spend", [Total Spend],
                "ActiveUsers", [Active Users]
            ),
            NOT ISBLANK({_DEPT}) && [Spend] > 0),
        [Spend], DESC)
ORDER BY [Spend] DESC
"""
    logger.info("department_spend(period=%s, provider=%s)", p, provider)
    return _df(pbi.execute_dax(dax))


def measure_values(
    measures: list[str] | str,
    period: str = "last_week",
    *,
    client: PowerBIClient | None = None,
) -> pd.DataFrame:
    """Read one or more of the model's OWN measures for *period* — one row.

    This is the general-purpose replacement for hand-written DAX. The agent chooses
    only WHICH allowlisted measures to read and over WHAT period; the DAX text is
    built here, so every figure is the dashboard's own measure, unmodified. No
    arithmetic is performed on the results.
    """
    names = [measures] if isinstance(measures, str) else list(measures or [])
    if not names:
        raise ValueError("measure_values requires at least one measure name.")
    resolved = [_resolve_measure(n) for n in names]

    p = _norm_period(period)
    self_scoped = [m for m in resolved if m in _SELF_SCOPED]
    if self_scoped and p != "all_time":
        raise ValueError(
            f"{self_scoped} already carry their own time window, so they must be "
            f"read with period='all_time' (got '{p}'). Their names state the period "
            f"they cover."
        )

    define, filt = _period_dax(p)
    body = ",\n        ".join(f'"{m}", {_calc(m, filt)}' for m in resolved)
    pbi = client or get_powerbi_client()
    dax = f"""
{define}
EVALUATE
    ROW(
        {body}
    )
"""
    logger.info("measure_values(measures=%s, period=%s)", resolved, p)
    return _df(pbi.execute_dax(dax))


_EMAIL = f"{_FACT}[email]"


def top_users(
    period: str = "last_week",
    *,
    provider: str | None = None,
    top_n: int = 10,
    client: PowerBIClient | None = None,
) -> pd.DataFrame:
    """Highest-spend USERS for *period*, with department and most-used product.

    Fixed query built on [Total Spend] and the model's own [Top Product], so the
    ranking and the totals reconcile with the dashboard. Replaces the hand-written
    DAX the one-pager's top-spenders table used to need.
    """
    p = _norm_period(period)
    if provider and provider.lower() not in ("anthropic", "openai", "microsoft"):
        raise ValueError("provider must be 'anthropic', 'openai', or 'microsoft'")
    define, filt = _period_dax(p)
    prov_filter = (
        f'FILTER(ALL({_PROVIDER}), {_PROVIDER} = "{provider.lower()}"),'
        if provider
        else ""
    )
    pbi = client or get_powerbi_client()
    dax = f"""
{define}
EVALUATE
    TOPN({int(top_n)},
        FILTER(
            SUMMARIZECOLUMNS(
                {_EMAIL},
                {_DEPT},
                {filt}
                {prov_filter}
                "Spend", [Total Spend],
                "TopProduct", [Top Product]
            ),
            NOT ISBLANK({_EMAIL}) && [Spend] > 0),
        [Spend], DESC)
ORDER BY [Spend] DESC
"""
    logger.info("top_users(period=%s, provider=%s, top_n=%d)", p, provider, top_n)
    return _df(pbi.execute_dax(dax))


_EMPLOYEE = "'ai_dim_employee'"


def org_headcount(*, client: PowerBIClient | None = None) -> pd.DataFrame:
    """The org's TRUE headcount: distinct ACTIVE employees in the HR roster.

    Fixed DAX over ``ai_dim_employee``, which the gold pipeline publishes from the
    HR feed. Deliberately does NOT read the model's ``[Org Headcount]`` measure:
    that is the hardcoded literal 4750 (the pipelines' fallback constant), which
    understates the org by ~1,000 people.

    Counts ``person_key`` (employee id, falling back to email) rather than email
    alone, because a distinct count ignores blanks and ~46 active employees have no
    email on file — the email-only definition silently loses them.

    ``ai_dim_employee`` is disconnected from the fact and date tables, so this is
    current headcount and is not affected by any period filter.
    """
    pbi = client or get_powerbi_client()
    dax = f"""
EVALUATE
    ROW(
        "org_headcount",
            CALCULATE (
                DISTINCTCOUNT ( {_EMPLOYEE}[person_key] ),
                {_EMPLOYEE}[is_active] = TRUE()
            )
    )
"""
    logger.info("org_headcount()")
    return _df(pbi.execute_dax(dax))


def org_adoption(
    period: str = "all_time",
    *,
    client: PowerBIClient | None = None,
) -> pd.DataFrame:
    """What % of the ORG actually uses these tools, against TRUE headcount.

    Combines two authoritative sources and does the single division in code so the
    answer is identical every run:

    - ``active_users``   — the model's [Active Users] measure, scoped to *period*.
    - ``org_headcount``  — distinct ACTIVE employees in ``ai_dim_employee``.

    Deliberately does NOT use the model's [% Org Adopted]: that divides by the
    hardcoded 4750, which overstates adoption by roughly 10 percentage points.
    Until that measure is fixed this tool and the dashboard tile will disagree, and
    THIS is the correct one — ``headcount_source`` records which denominator was
    used so the answer can say so.
    """
    p = _norm_period(period)
    users_df = measure_values(["Active Users"], p, client=client)
    active = float(users_df.iloc[0]["Active Users"]) if not users_df.empty else 0.0

    hc_df = org_headcount(client=client)
    headcount = int(hc_df.iloc[0]["org_headcount"]) if not hc_df.empty else 0
    if headcount <= 0:
        raise RuntimeError(
            "Could not read org headcount from ai_dim_employee. Report active users "
            "without a percentage rather than guessing a denominator."
        )

    logger.info("org_adoption(period=%s): %s / %s", p, active, headcount)
    return pd.DataFrame([{
        "period": p,
        "active_users": int(active),
        "org_headcount": headcount,
        "pct_of_org_adopted": active / headcount,
        "headcount_source": "HR feed: distinct employees with status 'active'",
    }])
