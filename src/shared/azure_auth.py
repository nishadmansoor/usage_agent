"""Shared passwordless Azure AD credential.

Used by the Fabric SQL connection, the Power BI DAX client, and (optionally)
Claude via Microsoft Foundry — one identity, several token scopes. Ported from
the sla_teams project.

The credential is tuned for this app's realities:

- **Managed identity is excluded by default** — its IMDS probe can hang for
  minutes on a corporate workstation. Set ``EXCLUDE_MANAGED_IDENTITY=false`` when
  deploying to Azure with a managed identity.
- **The environment (service-principal) credential is excluded unless all three
  of ``AZURE_TENANT_ID`` / ``AZURE_CLIENT_ID`` / ``AZURE_CLIENT_SECRET`` are
  actually populated.** ``.env`` files often carry these as blank placeholders,
  which ``python-dotenv`` loads as empty strings; without this guard
  ``EnvironmentCredential`` would activate and fail before ``az login`` is tried.

Net effect: with blank SP vars you get ``az login`` (local dev); fill them in and
the service principal is used instead.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_SP_VARS = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_credential(exclude_managed_identity: bool | None = None):
    """Return a ``DefaultAzureCredential`` configured for this app.

    ``exclude_managed_identity`` defaults to the ``EXCLUDE_MANAGED_IDENTITY`` env
    var (true if unset) so callers usually pass nothing.
    """
    # Imported lazily so code paths / tests that never authenticate don't need
    # the azure libraries at import time.
    from azure.identity import DefaultAzureCredential

    if exclude_managed_identity is None:
        exclude_managed_identity = _env_bool("EXCLUDE_MANAGED_IDENTITY", True)

    # Empty strings are falsy, so blank .env placeholders count as "not set".
    sp_configured = all(os.getenv(v) for v in _SP_VARS)

    log.debug(
        "Building Azure credential (exclude_managed_identity=%s, service_principal=%s)",
        exclude_managed_identity,
        sp_configured,
    )
    return DefaultAzureCredential(
        exclude_managed_identity_credential=exclude_managed_identity,
        exclude_environment_credential=not sp_configured,
    )
