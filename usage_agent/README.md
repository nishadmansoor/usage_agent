# `usage_agent/` — the agent package

The conversational Claude agent: a tool-use loop over the `openai_anthropic_*`
Fabric dataset. Everything the CLI and the Teams bot use lives here.

## Modules

| File | Responsibility |
|---|---|
| `agent.py` | `UsageAgent.run()` — the Anthropic tool-use loop. Sends the conversation + tool schemas to Claude, dispatches each requested tool to a Python callable, feeds results back, and returns the final text. DataFrame results are serialised to compact CSV. |
| `cli.py` / `__main__.py` | `python -m usage_agent "…"`. Parses args, configures logging/UTF-8/truststore, runs one question, or renders a `--one-pager` `.docx`. |
| `config.py` | `Settings` + `get_settings()` — all configuration from environment/`.env` (frozen dataclass, `lru_cache`d). No secrets in code. |
| `llm.py` | `get_claude_client()` — builds the Claude client for `LLM_BACKEND` (`api_key` default, or passwordless `foundry`). |
| `teams.py` | `post_to_teams()` — renders an Adaptive Card and posts it to a Teams channel via a Workflows webhook. No-op with a clear message if unconfigured. |
| `logging_config.py` | `configure_logging()` / `get_logger()`. |
| `clients/` | `PowerBIClient` — the single choke point that runs DAX via the Power BI REST `executeQueries` endpoint. See `clients/README.md`. |
| `tools/` | The agent's tools + registry (DAX, deterministic metrics, fixed read-only SQL inspection, the SQL safety net). See `tools/README.md`. |
| `prompts/` | The system prompt and the one-pager prompt. See `prompts/README.md`. |
| `reports/` | Renders the agent's structured JSON into a branded Word one-pager. See `reports/README.md`. |

## Query model (important)

- **DAX is the only way numbers are computed** — via `run_dax_query` or the fixed
  deterministic tools, all over the semantic model's measures so answers reconcile
  with the dashboard.
- **The agent never writes SQL.** Raw rows are inspected only through fixed,
  allowlisted, read-only tools in `tools/sql_tools.py`.
- **Scope is `openai_anthropic_*` only.**

## Design conventions

- **Single choke points** — one Claude client (`llm.py`), one DAX client
  (`clients/powerbi.py`), one Fabric SQL engine (`shared/fabric.py`).
- **Lazy imports + `lru_cache`** — network/Azure libraries are imported inside
  functions so the offline unit tests run against stubs. Cached singletons
  (`get_settings`, `get_claude_client`, `get_powerbi_client`) are process-lived;
  call `.cache_clear()` if you change env at runtime (e.g. in tests).
- **Injectable dependencies** — `UsageAgent`, the tools, and `post_to_teams` accept
  an injected client/poster so the whole loop is testable offline.
- **Graceful degradation** — an unconfigured backend returns a clear message rather
  than crashing mid-run.
