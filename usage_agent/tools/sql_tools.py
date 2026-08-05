"""Predefined, read-only SQL over the ``openai_anthropic_*`` Fabric tables.

The agent may **NOT** write SQL. Every query here is a FIXED, parameterised
statement scoped to an allowlist of ``openai_anthropic_*`` tables — the agent only
chooses a table (from that allowlist) and a row count, never the SQL text. Nothing
outside the allowlist is reachable (no ad-hoc tables, no ``INFORMATION_SCHEMA``
fishing beyond the allowlisted prefixes).

The PRIMARY analytical path is DAX over the semantic model's measures (see
``usage_metrics``). The tools here exist only so the agent can INSPECT the raw
rows/columns the model doesn't surface as measures — NEVER to report a figure.
Every reported number, without exception, comes from ``usage_metrics``.

Note these read the BRONZE lakehouse (``FABRIC_SQL_DATABASE``), a different layer
from the gold semantic model — and for ``*_usage_report`` / ``*_dim_user_dept`` /
``*_forecast`` a stale pre-split copy. That is the other reason never to quote them.

Two guards apply, belt-and-braces: the table **allowlist** (``_resolve``) keeps
queries inside the ``openai_anthropic_*`` dataset, and ``validation.ensure_read_only``
(called in ``_read``) re-verifies at execution time that only a single read-only
statement — no writes, DDL, batches, or ``OPENROWSET``-style escapes — ever runs.
"""

from __future__ import annotations

import pandas as pd

from ..config import get_settings
from ..logging_config import get_logger
from .validation import ensure_read_only

logger = get_logger(__name__)

# The ONLY tables these tools may read. Every entry is an ``openai_anthropic_*``
# table; the values are the real ``dbo`` table names. A table name that is not in
# this map is rejected — this is what enforces "only openai_anthropic".
_ALLOWED_TABLES: dict[str, str] = {
    "openai_anthropic_usage": "openai_anthropic_usage",
    "openai_anthropic_usage_report": "openai_anthropic_usage_report",
    "openai_anthropic_usage_clients": "openai_anthropic_usage_clients",
    "openai_anthropic_dim_user": "openai_anthropic_dim_user",
    "openai_anthropic_dim_user_dept": "openai_anthropic_dim_user_dept",
    "openai_anthropic_dim_product": "openai_anthropic_dim_product",
    "openai_anthropic_dim_provider": "openai_anthropic_dim_provider",
    "openai_anthropic_dim_date": "openai_anthropic_dim_date",
    "openai_anthropic_forecast": "openai_anthropic_forecast",
}

# Short aliases (e.g. "usage" -> "openai_anthropic_usage") so the agent can use the
# convenient name; both the full and short forms resolve to the same dbo table.
_ALIASES: dict[str, str] = {
    name.removeprefix("openai_anthropic_"): name for name in _ALLOWED_TABLES
}

# The canonical names to advertise to the model (used for the tool-schema enum).
ALLOWED_TABLE_NAMES: list[str] = sorted(_ALLOWED_TABLES)

_MAX_PREVIEW_ROWS = 100


def _engine():
    # Lazy import so importing the tool doesn't require the Fabric/ODBC stack.
    from shared.fabric import get_fabric_engine

    return get_fabric_engine()


def _resolve(table: str) -> str:
    """Map an agent-supplied name to an allowlisted ``dbo`` table, or raise.

    Accepts the full ``openai_anthropic_*`` name or its short alias (``usage``,
    ``dim_user_dept``, ...). Anything else is rejected — this is the guard that
    keeps these tools inside the openai_anthropic dataset.
    """
    key = (table or "").strip().lower()
    if key in _ALLOWED_TABLES:
        return _ALLOWED_TABLES[key]
    if key in _ALIASES:
        return _ALIASES[key]
    raise ValueError(
        f"Table {table!r} is not allowed. Choose one of the openai_anthropic "
        f"tables: {ALLOWED_TABLE_NAMES}"
    )


def _read(sql: str, params: dict | None = None, *, limit: int | None = None) -> pd.DataFrame:
    """Run a FIXED read-only statement and return a DataFrame (rows capped).

    ``ensure_read_only`` is the defense-in-depth safety net: although every caller
    here builds a fixed SELECT from the table allowlist, we re-verify at execution
    time that nothing but a single read-only statement reaches the engine.
    """
    from sqlalchemy import text

    ensure_read_only(sql)
    cap = limit or get_settings().query_row_limit
    with _engine().connect() as conn:
        df = pd.read_sql(text(sql), conn, params=params or {})
    if len(df) > cap:
        logger.warning("Query returned %d rows; truncating to %d.", len(df), cap)
        df = df.head(cap)
    return df


def list_data_tables() -> pd.DataFrame:
    """List the openai_anthropic tables and their columns (schema review).

    Fixed query — returns ``TABLE_NAME``, ``COLUMN_NAME``, ``DATA_TYPE`` for every
    ``openai_anthropic_*`` table so the agent can see what raw columns exist before
    previewing a table. The agent cannot alter this query.
    """
    sql = (
        "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME LIKE 'openai\\_anthropic%' ESCAPE '\\' "
        "ORDER BY TABLE_NAME, ORDINAL_POSITION"
    )
    logger.info("list_data_tables()")
    return _read(sql)


def preview_table(table: str, row_limit: int = 20) -> pd.DataFrame:
    """Return the first N rows of ONE allowlisted openai_anthropic table.

    Fixed ``SELECT TOP (N) *`` — the agent chooses only the table (validated
    against the openai_anthropic allowlist) and the row count (1-100). No SQL text
    comes from the agent.
    """
    dbo = _resolve(table)
    n = max(1, min(int(row_limit or 20), _MAX_PREVIEW_ROWS))
    sql = f"SELECT TOP ({n}) * FROM dbo.{dbo}"  # dbo from allowlist, n a clamped int
    logger.info("preview_table(%s, %d)", dbo, n)
    return _read(sql, limit=n)


def table_row_count(table: str) -> pd.DataFrame:
    """Return the row count of ONE allowlisted openai_anthropic table (fixed query)."""
    dbo = _resolve(table)
    sql = f"SELECT COUNT(*) AS row_count FROM dbo.{dbo}"  # dbo from allowlist
    logger.info("table_row_count(%s)", dbo)
    return _read(sql, limit=1)
