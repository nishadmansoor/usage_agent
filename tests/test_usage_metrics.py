"""Offline tests for the deterministic metric tools (Power BI + SQL stubbed)."""

from datetime import date

import pytest

from usage_agent.tools import usage_metrics as um
from usage_agent.tools.usage_metrics import (
    department_spend,
    spend_breakdown,
    weekly_spend_summary,
)


class StubPBI:
    def __init__(self, rows):
        self._rows = rows
        self.queries = []

    def execute_dax(self, dax, **kwargs):
        self.queries.append(dax)
        return self._rows


def test_weekly_spend_summary_columns():
    stub = StubPBI([{
        "[week_start]": "2026-07-06T00:00:00",
        "[spend_last_week]": 32384.44,
        "[spend_prior_week]": 32097.95,
        "[wow_dollar]": 286.49,
        "[wow_pct]": 0.0089,
        "[active_users_last_week]": 1358,
    }])
    df = weekly_spend_summary(client=stub)
    assert "spend_last_week" in df.columns and "wow_pct" in df.columns
    assert df.loc[0, "spend_last_week"] == 32384.44
    # fixed query scopes by complete week
    assert "is_complete_week" in stub.queries[-1]


def test_spend_breakdown_provider_last_week():
    stub = StubPBI([{"'openai_anthropic_dim_provider'[provider]": "anthropic", "[Spend]": 32000.0, "[ActiveUsers]": 1358}])
    df = spend_breakdown("provider", "last_week", client=stub)
    assert list(df.columns) == ["provider", "Spend", "ActiveUsers"]  # cleaned
    q = stub.queries[-1]
    assert "dim_provider'[provider]" in q
    assert "is_complete_week" in q  # last_week filter


def test_spend_breakdown_model_last_month_uses_month_filter():
    stub = StubPBI([{"'openai_anthropic_usage_report'[model]": "claude-opus-4-8", "[Spend]": 19000.0, "[ActiveUsers]": 599}])
    spend_breakdown("model", "last_month", client=stub)
    q = stub.queries[-1]
    assert "[month] =" in q and "usage_report'[model]" in q


def test_spend_breakdown_rejects_bad_dimension():
    with pytest.raises(ValueError):
        spend_breakdown("team", "last_week", client=StubPBI([]))


def test_period_and_month_helpers():
    assert um._prev_month(date(2026, 1, 15)) == "2025-12"
    assert um._prev_month(date(2026, 7, 15)) == "2026-06"
    assert um._this_month(date(2026, 7, 15)) == "2026-07"
    assert um._month_range("2026-06") == ("2026-06-01", "2026-07-01")
    assert um._month_range("2026-12") == ("2026-12-01", "2027-01-01")
    assert um._norm_period("Last Week") == "last_week"
    assert um._norm_period("previous_month") == "last_month"
    with pytest.raises(ValueError):
        um._norm_period("yesterday")


def test_department_spend_uses_dax_over_dim_user_dept():
    # Department now comes from the openai_anthropic model (no SQL, no Ivanti).
    stub = StubPBI([{"'openai_anthropic_dim_user_dept'[department]": "Engineering",
                     "[Spend]": 1000.0, "[ActiveUsers]": 12}])
    df = department_spend("all_time", client=stub)
    q = stub.queries[-1]
    assert "dim_user_dept'[department]" in q
    assert "[Total Spend]" in q
    assert "ivanti" not in q.lower()  # never touches the external directory
    assert df.loc[0, "department"] == "Engineering"  # column cleaned


def test_department_spend_last_week_and_provider_filter():
    stub = StubPBI([{"'openai_anthropic_dim_user_dept'[department]": "Sales",
                     "[Spend]": 500.0, "[ActiveUsers]": 4}])
    department_spend("last_week", provider="anthropic", client=stub)
    q = stub.queries[-1]
    assert "is_complete_week" in q                       # last_week scoping
    assert 'dim_provider\'[provider] = "anthropic"' in q  # provider filter
    assert "ivanti" not in q.lower()


def test_department_spend_rejects_bad_provider():
    with pytest.raises(ValueError):
        department_spend("last_week", provider="marketing", client=StubPBI([]))
