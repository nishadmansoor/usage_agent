"""Backend clients for the usage agent (currently: Power BI semantic model)."""

from __future__ import annotations

from .powerbi import PowerBIClient, get_powerbi_client

__all__ = ["PowerBIClient", "get_powerbi_client"]
