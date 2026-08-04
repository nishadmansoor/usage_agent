"""Tool registry: the agent's callable tools + their Anthropic tool schemas.

- ``TOOL_FUNCTIONS`` : name -> Python callable (dispatch map)
- ``TOOL_SPECS``     : Anthropic Messages API tool schemas
- ``get_tool_functions()`` / ``get_tool_specs()`` accessors

Query strategy the schemas steer Claude toward (openai_anthropic ONLY):
- ``run_dax_query``  — PRIMARY. DAX over the semantic model's measures; answers
                       reconcile with the Power BI dashboard.
- ``describe_model`` — discover real table/column/measure names before querying.
- ``list_data_tables`` / ``preview_table`` / ``table_row_count`` — FIXED read-only
                       SQL for inspecting raw openai_anthropic rows. The agent
                       never writes SQL; it only picks an allowlisted table.
"""

from __future__ import annotations

from typing import Any, Callable

from ..teams import post_to_teams
from .dax_tools import describe_model, run_dax_query
from .sql_tools import (
    ALLOWED_TABLE_NAMES,
    list_data_tables,
    preview_table,
    table_row_count,
)
from .usage_metrics import department_spend, spend_breakdown, weekly_spend_summary
from .web_tools import web_search

# name -> callable. The agent dispatches tool calls through this map.
TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    # Deterministic metric tools (fixed queries) — PREFER for the questions they cover.
    "weekly_spend_summary": weekly_spend_summary,
    "spend_breakdown": spend_breakdown,
    "department_spend": department_spend,
    # DAX over the semantic model (the ONLY way to compute numbers).
    "run_dax_query": run_dax_query,
    "describe_model": describe_model,
    # Predefined, read-only SQL for inspecting raw openai_anthropic rows.
    "list_data_tables": list_data_tables,
    "preview_table": preview_table,
    "table_row_count": table_row_count,
    "post_to_teams": post_to_teams,
    # Public-web search for external/industry benchmarks (real, citable sources).
    "web_search": web_search,
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
        "PRIMARY TOOL and the ONLY way to compute numbers. Run a DAX query against "
        "the openai_anthropic Power BI semantic model and return rows. Reference the "
        "model's EXISTING measures (from describe_model) so the numbers match the "
        "dashboard, e.g. EVALUATE SUMMARIZECOLUMNS("
        "'openai_anthropic_dim_user_dept'[department], \"Cost\", [Total Spend]). "
        "One EVALUATE statement per call. Only the openai_anthropic tables/measures "
        "exist in this model — never reference anything else.",
        {"dax": {"type": "string", "description": "A single DAX EVALUATE query over the openai_anthropic model."}},
        ["dax"],
    ),
    _spec(
        "list_data_tables",
        "Read-only schema review. Lists every openai_anthropic table and its "
        "columns. Use this to see what raw columns exist before previewing a table. "
        "Fixed query — you cannot write SQL.",
        {},
    ),
    _spec(
        "preview_table",
        "Read-only. Return the first N rows of ONE openai_anthropic table so you can "
        "inspect the raw data. Fixed SELECT TOP (N) * — you choose only the table "
        "(from the allowed list) and the row count; you never write SQL. Numbers "
        "here are raw and may differ from the dashboard — use run_dax_query for "
        "reported figures.",
        {
            "table": {
                "type": "string",
                "enum": ALLOWED_TABLE_NAMES,
                "description": "Which openai_anthropic table to preview.",
            },
            "row_limit": {"type": "integer", "description": "Rows to return (1-100, default 20).", "default": 20},
        },
        ["table"],
    ),
    _spec(
        "table_row_count",
        "Read-only. Return the row count of ONE openai_anthropic table. Fixed query.",
        {
            "table": {
                "type": "string",
                "enum": ALLOWED_TABLE_NAMES,
                "description": "Which openai_anthropic table to count.",
            }
        },
        ["table"],
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
    _spec(
        "web_search",
        "Search the public web for CURRENT external information — use for industry/"
        "peer benchmarks, vendor list pricing, and analyst figures (Gartner, IDC, "
        "Forrester, McKinsey, a16z, Bessemer, FinOps Foundation, etc.). Returns real "
        "results with titles, URLs, and dates to CITE. Use this instead of relying on "
        "memory for any external number; never fabricate a source or URL. Internal "
        "usage/cost figures still come from the Power BI/DAX tools, not web_search.",
        {
            "query": {"type": "string", "description": "The web search query."},
            "max_results": {"type": "integer", "description": "Results to return (1-10, default 5).", "default": 5},
        },
        ["query"],
    ),
]


def get_tool_specs() -> list[dict]:
    """Return the Anthropic ``tools`` schema list (for ``messages.create``)."""
    return TOOL_SPECS


def get_tool_functions() -> dict[str, Callable[..., Any]]:
    """Return the name -> callable dispatch map."""
    return TOOL_FUNCTIONS
