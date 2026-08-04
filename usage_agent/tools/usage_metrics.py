"""Deterministic metric tools — FIXED queries for the common questions.

The agent normally writes its own DAX/SQL, which is flexible but non-deterministic:
the same question can take a different path and return a slightly different number.
These tools run a *fixed*, dashboard-reconciling query for the highest-traffic
questions, so the answer is identical every run — CLI, bot, or repeat. Both
surfaces share this module, so they can't diverge.

Coverage:
- ``weekly_spend_summary``  — last COMPLETE week vs the prior week: total spend,
                              WoW $/%%, active users. (The weekly-briefing headline.)
- ``spend_breakdown``       — spend + active users by provider / model / product
                              for a chosen period.
- ``department_spend``      — spend by department (cross-source: usage -> dim_user
                              -> Ivanti directory), for a chosen period.

Period-scoping uses the model's own ``is_complete_week`` for weeks and calendar
months for months, matching the guidance in the system prompt.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..clients.powerbi import PowerBIClient, get_powerbi_client
from ..logging_config import get_logger

logger = get_logger(__name__)

_DATE = "'ai_dim_date'"

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


def _prev_month(today: date | None = None) -> str:
    d = today or date.today()
    return f"{d.year - 1}-12" if d.month == 1 else f"{d.year}-{d.month - 1:02d}"


def _this_month(today: date | None = None) -> str:
    d = today or date.today()
    return f"{d.year}-{d.month:02d}"


def _month_range(month: str) -> tuple[str, str]:
    """'YYYY-MM' -> ('YYYY-MM-01', first-of-next-month) as text dates."""
    y, m = int(month[:4]), int(month[5:7])
    start = f"{y:04d}-{m:02d}-01"
    end = f"{y + 1:04d}-01-01" if m == 12 else f"{y:04d}-{m + 1:02d}-01"
    return start, end


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


def _period_dax(period: str) -> tuple[str, str]:
    """Return (DEFINE block, SUMMARIZECOLUMNS filter fragment) for a period."""
    p = _norm_period(period)
    lw_var = (
        f"VAR LW = CALCULATE(MAX({_DATE}[week_start]), "
        f"FILTER(ALL({_DATE}), {_DATE}[is_complete_week] = TRUE()))"
    )
    if p == "last_week":
        return f"DEFINE {lw_var}", f"FILTER(ALL({_DATE}), {_DATE}[week_start] = LW),"
    if p == "prior_week":
        return f"DEFINE {lw_var} VAR PW = LW - 7", f"FILTER(ALL({_DATE}), {_DATE}[week_start] = PW),"
    if p == "last_month":
        return "", f'FILTER(ALL({_DATE}), {_DATE}[month] = "{_prev_month()}"),'
    if p == "this_month":
        return "", f'FILTER(ALL({_DATE}), {_DATE}[month] = "{_this_month()}"),'
    return "", ""  # all_time


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
    openai_anthropic semantic model.

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
