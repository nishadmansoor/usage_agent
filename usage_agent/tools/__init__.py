"""Tool registry: the agent's callable tools + their Anthropic tool schemas.

- ``TOOL_FUNCTIONS`` : name -> Python callable (dispatch map)
- ``TOOL_SPECS``     : Anthropic Messages API tool schemas
- ``get_tool_functions()`` / ``get_tool_specs()`` accessors

Query strategy the schemas enforce — the agent writes NO query language at all:
- ``measure_values`` / ``weekly_spend_summary`` / ``spend_breakdown`` /
  ``department_spend`` / ``top_users`` / ``org_adoption`` — FIXED DAX built in
  ``usage_metrics``. The agent picks an allowlisted measure/dimension/period; every
  reported figure is one of the dashboard's own measures, read back unmodified.
- ``describe_model`` — read-only metadata, so the agent names REAL measures.
- ``org_headcount`` — the true active-employee count, from ``ai_dim_employee``
  (NOT the ``[Org Headcount]`` measure, which is a hardcoded 4750).
- ``list_data_tables`` / ``preview_table`` / ``table_row_count`` — FIXED read-only
  SQL for INSPECTING raw rows. Never a source of reported numbers.

Deliberately NOT registered: ``run_dax_query``. Free-form DAX let the agent author
its own aggregates, which is how figures drifted from the dashboard. It remains in
``dax_tools`` for human/debug use only and is unreachable by the model.
"""

from __future__ import annotations

from typing import Any, Callable

from ..teams import post_to_teams
from .dax_tools import describe_model
from .model_query import forecast_outlook, query_model
from .sql_tools import (
    ALLOWED_TABLE_NAMES,
    list_data_tables,
    preview_table,
    table_row_count,
)
from .usage_metrics import (
    ALLOWED_MEASURE_NAMES,
    department_spend,
    measure_values,
    org_adoption,
    org_headcount,
    spend_breakdown,
    top_users,
    weekly_spend_summary,
)
from .web_tools import web_search

# name -> callable. The agent dispatches tool calls through this map.
TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    # Generic, metadata-validated reader — reaches the WHOLE model, still no
    # agent-authored DAX. Use when no narrower tool fits.
    "query_model": query_model,
    "forecast_outlook": forecast_outlook,
    # Deterministic metric tools (fixed queries) — the ONLY source of numbers.
    "measure_values": measure_values,
    "weekly_spend_summary": weekly_spend_summary,
    "spend_breakdown": spend_breakdown,
    "department_spend": department_spend,
    "top_users": top_users,
    "org_adoption": org_adoption,
    "org_headcount": org_headcount,
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
        "PREFERRED for 'spend by team/department' questions. Fixed DAX grouping "
        "[Total Spend] and [Active Users] by ai_dim_user_dept[department] for a "
        "period — entirely inside the semantic model, so it reconciles with the "
        "dashboard.",
        {
            "period": {"type": "string", "enum": _PERIOD_ENUM, "description": "Time window (default last_week)."},
            "provider": {"type": "string", "enum": ["anthropic", "openai"], "description": "Optional provider filter."},
            "top_n": {"type": "integer", "description": "Max departments.", "default": 15},
        },
    ),
    _spec(
        "top_users",
        "PREFERRED for 'top spenders / biggest users' questions. Fixed DAX: the "
        "highest-spend users for a period with each one's department and most-used "
        "product, ranked by [Total Spend].",
        {
            "period": {"type": "string", "enum": _PERIOD_ENUM, "description": "Time window (default last_week)."},
            "provider": {"type": "string", "enum": ["anthropic", "openai", "microsoft"], "description": "Optional provider filter."},
            "top_n": {"type": "integer", "description": "How many users.", "default": 10},
        },
    ),
    _spec(
        "org_adoption",
        "REQUIRED for 'what % of the org uses AI', adoption rate, or AI spend per "
        "employee. Returns active_users (the model's [Active Users]), the TRUE "
        "org_headcount (distinct active employees in the HR feed), and "
        "pct_of_org_adopted. Use this instead of the model's [% Org Adopted], which "
        "divides by a hardcoded 4750. State that headcount is HR active employees.",
        {"period": {"type": "string", "enum": _PERIOD_ENUM, "description": "Time window for active users (default all_time)."}},
    ),
    _spec(
        "org_headcount",
        "The organisation's TRUE headcount: distinct employees with HR status "
        "'active', from the ai_dim_employee roster. Fixed, parameterless query and "
        "the ONLY valid headcount — the [Org Headcount] MEASURE is a hardcoded 4750 "
        "that understates the org by ~1,000 people and must never be reported. "
        "Headcount is current and not affected by period. Prefer org_adoption for a "
        "percentage.",
        {},
    ),
    _spec(
        "query_model",
        "THE FLEXIBLE READER — use this whenever no narrower tool fits. Reads the "
        "model's OWN measures, optionally grouped and filtered, and returns them "
        "unchanged so every figure matches the dashboard. Reaches EVERY table, "
        "column and measure in the model (including the otter_* tables, and "
        "dimensions like region and connector_name). Read-only; you never write DAX. "
        "TRENDS: group_by 'ai_dim_date[week_start]' or 'ai_dim_date[month]'. "
        "Call describe_model first to get exact names — invalid names are refused. "
        "Do NOT do arithmetic on the results; if a figure needs a calculation that "
        "is not a measure, say so.",
        {
            "measures": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Measure names from describe_model, e.g. ['Total Spend'].",
            },
            "group_by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional Table[Column] refs to break down by, e.g. "
                               "['ai_dim_date[week_start]'] for a weekly trend, or "
                               "['ai_dim_user_dept[region]']. Omit for one total row.",
            },
            "period": {
                "type": "string",
                "description": "all_time | last_week | prior_week | last_month | "
                               "this_month | an exact month '2026-06' | a year '2026' "
                               "| a rolling window 'last_8_weeks' / 'last_30_days' / "
                               "'last_6_months'. Measures whose name already states a "
                               "window need all_time.",
            },
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string", "description": "Table[Column] to filter."},
                        "values": {"type": "array", "items": {"type": "string"}, "description": "Values to keep (IN)."},
                    },
                    "required": ["column", "values"],
                },
                "description": "Optional equality/IN filters, ANDed together.",
            },
            "top_n": {"type": "integer", "description": "Keep only the top N rows."},
            "order_by": {"type": "string", "description": "Measure to rank by (default: the first)."},
        },
        ["measures"],
    ),
    _spec(
        "forecast_outlook",
        "REQUIRED for projections / 'next N months' / outlook questions. Returns "
        "projected spend (with low/high band) and average active users per COMPLETE "
        "calendar month, for future weeks only. The weekly-to-monthly rollup and the "
        "partial-month exclusion are done in code, so the 'partial months' bug cannot "
        "happen — never attempt that rollup yourself. If fewer complete months are "
        "available than requested, fewer rows come back: say how many you got.",
        {
            "months": {"type": "integer", "description": "How many complete months ahead (default 3).", "default": 3},
            "provider": {"type": "string", "enum": ["anthropic", "openai"], "description": "Optional provider filter."},
        },
    ),
    _spec(
        "describe_model",
        "List the Power BI semantic model's tables, columns, and MEASURE names. "
        "Call this once when you need to confirm what exists. Note you cannot write "
        "DAX — to READ a measure, pass its name to measure_values.",
        {},
    ),
    _spec(
        "measure_values",
        "THE GENERAL-PURPOSE NUMBER TOOL. Reads the semantic model's OWN measures "
        "for a period and returns them unchanged, so every figure equals the "
        "dashboard's. You choose only which allowlisted measures to read and the "
        "period — you cannot write DAX and must not recompute or combine figures "
        "yourself. Measures whose name already states a window (e.g. 'Spend Last "
        "7d', 'Weekly Active Users', 'DAU Last Week (Copilot)', any forecast "
        "measure) must be read with period='all_time'.",
        {
            "measures": {
                "type": "array",
                "items": {"type": "string", "enum": ALLOWED_MEASURE_NAMES},
                "description": "One or more measure names to read.",
            },
            "period": {"type": "string", "enum": _PERIOD_ENUM, "description": "Time window (default last_week)."},
        },
        ["measures"],
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
        "(from the allowed list) and the row count; you never write SQL. These are "
        "BRONZE tables: raw, a different layer from the semantic model, and for the "
        "*_usage_report / *_dim_user_dept / *_forecast tables a stale pre-split copy. "
        "For inspection only — NEVER report a figure from here; use measure_values.",
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
        "Use ONLY when the user EXPLICITLY asks to compare against peers / the industry — "
        "never for internal usage/cost questions. "
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
