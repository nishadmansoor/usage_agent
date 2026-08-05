"""Prompt for the one-pager: a branded single-page executive briefing (Word .docx).

Every one-pager has the SAME fixed layout — only the data changes. To guarantee
that, the agent does not write free-form prose: it returns a single JSON object
with a fixed shape, and the renderer lays it out identically every time and does
all the week-over-week (WoW) arithmetic itself.

The default (no user prompt) is the Monday weekly briefing: the previous complete
week vs. the week before it. If the user supplies their own question with
``--one-pager``, we scope the same JSON to that question instead.
"""

from __future__ import annotations

# The single source of truth for a one-pager's structure. Both the default and
# the custom path require the model to fill exactly this shape.
_REPORT_SCHEMA = """\
Return your FINAL answer as a SINGLE JSON object and NOTHING else — no prose, no
explanation, no Markdown, no code fences. It must parse with json.loads. Use this
EXACT shape (all spend values are plain USD numbers — no "$", no commas, no
strings; counts are integers):

{
  "title": "AI Usage & Cost Weekly Briefing",
  "period_label": "<human label for LAST WEEK, e.g. 'Jun 29 - Jul 5, 2026'>",
  "prior_period_label": "<human label for the PRIOR week, e.g. 'Jun 22 - Jun 28, 2026'>",
  "kpis": {
    "total_spend":     {"last": <num>, "prior": <num>},
    "anthropic_spend": {"last": <num>, "prior": <num>},
    "openai_spend":    {"last": <num>, "prior": <num>},
    "active_users":    {"last": <int>, "prior": <int>}
  },
  "providers": [
    {"name": "Anthropic (Claude)", "last": <num>, "prior": <num>},
    {"name": "OpenAI (Codex)",     "last": <num>, "prior": <num>}
  ],
  "models":    [{"name": "<model name>", "last": <num>, "prior": <num>}],
  "top_users": [{"name": "<email or name>", "department": "<dept or ''>", "spend": <num>, "top_product": "<product used most>"}],
  "key_findings": ["<one short sentence>"],
  "recommended_actions": ["<one short, concrete action>"]
}

Rules for the data:
- For kpis, providers, and models: give BOTH "last" (LAST WEEK) and "prior" (the
  PRIOR week), scoped the SAME way so they are comparable. Do NOT compute the WoW
  change or add percent/arrow fields — the report computes and formats all deltas.
- "models": the top spenders by LAST-WEEK spend, sorted descending, AT MOST 6.
- "top_users": the 10 highest-spend users LAST WEEK, sorted descending (fewer only
  if fewer exist). This table is NOT week-over-week — give only "spend" (that user's
  LAST-WEEK spend, no prior) and "top_product" (the single product they used most
  that week, e.g. claude_code, chat, codex, or chatgpt). Include "department" when
  the model exposes it, else "".
- "providers" must sum to kpis.total_spend for each week (within rounding).
- "key_findings": 3-4 items; "recommended_actions": 3-4 items. One line each.
- Every number comes from tool output — never invent users, models, or figures.
  If a figure is genuinely unavailable, use 0 rather than guessing."""

# Default: the Monday briefing. Previous complete week vs. the week before it.
DEFAULT_ONE_PAGER_PROMPT = (
    "Produce the weekly AI usage & cost executive briefing for the PREVIOUS "
    "COMPLETE WEEK, compared to the week before it (the WoW trend). This runs on a "
    "Monday, once the previous week's data is complete.\n\n"
    "Time scoping — the tools do this for you, so use them and do not calculate:\n"
    "- Call weekly_spend_summary for the headline: it returns the last COMPLETE week "
    "and the prior week side by side (spend, WoW $/%, active users).\n"
    "- LAST WEEK = period='last_week'; the PRIOR WEEK = period='prior_week'. Both are "
    "resolved from the model's own is_complete_week, so they are comparable.\n"
    "- For every slice, call the tool once per period so both columns match:\n"
    "  spend_breakdown('provider'), spend_breakdown('model'), top_users().\n"
    "- top_users already returns each user's department and most-used product.\n"
    "- Take every figure verbatim from tool output; the renderer computes all deltas.\n\n"
    "Populate the JSON below: KPIs (total spend, Anthropic spend, OpenAI spend, "
    "weekly active users), the provider split, and spend by model (top 6) — each "
    "with its prior-week figure for the WoW trend — plus the top 10 spenders of the "
    "week with each one's most-used product (this last table is NOT week-over-week: "
    "just last-week spend and top product).\n\n"
    + _REPORT_SCHEMA
)

# Custom question: keep the user's scope but still return the fixed JSON shape.
_BRIEFING_SUFFIX = (
    "\n\nAnswer the question above as a one-pager for the period it implies. Treat "
    "'last week' as the most recent COMPLETE week and 'the prior week' as the seven "
    "days before it (or the analogous prior period for a month/quarter question), "
    "scoping both the same way so they are comparable.\n\n" + _REPORT_SCHEMA
)


def build_one_pager_prompt(user_prompt: str | None) -> str:
    """Return the effective agent prompt for a one-pager run.

    With no user prompt, use the default weekly briefing. Otherwise keep the
    user's question but force the fixed JSON report shape the renderer expects.
    """
    user_prompt = (user_prompt or "").strip()
    if not user_prompt:
        return DEFAULT_ONE_PAGER_PROMPT
    return user_prompt + _BRIEFING_SUFFIX
