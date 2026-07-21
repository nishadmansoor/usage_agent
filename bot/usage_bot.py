"""The Teams bot handler: message in -> UsageAgent -> chat reply out.

Replies are plain chat text (Markdown), not Adaptive Cards — the card format is
reserved for the outbound webhook posts (see ``usage_agent.teams``).

Multi-turn memory: a short rolling history of (question, answer) turns is kept per
user+conversation and passed to the agent, so follow-ups like "break that down by
team" or "what about last month?" resolve against the prior turn. History is
in-process (clears on restart), capped in length, and expires after a period of
inactivity so stale context doesn't leak into an unrelated later question. Users
can type "reset" to clear it.
"""

from __future__ import annotations

import asyncio
import os
import time

from botbuilder.core import ActivityHandler, MessageFactory, TurnContext
from botbuilder.schema import ActionTypes, Activity, ActivityTypes, CardAction, ChannelAccount

from usage_agent.agent import UsageAgent

# Resilience knobs (env-overridable): bound the agent's tool-loop and always
# reply within a time budget, so the bot can't hang on a runaway question.
BOT_MAX_STEPS = int(os.environ.get("BOT_MAX_STEPS", "12"))
BOT_TIMEOUT_SECONDS = int(os.environ.get("BOT_TIMEOUT_SECONDS", "90"))

# Multi-turn memory (env-overridable): keep the last N turns per user+conversation,
# forget after this much idle time, and cap stored answer length to bound tokens.
BOT_HISTORY_MESSAGES = int(os.environ.get("BOT_HISTORY_MESSAGES", "8"))  # 4 exchanges
BOT_HISTORY_TTL_SECONDS = int(os.environ.get("BOT_HISTORY_TTL_SECONDS", "1800"))  # 30 min
_MAX_ANSWER_CHARS = 4000  # truncate a long answer before storing it as context

_RESET_WORDS = {"reset", "clear", "start over", "new chat", "forget", "forget it"}
_HELP_WORDS = {"help", "hi", "hello", "hey", "?", "menu", "commands", "what can you do", "what can you do?"}

_WELCOME = (
    "Hi! I can answer questions about EA's AI usage & cost (Claude + Codex). "
)
_HELP = (
    'Ask me about AI usage or cost, e.g. "how much did we spend on Claude last '
    'week?" or "how many people used Codex in June?"'
)

# Starter quick-reply chips (title -> message the tap sends via imBack). Shown when
# there's no conversation context yet: welcome, the help prompt, and after a reset.
_STARTERS = [
    ("Weekly spend", "How much did we spend last week, and what was the week-over-week change?"),
    ("By provider", "Spend by provider last week"),
    ("Top spenders", "Who were the top 10 spenders last week?"),
]


def _with_starters(text: str) -> Activity:
    """A message carrying the starter quick-reply chips."""
    return MessageFactory.suggested_actions(
        [CardAction(type=ActionTypes.im_back, title=t, value=v) for t, v in _STARTERS],
        text,
    )


class UsageBot(ActivityHandler):
    """Runs the usage agent per message, with short per-user conversation memory."""

    def __init__(self) -> None:
        super().__init__()
        # key -> {"messages": [{"role", "content"}, ...], "ts": monotonic seconds}
        self._history: dict[str, dict] = {}

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        # Strip the bot @mention (in a channel); in 1:1 chat this is a no-op.
        text = (TurnContext.remove_recipient_mention(turn_context.activity) or "").strip()
        if not text:
            await turn_context.send_activity(_with_starters(_HELP))
            return

        # "help" / greetings -> show what I can do + starter chips, instead of
        # round-tripping the literal word to the model.
        if text.lower() in _HELP_WORDS:
            await turn_context.send_activity(_with_starters(_HELP))
            return

        key = self._conv_key(turn_context)

        # Let the user explicitly clear the conversation context.
        if text.lower() in _RESET_WORDS:
            self._history.pop(key, None)
            await turn_context.send_activity(
                _with_starters("Cleared our conversation context — ask me anything fresh.")
            )
            return

        history = self._get_history(key)

        # Acknowledge quickly — agent runs can take 15–60s.
        await turn_context.send_activity(Activity(type=ActivityTypes.typing))
        await turn_context.send_activity(MessageFactory.text("On it — checking the data…"))

        # UsageAgent.run is synchronous and slow; run it off the event loop with a
        # hard time budget so the bot ALWAYS replies, even if a run overruns.
        ok = False
        try:
            answer = await asyncio.wait_for(
                asyncio.to_thread(self._run_agent, text, history),
                timeout=BOT_TIMEOUT_SECONDS,
            )
            ok = True
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

        # Only remember successful exchanges, so errors/timeouts don't poison context.
        if ok:
            self._remember(key, text, answer)

        await turn_context.send_activity(MessageFactory.text(answer))

    async def on_members_added_activity(
        self, members_added: list[ChannelAccount], turn_context: TurnContext
    ) -> None:
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(_with_starters(_WELCOME))

    # -- conversation memory ------------------------------------------------
    @staticmethod
    def _conv_key(turn_context: TurnContext) -> str:
        """Per user + conversation, so different users in a shared channel keep
        separate context and don't see each other's follow-ups."""
        a = turn_context.activity
        conv = getattr(getattr(a, "conversation", None), "id", "conv")
        user = getattr(getattr(a, "from_property", None), "id", "user")
        return f"{conv}|{user}"

    def _get_history(self, key: str) -> list[dict]:
        entry = self._history.get(key)
        if not entry or (time.monotonic() - entry["ts"] > BOT_HISTORY_TTL_SECONDS):
            return []
        return list(entry["messages"])

    def _remember(self, key: str, question: str, answer: str) -> None:
        entry = self._history.get(key)
        stale = not entry or (time.monotonic() - entry["ts"] > BOT_HISTORY_TTL_SECONDS)
        messages = [] if stale else list(entry["messages"])
        messages += [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer[:_MAX_ANSWER_CHARS]},
        ]
        self._history[key] = {
            "messages": messages[-BOT_HISTORY_MESSAGES:],
            "ts": time.monotonic(),
        }

    @staticmethod
    def _run_agent(text: str, history: list[dict]) -> str:
        """Fresh agent per message (clients are cached), with a bounded tool-loop
        and the prior turns for multi-turn context."""
        return UsageAgent(max_steps=BOT_MAX_STEPS).run(text, history=history).answer
