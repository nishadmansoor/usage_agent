"""Tests for the predefined, read-only openai_anthropic SQL tools (offline)."""

import pandas as pd
import pytest

from usage_agent.tools import sql_tools as st


def test_resolve_accepts_full_and_alias_names():
    assert st._resolve("openai_anthropic_usage") == "openai_anthropic_usage"
    assert st._resolve("usage") == "openai_anthropic_usage"
    assert st._resolve("DIM_USER_DEPT") == "openai_anthropic_dim_user_dept"


@pytest.mark.parametrize("bad", ["ivanti_neurons_users", "sys.tables", "usage; DROP TABLE x", ""])
def test_resolve_rejects_anything_outside_openai_anthropic(bad):
    with pytest.raises(ValueError):
        st._resolve(bad)


def test_allowed_tables_are_all_openai_anthropic():
    assert st.ALLOWED_TABLE_NAMES  # non-empty
    assert all(t.startswith("openai_anthropic_") for t in st.ALLOWED_TABLE_NAMES)


def test_preview_table_builds_fixed_top_query(monkeypatch):
    captured = {}
    monkeypatch.setattr(st, "_read", lambda sql, params=None, **k: captured.update(sql=sql) or pd.DataFrame())
    st.preview_table("usage", row_limit=5)
    assert captured["sql"] == "SELECT TOP (5) * FROM dbo.openai_anthropic_usage"


def test_preview_table_clamps_row_limit(monkeypatch):
    captured = {}
    monkeypatch.setattr(st, "_read", lambda sql, params=None, **k: captured.update(sql=sql) or pd.DataFrame())
    st.preview_table("openai_anthropic_usage_report", row_limit=10_000)
    assert f"TOP ({st._MAX_PREVIEW_ROWS})" in captured["sql"]


def test_preview_table_rejects_unlisted_table(monkeypatch):
    monkeypatch.setattr(st, "_read", lambda *a, **k: pd.DataFrame())
    with pytest.raises(ValueError):
        st.preview_table("ivanti_neurons_users")


def test_row_count_and_list_are_fixed_openai_anthropic_queries(monkeypatch):
    captured = {}
    monkeypatch.setattr(st, "_read", lambda sql, params=None, **k: captured.setdefault("sqls", []).append(sql) or pd.DataFrame())
    st.table_row_count("dim_product")
    st.list_data_tables()
    assert "COUNT(*) AS row_count FROM dbo.openai_anthropic_dim_product" in captured["sqls"][0]
    assert "openai\\_anthropic%" in captured["sqls"][1]  # schema listing is prefix-scoped
