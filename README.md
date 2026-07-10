# Usage Agent

A conversational Claude agent that answers natural-language questions about your
organization's **AI usage & cost** (Anthropic Claude + OpenAI Codex) held in
Microsoft Fabric.

It queries your **Power BI semantic model** with DAX — so its numbers reconcile
with the dashboard's own measures — and falls back to raw **Fabric SQL** for
drill-downs the model doesn't expose. Authentication is **passwordless** (your
Azure identity via `az login` / managed identity); no API keys to manage.

```
You ─▶ usage-agent "which department spends the most on Claude?"
          │
          ▼
    Claude (via Azure Foundry)  ──tool calls──▶  describe_model   (learn measures)
                                                  run_dax_query    (PRIMARY — matches dashboard)
                                                  run_sql_query    (drill-down fallback)
          │
          ▼
    grounded answer / briefing
```

---

## Features

- **Ask in plain English** — spend, tokens, model mix, adoption, week-over-week trends.
- **Reconciles with the dashboard** — prefers the semantic model's measures over
  re-derived numbers.
- **Cross-source drill-down** — can join usage to other Fabric tables (e.g. an HR
  users table for per-department spend) via the SQL fallback.
- **Passwordless** — one Azure identity, three token scopes (Foundry / Power BI /
  Fabric SQL). No secrets in code.
- **Two output modes** — concise one-liners by default; a full four-section
  briefing when you ask for a "report"/"briefing"/"breakdown".

---

## How it works

1. `UsageAgent.run()` drives a standard Anthropic tool-use loop.
2. Claude calls `describe_model` once to learn the real tables/columns/measures.
3. For any quantitative question it writes DAX (`run_dax_query`) against the
   semantic model, referencing existing measures.
4. `run_sql_query` is a last-resort drill-down against the Fabric SQL endpoint.
5. Results (DataFrames) are serialised back to Claude, which composes the answer.

Claude auth is pluggable via `LLM_BACKEND`:
- `foundry` (recommended here) — `AnthropicFoundry` + your Azure AD token.
- `api_key` — a direct `ANTHROPIC_API_KEY` (optionally via `ANTHROPIC_BASE_URL`
  for an org gateway).

---

## Repo layout (standalone)

```
usage_agent/
├── README.md
├── requirements.txt
├── .env                        # your config (git-ignored; template in .env.example)
├── usage_agent/                # the agent package
│   ├── agent.py                # UsageAgent tool-use loop
│   ├── cli.py / __main__.py    # `python -m usage_agent`
│   ├── config.py               # env-driven Settings
│   ├── llm.py                  # get_claude_client (foundry | api_key)
│   ├── logging_config.py
│   ├── clients/powerbi.py      # PowerBIClient.execute_dax (executeQueries)
│   ├── tools/                  # dax_tools, sql_tools, validation, registry
│   ├── reports/one_pager.py    # structured JSON -> branded Word (.docx) briefing
│   └── prompts/                # system_prompt.py, one_pager.py
├── shared/                     # azure_auth.py, fabric.py (passwordless Azure)
└── tests/                      # offline tests (stubbed Claude + Power BI)
```

Run everything from the repo root so the `usage_agent` and `shared` packages
import (no install step needed beyond the dependencies).


## Prerequisites

- **Python 3.11+**
- **Microsoft ODBC Driver 18 for SQL Server** (for the SQL drill-down tool)
- **Azure CLI**, logged in: `az login`
- Access, granted to your identity:
  - **Azure AI Foundry** resource with Claude deployed (for `LLM_BACKEND=foundry`)
  - **Power BI semantic model**: *Build* permission + *XMLA read* enabled on the dataset
  - **Fabric SQL** endpoint read access (optional; only for the SQL fallback)

---

## Install

```powershell
# from the repo root
pip install -r requirements.txt
az login
```

---

## Configuration

Create a `.env` in the repo root (template under "`.env` example" below):

| Variable | Required | Description |
|---|---|---|
| `LLM_BACKEND` | no | `foundry` (recommended) or `api_key`. Default `api_key`. |
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
| `FABRIC_SQL_DATABASE` | for SQL | Lakehouse/warehouse name holding the usage tables. |
| `TEAMS_TARGET` | no | Delivery target for `post_to_teams`. Only `webhook` is implemented (the default). |
| `TEAMS_WEBHOOK_URL` | for Teams | Power Automate / Teams Workflows Incoming-Webhook URL. Enables posting; if unset, `post_to_teams` is a no-op. |
| `EXCLUDE_MANAGED_IDENTITY` | no | `true` (default) keeps `az login` fast locally; set `false` in Azure. |
| `LOG_LEVEL` | no | Default `INFO`. |
| `QUERY_ROW_LIMIT` | no | Max rows returned per query. Default `1000`. |

\* Required for the primary DAX path. Find the IDs in the model's portal URL:
`app.powerbi.com/groups/<WORKSPACE_ID>/datasets/<DATASET_ID>/...`.

## Usage

Run from the repo root (so `.env` is loaded):

```powershell
python -m usage_agent "which department spends the most on Claude?"
python -m usage_agent "codex vs claude token usage over the last 30 days"
python -m usage_agent            # no question -> default weekly briefing
```

**Options**
- positional `prompt` — your question (quote it).
- `--log-level` — override `LOG_LEVEL` for one run (e.g. `WARNING` to hide info logs).
- `--one-pager` — render a branded executive briefing to a **Word (.docx)** doc
  instead of printing to the terminal. With no prompt it produces the weekly Monday
  briefing: the **previous complete week vs. the week before it**, with the
  week-over-week (WoW) trend on spend, provider split, and active users. With a
  prompt it renders the answer to that question as a one-pager. (Intended to run on
  a Monday, once the prior week's dataset is complete.)
- `-o` / `--output` — output path for the `--one-pager` doc (default
  `ai_usage_one_pager_<date>.docx` in the current directory).

```powershell
python -m usage_agent --one-pager                                   # prev-week vs week-before (WoW) -> .docx
python -m usage_agent --one-pager "claude spend by department last month"
python -m usage_agent --one-pager -o reports/weekly.docx
```

Every one-pager has the **same fixed layout** — an EisnerAmper black/gold masthead,
a row of KPI tiles, three WoW tables (by provider, by model, and the top 10 users),
key findings, recommended actions, and a generated-at timestamp — only the data
changes. The agent returns a structured JSON payload and the renderer
(`python-docx`) computes every WoW delta and lays it out identically each run.

---

## Tools

| Tool | Purpose |
|---|---|
| `describe_model` | Lists the semantic model's tables, columns, and measures (via DAX `INFO.VIEW.*`). Called once up front. |
| `run_dax_query` | **Primary.** Runs a DAX `EVALUATE` against the model; results match the dashboard's measures. |
| `run_sql_query` | Read-only SQL (SELECT/WITH only) against the Fabric SQL endpoint, for raw drill-down. |
| `post_to_teams` | Posts the answer/briefing to Teams as an Adaptive Card via a Workflows webhook. Used only when you explicitly ask to send/share to Teams; |

---

## Microsoft Teams delivery

Ask the agent your question, followed by the phrase *"…and post it to Teams"*. The program renders the answer as an Adaptive
Card and sends it to a channel via a **Power Automate / Teams Workflows** webhook
(it arrives as the Flow bot). Setup:

1. In Teams/Power Automate, create a flow with the **"Send a webhook alert to a channel."**
2. Copy the link that is generated and put it in the .env

No Azure AD permission is needed 
---

## Testing

```powershell
python -m pytest            # run from the repo root
```

Tests are fully offline

---

## Limitations

- **Time-scoped breakdowns:** the raw `usage` fact table's `usage_date` is text and
  isn't a date-related dimension, so hand-written date filters on the raw table may
  not scope correctly. Prefer the model's period measures / the reporting table for
  "last week"/"WoW" questions. (Prompt guidance steers Claude this way; verify
  detail tables sum to the period total.)
- **`executeQueries` limits:** one DAX query per call, ~100k-row cap. Big pulls
  should aggregate or use the SQL tool.
- **SQL vs measures:** numbers from `run_sql_query` are raw and may differ slightly
  from a dashboard measure's definition; the DAX path is authoritative for anything
  the dashboard shows.
- **Model metadata:** `describe_model` uses `INFO.VIEW.*` (not `INFO.*`, which some
  engines reject) and degrades gracefully if unavailable.

---

## Next Steps

- **Weekly comparative digest** (scheduled Monday briefing with WoW deltas).
- **Cost-governance tools** (model right-sizing, cache efficiency, idle seats,
  cross-provider overlap, budget/forecast alerts).
- **Interactive Teams bot** (inbound Q&A, reusing this agent as the brain).
