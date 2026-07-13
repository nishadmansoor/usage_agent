"""aiohttp web service hosting the Teams bot (the /api/messages endpoint).

Run locally:  python -m bot   ->  http://localhost:3978/api/messages
Then point the Bot Framework Emulator at that URL (blank App ID/password).
"""

from __future__ import annotations

import sys

from aiohttp import web
from aiohttp.web import Request, Response
from botbuilder.core import TurnContext
from botbuilder.core.integration import aiohttp_error_middleware
from botbuilder.integration.aiohttp import (
    CloudAdapter,
    ConfigurationBotFrameworkAuthentication,
)

from bot.config import BotConfig
from bot.usage_bot import UsageBot

# Use the OS certificate store (corporate SSL inspection). Optional dependency.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

CONFIG = BotConfig()
ADAPTER = CloudAdapter(ConfigurationBotFrameworkAuthentication(CONFIG))
BOT = UsageBot()


async def _on_error(context: TurnContext, error: Exception) -> None:
    print(f"[on_turn_error] {error}", file=sys.stderr)
    await context.send_activity("Sorry, something went wrong handling that message.")


ADAPTER.on_turn_error = _on_error


async def messages(req: Request) -> Response:
    """Bot Framework channel endpoint."""
    return await ADAPTER.process(req, BOT)


def create_app() -> web.Application:
    app = web.Application(middlewares=[aiohttp_error_middleware])
    app.router.add_post("/api/messages", messages)
    return app


def main() -> None:
    web.run_app(create_app(), host="localhost", port=CONFIG.PORT)


if __name__ == "__main__":
    main()
