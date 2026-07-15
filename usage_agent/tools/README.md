# `usage_agent/tools/` — the agent's tools

Two things live here: the **tool registry** (`__init__.py`) that Claude sees, and
the **tool implementations**. The guiding rule: **the agent computes only with DAX
and never writes SQL.**

## Registry — `__init__.py`

- `TOOL_FUNCTIONS` — `name -> Python callable` dispatch map used by `UsageAgent`.
- `TOOL_SPECS` — the Anthropic tool schemas advertised to Claude.
- `get_tool_functions()` / `get_tool_specs()` accessors.

`test_tools_registry.py` asserts the two stay in sync and that `run_sql_query` is
**absent** (the agent must not be able to write SQL).

## The tools

| Tool | File | What it does |
|---|---|---|
| `describe_model` | `dax_tools.py` | Lists the model's visible tables, columns, and **measures** (incl. hidden measures) via DAX `INFO.VIEW.*`. Call once up front so the agent references real measure names. |
| `run_dax_query` | `dax_tools.py` | **PRIMARY / only compute path.** Runs one DAX `EVALUATE` and returns rows. Rows capped at `QUERY_ROW_LIMIT`. |
| `weekly_spend_summary` | `usage_metrics.py` | Fixed DAX: last complete week vs prior week (spend, WoW $/%, active users). Anchored to the model's `is_complete_week`. |
| `spend_breakdown` | `usage_metrics.py` | Fixed DAX: spend + active users by `provider` / `model` / `product` for a period. |
| `department_spend` | `usage_metrics.py` | Fixed DAX: spend + active users by `openai_anthropic_dim_user_dept[department]` (no external directory). |
| `list_data_tables` | `sql_tools.py` | Fixed read-only SQL: schema (table + column names) of the `openai_anthropic_*` tables. |
| `preview_table` | `sql_tools.py` | Fixed read-only SQL: `SELECT TOP (N) *` from one allowlisted table. Inspection only. |
| `table_row_count` | `sql_tools.py` | Fixed read-only SQL: `COUNT(*)` of one allowlisted table. |

`post_to_teams` is registered here too but lives in `usage_agent/teams.py`.

## How the SQL tools stay safe (two guards, belt-and-braces)

1. **Table allowlist** (`sql_tools._ALLOWED_TABLES` / `_resolve`). The agent supplies
   only a table name; `_resolve` maps it to a `dbo.openai_anthropic_*` table or
   raises. Nothing outside the openai_anthropic dataset is reachable — no Ivanti,
   no `sys.*`, no ad-hoc tables. The tool schema also pins `table` to an `enum`.
2. **Read-only safety net** (`validation.ensure_read_only`, called in
   `sql_tools._read`). Every statement is re-checked at execution time: single
   read-only `SELECT`/`WITH` only — no writes, DDL, stored procedures, batches, or
   external-data-source functions (`OPENROWSET`, `OPENQUERY`, `OPENDATASOURCE`,
   `OPENXML`, `BULK`, `WAITFOR`, …). Comments are stripped first so tokens can't
   hide. This is defense-in-depth; the queries are already fixed.

The **primary** control is still least privilege: the Fabric SQL principal should
have SELECT-only on the `openai_anthropic_*` tables (see `shared/README.md`).

## `validation.py`

The SQL safety net (`is_read_only_sql`, `ensure_read_only`, `validate_identifier`).
Values are always passed as **bound parameters** — never string-formatted into SQL.
Identifiers that must be interpolated (table names) go through the allowlist and/or
`validate_identifier`. Covered by `tests/test_validation.py`.

## Time-scoping helpers (`usage_metrics.py`)

`_period_dax(period)` builds the `DEFINE`/filter fragments for `last_week`,
`prior_week`, `last_month`, `this_month`, `all_time`. Weekly periods anchor to the
model's `is_complete_week`; monthly periods use calendar-month text filters derived
from the host clock (see the limitation noted in the top-level README).
