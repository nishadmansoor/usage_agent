"""Offline tests for the deterministic metric tools (Power BI + SQL stubbed)."""

from datetime import date

import pandas as pd
import pytest

from usage_agent.tools import usage_metrics as um
from usage_agent.tools.usage_metrics import (
    department_spend,
    measure_values,
    org_adoption,
    org_headcount,
    spend_breakdown,
    top_users,
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
    stub = StubPBI([{"'ai_usage_report'[model]": "claude-opus-4-8", "[Spend]": 19000.0, "[ActiveUsers]": 599}])
    spend_breakdown("model", "last_month", client=stub)
    q = stub.queries[-1]
    assert "usage_report'[model]" in q
    # Anchored to the model's own is_complete_month, never a wall-clock month string.
    assert "is_complete_month" in q and "[month_start] = LM" in q


def test_month_periods_never_use_the_wall_clock():
    """A clock-derived 'YYYY-MM' silently disagrees with the dashboard."""
    for period in ("last_month", "this_month"):
        define, filt = um._period_dax(period)
        assert "month_start" in filt
        assert f"{date.today().year}-" not in define + filt


def test_spend_breakdown_rejects_bad_dimension():
    with pytest.raises(ValueError):
        spend_breakdown("team", "last_week", client=StubPBI([]))


def test_period_normalisation():
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


# --- measure_values: the allowlist is the guarantee ------------------------
def test_measure_values_reads_named_measures_scoped():
    stub = StubPBI([{"[Total Spend]": 32384.44, "[Active Users]": 1358}])
    df = measure_values(["Total Spend", "Active Users"], "last_week", client=stub)
    assert df.loc[0, "Total Spend"] == 32384.44
    q = stub.queries[-1]
    assert "CALCULATE([Total Spend]" in q and "is_complete_week" in q


def test_measure_values_accepts_a_bare_string_and_bracketed_name():
    stub = StubPBI([{"[Total Spend]": 1.0}])
    measure_values("[total spend]", "all_time", client=stub)
    assert "[Total Spend]" in stub.queries[-1]


def test_measure_values_rejects_unknown_measure():
    with pytest.raises(ValueError, match="not in the allowlist"):
        measure_values(["Total Spend Ish"], "last_week", client=StubPBI([]))


def test_measure_values_cannot_be_used_to_inject_dax():
    with pytest.raises(ValueError):
        measure_values(['Total Spend], "x", SUM(ai_usage[cost_usd])'], client=StubPBI([]))


def test_org_headcount_measure_is_blocked_with_the_reason():
    """The 4750 regression: this measure must be unreachable."""
    with pytest.raises(ValueError, match="4750"):
        measure_values(["Org Headcount"], "all_time", client=StubPBI([]))
    with pytest.raises(ValueError, match="4750"):
        measure_values(["% Org Adopted"], "all_time", client=StubPBI([]))


def test_known_broken_measures_are_blocked():
    for name in ("Spend LW", "Spend LM", "WoW $", "MoM %"):
        with pytest.raises(ValueError):
            measure_values([name], "all_time", client=StubPBI([]))


def test_self_scoped_measure_refuses_double_scoping():
    with pytest.raises(ValueError, match="own time window"):
        measure_values(["Weekly Active Users"], "last_week", client=StubPBI([]))
    # ...but is fine unscoped, and is then read bare rather than re-filtered.
    stub = StubPBI([{"[Weekly Active Users]": 1358}])
    measure_values(["Weekly Active Users"], "all_time", client=stub)
    assert "CALCULATE" not in stub.queries[-1]


def test_blocked_measures_are_absent_from_the_advertised_enum():
    for blocked in ("Org Headcount", "% Org Adopted", "Spend LW", "WoW $"):
        assert blocked not in um.ALLOWED_MEASURE_NAMES


# --- top_users / org_adoption ---------------------------------------------
def test_top_users_ranks_by_total_spend_with_dept_and_product():
    stub = StubPBI([{"'ai_usage_report'[email]": "a@x.com",
                     "'ai_dim_user_dept'[department]": "Tax",
                     "[Spend]": 900.0, "[TopProduct]": "claude_code"}])
    df = top_users("last_week", top_n=5, client=stub)
    assert list(df.columns) == ["email", "department", "Spend", "TopProduct"]
    q = stub.queries[-1]
    assert "[Total Spend]" in q and "[Top Product]" in q and "TOPN(5" in q


def test_org_headcount_counts_person_key_not_email():
    """Email alone drops the ~46 active employees who have none on file."""
    stub = StubPBI([{"[org_headcount]": 5749}])
    df = org_headcount(client=stub)
    assert df.loc[0, "org_headcount"] == 5749
    q = stub.queries[-1]
    assert "ai_dim_employee'[person_key]" in q
    assert "[is_active] = TRUE()" in q
    assert "email_k" not in q
    # Must NOT go via the hardcoded measure.
    assert "[Org Headcount]" not in q


class _SeqPBI:
    """Answers [Active Users] then org_headcount, in call order."""

    def __init__(self, users, headcount):
        self._rows = [[{"[Active Users]": users}], [{"[org_headcount]": headcount}]]
        self.queries = []

    def execute_dax(self, dax, **kwargs):
        self.queries.append(dax)
        return self._rows.pop(0)


def test_org_adoption_uses_true_headcount_not_4750():
    stub = _SeqPBI(users=2716, headcount=5749)
    row = org_adoption("all_time", client=stub).loc[0]
    assert row["org_headcount"] == 5749          # roster count, not the constant
    assert row["active_users"] == 2716
    assert row["pct_of_org_adopted"] == pytest.approx(2716 / 5749)
    assert "active" in row["headcount_source"]
    # Never the model's [% Org Adopted], which divides by the hardcoded 4750.
    assert not any("% Org Adopted" in q for q in stub.queries)
    # Sanity: the honest number must be well below the 4750-based one.
    assert row["pct_of_org_adopted"] < 2716 / 4750


def test_org_adoption_refuses_to_guess_a_denominator():
    stub = _SeqPBI(users=1200, headcount=0)
    with pytest.raises(RuntimeError, match="without a percentage"):
        org_adoption("all_time", client=stub)
