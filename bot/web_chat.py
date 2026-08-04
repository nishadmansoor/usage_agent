from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
from pathlib import Path

from aiohttp import web

from bot.config import BotConfig
from usage_agent.agent import UsageAgent


CHAT_MAX_STEPS = int(os.environ.get("BOT_MAX_STEPS", "12"))
CHAT_TIMEOUT_SECONDS = int(os.environ.get("BOT_TIMEOUT_SECONDS", "90"))
CHAT_HISTORY_MESSAGES = int(os.environ.get("BOT_HISTORY_MESSAGES", "8"))
CHAT_HISTORY_TTL_SECONDS = int(
    os.environ.get("BOT_HISTORY_TTL_SECONDS", "1800")
)

_MAX_ANSWER_CHARS = 4000
_INDEX_FILE = Path(__file__).parent / "static" / "index.html"

# session ID -> {"messages": [...], "timestamp": monotonic time}
_HISTORY: dict[str, dict] = {}


def _get_history(session_id: str) -> list[dict]:
    """Return non-expired conversation history for a browser session."""
    entry = _HISTORY.get(session_id)

    if not entry:
        return []

    if time.monotonic() - entry["timestamp"] > CHAT_HISTORY_TTL_SECONDS:
        _HISTORY.pop(session_id, None)
        return []

    return list(entry["messages"])


def _remember(
    session_id: str,
    question: str,
    answer: str,
) -> None:
    """Store a successful question and answer for follow-up questions."""
    previous = _get_history(session_id)

    messages = previous + [
        {"role": "user", "content": question},
        {
            "role": "assistant",
            "content": answer[:_MAX_ANSWER_CHARS],
        },
    ]

    _HISTORY[session_id] = {
        "messages": messages[-CHAT_HISTORY_MESSAGES:],
        "timestamp": time.monotonic(),
    }


def _run_agent(message: str, history: list[dict]) -> str:
    """Run the synchronous UsageAgent outside the web event loop."""
    result = UsageAgent(max_steps=CHAT_MAX_STEPS).run(
        message,
        history=history,
    )
    return result.answer


async def chat_page(request: web.Request) -> web.StreamResponse:
    """Serve the standalone chat webpage."""
    if not _INDEX_FILE.exists():
        raise web.HTTPNotFound(
            text="The chat interface has not been created yet."
        )

    return web.FileResponse(_INDEX_FILE)


async def health(request: web.Request) -> web.Response:
    """Simple health endpoint for Azure Container Apps."""
    return web.json_response(
        {
            "status": "healthy",
            "service": "usage-agent-chat",
        }
    )


async def chat(request: web.Request) -> web.Response:
    """Accept a chat message and return the agent's answer."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": "The request body must be valid JSON."},
            status=400,
        )

    message = body.get("message")
    session_id = body.get("sessionId")

    if not isinstance(message, str) or not message.strip():
        return web.json_response(
            {"error": "A non-empty message is required."},
            status=400,
        )

    message = message.strip()

    if not isinstance(session_id, str) or not session_id.strip():
        session_id = secrets.token_urlsafe(24)
    else:
        session_id = session_id.strip()[:128]

    history = _get_history(session_id)

    try:
        answer = await asyncio.wait_for(
            asyncio.to_thread(_run_agent, message, history),
            timeout=CHAT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return web.json_response(
            {
                "error": (
                    f"The request took longer than "
                    f"{CHAT_TIMEOUT_SECONDS} seconds. "
                    "Try asking a narrower question."
                ),
                "sessionId": session_id,
            },
            status=504,
        )
    except Exception as exc:
        # For an internal first version this returns a useful error.
        # We can replace it with a generic message before production.
        return web.json_response(
            {
                "error": f"The agent could not complete the request: {exc}",
                "sessionId": session_id,
            },
            status=500,
        )

    _remember(session_id, message, answer)

    return web.json_response(
        {
            "answer": answer,
            "sessionId": session_id,
        }
    )


async def reset_chat(request: web.Request) -> web.Response:
    """Clear conversation history for one browser session."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    session_id = body.get("sessionId")

    if isinstance(session_id, str):
        _HISTORY.pop(session_id.strip(), None)

    return web.json_response({"status": "reset"})


async def chat_stream(request: web.Request) -> web.StreamResponse:
    """Server-Sent Events variant of :func:`chat`.

    Streams the agent's progress as it runs, as ``data: {json}\\n\\n`` frames:
      {"type":"tool","name":...}  — a tool (DB query) is being called
      {"type":"text","text":...}  — a chunk of answer text
      {"type":"error","text":...} — a failure
      {"type":"done","sessionId":...} — end of stream
    Falls back to a full non-streaming run if streaming errors before any output,
    so the browser always gets an answer.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "The request body must be valid JSON."}, status=400)

    message = body.get("message")
    session_id = body.get("sessionId")

    if not isinstance(message, str) or not message.strip():
        return web.json_response({"error": "A non-empty message is required."}, status=400)
    message = message.strip()

    if not isinstance(session_id, str) or not session_id.strip():
        session_id = secrets.token_urlsafe(24)
    else:
        session_id = session_id.strip()[:128]

    print(f"[web] user={_current_user(request) or 'anonymous'} asked: {message!r}", flush=True)

    history = _get_history(session_id)

    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # tell proxies not to buffer, so events flush live
        }
    )
    await response.prepare(request)

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    DONE = object()

    def worker() -> None:
        """Run the (synchronous) streaming agent off the event loop, pushing
        each event onto the async queue."""
        parts: list[str] = []
        emitted = False
        try:
            for ev in UsageAgent(max_steps=CHAT_MAX_STEPS).run_stream(message, history=history):
                if ev.get("type") == "text":
                    parts.append(ev.get("text", ""))
                emitted = True
                loop.call_soon_threadsafe(queue.put_nowait, ev)
        except Exception as exc:  # noqa: BLE001
            # If streaming failed before producing anything, fall back to a full run.
            if not emitted:
                try:
                    answer = UsageAgent(max_steps=CHAT_MAX_STEPS).run(message, history=history).answer
                    parts = [answer]
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "text", "text": answer})
                except Exception as exc2:  # noqa: BLE001
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "text": str(exc2)})
            else:
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "text": str(exc)})
        finally:
            answer = "".join(parts).strip()
            if answer:
                _remember(session_id, message, answer)
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "done", "sessionId": session_id})
            loop.call_soon_threadsafe(queue.put_nowait, DONE)

    threading.Thread(target=worker, daemon=True).start()

    try:
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=CHAT_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                await response.write(
                    b'data: {"type":"error","text":"The request took too long. Try a narrower question."}\n\n'
                )
                break
            if ev is DONE:
                break
            await response.write(("data: " + json.dumps(ev) + "\n\n").encode("utf-8"))
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        try:
            await response.write_eof()
        except Exception:  # noqa: BLE001
            pass

    return response


def _current_user(request: web.Request) -> str | None:
    """The signed-in user's name from Azure Easy Auth headers, or None (anonymous).

    When Easy Auth is enabled on the Container App, the platform injects the
    authenticated identity as ``X-MS-CLIENT-PRINCIPAL-*`` headers. Locally there is
    no auth layer, so this is None — set ``DEV_AUTH_USER`` to simulate a signed-in
    user while testing the UI on your laptop.
    """
    return (
        request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME")
        or os.environ.get("DEV_AUTH_USER")
        or None
    )


async def me(request: web.Request) -> web.Response:
    """Report the current signed-in user, so the UI can show "Signed in as …"."""
    return web.json_response({"user": _current_user(request)})


# ---------------------------------------------------------------------------
# Web application (routes + server).
#
# This replaces the Bot Framework adapter (bot/app.py) for the web-app deploy:
# the browser (on the corp network) calls IN, so no public inbound is needed —
# which is exactly what makes this work where a Teams messaging endpoint can't.
#
# Run locally:  python -m bot.web_chat   ->  http://localhost:3978/
# ---------------------------------------------------------------------------
CONFIG = BotConfig()


def create_app() -> web.Application:
    """Build the aiohttp app: the chat page, its JSON API, and a health probe.

    Route paths match what bot/static/index.html calls (/api/chat, /api/chat/reset).
    """
    app = web.Application()
    app.router.add_get("/", chat_page)
    app.router.add_get("/health", health)
    app.router.add_get("/api/me", me)
    app.router.add_post("/api/chat", chat)
    app.router.add_post("/api/chat/stream", chat_stream)
    app.router.add_post("/api/chat/reset", reset_chat)
    # Serve vendored assets (marked.min.js, purify.min.js) from bot/static/, so the
    # page has no external CDN dependency — it works on a locked-down network.
    app.router.add_static("/static", str(_INDEX_FILE.parent))
    return app


def main() -> None:
    # Use the OS certificate store (corporate SSL inspection), as bot/app.py does,
    # so the agent's outbound calls to Foundry / Power BI / Fabric work locally.
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass

    web.run_app(create_app(), host=CONFIG.HOST, port=CONFIG.PORT)


if __name__ == "__main__":
    main()