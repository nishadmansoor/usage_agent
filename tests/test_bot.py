"""Offline tests for the Teams bot handler (agent + Bot Framework I/O stubbed)."""

import asyncio
import types

import pytest

import bot.usage_bot as ub
from bot.usage_bot import UsageBot


class FakeTurnContext:
    """Captures send_activity calls; mimics the bits the handler touches.

    Same conv_id + user_id across instances => they share conversation memory
    (that's how the multi-turn test exercises history).
    """

    def __init__(self, text, conv_id="conv-1", user_id="user-1"):
        self.activity = types.SimpleNamespace(
            text=text,
            type="message",
            entities=None,  # so remove_recipient_mention returns activity.text
            recipient=types.SimpleNamespace(id="bot-id"),
            conversation=types.SimpleNamespace(id=conv_id),
            from_property=types.SimpleNamespace(id=user_id),
        )
        self.sent = []

    async def send_activity(self, activity):
        self.sent.append(activity)

    def _texts(self):
        return [getattr(a, "text", None) for a in self.sent]


def _fake_agent(monkeypatch, answer="Engineering spent the most, at $4,210."):
    class FakeAgent:
        def __init__(self, *a, **k):
            pass

        def run(self, text, history=None):
            return types.SimpleNamespace(answer=f"{answer} [{text}]")

    monkeypatch.setattr(ub, "UsageAgent", FakeAgent)


def test_bot_replies_with_agent_answer(monkeypatch):
    _fake_agent(monkeypatch)
    ctx = FakeTurnContext("which team spent the most on Claude?")
    asyncio.run(UsageBot().on_message_activity(ctx))
    texts = [t for t in ctx._texts() if t]
    assert any("Engineering spent the most" in t for t in texts)
    assert "which team spent the most" in texts[-1]  # echoed input proves agent ran


def test_bot_prompts_when_empty(monkeypatch):
    _fake_agent(monkeypatch)  # should not be called
    ctx = FakeTurnContext("   ")
    asyncio.run(UsageBot().on_message_activity(ctx))
    texts = [t for t in ctx._texts() if t]
    assert len(texts) == 1 and "Ask me about AI usage" in texts[0]


def test_bot_times_out_gracefully(monkeypatch):
    import time

    class SlowAgent:
        def __init__(self, *a, **k):
            pass

        def run(self, text, history=None):
            time.sleep(0.5)
            return types.SimpleNamespace(answer="too late")

    monkeypatch.setattr(ub, "UsageAgent", SlowAgent)
    monkeypatch.setattr(ub, "BOT_TIMEOUT_SECONDS", 0.1)
    ctx = FakeTurnContext("a slow question")
    asyncio.run(UsageBot().on_message_activity(ctx))
    assert any(t and "took longer" in t for t in ctx._texts())


def test_bot_uses_bounded_step_cap(monkeypatch):
    captured = {}

    class RecordingAgent:
        def __init__(self, *a, **k):
            captured["max_steps"] = k.get("max_steps")

        def run(self, text, history=None):
            return types.SimpleNamespace(answer="ok")

    monkeypatch.setattr(ub, "UsageAgent", RecordingAgent)
    monkeypatch.setattr(ub, "BOT_MAX_STEPS", 8)
    ctx = FakeTurnContext("a question")
    asyncio.run(UsageBot().on_message_activity(ctx))
    assert captured["max_steps"] == 8


def test_bot_reports_agent_error(monkeypatch):
    class BoomAgent:
        def __init__(self, *a, **k):
            pass

        def run(self, text, history=None):
            raise RuntimeError("403 Public access is disabled")

    monkeypatch.setattr(ub, "UsageAgent", BoomAgent)
    ctx = FakeTurnContext("how much did we spend")
    asyncio.run(UsageBot().on_message_activity(ctx))
    assert any(t and "Sorry" in t for t in ctx._texts())


def _recording_agent(monkeypatch, seen):
    class RecordingAgent:
        def __init__(self, *a, **k):
            pass

        def run(self, text, history=None):
            seen.append(list(history or []))
            return types.SimpleNamespace(answer=f"answer to {text}")

    monkeypatch.setattr(ub, "UsageAgent", RecordingAgent)


def test_bot_multi_turn_memory(monkeypatch):
    seen = []
    _recording_agent(monkeypatch, seen)
    bot = UsageBot()  # one instance => shared history across the two messages
    asyncio.run(bot.on_message_activity(FakeTurnContext("first question")))
    asyncio.run(bot.on_message_activity(FakeTurnContext("second question")))

    assert seen[0] == []  # first turn: no prior history
    contents = [m["content"] for m in seen[1]]
    assert "first question" in contents  # prior question carried forward
    assert any("answer to first question" in c for c in contents)  # and its answer


def test_bot_reset_clears_history(monkeypatch):
    seen = []
    _recording_agent(monkeypatch, seen)
    bot = UsageBot()
    asyncio.run(bot.on_message_activity(FakeTurnContext("first question")))

    rctx = FakeTurnContext("reset")
    asyncio.run(bot.on_message_activity(rctx))
    assert any(t and "Cleared" in t for t in rctx._texts())  # reset acknowledged

    asyncio.run(bot.on_message_activity(FakeTurnContext("third question")))
    assert seen[-1] == []  # context was cleared, so no history on the next turn


def test_help_shows_three_starter_chips(monkeypatch):
    _fake_agent(monkeypatch)  # not called
    ctx = FakeTurnContext("   ")  # empty -> help + starter chips
    asyncio.run(UsageBot().on_message_activity(ctx))
    actions = ctx.sent[-1].suggested_actions.actions
    assert [a.title for a in actions] == ["Weekly spend", "By provider", "Top spenders"]
    assert all(a.type == "imBack" for a in actions)


def test_help_command_short_circuits(monkeypatch):
    _fake_agent(monkeypatch)  # must NOT be called
    ctx = FakeTurnContext("help")
    asyncio.run(UsageBot().on_message_activity(ctx))
    texts = [t for t in ctx._texts() if t]
    # Single reply = help text (no "On it…" ack + answer => agent wasn't invoked).
    assert len(texts) == 1 and "Ask me about AI usage" in texts[0]
    assert ctx.sent[-1].suggested_actions.actions  # starter chips attached


def test_answer_has_no_chips(monkeypatch):
    _fake_agent(monkeypatch)
    ctx = FakeTurnContext("spend last week")
    asyncio.run(UsageBot().on_message_activity(ctx))
    # The answer message is plain text — no follow-up chips attached.
    assert getattr(ctx.sent[-1], "suggested_actions", None) is None


def test_bot_separates_users(monkeypatch):
    seen = []
    _recording_agent(monkeypatch, seen)
    bot = UsageBot()
    asyncio.run(bot.on_message_activity(FakeTurnContext("alice q", user_id="alice")))
    asyncio.run(bot.on_message_activity(FakeTurnContext("bob q", user_id="bob")))
    # Bob's turn must NOT see Alice's history (per-user isolation).
    assert seen[1] == []
