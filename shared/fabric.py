"""Passwordless Microsoft Fabric SQL connection (shared by pipeline + agent).

Ported from sla_teams. An Azure AD access token from the shared credential is
handed to the ODBC driver — no SQL username/password is ever used or stored. A
fresh token is minted for each physical connection (via SQLAlchemy's ``creator``
hook), so long-lived engines keep working as tokens rotate.

Connection settings come from the environment (no secrets in code):

    FABRIC_SQL_SERVER     (required)
    FABRIC_SQL_DATABASE   (required)
    FABRIC_SQL_PORT       (default 1433)
    ODBC_DRIVER           (default 'ODBC Driver 18 for SQL Server')
"""

from __future__ import annotations

import logging
import os
import struct
import urllib.parse
from functools import lru_cache

log = logging.getLogger(__name__)

# ODBC connection attribute id for an Azure AD access token.
_SQL_COPT_SS_ACCESS_TOKEN = 1256
# Token audience for Azure SQL / Fabric SQL (TDS) endpoints.
_TOKEN_SCOPE = "https://database.windows.net/.default"


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Set it in your .env (see AGENT_PLAN.md -> Configuration)."
        )
    return value


def _build_odbc_str() -> str:
    """Compose the ODBC connection string from the environment (no secrets)."""
    server = _require("FABRIC_SQL_SERVER")
    database = _require("FABRIC_SQL_DATABASE")
    port = os.getenv("FABRIC_SQL_PORT", "1433")
    driver = os.getenv("ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
    return (
        f"Driver={{{driver}}};"
        f"Server={server},{port};"
        f"Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )


def _access_token_struct() -> bytes:
    """Acquire an Azure AD token and pack it into the ODBC struct format."""
    # Lazy import keeps azure libs off the import path for offline tests.
    from .azure_auth import get_credential

    token = get_credential().get_token(_TOKEN_SCOPE).token
    token_bytes = token.encode("UTF-16-LE")
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


def _create_raw_connection():
    """pyodbc connection factory used by SQLAlchemy's ``creator``."""
    import pyodbc

    return pyodbc.connect(
        _build_odbc_str(),
        attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: _access_token_struct()},
    )


@lru_cache(maxsize=1)
def get_fabric_engine():
    """Securely create (and cache) a SQLAlchemy engine for Fabric SQL.

    Returns a connection pool (Engine) reusable across the app; each pooled
    connection authenticates with a freshly minted Azure AD token.
    """
    from sqlalchemy import create_engine

    odbc_str = _build_odbc_str()
    engine = create_engine(
        f"mssql+pyodbc:///?odbc_connect={urllib.parse.quote_plus(odbc_str)}",
        creator=_create_raw_connection,
        pool_pre_ping=True,  # transparently recover from dropped connections
    )
    log.info("Initialised Fabric SQL engine.")
    return engine
