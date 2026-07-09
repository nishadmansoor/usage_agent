"""DAX tool tests (offline) — a stub Power BI client replaces the network."""

import pandas as pd
import pytest

from usage_agent.tools import dax_tools


class StubPBI:
    """Returns canned rows; routes metadata queries by INFO.* substring."""

    def __init__(self, rows=None):
        self._rows = rows or []
        self.queries = []

    def execute_dax(self, dax, **kwargs):
        self.queries.append(dax)
        if "INFO.VIEW.TABLES" in dax:
            return [{"[Name]": "Usage", "[IsHidden]": False}]
        if "INFO.VIEW.COLUMNS" in dax:
            return [{"[Table]": "Usage", "[Name]": "cost_usd", "[IsHidden]": False, "[DataType]": "Double"}]
        if "INFO.VIEW.MEASURES" in dax:
            return [{"[Table]": "Usage", "[Name]": "Total Cost USD", "[IsHidden]": False}]
        return self._rows


def test_run_dax_query_returns_dataframe():
    stub = StubPBI(rows=[{"Dept": "Eng", "Cost": 100}, {"Dept": "Sales", "Cost": 50}])
    df = dax_tools.run_dax_query("EVALUATE SUMMARIZECOLUMNS(...)", client=stub)
    assert isinstance(df, pd.DataFrame)
    assert list(df["Dept"]) == ["Eng", "Sales"]
    assert stub.queries == ["EVALUATE SUMMARIZECOLUMNS(...)"]


def test_run_dax_query_row_cap():
    stub = StubPBI(rows=[{"n": i} for i in range(10)])
    df = dax_tools.run_dax_query("EVALUATE X", client=stub, row_limit=3)
    assert len(df) == 3


def test_run_dax_query_rejects_empty():
    with pytest.raises(ValueError):
        dax_tools.run_dax_query("   ", client=StubPBI())


def test_describe_model_lists_tables_columns_measures():
    text = dax_tools.describe_model(client=StubPBI())
    assert "Usage" in text          # table
    assert "cost_usd" in text       # column
    assert "Total Cost USD" in text  # measure (the important one)


def test_describe_model_degrades_when_metadata_fails():
    class Failing(StubPBI):
        def execute_dax(self, dax, **kwargs):
            raise RuntimeError("INFO functions not supported")

    text = dax_tools.describe_model(client=Failing())
    # No crash; sections note the unavailable metadata.
    assert "unavailable" in text.lower()
