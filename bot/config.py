"""Bot configuration (from environment).

For LOCAL testing with the Bot Framework Emulator, leave the Microsoft App
settings blank — the Emulator connects unauthenticated. They're only required
once the bot is registered with Azure Bot Service and deployed.
"""

from __future__ import annotations

import os


class BotConfig:
    """Bot Framework auth + server settings, read from env vars."""

    PORT = int(os.environ.get("PORT", "3978"))
    # Bind address — 0.0.0.0 so the server is reachable inside a container / Azure
    # (localhost would only listen on the container's loopback). Override with HOST.
    HOST = os.environ.get("HOST", "0.0.0.0")
    # Azure Bot registration (blank = local/Emulator, unauthenticated).
    APP_ID = os.environ.get("MicrosoftAppId", "")
    APP_PASSWORD = os.environ.get("MicrosoftAppPassword", "")
    APP_TYPE = os.environ.get("MicrosoftAppType", "MultiTenant")
    APP_TENANTID = os.environ.get("MicrosoftAppTenantId", "")
    APP_MSI_RESOURCE_id = os.environ.get("MicrosoftAppMSIResourceId", "")
