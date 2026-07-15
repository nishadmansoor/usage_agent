# `usage_agent/reports/` — branded one-pager renderer

## `one_pager.py`

Turns the agent's structured JSON briefing into a branded EisnerAmper Word (`.docx`)
one-pager. Every report has the **same fixed layout** — only the data changes — so
the arithmetic and formatting are identical run to run.

- **`parse_report(text) -> dict`** — parses the agent's answer as JSON (tolerates a
  stray code fence / preamble by extracting the outermost `{…}`). Raises if no JSON
  object can be recovered.
- **`render_one_pager_docx(report, path)`** — renders the dict to `.docx` at `path`.
- **`default_output_path()`** — `ai_usage_one_pager_<date>.docx`.

### Layout (fixed)

Masthead (black/gold `EISNER`/`AMPER` + generated-at) → title/period → KPI tiles
(total / Anthropic / OpenAI spend + weekly active users) → three tables (spend by
provider WoW, spend by model WoW, top-10 spenders with most-used product) → key
findings + recommended actions side by side → footer.

### Notes

- Brand colours: ink `#000000`, gold `#BD9B60`, plus tints for tiles/zebra rows.
- **The renderer computes every WoW delta itself** (`_delta`) from the JSON's `last`
  / `prior` values — the agent supplies raw numbers, not formatted deltas, so the
  formatting can't drift.
- Delta direction is carried by the `+`/`-` sign as well as colour, so meaning never
  rests on colour alone.
- Findings/actions are capped at 4 each so the report can't spill onto a second page.
- Low-level OOXML helpers (`_shade`, `_table_borders`, `_cell_top_border`, …) exist
  because python-docx doesn't expose cell shading / fine borders directly.
