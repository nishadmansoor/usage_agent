"""CLI entry point for the usage agent.

    usage-agent "which department spent the most on Claude last month?"
    python -m usage_agent "codex vs claude token trend over the last 30 days"
    python -m usage_agent --one-pager            # weekly exec briefing -> Word doc
    python -m usage_agent --one-pager "claude spend by department last month"

With no prompt it runs a default weekly-style briefing question. With
``--one-pager`` it renders a branded executive briefing to a Word (.docx) doc.
"""

from __future__ import annotations

import argparse
import sys

from .agent import UsageAgent
from .config import get_settings
from .logging_config import configure_logging, get_logger
from .prompts.one_pager import build_one_pager_prompt
from .reports.one_pager import default_output_path, parse_report, render_one_pager_docx

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
    parser.add_argument(
        "--one-pager",
        action="store_true",
        help="Render a branded executive briefing to a Word (.docx) doc. With no "
        "prompt, produces the weekly previous-week-vs-prior-week summary.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output path for the --one-pager doc (default: ai_usage_one_pager_<date>.docx).",
    )
    args = parser.parse_args(argv)

    user_prompt = " ".join(args.prompt).strip()
    prompt = user_prompt or _DEFAULT_PROMPT

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

    if args.one_pager:
        return _run_one_pager(agent, user_prompt, args.output, logger)

    result = agent.run(prompt)

    print("\n" + "=" * 70)
    print(result.answer)
    print("=" * 70)
    return 0


def _run_one_pager(agent, user_prompt: str, output, logger) -> int:
    """Generate a briefing and render it to a branded Word (.docx). Returns exit code."""
    result = agent.run(build_one_pager_prompt(user_prompt))
    output_path = output or default_output_path()
    try:
        report = parse_report(result.answer)
        written = render_one_pager_docx(report, output_path)
    except Exception:  # noqa: BLE001 - surface a clean message, not a traceback
        logger.exception("Failed to build the one-pager document")
        # Don't lose the content: fall back to printing the raw answer.
        print(result.answer)
        return 1

    print("\n" + "=" * 70)
    print(f"One-pager written to: {written}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
