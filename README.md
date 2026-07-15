# Usage Agent

A conversational Claude agent that answers natural-language questions about your
organization's **AI usage & cost** (Anthropic Claude + OpenAI Codex) held in
Microsoft Fabric.

It works **only** with the `openai_anthropic_*` dataset and computes every number
with **DAX against your Power BI semantic model** — so its figures reconcile with
the dashboard's own measures. It **never writes SQL**: for raw-row inspection it
uses a small set of fixed, read-only, allowlisted SQL tools. Authentication is
**passwordless** (your Azure identity via `az login` / managed identity); no API
keys to manage.

```
You ─▶ usage-agent "which department spends the most on Claude?"
          │
          ▼
    Claude (Foundry or API key)
          │  tool calls
          ├─▶ describe_model            learn the real tables/columns/measures (once)
          ├─▶ weekly_spend_summary /    deterministic, dashboard-reconciling shortcuts
          │   spend_breakdown /         for the common questions
          │   department_spend
          ├─▶ run_dax_query             PRIMARY — the ONLY way numbers are computed
          └─▶ list_data_tables /        fixed, read-only inspection of raw
              preview_table /           openai_anthropic rows (agent never writes SQL)
              table_row_count
          │
          ▼
    grounded answer / briefing  (optionally posted to Teams)
```

---

## What it can and can't do

- **Compute** — only via `run_dax_query` (or the deterministic tools, which are
  fixed DAX). Every reported figure comes from the semantic model's measures.
- **Inspect raw rows** — only via `list_data_tables` / `preview_table` /
  `table_row_count`, which run **fixed** read-only SELECTs against an **allowlist**
  of `openai_anthropic_*` tables. The agent cannot compose SQL.
- **Scope** — the model contains only `openai_anthropic_*` tables (plus a hidden
  `_Measures` table that holds measures). The agent never touches anything else.

---

## Features

- **Ask in plain English** — spend, tokens, model mix, adoption, week-over-week trends.
- **Reconciles with the dashboard** — all numbers come from the model's measures.
- **No self-written SQL** — the agent computes with DAX and only *inspects* raw data
  through fixed, allowlisted, read-only tools, backed by a validation safety net.
- **Passwordless** — one Azure identity, three token scopes (Foundry / Power BI /
  Fabric SQL). No secrets in code.
- **Two output modes** — concise one-liners by default; a full four-section
  briefing when you ask for a "report"/"briefing"/"breakdown".
- **Delivery** — CLI, a branded Word (`.docx`) one-pager, an outbound Teams card, or
  an interactive Teams bot (`bot/`).

---

## How it works

1. `UsageAgent.run()` drives a standard Anthropic tool-use loop (`usage_agent/agent.py`).
2. Claude calls `describe_model` once to learn the real tables/columns/measures.
3. For the common questions it uses a **deterministic tool** (`weekly_spend_summary`,
   `spend_breakdown`, `department_spend`) — fixed DAX that returns the same
   dashboard-reconciling answer every run.
4. For anything else it writes DAX with `run_dax_query`, referencing existing
   measures. This is the **only** path that produces reported numbers.
5. To understand a raw column it can `list_data_tables` then `preview_table` — fixed
   read-only SQL over the openai_anthropic allowlist; those raw numbers are for
   inspection only and are never reported as figures.
6. Results (DataFrames) are serialised back to Claude, which composes the answer.

Claude auth is pluggable via `LLM_BACKEND`:
- `foundry` — `AnthropicFoundry` + your Azure AD token (single-identity story).
- `api_key` (default) — a direct `ANTHROPIC_API_KEY` (optionally via
  `ANTHROPIC_BASE_URL` for an org gateway).

---

## Repo layout

```
usage_agent/                       # repo root
├── README.md                      # this file
├── AGENT_PLAN.md                  # original design/implementation plan (history)
├── requirements.txt
├── .env                           # your config (git-ignored; template in .env.example)
├── usage_agent/                   # the agent package  (see usage_agent/README.md)
│   ├── agent.py                   # UsageAgent tool-use loop
│   ├── cli.py / __main__.py       # `python -m usage_agent`
│   ├── config.py                  # env-driven Settings
│   ├── llm.py                     # get_claude_client (foundry | api_key)
│   ├── teams.py                   # outbound Adaptive Card via Workflows webhook
│   ├── logging_config.py
│   ├── clients/                   # PowerBIClient  (see clients/README.md)
│   ├── tools/                     # the agent's tools  (see tools/README.md)
│   ├── prompts/                   # system + one-pager prompts (prompts/README.md)
│   └── reports/                   # JSON -> branded Word (.docx)  (reports/README.md)
├── shared/                        # passwordless Azure auth + Fabric SQL (shared/README.md)
├── bot/                           # interactive Teams bot  (bot/README.md)
├── teams_app/                     # Teams app manifest for the bot
└── tests/                         # offline tests (stubbed Claude + Power BI)
```

Run everything from the repo root so the `usage_agent`, `shared`, and `bot`
packages import (no install step beyond the dependencies).

---

## Prerequisites

- **Python 3.11+**
- **Microsoft ODBC Driver 18 for SQL Server** (for the read-only SQL inspection tools)
- **Azure CLI**, logged in: `az login`
- Access, granted to your identity:
  - **Claude** — an Azure AI Foundry deployment (`LLM_BACKEND=foundry`) or an
    `ANTHROPIC_API_KEY` (`LLM_BACKEND=api_key`).
  - **Power BI semantic model** — *Build* permission + *XMLA read* on the dataset.
  - **Fabric SQL** endpoint — **read-only** access to the `openai_anthropic_*`
    tables (optional; only for the inspection tools). Grant SELECT only.

---

## Install

```powershell
# from the repo root
pip install -r requirements.txt
az login
```

---

## Configuration

Create a `.env` in the repo root (see `.env.example`):

| Variable | Required | Description |
|---|---|---|
| `LLM_BACKEND` | no | `api_key` (default) or `foundry`. |
| `FOUNDRY_RESOURCE` | if foundry | Azure AI Foundry resource name (Claude deployed there). |
| `FOUNDRY_TOKEN_SCOPE` | no | Default `https://cognitiveservices.azure.com/.default`. |
| `ANTHROPIC_API_KEY` | if api_key | Standard Anthropic key. |
| `ANTHROPIC_BASE_URL` | no | Override for an org gateway/proxy (api_key backend). |
| `ANTHROPIC_MODEL` | no | Default `claude-sonnet-4-6`. |
| `CLAUDE_MAX_TOKENS` | no | Default `4096`. |
| `POWERBI_WORKSPACE_ID` | yes* | Fabric workspace (group) GUID of the semantic model. |
| `POWERBI_DATASET_ID` | yes* | Semantic model (dataset) GUID. |
| `POWERBI_TOKEN_SCOPE` | no | Default `https://analysis.windows.net/powerbi/api/.default`. |
| `FABRIC_SQL_SERVER` | for SQL | Fabric SQL endpoint, e.g. `xxxx.datawarehouse.fabric.microsoft.com`. |
| `FABRIC_SQL_DATABASE` | for SQL | Lakehouse/warehouse holding the `openai_anthropic_*` tables. |
| `FABRIC_SQL_TRUST_SERVER_CERT` | no | `false` (default). Set `true` only if the SQL connection fails with `SSL Provider: The target principal name is incorrect` — relaxes ODBC Driver 18's cert-**name** check against some Fabric FQDNs. Connection stays encrypted; skips name validation. |
| `TEAMS_TARGET` | no | Delivery target for `post_to_teams`. Only `webhook` is implemented (default). |
| `TEAMS_WEBHOOK_URL` | for Teams | Power Automate / Teams Workflows Incoming-Webhook URL. If unset, `post_to_teams` is a no-op. |
| `EXCLUDE_MANAGED_IDENTITY` | no | `true` (default) keeps `az login` fast locally; set `false` in Azure. |
| `LOG_LEVEL` | no | Default `INFO`. |
| `QUERY_ROW_LIMIT` | no | Max rows returned per query. Default `1000`. |

\* Required for the DAX path (the only compute path). Find the IDs in the model's
portal URL: `app.powerbi.com/groups/<WORKSPACE_ID>/datasets/<DATASET_ID>/...`.

---

## Usage

Run from the repo root (so `.env` loads):

```powershell
python -m usage_agent "which department spends the most on Claude?"
python -m usage_agent "codex vs claude token usage over the last 30 days"
python -m usage_agent            # no question -> default weekly briefing
```

**Options**
- positional `prompt` — your question (quote it).
- `--log-level` — override `LOG_LEVEL` for one run.
- `--one-pager` — render a branded executive briefing to a **Word (.docx)** doc.
  With no prompt it produces the Monday briefing: **previous complete week vs. the
  week before it** (WoW trend on spend, provider split, active users).
- `-o` / `--output` — output path for the `--one-pager` doc.

```powershell
python -m usage_agent --one-pager
python -m usage_agent --one-pager "claude spend by department last month"
python -m usage_agent --one-pager -o reports/weekly.docx
```

Every one-pager has the **same fixed layout** — an EisnerAmper black/gold masthead,
KPI tiles, WoW tables (provider, model, top-10 users), key findings, recommended
actions, timestamp. The agent returns structured JSON; the renderer computes every
WoW delta and lays it out identically each run (`usage_agent/reports/one_pager.py`).

---

## Tools

| Tool | Kind | Purpose |
|---|---|---|
| `describe_model` | read | Lists the model's tables, columns, and measures (DAX `INFO.VIEW.*`). Called once up front. |
| `weekly_spend_summary` | deterministic | Fixed DAX: last complete week vs prior week (spend, WoW, active users). |
| `spend_breakdown` | deterministic | Fixed DAX: spend + active users by provider / model / product for a period. |
| `department_spend` | deterministic | Fixed DAX: spend by department via `openai_anthropic_dim_user_dept`. |
| `run_dax_query` | **primary** | The ONLY way to compute numbers. One DAX `EVALUATE` over the openai_anthropic model. |
| `list_data_tables` | fixed read-only SQL | Schema of the openai_anthropic tables. |
| `preview_table` | fixed read-only SQL | First N rows of one allowlisted openai_anthropic table (inspection only). |
| `table_row_count` | fixed read-only SQL | Row count of one allowlisted openai_anthropic table. |
| `post_to_teams` | action | Posts the answer/briefing to Teams as an Adaptive Card. Only when you explicitly ask. |

There is **no** free-form SQL tool — the agent cannot compose SQL. See
`usage_agent/tools/README.md` for the full mechanics and safety guards.

---

## Security model (short version)

- **DAX is read-only by construction** and confined to the openai_anthropic model.
- **SQL is fixed + allowlisted**: the agent only names a table (validated against
  the `openai_anthropic_*` allowlist) and a row count; it never supplies SQL text.
  `usage_agent/tools/validation.py` re-checks every statement at execution time as
  a defense-in-depth safety net (single read-only `SELECT`/`WITH`; no writes, DDL,
  batches, or `OPENROWSET`-style escapes).
- **Least privilege is the primary control** — grant the Fabric SQL principal
  SELECT-only on the `openai_anthropic_*` tables.
- **RLS / data sensitivity** — cost data includes per-user detail; if you expose
  the agent broadly, enforce Row-Level Security on the model and confirm the query
  identity respects it. See the final section of `AGENT_PLAN.md`.

---

## Microsoft Teams

**Outbound (any surface):** end your question with *"…and post it to Teams"*. The
answer is rendered as an Adaptive Card and sent to a channel via a **Power Automate /
Teams Workflows** webhook (arrives as the Flow bot). Set `TEAMS_WEBHOOK_URL`; no
Azure AD permission needed.

**Interactive bot:** `bot/` hosts an aiohttp service exposing `/api/messages` for the
Bot Framework. See `bot/README.md`.

---

## Testing

```powershell
python -m pytest            # from the repo root
```

Tests are fully offline (Claude and Power BI are stubbed; the SQL tools' query
construction and safety net are unit-tested without a database).

---

## Limitations

- **`executeQueries` limits:** one DAX query per call, ~100k-row cap. Large pulls
  should aggregate.
- **Inspection ≠ reporting:** numbers from `preview_table`/`table_row_count` are raw
  and may differ from a measure's definition — the DAX path is authoritative.
- **Monthly periods** in `spend_breakdown` / `department_spend` derive the calendar
  month from the host clock; if the data lags the calendar, monthly windows can be
  partial. Weekly periods anchor to the model's own `is_complete_week` and are safe.
- **Model metadata:** `describe_model` uses `INFO.VIEW.*` and degrades gracefully if
  a metadata query is unavailable.

---

## Next steps

- **Cost-governance tools** (model right-sizing, cache efficiency, idle seats,
  cross-provider overlap, budget/forecast alerts).
- **Scheduled Monday digest** posted to Teams.
- **RLS / identity-aware access** before broad rollout.
