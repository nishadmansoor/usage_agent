"""SQL safety-net validation tests (offline)."""

import pytest

from usage_agent.tools import validation


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "WITH t AS (SELECT 1) SELECT * FROM t",
        "SELECT TOP (20) * FROM dbo.openai_anthropic_usage",
        "SELECT COUNT(*) AS row_count FROM dbo.openai_anthropic_usage_report",
        # A leading comment must not disguise (or block) the SELECT.
        "-- preview\nSELECT 1",
        "/* note */ SELECT 1",
        # A forbidden keyword that lives ONLY inside a comment is inert (SQL Server
        # never executes commented text), so the statement is still read-only.
        "SELECT 1 /* ; DROP TABLE usage */",
        "SELECT 1 -- ; DROP TABLE usage\n",
    ],
)
def test_accepts_read_only(sql):
    assert validation.is_read_only_sql(sql)
    validation.ensure_read_only(sql)  # must not raise


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE usage SET cost_usd = 0",
        "DELETE FROM usage",
        "DROP TABLE usage",
        "SELECT 1; DROP TABLE usage",              # stacked / batched
        "EXEC sp_who",
        "SELECT * INTO other FROM usage",
        "",
        # external-data-source / file-read vectors — the denylist bug fix
        "SELECT * FROM OPENROWSET('SQLNCLI','...','SELECT 1')",
        "SELECT * FROM OPENQUERY(srv, 'SELECT 1')",
        "SELECT * FROM OPENDATASOURCE('SQLNCLI','...')",
        # time-delay / server-control
        "WAITFOR DELAY '00:00:10'",
        "SELECT * FROM usage WHERE 1=1; SHUTDOWN",
    ],
)
def test_rejects_non_readonly(sql):
    assert not validation.is_read_only_sql(sql)
    with pytest.raises(ValueError):
        validation.ensure_read_only(sql)


def test_validate_identifier():
    assert validation.validate_identifier("dbo.openai_anthropic_usage") == "dbo.openai_anthropic_usage"
    assert validation.validate_identifier("usage") == "usage"
    with pytest.raises(ValueError):
        validation.validate_identifier("usage; DROP TABLE x")
