"""Offline tests for the flexible model reader (Power BI stubbed).

The guarantee under test: the agent can reach the whole model, but every identifier
that reaches the DAX comes from the model's own metadata — never from agent text.
"""

import pytest

from usage_agent.tools import model_query as mq
from usage_agent.tools.model_query import forecast_outlook, query_model, resolve_period
from usage_agent.tools.validation import is_read_only_dax


class StubPBI:
    """Serves INFO.VIEW.* metadata, then canned rows for the real query."""

    MEASURES = ["Total Spend", "Active Users", "Weekly Active Users", "Trend Spend"]
    COLUMNS = [
        ("ai_dim_date", "week_start"), ("ai_dim_date", "month"),
        ("ai_dim_date", "day_name"), ("ai_usage_report", "provider"),
        ("ai_dim_user_dept", "region"), ("ai_dim_user_dept", "department"),
        ("otter_daily_users", "active_users"),
    ]

    def __init__(self, rows=None):
        self._rows = rows or [{"[Total Spend]": 1.0}]
        self.queries = []

    def execute_dax(self, dax, **kwargs):
        self.queries.append(dax)
        if "INFO.VIEW.MEASURES" in dax:
            return [{"[Name]": m} for m in self.MEASURES]
        if "INFO.VIEW.COLUMNS" in dax:
            return [{"[Table]": t, "[Name]": c} for t, c in self.COLUMNS]
        return self._rows

    @property
    def last(self):
        return self.queries[-1]


# --- validation is the security boundary ----------------------------------
def test_rejects_measure_that_does_not_exist():
    with pytest.raises(ValueError, match="does not exist"):
        query_model(["Made Up Measure"], client=StubPBI())


def test_rejects_column_that_does_not_exist():
    with pytest.raises(ValueError, match="does not exist"):
        query_model(["Total Spend"], ["ai_dim_date[nope]"], client=StubPBI())


def test_rejects_malformed_column_ref():
    with pytest.raises(ValueError, match="malformed"):
        query_model(["Total Spend"], ["just_a_table_name"], client=StubPBI())


def test_dax_injection_via_column_is_impossible():
    """Even a ref that parses must MATCH metadata, and is rebuilt from it."""
    with pytest.raises(ValueError):
        query_model(
            ["Total Spend"],
            ["ai_dim_date[week_start] ) EVALUATE ROW(\"x\",1"],
            client=StubPBI(),
        )


def test_dax_injection_via_measure_is_impossible():
    with pytest.raises(ValueError):
        query_model(['Total Spend], "x", 1'], client=StubPBI())


def test_filter_values_are_escaped_not_interpolated():
    stub = StubPBI()
    query_model(
        ["Total Spend"], ["ai_usage_report[provider]"], "all_time",
        filters=[{"column": "ai_usage_report[provider]", "values": ['an"thropic']}],
        client=stub,
    )
    # The embedded quote is doubled, so it cannot terminate the DAX literal.
    assert '"an""thropic"' in stub.last
    assert is_read_only_dax(stub.last)


def test_every_generated_query_is_read_only():
    stub = StubPBI()
    query_model(["Total Spend"], ["ai_dim_date[week_start]"], "last_8_weeks", client=stub)
    assert is_read_only_dax(stub.last)
    query_model(["Total Spend"], period="2026-06", client=stub)
    assert is_read_only_dax(stub.last)


# --- period flexibility ---------------------------------------------------
@pytest.mark.parametrize("period,expect", [
    ("all_time", ""),
    ("last_week", "is_complete_week"),
    ("prior_week", "LW - 7"),
    ("last_month", "is_complete_month"),
    ("2026-06", '[month] = "2026-06"'),
    ("2026", "[year] = 2026"),
    ("last_8_weeks", "LW - 56"),
    ("last_30_days", "AD - 30"),
    ("last_6_months", "EDATE(LM, -6)"),
])
def test_period_forms(period, expect):
    # Named windows put their anchor in the DEFINE block and reference it from the
    # filter, so check both halves of what resolve_period returns.
    define, filt = resolve_period(period)
    assert expect in define + filt


def test_period_rejects_nonsense_with_guidance():
    with pytest.raises(ValueError, match="rolling window"):
        resolve_period("since christmas")


def test_period_rejects_impossible_month():
    with pytest.raises(ValueError, match="valid month"):
        resolve_period("2026-13")


# --- trends must read chronologically -------------------------------------
def test_time_breakdown_sorts_by_time_not_by_size():
    """A trend sorted by magnitude is unreadable and invites misreporting."""
    stub = StubPBI()
    query_model(["Total Spend"], ["ai_dim_date[week_start]"], "last_8_weeks", client=stub)
    assert "ORDER BY 'ai_dim_date'[week_start] ASC" in stub.last


def test_non_time_breakdown_still_ranks_by_measure():
    stub = StubPBI()
    query_model(["Total Spend"], ["ai_dim_user_dept[region]"], "last_week", client=stub)
    assert "ORDER BY [Total Spend] DESC" in stub.last


def test_day_name_is_not_treated_as_temporal():
    """[day_name] is 'Mon'/'Tue' — alphabetical order would be worse than by size."""
    stub = StubPBI()
    query_model(["Total Spend"], ["ai_dim_date[day_name]"], client=stub)
    assert "ORDER BY [Total Spend] DESC" in stub.last


def test_top_n_ranks_by_measure_even_for_a_time_column():
    stub = StubPBI()
    query_model(["Total Spend"], ["ai_dim_date[week_start]"], top_n=3, client=stub)
    assert "TOPN(3" in stub.last and "[Total Spend], DESC" in stub.last


# --- reach + guards -------------------------------------------------------
def test_reaches_tables_the_old_fixed_tools_never_exposed():
    stub = StubPBI()
    for col in ("ai_dim_user_dept[region]", "otter_daily_users[active_users]"):
        query_model(["Total Spend"], [col], client=stub)
        assert col.split("[")[0] in stub.last


def test_ungrouped_query_returns_one_scoped_row():
    stub = StubPBI()
    query_model(["Total Spend", "Active Users"], period="last_week", client=stub)
    assert "ROW(" in stub.last and "CALCULATE([Total Spend]" in stub.last


def test_self_scoped_measure_refuses_a_period():
    with pytest.raises(ValueError, match="own time window"):
        query_model(["Weekly Active Users"], period="last_week", client=StubPBI())


def test_blocked_measure_keeps_its_explanation():
    with pytest.raises(ValueError, match="4750"):
        query_model(["Org Headcount"], client=StubPBI())


def test_requires_at_least_one_measure():
    with pytest.raises(ValueError, match="at least one measure"):
        query_model([], client=StubPBI())


# --- forecast rollup ------------------------------------------------------
def test_forecast_drops_partial_months(monkeypatch):
    """Aug-2026 has 5 Mondays; a month missing any is never reported."""
    import pandas as pd

    weekly = pd.DataFrame({
        # Sep 2026 complete (4 Mondays), Oct deliberately short one Monday.
        "week": ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                 "2026-10-05", "2026-10-12"],
        "Trend Spend": [10.0] * 6,
        "Trend Spend Low": [9.0] * 6,
        "Trend Spend High": [11.0] * 6,
        "Trend Users": [100] * 6,
    })
    calls = {"n": 0}

    def fake_query(measures, group_by=None, period="all_time", **kw):
        calls["n"] += 1
        if "Last Complete Week" in measures:
            return pd.DataFrame([{"Last Complete Week": "2026-08-31"}])
        return weekly

    monkeypatch.setattr(mq, "query_model", fake_query)
    out = forecast_outlook(3)
    assert list(out["month"]) == ["2026-09"]      # Oct is partial -> excluded
    assert out.iloc[0]["weeks"] == 4
    assert out.iloc[0]["spend"] == pytest.approx(40.0)
    assert out.iloc[0]["spend_low"] == pytest.approx(36.0)


def test_forecast_rejects_zero_months():
    with pytest.raises(ValueError, match="at least 1"):
        forecast_outlook(0)
