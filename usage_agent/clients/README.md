# `usage_agent/clients/` — backend clients

## `powerbi.py` — `PowerBIClient`

The single choke point for talking to the Power BI **semantic model**. Everything
that runs DAX (the `dax_tools` and the deterministic `usage_metrics` tools) goes
through here.

- **Transport:** `POST {base}/…/datasets/{id}/executeQueries` (workspace-scoped when
  `POWERBI_WORKSPACE_ID` is set). One DAX query per call; results ~100k rows / ~1M
  values, capped server-side.
- **Auth:** passwordless. A bearer token from the shared `DefaultAzureCredential`,
  scoped to the Power BI API (`https://analysis.windows.net/powerbi/api/.default`).
  No API key or secret.
- **`execute_dax(dax) -> list[dict]`** — runs an `EVALUATE`, returns the result
  rows (each keyed by the column/measure name as Power BI returns it, e.g.
  `"[Total Spend]"`). Raises on any non-2xx so failures surface clearly.
- **`get_powerbi_client()`** — builds and `lru_cache`s the client from settings.

The client is **injectable** (`token_provider`, `dataset_id`, …) so tests pass a
stub and never touch Azure or the network. `run_dax_query` never passes a
`dataset_id`, so every query is pinned to the configured `POWERBI_DATASET_ID`.

Why DAX over the model (not raw SQL) is the primary path: the dashboard's figures
*are* these measures, so querying them is what makes the agent's numbers reconcile
with the dashboard.
