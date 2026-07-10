"""Outgoing Teams notifications via a Power Automate / Workflows webhook.

Renders the agent's answer (or a briefing) as an Adaptive Card and posts it to a
Teams channel through a Workflows "Post to a channel when a webhook request is
received" trigger — i.e. it arrives as the Flow bot. Ported from the sla_teams
project's webhook delivery path.

No Azure AD token or Graph permission is involved: the secret is the webhook URL
itself (``TEAMS_WEBHOOK_URL``). If Teams isn't configured, posting is a no-op that
returns a clear, non-fatal message, so the agent works with or without Teams set
up. Genuine transport failures still raise.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .config import get_settings
from .logging_config import get_logger

logger = get_logger(__name__)

# Adaptive Card content type used inside the Workflows message payload.
_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"
# Teams renders Adaptive Cards schema 1.4 reliably in channel messages.
_CARD_SCHEMA_VERSION = "1.4"


def build_adaptive_card(title: str, text: str) -> dict[str, Any]:
    """Return an Adaptive Card payload (bold title heading + wrapped body text).

    ``text`` may contain a light subset of Markdown (bold, links, bullet lists),
    which Teams renders inside a wrapped ``TextBlock``.

    >>> card = build_adaptive_card("Hello", "World")
    >>> card["type"], card["version"], card["body"][0]["text"]
    ('AdaptiveCard', '1.4', 'Hello')
    """
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": _CARD_SCHEMA_VERSION,
        "body": [
            {"type": "TextBlock", "text": title, "size": "Large", "weight": "Bolder", "wrap": True},
            {"type": "TextBlock", "text": text, "wrap": True},
        ],
    }


def _webhook_payload(card: dict[str, Any]) -> dict[str, Any]:
    """Wrap an Adaptive Card for the Workflows Incoming-Webhook trigger.

    The "Post to a channel when a webhook request is received" trigger expects a
    top-level ``attachments`` array of Adaptive Card attachments (no Graph
    chatMessage envelope).
    """
    return {
        "type": "message",
        "attachments": [{"contentType": _CARD_CONTENT_TYPE, "content": card}],
    }


def _post_webhook(url: str, card: dict[str, Any]) -> str:
    """POST an Adaptive Card to a Teams Workflows webhook URL; return a status.

    The Workflows trigger returns 202 Accepted on success (it queues the post), so
    any 2xx is treated as delivered. Raises with the response text on non-2xx.
    """
    import httpx

    logger.info("Posting Teams message via Workflows webhook")
    response = httpx.post(url, json=_webhook_payload(card), timeout=30.0)
    if response.is_error:
        raise RuntimeError(
            f"Teams webhook POST failed ({response.status_code}): {response.text}"
        )
    return "Posted to Teams via Workflows webhook (Flow bot)."


def post_to_teams(
    text: str,
    title: str | None = None,
    *,
    question: str | None = None,
    poster: Callable[[str, dict[str, Any]], str] | None = None,
) -> str:
    """Post *text* to the configured Teams channel as an Adaptive Card.

    Parameters
    ----------
    text:
        Message body (the answer / briefing). A light subset of Markdown renders.
    title:
        Optional card heading. Defaults to a timestamped title.
    question:
        The original user question. When provided it's shown above the answer so
        the card stands alone. The agent passes this automatically.
    poster:
        Optional injected ``(url, card) -> status`` sender (tests pass a stub;
        production uses the Workflows webhook).

    Returns
    -------
    str
        A status string ("Posted to Teams …" on success, or a non-fatal
        "not configured" notice).
    """
    if not text or not text.strip():
        raise ValueError("post_to_teams requires non-empty 'text'.")

    settings = get_settings()
    if not settings.teams_enabled:
        msg = (
            "Teams is not configured: set TEAMS_WEBHOOK_URL (a Power Automate / "
            "Workflows Incoming Webhook URL) with TEAMS_TARGET=webhook to enable "
            "posting. Skipped."
        )
        logger.warning(msg)
        return msg

    if settings.teams_target != "webhook":
        return (
            f"Teams target '{settings.teams_target}' is not supported yet "
            "(only 'webhook' is implemented). Skipped."
        )

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    heading = title or f"AI Usage & Cost Agent — {stamp}"
    # Prepend the question so the card stands alone for whoever reads it in Teams.
    body = (
        f"**Question:** {question.strip()}\n\n{text}"
        if question and question.strip()
        else text
    )
    card = build_adaptive_card(heading, body)
    send = poster or _post_webhook
    return send(settings.teams_webhook_url, card)
