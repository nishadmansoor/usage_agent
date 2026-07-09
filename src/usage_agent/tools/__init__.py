"""Tool registry: the agent's callable tools + their Anthropic tool schemas.

- ``TOOL_FUNCTIONS`` : name -> Python callable (dispatch map)
- ``TOOL_SPECS``     : Anthropic Messages API tool schemas
- ``get_tool_functions()`` / ``get_tool_specs()`` accessors

Query strategy the schemas steer Claude toward:
- ``run_dax_query``  — PRIMARY. Answers reconcile with the Power BI dashboard.
- ``describe_model`` — discover real table/column/measure names before querying.
- ``run_sql_query``  — drill-down fallback for columns the model doesn't expose.
"""

from __future__ import annotations

from typing import Any, Callable

from .dax_tools import describe_model, run_dax_query
from .sql_tools import run_sql_query

# name -> callable. The agent dispatches tool calls through this map.
TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "run_dax_query": run_dax_query,
    "describe_model": describe_model,
    "run_sql_query": run_sql_query,
}


def _spec(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    """Build a single Anthropic tool schema entry."""
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    }


TOOL_SPECS: list[dict] = [
    _spec(
        "describe_model",
        "List the Power BI semantic model's tables, columns, and MEASURE names. "
        "Call this first (once) when you need to know what measures/columns exist "
        "so you can reference the model's own measures in run_dax_query.",
        {},
    ),
    _spec(
        "run_dax_query",
        "PRIMARY TOOL. Run a DAX query against the Fabric Power BI semantic model "
        "and return rows. Prefer this for any usage/cost/adoption question so the "
        "numbers match the dashboard: reference the model's existing measures "
        "(e.g. EVALUATE SUMMARIZECOLUMNS('DimUser'[Department], \"Cost\", "
        "[Total Cost USD])). One EVALUATE statement per call.",
        {"dax": {"type": "string", "description": "A single DAX EVALUATE query."}},
        ["dax"],
    ),
    _spec(
        "run_sql_query",
        "Drill-down fallback. Run a read-only SQL (SELECT/WITH) query against the "
        "Fabric SQL endpoint for raw columns the semantic model doesn't expose. "
        "T-SQL; prefer TOP N / aggregation. Note: numbers here are raw and may "
        "differ from the dashboard's measures — prefer run_dax_query when possible.",
        {"sql": {"type": "string", "description": "A single read-only SELECT/WITH statement."}},
        ["sql"],
    ),
]


def get_tool_specs() -> list[dict]:
    """Return the Anthropic ``tools`` schema list (for ``messages.create``)."""
    return TOOL_SPECS


def get_tool_functions() -> dict[str, Callable[..., Any]]:
    """Return the name -> callable dispatch map."""
    return TOOL_FUNCTIONS
