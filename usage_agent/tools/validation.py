"""SQL and DAX safety nets for the read-only data tools.

The queries in ``sql_tools`` are already FIXED and built only from an allowlist of
``openai_anthropic_*`` tables — the agent never supplies SQL text. This module is
the **defense-in-depth** layer: every statement is re-checked at execution time
(see ``sql_tools._read``) to guarantee it is a single, read-only ``SELECT``/``WITH``
with no writes, DDL, stored procedures, batches, or external-data-source escape
functions. It is deliberately conservative — if a future change ever let untrusted
text reach the engine, this catch still holds.

Notes:
- The primary control is still least privilege: connect to Fabric with a principal
  that only has SELECT on the ``openai_anthropic_*`` tables. This validator is the
  belt to that principal's braces.
- Values must always be passed as **bound parameters**, never string-formatted in.
- The denylist blocks the file/remote-source functions (``OPENROWSET`` etc.) that a
  bare "must start with SELECT" check would otherwise let through — those are real
  SSRF / file-read vectors even inside a SELECT.
"""

from __future__ import annotations

import re

# A read-only statement must start with one of these (after comments are stripped).
_READ_ONLY_PREFIXES = ("select", "with")

# Whole-word tokens that indicate a write, DDL, procedural, batching, or
# external-data-source statement. Any match is rejected.
_FORBIDDEN = re.compile(
    r"\b("
    # data changes / DDL
    r"insert|update|delete|drop|alter|truncate|merge|create|replace|"
    r"grant|revoke|deny|"
    # stored procedures / dynamic execution
    r"exec|execute|sp_\w+|xp_\w+|"
    # write-via-select, delays, server control
    r"into|waitfor|shutdown|reconfigure|dbcc|"
    # external data source / file access (SSRF & local-file read vectors)
    r"openrowset|openquery|opendatasource|openxml|openjson|bulk"
    r")\b",
    re.IGNORECASE,
)

# Valid bare SQL identifier (table, schema, column).
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# SQL comment forms — stripped before scanning so a forbidden token can't hide in
# a comment (and so a leading comment can't disguise the statement's first keyword).
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")


def _strip_comments(sql: str) -> str:
    """Remove ``/* block */`` and ``-- line`` comments, replacing each with a space."""
    return _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", sql))


def is_read_only_sql(sql: str) -> bool:
    """Return True if *sql* is a single, read-only SELECT/WITH statement.

    Rejects: empty input, batches/stacked statements (a ``;`` after the optional
    trailing one), anything not starting with SELECT/WITH, and any statement
    containing a forbidden token (writes, DDL, stored procs, ``OPENROWSET`` &
    friends, ``WAITFOR``, etc.). Comments are stripped first so tokens can't hide.
    """
    statement = _strip_comments(sql or "").strip().rstrip(";").strip()
    if not statement:
        return False
    # Reject batches / stacked statements (a single trailing ``;`` was removed).
    if ";" in statement:
        return False
    head = statement.lower().lstrip("(").lstrip()
    if not head.startswith(_READ_ONLY_PREFIXES):
        return False
    return _FORBIDDEN.search(statement) is None


def ensure_read_only(sql: str) -> None:
    """Raise ``ValueError`` unless *sql* is a single read-only statement.

    Called by ``sql_tools._read`` before every query as the execution-time guard.
    """
    if not is_read_only_sql(sql):
        raise ValueError(
            "Rejected SQL: only a single read-only SELECT/WITH statement is "
            "permitted (no writes, DDL, stored procedures, batches, or external "
            "data-source functions such as OPENROWSET)."
        )


# --- DAX ------------------------------------------------------------------
# DAX has no DML, so the risk here is not a write — it is a MALFORMED or injected
# query. ``model_query`` composes every statement from metadata-validated
# identifiers, so this is the belt to that braces: it confirms what actually reaches
# the engine is a single read-only EVALUATE and nothing has been appended to it.
_DAX_FORBIDDEN = re.compile(
    r"\b("
    # table/row mutation functions
    r"ALTER|CREATE|DROP|INSERT|UPDATE|DELETE|MERGE|"
    # dataset/model management + external evaluation
    r"REFRESH|BACKUP|RESTORE|ATTACH|DETACH|EXECUTE|CALCULATIONGROUP"
    r")\b",
    re.IGNORECASE,
)


def is_read_only_dax(dax: str) -> bool:
    """True if *dax* is a single read-only DAX query.

    Requires exactly one ``EVALUATE`` (optionally preceded by ``DEFINE``), rejects a
    trailing statement appended after the query, and rejects the mutation/management
    keywords above.
    """
    text = (dax or "").strip()
    if not text:
        return False
    head = text.lstrip("(").lstrip().upper()
    if not head.startswith(("DEFINE", "EVALUATE")):
        return False
    # Exactly one result-returning statement.
    if len(re.findall(r"\bEVALUATE\b", text, re.IGNORECASE)) != 1:
        return False
    return _DAX_FORBIDDEN.search(text) is None


def ensure_read_only_dax(dax: str) -> None:
    """Raise ``ValueError`` unless *dax* is a single read-only DAX query."""
    if not is_read_only_dax(dax):
        raise ValueError(
            "Rejected DAX: only a single read-only EVALUATE (optionally preceded by "
            "DEFINE) is permitted, with no model-management or mutation functions."
        )


def validate_identifier(name: str) -> str:
    """Validate a (optionally schema-qualified) SQL identifier and return it.

    Used to vet any identifier before it is interpolated into SQL (table/schema
    names can't be passed as bind parameters).

    >>> validate_identifier("dbo.openai_anthropic_usage")
    'dbo.openai_anthropic_usage'
    """
    parts = name.split(".")
    if not (1 <= len(parts) <= 2) or not all(_IDENTIFIER.match(p) for p in parts):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name
