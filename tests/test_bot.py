"""Offline tests for the Teams bot handler (agent + Bot Framework I/O stubbed)."""

import asyncio
import types

import pytest

import bot.usage_bot as ub
from bot.usage_bot import UsageBot


class FakeTurnContext:
    """Captures send_activity calls; mimics the bits the handler touches."""

    def __init__(self, text):
        self.activity = types.SimpleNamespace(
            text=text,
            type="message",
            entities=None,  # so remove_recipient_mention returns activity.text
            recipient=types.SimpleNamespace(id="bot-id"),
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

        def run(self, text):
            return types.SimpleNamespace(answer=f"{answer} [{text}]")

    monkeypatch.setattr(ub, "UsageAgent", FakeAgent)


def test_bot_replies_with_agent_answer(monkeypatch):
    _fake_agent(monkeypatch)
    ctx = FakeTurnContext("which team spent the most on Claude?")
    asyncio.run(UsageBot().on_message_activity(ctx))
    texts = [t for t in ctx._texts() if t]
    # Last message is the agent's answer.
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

        def run(self, text):
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

        def run(self, text):
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

        def run(self, text):
            raise RuntimeError("403 Public access is disabled")

    monkeypatch.setattr(ub, "UsageAgent", BoomAgent)
    ctx = FakeTurnContext("how much did we spend")
    asyncio.run(UsageBot().on_message_activity(ctx))
    assert any(t and "Sorry" in t for t in ctx._texts())
