"""The Teams bot handler: message in -> UsageAgent -> chat reply out.

Replies are plain chat text (Markdown), not Adaptive Cards — the card format is
reserved for the outbound webhook posts (see ``usage_agent.teams``).
"""

from __future__ import annotations

import asyncio
import os

from botbuilder.core import ActivityHandler, MessageFactory, TurnContext
from botbuilder.schema import Activity, ActivityTypes, ChannelAccount

from usage_agent.agent import UsageAgent

# Resilience knobs (env-overridable): bound the agent's tool-loop and always
# reply within a time budget, so the bot can't hang on a runaway question.
BOT_MAX_STEPS = int(os.environ.get("BOT_MAX_STEPS", "12"))
BOT_TIMEOUT_SECONDS = int(os.environ.get("BOT_TIMEOUT_SECONDS", "90"))

_WELCOME = (
    "Hi! I can answer questions about EA's AI usage & cost (Claude + Codex). "
)
_HELP = (
    'Ask me about AI usage or cost, e.g. "how much did we spend on Claude last '
    'week?" or "how many people used Codex in June?"'
)


class UsageBot(ActivityHandler):
    """Runs the usage agent for each incoming message and replies with text."""

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        # Strip the bot @mention (in a channel); in 1:1 chat this is a no-op.
        text = (TurnContext.remove_recipient_mention(turn_context.activity) or "").strip()
        if not text:
            await turn_context.send_activity(MessageFactory.text(_HELP))
            return

        # Acknowledge quickly — agent runs can take 15–60s.
        await turn_context.send_activity(Activity(type=ActivityTypes.typing))
        await turn_context.send_activity(MessageFactory.text("On it — checking the data…"))

        # UsageAgent.run is synchronous and slow; run it off the event loop with a
        # hard time budget so the bot ALWAYS replies, even if a run overruns.
        # (On timeout the worker thread finishes in the background and its result
        # is discarded — Python can't cancel a running thread.)
        try:
            answer = await asyncio.wait_for(
                asyncio.to_thread(self._run_agent, text),
                timeout=BOT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            answer = (
                f"That took longer than {BOT_TIMEOUT_SECONDS}s, so I stopped waiting. "
                "Try a narrower question — a single metric or a specific week/month "
                "usually comes back fast."
            )
        except Exception as exc:  # noqa: BLE001 - reply with a friendly message, not a stack trace
            answer = (
                f"Sorry — I couldn't complete that: {exc}\n\n"
                "If this is a connectivity/permissions issue, it may be the model or "
                "Power BI endpoint (network/VPN)."
            )

        await turn_context.send_activity(MessageFactory.text(answer))

    async def on_members_added_activity(
        self, members_added: list[ChannelAccount], turn_context: TurnContext
    ) -> None:
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(MessageFactory.text(_WELCOME))

    @staticmethod
    def _run_agent(text: str) -> str:
        """Fresh agent per message (concurrency-safe; clients are cached), with a
        bounded tool-loop so a bad question can't spiral."""
        return UsageAgent(max_steps=BOT_MAX_STEPS).run(text).answer
