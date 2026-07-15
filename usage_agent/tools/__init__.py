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

from ..teams import post_to_teams
from .dax_tools import describe_model, run_dax_query
from .sql_tools import run_sql_query
from .usage_metrics import department_spend, spend_breakdown, weekly_spend_summary

# name -> callable. The agent dispatches tool calls through this map.
TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    # Deterministic metric tools (fixed queries) — PREFER for the questions they cover.
    "weekly_spend_summary": weekly_spend_summary,
    "spend_breakdown": spend_breakdown,
    "department_spend": department_spend,
    # General-purpose query tools.
    "run_dax_query": run_dax_query,
    "describe_model": describe_model,
    "run_sql_query": run_sql_query,
    "post_to_teams": post_to_teams,
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


_PERIOD_ENUM = ["last_week", "prior_week", "last_month", "this_month", "all_time"]

TOOL_SPECS: list[dict] = [
    _spec(
        "weekly_spend_summary",
        "PREFERRED for weekly spend / week-over-week questions. Returns a FIXED, "
        "dashboard-reconciling result for the LAST COMPLETE week vs the prior week: "
        "total spend, WoW $ and %, and active users. Deterministic — use this "
        "instead of hand-writing DAX for 'last week' / 'WoW' questions.",
        {},
    ),
    _spec(
        "spend_breakdown",
        "PREFERRED for 'spend by provider/model/product' questions. Fixed, "
        "dashboard-reconciling spend + active users grouped by a dimension for a "
        "period. Use instead of hand-writing DAX.",
        {
            "dimension": {"type": "string", "enum": ["provider", "model", "product"], "description": "What to group by."},
            "period": {"type": "string", "enum": _PERIOD_ENUM, "description": "Time window (default last_week)."},
            "top_n": {"type": "integer", "description": "Max rows.", "default": 20},
        },
        ["dimension"],
    ),
    _spec(
        "department_spend",
        "PREFERRED for 'spend by team/department' questions. Fixed cross-source "
        "query (usage joined to the Ivanti directory by email) grouped by "
        "Department for a period. Raw SQL aggregate (department isn't a dashboard "
        "measure), but deterministic.",
        {
            "period": {"type": "string", "enum": _PERIOD_ENUM, "description": "Time window (default last_week)."},
            "provider": {"type": "string", "enum": ["anthropic", "openai"], "description": "Optional provider filter."},
            "top_n": {"type": "integer", "description": "Max departments.", "default": 15},
        },
    ),
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
    _spec(
        "post_to_teams",
        "Post the answer or briefing to Microsoft Teams as an Adaptive Card via the "
        "configured Workflows webhook. Use ONLY when the user explicitly asks to "
        "send/post/share/notify to Teams or a channel. Pass the full message text "
        "(Markdown supported). If Teams isn't configured the call is skipped and "
        "reports that, so it is safe to attempt.",
        {
            "text": {"type": "string", "description": "Message body to post (Markdown supported)."},
            "title": {"type": "string", "description": "Optional card heading; defaults to a timestamped title."},
            "question": {"type": "string", "description": "The original user question, shown above the answer in the card."},
        },
        ["text"],
    ),
]


def get_tool_specs() -> list[dict]:
    """Return the Anthropic ``tools`` schema list (for ``messages.create``)."""
    return TOOL_SPECS


def get_tool_functions() -> dict[str, Callable[..., Any]]:
    """Return the name -> callable dispatch map."""
    return TOOL_FUNCTIONS
