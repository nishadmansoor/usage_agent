"""CLI entry point for the usage agent.

    usage-agent "which department spent the most on Claude last month?"
    python -m usage_agent "codex vs claude token trend over the last 30 days"

With no prompt it runs a default weekly-style briefing question.
"""

from __future__ import annotations

import argparse
import sys

from .agent import UsageAgent
from .config import get_settings
from .logging_config import configure_logging, get_logger

_DEFAULT_PROMPT = (
    "Give me a briefing on AI usage and cost over the last 7 days: top spenders, "
    "model mix across providers, and any notable week-over-week changes."
)


def main(argv: list[str] | None = None) -> int:
    """Run the agent once against a CLI prompt. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        description="Conversational agent over AI usage & cost data in Microsoft Fabric."
    )
    parser.add_argument("prompt", nargs="*", help="Question to ask (quote it).")
    parser.add_argument("--log-level", default=None, help="Override LOG_LEVEL.")
    args = parser.parse_args(argv)

    prompt = " ".join(args.prompt).strip() or _DEFAULT_PROMPT

    # Claude may emit non-cp1252 characters (emoji, smart quotes); make stdout
    # UTF-8 safe so printing the answer never crashes on a Windows console.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    # Use the OS certificate store (corporate SSL inspection). Optional dep.
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass

    settings = get_settings()
    configure_logging(args.log_level or settings.log_level)
    logger = get_logger(__name__)
    logger.info("Starting usage agent.")

    agent = UsageAgent()
    result = agent.run(prompt)

    print("\n" + "=" * 70)
    print(result.answer)
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
