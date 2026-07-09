"""Conversational Claude agent over AI usage & cost data in Microsoft Fabric.

Answers natural-language questions about AI usage/cost by querying the Fabric
Power BI **semantic model** (DAX, so numbers reconcile with the dashboard) with a
raw Fabric **SQL** path for ad-hoc drill-down. The passwordless Azure auth and
Fabric connection live in the sibling ``shared`` package (both under ``src/``),
so no ``sys.path`` juggling is needed — either is importable when installed or
when ``src`` is on the path.
"""
