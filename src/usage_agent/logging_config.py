"""Logging setup for the usage agent (consistent with the repo's stdlib style)."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once, matching the pipeline's format."""
    logging.basicConfig(
        level=str(level).upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def get_logger(name: str) -> logging.Logger:
    """Return a module logger."""
    return logging.getLogger(name)
