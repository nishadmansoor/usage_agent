"""Environment-driven configuration for the usage agent.

All configuration is read from environment variables (loaded from a local
``.env`` in development via ``python-dotenv``). Credentials are never hardcoded.

Two backends the agent reads from:
- **Power BI semantic model** (primary): DAX via the REST ``executeQueries``
  endpoint, so answers reconcile with the dashboard's own measures.
- **Fabric SQL** (drill-down): raw read-only SQL; connection details live in
  ``shared.fabric`` (env-driven) and are not duplicated here.

Claude auth is pluggable (see ``LLM_BACKEND``): the repo's existing direct
``ANTHROPIC_API_KEY`` by default, or passwordless Microsoft Foundry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

# Load .env once at import time. In production the platform typically injects the
# variables, so a missing .env file is not an error.
load_dotenv()

_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_POWERBI_BASE_URL = "https://api.powerbi.com/v1.0/myorg"
# Azure AD token scope for the Power BI REST API (semantic-model DAX queries).
_DEFAULT_POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
# Azure AD token scope for Claude via Microsoft Foundry (Cognitive Services).
_DEFAULT_FOUNDRY_SCOPE = "https://cognitiveservices.azure.com/.default"


def _get(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = os.getenv(name)
    if value in (None, ""):
        if required:
            raise RuntimeError(
                f"Missing required environment variable: {name}. "
                f"See AGENT_PLAN.md -> Configuration."
            )
        return default
    return value


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable application settings."""

    # --- Claude (see LLM_BACKEND) ---
    llm_backend: str            # "api_key" (default) or "foundry"
    anthropic_api_key: str | None
    anthropic_base_url: str | None  # override for an org gateway/proxy (optional)
    anthropic_model: str
    claude_max_tokens: int
    foundry_resource: str | None
    foundry_token_scope: str

    # --- Power BI semantic model (primary read backend) ---
    powerbi_base_url: str
    powerbi_workspace_id: str | None
    powerbi_dataset_id: str | None
    powerbi_token_scope: str

    # --- Microsoft Teams notifications (Workflows webhook / Flow bot) ---
    teams_target: str
    teams_webhook_url: str | None

    # --- Azure auth (shared credential) ---
    exclude_managed_identity: bool

    # --- App behaviour ---
    log_level: str
    query_row_limit: int

    @property
    def powerbi_enabled(self) -> bool:
        """True when a target semantic model (dataset id) is configured."""
        return bool(self.powerbi_dataset_id)

    @property
    def teams_enabled(self) -> bool:
        """True when the configured Teams target has what it needs to deliver.

        Only the ``webhook`` target is implemented, so this is true when a
        Workflows webhook URL is set. The posting layer degrades gracefully
        (a clear "not configured" message) when it isn't.
        """
        if self.teams_target == "webhook":
            return bool(self.teams_webhook_url)
        return False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build (and cache) the application settings from the environment."""
    return Settings(
        # Claude
        llm_backend=(_get("LLM_BACKEND", "api_key") or "api_key").strip().lower(),
        anthropic_api_key=_get("ANTHROPIC_API_KEY"),
        anthropic_base_url=_get("ANTHROPIC_BASE_URL"),
        anthropic_model=_get("ANTHROPIC_MODEL", _DEFAULT_MODEL),
        claude_max_tokens=int(_get("CLAUDE_MAX_TOKENS", "4096")),
        foundry_resource=_get("FOUNDRY_RESOURCE"),
        foundry_token_scope=_get("FOUNDRY_TOKEN_SCOPE", _DEFAULT_FOUNDRY_SCOPE),
        # Power BI
        powerbi_base_url=_get("POWERBI_BASE_URL", _DEFAULT_POWERBI_BASE_URL),
        powerbi_workspace_id=_get("POWERBI_WORKSPACE_ID"),
        powerbi_dataset_id=_get("POWERBI_DATASET_ID"),
        powerbi_token_scope=_get("POWERBI_TOKEN_SCOPE", _DEFAULT_POWERBI_SCOPE),
        # Microsoft Teams
        teams_target=(_get("TEAMS_TARGET", "webhook") or "webhook").strip().lower(),
        teams_webhook_url=_get("TEAMS_WEBHOOK_URL"),
        # Azure auth
        exclude_managed_identity=_get_bool("EXCLUDE_MANAGED_IDENTITY", True),
        # App
        log_level=_get("LOG_LEVEL", "INFO"),
        query_row_limit=int(_get("QUERY_ROW_LIMIT", "1000")),
    )
