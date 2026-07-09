"""Controlled Fabric SQL execution — the drill-down / escape-hatch tool.

``run_sql_query`` is the single choke point for raw SQL against the Fabric SQL
endpoint. It enforces the read-only guarantee, binds parameters safely, and caps
the number of rows returned. The **primary** query path is DAX against the
semantic model (see ``dax_tools``); this exists for ad-hoc columns the model
doesn't expose. Ported from sla_teams.
"""

from __future__ import annotations

import pandas as pd

from ..config import get_settings
from ..logging_config import get_logger
from .validation import ensure_read_only

logger = get_logger(__name__)


def run_sql_query(
    sql: str,
    params: dict | None = None,
    *,
    row_limit: int | None = None,
) -> pd.DataFrame:
    """Run a controlled, read-only SQL query and return a pandas DataFrame.

    Parameters
    ----------
    sql:
        A single read-only ``SELECT``/``WITH`` statement. Use ``:name`` style
        placeholders and pass values via *params* — never format values in.
    params:
        Mapping of bind-parameter name to value.
    row_limit:
        Maximum rows to return. Defaults to ``QUERY_ROW_LIMIT`` from settings;
        a safety net even if the query forgets a ``TOP``.
    """
    ensure_read_only(sql)
    limit = row_limit or get_settings().query_row_limit

    # Lazy imports so importing the tool doesn't require the Fabric/ODBC stack.
    from sqlalchemy import text

    from shared.fabric import get_fabric_engine

    engine = get_fabric_engine()
    logger.debug("Executing SQL (limit=%d) params=%s", limit, params)
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn, params=params or {})

    if len(df) > limit:
        logger.warning("Query returned %d rows; truncating to %d.", len(df), limit)
        df = df.head(limit)
    return df
