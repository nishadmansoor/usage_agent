# `usage_agent/prompts/` — prompts

## `system_prompt.py` — `get_agent_system_prompt()`

The system prompt that defines the agent's role and operating rules. Key sections:

- **SCOPE** — the agent works ONLY with the `openai_anthropic_*` dataset and will
  never write SQL.
- **Tools & query strategy** — deterministic tools first; `run_dax_query` is the
  primary and only compute path; raw rows are inspected with the fixed read-only
  tools (`list_data_tables` / `preview_table` / `table_row_count`).
- **Data model & time-scoping** — how to scope by `openai_anthropic_dim_date`, the
  column types to filter with, and which measures are reliable vs. broken.
- **Forecast rules**, **counting rules**, **domain notes** (providers, token types,
  products, users, and department via `openai_anthropic_dim_user_dept`).
- **Output format** — CONCISE (default) vs BRIEFING mode.

> This prompt hard-codes model-specific facts (e.g. which measures are reliable). If
> the semantic model changes, update the prompt to match — `describe_model` gives
> the agent live measure *names*, but the reliability notes here are static.

## `one_pager.py` — `build_one_pager_prompt(user_prompt)`

Produces the prompt for the `--one-pager` run. It forces the agent to return a
**single JSON object** with a fixed shape (`_REPORT_SCHEMA`) — KPIs, provider/model
breakdowns, top spenders, findings, actions — so the renderer
(`usage_agent/reports/one_pager.py`) can lay it out identically every time and do
all the week-over-week arithmetic itself. With no user prompt it builds the default
Monday weekly briefing (previous complete week vs. the week before).
