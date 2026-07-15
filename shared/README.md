# `shared/` — passwordless Azure auth + Fabric SQL

Shared infrastructure used by both the read path (this agent) and, in the wider
project, the write path (pipeline). One identity, several token scopes, no secrets
in code.

## `azure_auth.py` — `get_credential()`

Returns a `DefaultAzureCredential` tuned for this app:

- **Managed identity excluded by default** (`EXCLUDE_MANAGED_IDENTITY=true`) — its
  IMDS probe can hang for minutes on a corporate workstation. Set `false` when
  deploying to Azure with a managed identity.
- **Environment (service-principal) credential excluded unless all of
  `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` are populated** —
  blank `.env` placeholders would otherwise activate `EnvironmentCredential` and
  fail before `az login` is tried.

Net effect: blank SP vars → `az login` (local dev); fill them in → the service
principal is used. The same credential mints tokens for Power BI (DAX), Fabric SQL,
and (optionally) Claude via Foundry — each by scope.

## `fabric.py` — `get_fabric_engine()`

A passwordless SQLAlchemy engine for the Fabric SQL endpoint, used only by the fixed
read-only inspection tools (`usage_agent/tools/sql_tools.py`).

- An Azure AD access token (scope `https://database.windows.net/.default`) is handed
  to the ODBC driver via `attrs_before` — **no SQL username/password**.
- A fresh token is minted per physical connection (SQLAlchemy `creator` hook), so a
  long-lived pooled engine keeps working as tokens rotate.
- Connection details come from the environment: `FABRIC_SQL_SERVER`,
  `FABRIC_SQL_DATABASE`, `FABRIC_SQL_PORT` (1433), `ODBC_DRIVER` (ODBC Driver 18).

> **Least privilege:** grant this identity **SELECT-only** on the
> `openai_anthropic_*` tables. That principal's permissions are the primary security
> boundary for the SQL tools; the allowlist + read-only validator are belt-and-braces
> on top.
