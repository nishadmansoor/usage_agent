# AI Usage Analytics Agent — Implementation Plan

A conversational Claude agent that answers natural-language questions about AI
usage & cost, reconciling with the Power BI dashboard, and grows into scheduled
digests, cost-governance alerts, and an interactive Teams bot.

The agent's design is ported from the `sla_teams` project (a Claude tool-calling
agent over Fabric SQL), re-pointed from Ivanti/SLA data to this project's
**AI usage/cost data in Microsoft Fabric**.

> **Status note (current code vs. this plan).** This document is the original
> design plan and is kept for history. The shipped code has since tightened the
> query model — where the plan below says "free-form `run_sql_query` fallback",
> the current behaviour is:
> - **DAX is the ONLY compute path.** `run_dax_query` (and the deterministic tools)
>   over the semantic model produce every reported number.
> - **The agent cannot write SQL.** The free-form `run_sql_query` tool was
>   removed. Raw-row inspection goes through **fixed, read-only, allowlisted** tools
>   (`list_data_tables`, `preview_table`, `table_row_count`) scoped to the
>   `openai_anthropic_*` tables only.
> - **`validation.py` is now a defense-in-depth safety net**, re-checked on every
>   SQL execution — not a gate on agent-authored SQL (there is none).
> - **Scope is `openai_anthropic_*` only**, including department (now in
>   `openai_anthropic_dim_user_dept`, so the old external Ivanti join was dropped).
> - Phases 1–2 and the interactive Teams bot (Phase 4, `bot/`) are implemented.
> See `README.md` and the per-folder READMEs for the current design.

---

## 1. Objective

Build a Claude tool-calling agent that:

1. Answers free-form questions about AI usage & cost ("which department spent the
   most on Claude last month?", "Codex vs Claude token trend", "who's licensed
   but inactive?").
2. **Reconciles with the Power BI dashboard** by querying the Fabric **semantic
   model** (DAX) and returning the model's own measures — not re-derived numbers.
3. Keeps a raw **Fabric SQL** path for ad-hoc drill-down the model doesn't expose.
4. Serves as the foundation for: a weekly comparative digest, threshold-based
   cost alerts, and an inbound interactive Teams bot.

---

## 2. Guiding principles (carried over from `sla_teams`)

- **Passwordless, one Azure identity, many scopes.** A single
  `DefaultAzureCredential` mints tokens for each backend by scope — no keys/secrets
  stored. sla_teams already does this for Fabric SQL, Foundry, and Graph.
- **Single choke points.** One Claude client, one DAX/Power BI client, one SQL
  client — each the only place that talks to its backend. Easy to audit/secure.
- **Grounding & auditability.** Every number comes from a query/measure; the
  agent reports the DAX/SQL it used so answers reconcile with the dashboard.
- **Prefer existing measures over recomputation** — the analogue of sla_teams'
  "canonical definition, use it verbatim" rule that made numbers identical run to run.
- **Lazy imports + `lru_cache`; offline-testable** (network/Azure libs imported
  inside functions so unit tests run with stubs).
- **Graceful degradation** — a missing/unconfigured backend returns a clear
  message, never a hard crash mid-run.

---

## 3. Target architecture & package layout

New top-level `agent/` package alongside `anthropic/`, `codex_usage_pipeline/`,
`shared/`. Auth + backend connections move into `shared/` so the **write path**
(pipeline) and the **read path** (agent) use one credential and one place for
Fabric access.

```
openai-anthropic_pipeline/
├── shared/
│   ├── loader.py               # (existing) unified schema loader
│   ├── schema.sql              # (existing) unified usage schema
│   ├── azure_auth.py           # NEW — get_credential() (ported from sla_teams)
│   └── fabric.py               # NEW — Fabric SQL engine (ported: fabric_connection.py)
├── agent/
│   ├── pyproject.toml          # console script: usage-agent  (mirrors codex pkg)
│   └── src/usage_agent/
│       ├── config.py           # Settings/get_settings (dataclass + dotenv)
│       ├── logging_config.py   # ported
│       ├── llm.py              # get_claude_client()  (see Decision A)
│       ├── agent.py            # UsageAgent + AgentResult  (the tool-use loop)
│       ├── cli.py / __main__.py# entry point (mirrors codex cli.py)
│       ├── clients/
│       │   └── powerbi.py      # NEW — PowerBIClient.execute_dax() via REST
│       ├── tools/
│       │   ├── __init__.py     # TOOL_FUNCTIONS + TOOL_SPECS registry
│       │   ├── dax_tools.py    # run_dax_query, describe_model  (PRIMARY)
│       │   ├── sql_tools.py    # run_sql_query  (ported; drill-down fallback)
│       │   ├── validation.py   # ensure_read_only / validate_identifier (ported)
│       │   └── reporting_tools.py # summarize / generate_report (re-themed)
│       ├── prompts/
│       │   ├── system_prompt.py  # role + schema/measures + measure-first rule
│       │   └── report_prompt.py  # usage/cost briefing template
│       └── models/
│           └── report.py       # UsageReport (ported OperationsReport)
└── tests/  (agent)             # offline stubs, mirrors sla_teams tests
```

---

## 4. Component mapping (`sla_teams` → here)

| sla_teams | Action | New home |
|---|---|---|
| `app/azure_auth.py` | Port ~verbatim | `shared/azure_auth.py` |
| `app/tools/fabric_connection.py` | Port ~verbatim | `shared/fabric.py` |
| `app/tools/sql_tools.py` | Port; re-point table names | `agent/.../tools/sql_tools.py` |
| `app/utils/validation.py` | Port verbatim (SQL safety) | `agent/.../tools/validation.py` |
| `app/agent.py` | Port loop; rename to `UsageAgent` | `agent/.../agent.py` |
| `app/llm.py` | Port; see Decision A | `agent/.../llm.py` |
| `app/config.py` | Rewrite for new env vars | `agent/.../config.py` |
| `app/prompts/system_prompt.py` | **Rewrite** (usage domain + measures) | `agent/.../prompts/` |
| `app/prompts/report_prompt.py` | Re-theme to usage briefing | `agent/.../prompts/` |
| `app/tools/reporting_tools.py` | Port; re-theme | `agent/.../tools/reporting_tools.py` |
| `app/models/report.py` | Port; rename `UsageReport` | `agent/.../models/report.py` |
| `app/tools/ticket_tools.py`, `analytics_tools.py` | **Replace** with usage/cost tools | `agent/.../tools/dax_tools.py` + purpose tools |
| `app/teams/*` | Defer to Phase 2/4 | — |
| `run.py` / `main.py` | Port to `cli.py` (repo style) | `agent/.../cli.py` |

**New (no sla_teams equivalent):** `clients/powerbi.py` (the DAX client) and
`tools/dax_tools.py`. These mirror how sla_teams added `TeamsGraphClient` — same
passwordless auth pattern, a new token scope.

---

## 5. Key new component — Power BI DAX client

Mirrors `TeamsGraphClient`: a thin, passwordless client that is the only thing
that talks to the semantic model.

- **Endpoint:** `POST https://api.powerbi.com/v1.0/myorg/datasets/{datasetId}/executeQueries`
- **Auth scope:** `https://analysis.windows.net/powerbi/api/.default` (one more
  scope on the shared `DefaultAzureCredential`).
- **`execute_dax(query) -> rows`** — runs a DAX `EVALUATE`, returns rows.
- **`describe_model()`** — runs `INFO.TABLES()` / `INFO.MEASURES()` (via the same
  endpoint) so the agent knows real table/column/**measure** names.

Two agent tools wrap it:
- `run_dax_query(dax)` — **primary**; answers reconcile with the dashboard.
- `describe_model()` — lets Claude discover measures instead of inventing SQL.

Plus the ported `run_sql_query(sql)` against the Fabric SQL endpoint as a
drill-down escape hatch (executeQueries is capped ~100k rows / one query per call).

---

## 6. Phased delivery

### Phase 0 — Scaffolding & shared auth refactor
- Create `agent/` package (pyproject + console script `usage-agent`).
- Move `azure_auth` and Fabric connection into `shared/`; wire pipeline + agent to it.
- Config + logging modules.
- **Done when:** `usage-agent --help` runs; `get_credential()` shared by both paths.

### Phase 1 — Conversational agent (THE foundation) ⭐
- Port the tool-use loop (`UsageAgent`).
- `PowerBIClient` + `run_dax_query` + `describe_model` (primary path).
- Ported `run_sql_query` (fallback) + SQL safety validation.
- System prompt: role, unified `usage`/`dim_user` schema, discovered measures, and
  the **measure-first / grounding** rules.
- CLI: `usage-agent "which team spent the most on Claude last month?"`.
- **Done when:** NL questions return correct answers whose numbers match the
  dashboard's measures, with the DAX shown for auditability.

### Phase 2 — Weekly comparative digest
- `generate_report` re-themed to an "AI Spend & Adoption" briefing.
- Scheduled Monday run (cron/`schedule`) for the previous week, **week-over-week
  deltas** (not a static snapshot).
- Optional: port sla_teams' outbound `post_to_teams` (Adaptive Card) to deliver it.
- Optional: per-department targeting using `dim_user`.
- **Done when:** a Monday briefing is produced (and optionally posted to Teams).

### Phase 3 — Cost-governance tool set
- Purpose-built tools: cache efficiency (cache-read vs cache-creation), model
  right-sizing, idle-seat detection, cross-provider overlap, budget vs run-rate
  forecast, threshold spike alerts.
- **Done when:** the agent surfaces concrete savings/recommendations, not just numbers.

### Phase 4 — Interactive inbound Teams bot
- Azure Bot Service registration + hosted messaging endpoint + Teams app manifest.
- Multi-turn conversation context; reuse the Phase 1 agent as the brain.
- Reuses the same Teams client/auth from sla_teams for replies.
- **Done when:** users ask in a Teams chat and get grounded answers.

### Cross-cutting (fold in from Phase 1)
- **Access control / RLS:** cost data is sensitive; once exposed to many users the
  agent must respect *who's asking* (manager sees only their org) via RLS on the
  semantic model + the asker's identity.
- **Auditable answers:** always return the measure/DAX used.
- **Tests:** offline stubs for the Claude client and `PowerBIClient` (mirrors
  `tests/test_teams.py`); unit tests for validation, DAX payload building, dispatch.
- **Dogfooding:** log each question + generated DAX; the agent's own Claude calls
  feed back into the usage data it reports on.

---

## 7. Open decisions (please confirm)

**Decision A — how the agent authenticates to Claude:**
- *Option 1 (fastest):* reuse this repo's existing `ANTHROPIC_API_KEY` +
  `Anthropic(...)` client (already wired in `anthropic/clients/anthropic_client.py`).
- *Option 2 (single-identity):* port sla_teams' `AnthropicFoundry` +
  `DefaultAzureCredential` (Azure passwordless, matches the Fabric/Power BI story).
- **Recommendation:** keep `llm.py` provider-agnostic behind `get_claude_client()`,
  **default to Option 1** for Phase 1 speed, leave Option 2 as a config swap
  (the shared credential already exists).

**Decision B — Phase 1 includes Teams?** Recommendation: **no** — CLI only; add
outbound Teams in Phase 2, inbound bot in Phase 4.

**Decision C — semantic-model prerequisites (confirm access):** dataset on
Premium/PPU/Fabric capacity, **XMLA read enabled**, the agent identity has
**Build** permission, and you can supply the **dataset ID + workspace ID**.

---

## 8. Configuration (new env vars)

| Var | Purpose |
|---|---|
| `POWERBI_WORKSPACE_ID` / `POWERBI_DATASET_ID` | Target semantic model |
| `POWERBI_TOKEN_SCOPE` | default `https://analysis.windows.net/powerbi/api/.default` |
| `FABRIC_SQL_SERVER` / `FABRIC_SQL_DATABASE` / `FABRIC_SQL_PORT` / `ODBC_DRIVER` | Drill-down SQL (reused from sla_teams) |
| `EXCLUDE_MANAGED_IDENTITY` | fast local `az login` (default true) |
| `ANTHROPIC_API_KEY` *or* `FOUNDRY_RESOURCE` + `FOUNDRY_TOKEN_SCOPE` | Claude (per Decision A) |
| `ANTHROPIC_MODEL` | default `claude-sonnet-4-6` |
| `LOG_LEVEL`, `QUERY_ROW_LIMIT` | app behaviour |

Dependencies to add: `anthropic`, `httpx` (already present), `pyodbc` +
`sqlalchemy` (drill-down), `azure-identity`, `python-dotenv`.

---

## 9. Risks & mitigations

- **`executeQueries` limits** (one query/call, ~100k rows) → keep the raw-SQL tool
  for large/edge pulls; agent prefers aggregates.
- **DAX correctness** → `describe_model()` feeds real measure names into the prompt;
  measure-first rule; return the DAX for review.
- **Cost-data sensitivity** → RLS + identity-aware access before broad Teams exposure.
- **Number drift vs dashboard** → always query measures, never re-derive.
- **Buy-vs-build overlap** → Power BI Copilot already does basic model Q&A; this
  agent's edge is cross-provider reasoning, narrative briefings, governance, and
  proactive alerts.

---

## 10. Suggested sequence

**Phase 1 (CLI, DAX-first)** → **Phase 2 (weekly comparative digest + outbound
Teams)** → **Phase 3 (cost-governance tools — highest ROI for this data)** →
**Phase 4 (interactive Teams bot — biggest infra lift, inherits everything).**
