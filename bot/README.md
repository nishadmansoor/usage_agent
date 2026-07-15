# `bot/` — interactive Teams bot

An aiohttp web service that hosts the usage agent as a **Bot Framework** bot, so
users can ask questions in a Teams chat/channel and get grounded answers. It reuses
`UsageAgent` unchanged — same tools, same openai_anthropic scope, same "no
self-written SQL" guarantees as the CLI.

## Modules

| File | Responsibility |
|---|---|
| `app.py` | aiohttp app exposing `POST /api/messages`; wires the Bot Framework `CloudAdapter` to `UsageBot`. `python -m bot` runs it locally on `:3978`. |
| `usage_bot.py` | `UsageBot` (`ActivityHandler`). Per message: strips the @mention, sends a quick "on it" ack, runs the agent off the event loop with a hard timeout, replies with plain Markdown text. |
| `config.py` | Bot Framework app id/password + port from the environment. |
| `__main__.py` | `python -m bot` entry point. |

## Behaviour & resilience

- **Replies are plain chat text** (Markdown). Adaptive Cards are reserved for the
  outbound webhook posts (`usage_agent/teams.py`).
- **Bounded**: `BOT_MAX_STEPS` (default 12) caps the agent's tool loop;
  `BOT_TIMEOUT_SECONDS` (default 90) guarantees a reply even if a run overruns (the
  worker thread finishes in the background and its result is discarded — Python
  can't cancel a running thread).
- **Fresh agent per message** — concurrency-safe; the underlying clients are cached.
- **Friendly errors** — connectivity/permission failures come back as a short
  message, never a stack trace.

## Running locally

```powershell
python -m bot            # -> http://localhost:3978/api/messages
```

Point the Bot Framework Emulator at that URL (blank App ID/password for local). The
Teams app manifest lives in `teams_app/` (see `teams_app/README.md`).
