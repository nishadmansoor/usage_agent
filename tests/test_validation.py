"""SQL safety validation tests (offline)."""

import pytest

from usage_agent.tools import validation


def test_accepts_select_and_with():
    assert validation.is_read_only_sql("SELECT 1")
    assert validation.is_read_only_sql("WITH t AS (SELECT 1) SELECT * FROM t")


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE usage SET cost_usd = 0",
        "DELETE FROM usage",
        "DROP TABLE usage",
        "SELECT 1; DROP TABLE usage",         # stacked
        "EXEC sp_who",
        "SELECT * INTO other FROM usage",
        "",
    ],
)
def test_rejects_non_readonly(sql):
    assert not validation.is_read_only_sql(sql)
    with pytest.raises(ValueError):
        validation.ensure_read_only(sql)


def test_validate_identifier():
    assert validation.validate_identifier("dbo.usage") == "dbo.usage"
    assert validation.validate_identifier("usage") == "usage"
    with pytest.raises(ValueError):
        validation.validate_identifier("usage; DROP TABLE x")
