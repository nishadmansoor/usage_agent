"""Power BI REST client for querying a Fabric semantic model with DAX.

Passwordless and transport-only: a bearer token from the shared
``DefaultAzureCredential`` scoped to the Power BI API, plus one ``_request``
choke point. This mirrors sla_teams' ``TeamsGraphClient`` — the only place that
talks to the semantic model, so any later feature reuses the same auth + HTTP.

Querying the model's **measures** via DAX (``executeQueries``) is what makes the
agent's numbers reconcile with the dashboard.

Docs: https://learn.microsoft.com/rest/api/power-bi/datasets/execute-queries

Notes / limits: ``executeQueries`` runs a single DAX query per call and caps the
result (~100k rows / ~1M values). The agent should ask for aggregates; large or
raw pulls go through the Fabric SQL tool instead.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from ..config import get_settings
from ..logging_config import get_logger

logger = get_logger(__name__)


class PowerBIClient:
    """Minimal Power BI client scoped to running DAX against one semantic model.

    Parameters
    ----------
    token_provider:
        Zero-arg callable returning a valid Power BI bearer token. Injectable so
        tests can supply a stub without touching Azure.
    base_url:
        Power BI REST base (defaults to the configured ``POWERBI_BASE_URL``).
    workspace_id / dataset_id:
        Target semantic model. ``workspace_id`` is optional; when set the
        workspace-scoped ``/groups/{id}/...`` route is used.
    """

    def __init__(
        self,
        token_provider,
        *,
        base_url: str,
        workspace_id: str | None = None,
        dataset_id: str | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._workspace_id = workspace_id
        self._dataset_id = dataset_id

    def get_token(self) -> str:
        """Return a current Power BI bearer token (delegated to the provider)."""
        return self._token_provider()

    def _execute_path(self, dataset_id: str) -> str:
        if self._workspace_id:
            return f"/groups/{self._workspace_id}/datasets/{dataset_id}/executeQueries"
        return f"/datasets/{dataset_id}/executeQueries"

    def execute_dax(
        self,
        dax: str,
        *,
        dataset_id: str | None = None,
        include_nulls: bool = True,
    ) -> list[dict[str, Any]]:
        """Run a DAX ``EVALUATE`` query and return the result rows.

        Each row is a dict keyed by the column/measure name as Power BI returns
        it (e.g. ``"DimUser[Department]"`` or ``"[Total Cost USD]"``). Raises on a
        non-2xx response so failures surface clearly.
        """
        dataset_id = dataset_id or self._dataset_id
        if not dataset_id:
            raise RuntimeError(
                "No Power BI dataset id configured (set POWERBI_DATASET_ID)."
            )
        body = {
            "queries": [{"query": dax}],
            "serializerSettings": {"includeNulls": include_nulls},
        }
        logger.info("Executing DAX against dataset=%s", dataset_id)
        data = self._request("POST", self._execute_path(dataset_id), json=body)

        # Shape: { "results": [ { "tables": [ { "rows": [ {...}, ... ] } ] } ] }
        results = data.get("results") or []
        if not results:
            return []
        tables = results[0].get("tables") or []
        if not tables:
            return []
        return tables[0].get("rows") or []

    # -- transport ---------------------------------------------------------
    def _request(self, method: str, path: str, json: dict | None = None) -> dict[str, Any]:
        """Issue an authenticated Power BI request and return parsed JSON."""
        # Lazy import: tests stub execute_dax and never import httpx.
        import httpx

        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.get_token()}",
            "Content-Type": "application/json",
        }
        response = httpx.request(method, url, headers=headers, json=json, timeout=60.0)
        if response.is_error:
            raise RuntimeError(
                f"Power BI {method} {path} failed ({response.status_code}): {response.text}"
            )
        return response.json()


@lru_cache(maxsize=1)
def get_powerbi_client() -> PowerBIClient:
    """Build (and cache) a passwordless ``PowerBIClient`` from settings."""
    # Lazy imports so non-network unit tests don't require azure libs.
    from azure.identity import get_bearer_token_provider

    from shared.azure_auth import get_credential

    settings = get_settings()
    token_provider = get_bearer_token_provider(
        get_credential(settings.exclude_managed_identity), settings.powerbi_token_scope
    )
    logger.info(
        "Initialising Power BI client: base_url=%s dataset=%s",
        settings.powerbi_base_url,
        settings.powerbi_dataset_id,
    )
    return PowerBIClient(
        token_provider,
        base_url=settings.powerbi_base_url,
        workspace_id=settings.powerbi_workspace_id,
        dataset_id=settings.powerbi_dataset_id,
    )
