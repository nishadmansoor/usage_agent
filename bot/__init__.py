"""Interactive Microsoft Teams bot that wraps the usage agent.

A thin Bot Framework (aiohttp) web service: it receives a user's message, runs
``UsageAgent`` on it, and replies with the answer as regular chat text. The
outbound "push" path (scheduled/answer cards) lives in ``usage_agent.teams``;
this package is the inbound, conversational surface.
"""
