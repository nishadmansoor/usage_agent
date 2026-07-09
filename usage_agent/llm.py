"""Claude client — pluggable between a direct Anthropic API key and Foundry.

Default backend is this repo's existing direct ``ANTHROPIC_API_KEY`` (see
``anthropic/clients/anthropic_client.py``). Set ``LLM_BACKEND=foundry`` to use
passwordless Microsoft Foundry with the shared Azure AD identity instead — a
config swap, no code change. Either way the returned client's ``messages.create``
surface matches the first-party Anthropic SDK.

Imports are lazy so offline unit tests (which stub the client) don't need the
``anthropic`` / ``azure`` libraries at import time.
"""

from __future__ import annotations

from functools import lru_cache

from .config import Settings, get_settings
from .logging_config import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_claude_client():
    """Build (and cache) a Claude client for the configured backend."""
    settings = get_settings()
    backend = settings.llm_backend
    if backend == "api_key":
        return _build_api_key_client(settings)
    if backend == "foundry":
        return _build_foundry_client(settings)
    raise RuntimeError(
        f"Unknown LLM_BACKEND '{backend}'. Use 'api_key' (default) or 'foundry'."
    )


def _build_api_key_client(settings: Settings):
    from anthropic import Anthropic

    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set (LLM_BACKEND=api_key). Add it to your .env."
        )
    # Optional base-URL override for an org gateway/proxy (public API if unset).
    kwargs: dict = {"api_key": settings.anthropic_api_key.strip()}
    if settings.anthropic_base_url:
        kwargs["base_url"] = settings.anthropic_base_url.strip()
        logger.info(
            "Initialising Claude client (API key via base_url=%s): model=%s",
            kwargs["base_url"],
            settings.anthropic_model,
        )
    else:
        logger.info("Initialising Claude client (direct API key): model=%s", settings.anthropic_model)
    return Anthropic(**kwargs)


def _build_foundry_client(settings: Settings):
    from anthropic import AnthropicFoundry
    from azure.identity import get_bearer_token_provider

    from shared.azure_auth import get_credential

    if not settings.foundry_resource:
        raise RuntimeError("FOUNDRY_RESOURCE is not set (LLM_BACKEND=foundry).")
    token_provider = get_bearer_token_provider(
        get_credential(settings.exclude_managed_identity), settings.foundry_token_scope
    )
    logger.info(
        "Initialising Claude (Foundry) client: resource=%s model=%s",
        settings.foundry_resource,
        settings.anthropic_model,
    )
    return AnthropicFoundry(
        resource=settings.foundry_resource,
        azure_ad_token_provider=token_provider,
    )
