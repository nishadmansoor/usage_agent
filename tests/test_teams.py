"""Tests for the Teams webhook notification layer (fully offline).

The HTTP send is replaced with an injected ``poster`` stub and settings are
monkeypatched, so no network or webhook URL is needed.
"""

import pytest

from usage_agent import teams


class _Settings:
    """Minimal settings double mirroring the real teams_enabled semantics."""

    def __init__(self, enabled=True, target="webhook"):
        self.teams_target = target
        self.teams_webhook_url = "https://example.com/webhook" if enabled else None

    @property
    def teams_enabled(self):
        if self.teams_target == "webhook":
            return bool(self.teams_webhook_url)
        return False


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(teams, "get_settings", lambda: _Settings(enabled=True))


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.setattr(teams, "get_settings", lambda: _Settings(enabled=False))


def test_build_adaptive_card_structure():
    card = teams.build_adaptive_card("Heading", "Body text")
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == teams._CARD_SCHEMA_VERSION
    assert card["body"][0]["text"] == "Heading"
    assert card["body"][0]["weight"] == "Bolder"
    assert card["body"][1]["text"] == "Body text"
    assert card["body"][1]["wrap"] is True


def test_webhook_payload_shape():
    card = teams.build_adaptive_card("t", "b")
    payload = teams._webhook_payload(card)
    assert payload["type"] == "message"
    assert len(payload["attachments"]) == 1
    attachment = payload["attachments"][0]
    assert attachment["contentType"] == teams._CARD_CONTENT_TYPE
    assert attachment["content"] is card


def test_post_to_teams_posts_card(enabled):
    calls = []

    def poster(url, card):
        calls.append((url, card))
        return "Posted to Teams via Workflows webhook (Flow bot)."

    result = teams.post_to_teams(
        "Hello team", title="Alert", question="How much did we spend?", poster=poster
    )
    assert result.startswith("Posted to Teams")
    assert len(calls) == 1
    url, card = calls[0]
    assert url == "https://example.com/webhook"
    assert card["body"][0]["text"] == "Alert"
    # The question is prepended to the body so the card stands alone.
    body = card["body"][1]["text"]
    assert "How much did we spend?" in body
    assert "Hello team" in body


def test_post_to_teams_default_title(enabled):
    captured = {}

    def poster(url, card):
        captured["card"] = card
        return "Posted to Teams via Workflows webhook (Flow bot)."

    teams.post_to_teams("Body only", poster=poster)
    assert "AI Usage & Cost Agent" in captured["card"]["body"][0]["text"]


def test_post_to_teams_not_configured_is_non_fatal(disabled):
    calls = []
    result = teams.post_to_teams("hi", poster=lambda u, c: calls.append(1))
    assert "not configured" in result.lower()
    assert calls == []  # nothing posted


def test_post_to_teams_rejects_empty(enabled):
    with pytest.raises(ValueError):
        teams.post_to_teams("   ", poster=lambda u, c: "x")
