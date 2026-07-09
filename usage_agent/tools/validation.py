"""SQL safety / input-validation helpers (for the Fabric SQL drill-down tool).

Enforces the hard rule: the agent may only run **single, read-only** queries, and
any identifier interpolated into SQL must be whitelisted. Values are always passed
as bound parameters, never string-formatted into the query.

Defence-in-depth: the primary control is connecting to Fabric with a principal
that only has SELECT permission. Ported verbatim from sla_teams.
"""

from __future__ import annotations

import re

# A read-only statement must start with one of these.
_READ_ONLY_PREFIXES = ("select", "with")

# Any of these tokens (as whole words) indicate a write / DDL / procedural
# statement and are therefore rejected.
_FORBIDDEN = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|truncate|merge|create|"
    r"grant|revoke|exec|execute|into|"
    r"sp_\w+|xp_\w+"
    r")\b",
    re.IGNORECASE,
)

# Valid bare SQL identifier (table, schema, column).
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_read_only_sql(sql: str) -> bool:
    """Return True if *sql* is a single, read-only SELECT/WITH statement."""
    statement = sql.strip().rstrip(";").strip()
    if not statement:
        return False
    # Reject batches / stacked statements (a trailing ``;`` was already removed).
    if ";" in statement:
        return False
    head = statement.lower().lstrip("(").lstrip()
    if not head.startswith(_READ_ONLY_PREFIXES):
        return False
    return _FORBIDDEN.search(statement) is None


def ensure_read_only(sql: str) -> None:
    """Raise ``ValueError`` unless *sql* is a single read-only statement."""
    if not is_read_only_sql(sql):
        raise ValueError(
            "Rejected SQL: only a single read-only SELECT/WITH statement is "
            "permitted (no writes, DDL, stored procedures, or batches)."
        )


def validate_identifier(name: str) -> str:
    """Validate a (optionally schema-qualified) SQL identifier and return it.

    >>> validate_identifier("dbo.usage")
    'dbo.usage'
    """
    parts = name.split(".")
    if not (1 <= len(parts) <= 2) or not all(_IDENTIFIER.match(p) for p in parts):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name
