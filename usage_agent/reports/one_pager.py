"""Render the agent's structured JSON briefing into a branded Word (.docx) doc.

Every one-pager has the SAME fixed layout — an EisnerAmper black/gold masthead, a
row of KPI tiles, three week-over-week (WoW) tables (provider, model, top-10
users), key findings, recommended actions, and a generated-at timestamp. Only the
data changes.

The agent returns a JSON object (see ``prompts/one_pager``); this module parses
it, computes every WoW delta itself (so the arithmetic and formatting are
identical run to run), and writes it into a fixed python-docx layout.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from ..logging_config import get_logger

logger = get_logger(__name__)

# --- EisnerAmper brand (hex without '#') -----------------------------------
_INK = "000000"          # primary: black
_GOLD = "BD9B60"         # secondary: gold
_GOLD_TINT = "F7F2E8"    # faint gold wash for tiles / total rows
_ZEBRA = "FAF7F0"        # alternate table-row wash
_ROW_LINE = "E3DDCF"     # hairline between table rows
_WHITE = "FFFFFF"
_MUTED = "6B6B6B"
_FAINT = "9A9A9A"

# WoW delta status colors — direction is also carried by the +/- sign, so meaning
# never rests on color alone.
_POS = "0F7A34"  # movement in the "good" direction
_NEG = "B23B3B"  # movement in the "attention" direction
_FLAT = "6B6B6B"

_RIGHT = WD_ALIGN_PARAGRAPH.RIGHT
_LEFT = WD_ALIGN_PARAGRAPH.LEFT
_MAX_MODELS = 6
_MAX_USERS = 10


# --- formatting helpers ----------------------------------------------------
def _money(n: float) -> str:
    return f"${round(float(n)):,.0f}"


def _count(n: float) -> str:
    return f"{round(float(n)):,.0f}"


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _delta(last: float, prior: float, *, higher_is_better: bool, money: bool) -> tuple[str, str]:
    """Return ``(text, hex_color)`` for a WoW delta, e.g. ``('+$1,000 (+12%)', '0F7A34')``."""
    d = last - prior
    if d > 0:
        color, sign = (_POS if higher_is_better else _NEG), "+"
    elif d < 0:
        color, sign = (_NEG if higher_is_better else _POS), "-"
    else:
        color, sign = _FLAT, ""
    mag = _money(abs(d)) if money else _count(abs(d))
    pct = f"{(d / prior) * 100:+.0f}%" if prior else ("new" if d else "0%")
    return f"{sign}{mag} ({pct})", color


# --- low-level docx / OOXML helpers ----------------------------------------
def _shade(cell, fill: str) -> None:
    """Set a table cell's background fill color."""
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    cell._tc.get_or_add_tcPr().append(shd)


def _table_borders(table, edges: dict[str, tuple[str, str, str]]) -> None:
    """Set a table's borders. *edges* maps edge name -> (style, size, color);
    any of top/left/bottom/right/insideH/insideV not present is set to none."""
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        if edge in edges:
            style, size, color = edges[edge]
            el.set(qn("w:val"), style)
            el.set(qn("w:sz"), size)
            el.set(qn("w:color"), color)
        else:
            el.set(qn("w:val"), "none")
        el.set(qn("w:space"), "0")
        borders.append(el)
    table._tbl.tblPr.append(borders)


def _cell_top_border(cell, color: str, size: str) -> None:
    borders = OxmlElement("w:tcBorders")
    top = OxmlElement("w:top")
    top.set(qn("w:val"), "single")
    top.set(qn("w:sz"), size)
    top.set(qn("w:space"), "0")
    top.set(qn("w:color"), color)
    borders.append(top)
    cell._tc.get_or_add_tcPr().append(borders)


def _para_bottom_border(paragraph, color: str, size: str) -> None:
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), size)
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), color)
    pbdr.append(bottom)
    paragraph._p.get_or_add_pPr().append(pbdr)


def _run(paragraph, text: str, *, bold: bool = False, size: float | None = None, color: str | None = None):
    run = paragraph.add_run(text)
    run.bold = bold
    if size is not None:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = RGBColor.from_string(color)
    return run


def _tight(paragraph):
    """Remove paragraph spacing (used inside tiles/cells to keep them compact)."""
    pf = paragraph.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing = 1.0
    return paragraph


def _fill_cell(cell, text: str, *, bold: bool = False, size: float = 9, color: str | None = None, align=None) -> None:
    cell.text = ""
    p = cell.paragraphs[0]
    _tight(p)
    if align is not None:
        p.alignment = align
    _run(p, text, bold=bold, size=size, color=color)


# --- report parsing --------------------------------------------------------
def parse_report(text: str) -> dict:
    """Parse the agent's answer into the report dict.

    The model is asked for bare JSON, but tolerate a stray code fence or a line
    of preamble by extracting the outermost ``{...}`` block. Raises
    ``ValueError`` if no JSON object can be recovered.
    """
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"Could not parse report JSON: {exc}") from exc
    raise ValueError("Agent did not return a JSON report object.")


# --- section builders ------------------------------------------------------
def _add_masthead(doc, stamp: str) -> None:
    table = doc.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    left, right = table.rows[0].cells
    _shade(left, _INK)
    _shade(right, _INK)

    lp = left.paragraphs[0]
    _tight(lp)
    _run(lp, "EISNER", bold=True, size=15, color=_WHITE)
    _run(lp, "AMPER", bold=True, size=15, color=_GOLD)

    rp = right.paragraphs[0]
    _tight(rp)
    rp.alignment = _RIGHT
    _run(rp, f"Generated {stamp}", size=8.5, color=_GOLD)

    _table_borders(table, {"bottom": ("single", "18", _GOLD)})


def _add_title(doc, title: str, period: str, prior: str) -> None:
    tp = doc.add_paragraph()
    tp.paragraph_format.space_before = Pt(4)
    tp.paragraph_format.space_after = Pt(1)
    _run(tp, title, bold=True, size=16, color=_INK)

    pp = doc.add_paragraph()
    _tight(pp)
    pp.paragraph_format.space_after = Pt(3)
    label = period
    if prior:
        label += f"  ·  vs. prior week ({prior})"
    _run(pp, label, size=9, color=_MUTED)


def _add_heading(doc, text: str):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(1)
    _run(p, text, bold=True, size=10.5, color=_INK)
    _para_bottom_border(p, _GOLD, "12")
    return p


def _add_tiles(doc, kpis: dict) -> None:
    specs = [
        ("Total spend", "total_spend", True, False),
        ("Anthropic", "anthropic_spend", True, False),
        ("OpenAI", "openai_spend", True, False),
        ("Weekly active users", "active_users", False, True),
    ]
    table = doc.add_table(rows=1, cols=4)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    cells = table.rows[0].cells
    for cell, (label, key, money, higher_is_better) in zip(cells, specs):
        item = kpis.get(key) or {}
        last, prior = _num(item.get("last")), _num(item.get("prior"))
        _shade(cell, _GOLD_TINT)

        lp = cell.paragraphs[0]
        _tight(lp)
        _run(lp, label.upper(), size=7, color=_MUTED)

        vp = _tight(cell.add_paragraph())
        _run(vp, _money(last) if money else _count(last), bold=True, size=13, color=_INK)

        dp = _tight(cell.add_paragraph())
        dtext, dcolor = _delta(last, prior, higher_is_better=higher_is_better, money=money)
        _run(dp, dtext + " ", size=7.5, color=dcolor)
        _run(dp, "WoW", size=7.5, color=_FAINT)

    _table_borders(
        table,
        {edge: ("single", "6", _GOLD) for edge in ("top", "left", "bottom", "right", "insideV")},
    )


def _add_spend_table(
    doc,
    title: str,
    rows: list[dict],
    *,
    label_header: str,
    extra_col: str | None = None,
    extra_key: str = "department",
    total_label: str | None = None,
) -> None:
    _add_heading(doc, title)
    headers = [label_header]
    if extra_col:
        headers.append(extra_col)
    headers += ["Last week", "Prior week", "WoW change"]

    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, (cell, head) in enumerate(zip(table.rows[0].cells, headers)):
        _shade(cell, _INK)
        align = _RIGHT if head in ("Last week", "Prior week") else _LEFT
        _fill_cell(cell, head, bold=True, size=8.5, color=_WHITE, align=align)

    total_last = total_prior = 0.0
    for idx, row in enumerate(rows):
        last, prior = _num(row.get("last")), _num(row.get("prior"))
        total_last += last
        total_prior += prior
        cells = table.add_row().cells
        col = 0
        _fill_cell(cells[col], str(row.get("name") or ""), size=9)
        col += 1
        if extra_col:
            _fill_cell(cells[col], str(row.get(extra_key) or "—"), size=9)
            col += 1
        _fill_cell(cells[col], _money(last), size=9, align=_RIGHT)
        col += 1
        _fill_cell(cells[col], _money(prior), size=9, align=_RIGHT)
        col += 1
        dtext, dcolor = _delta(last, prior, higher_is_better=False, money=True)
        _fill_cell(cells[col], dtext, size=9, color=dcolor)
        if idx % 2 == 1:
            for c in cells:
                _shade(c, _ZEBRA)

    if not rows:
        cells = table.add_row().cells
        _fill_cell(cells[0], "No data for this period.", size=9, color=_FAINT)
    elif total_label:
        cells = table.add_row().cells
        col = 0
        _fill_cell(cells[col], total_label, bold=True, size=9)
        col += 1
        if extra_col:
            _fill_cell(cells[col], "", size=9)
            col += 1
        _fill_cell(cells[col], _money(total_last), bold=True, size=9, align=_RIGHT)
        col += 1
        _fill_cell(cells[col], _money(total_prior), bold=True, size=9, align=_RIGHT)
        col += 1
        dtext, dcolor = _delta(total_last, total_prior, higher_is_better=False, money=True)
        _fill_cell(cells[col], dtext, bold=True, size=9, color=dcolor)
        for c in cells:
            _shade(c, _GOLD_TINT)
            _cell_top_border(c, _GOLD, "12")

    _table_borders(table, {"insideH": ("single", "4", _ROW_LINE)})


def _add_top_spenders_table(doc, title: str, rows: list[dict], *, total_label: str | None = None) -> None:
    """Top spenders of the week: spend + each user's most-used product (no WoW)."""
    _add_heading(doc, title)
    headers = ["User", "Department", "Spend", "Most used product"]
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, head in zip(table.rows[0].cells, headers):
        _shade(cell, _INK)
        align = _RIGHT if head == "Spend" else _LEFT
        _fill_cell(cell, head, bold=True, size=8.5, color=_WHITE, align=align)

    total = 0.0
    for idx, row in enumerate(rows):
        spend = _num(row.get("spend", row.get("last")))   # accept "spend" (new) or "last"
        total += spend
        cells = table.add_row().cells
        _fill_cell(cells[0], str(row.get("name") or ""), size=9)
        _fill_cell(cells[1], str(row.get("department") or "—"), size=9)
        _fill_cell(cells[2], _money(spend), size=9, align=_RIGHT)
        _fill_cell(cells[3], str(row.get("top_product") or "—"), size=9)
        if idx % 2 == 1:
            for c in cells:
                _shade(c, _ZEBRA)

    if not rows:
        cells = table.add_row().cells
        _fill_cell(cells[0], "No data for this period.", size=9, color=_FAINT)
    elif total_label:
        cells = table.add_row().cells
        _fill_cell(cells[0], total_label, bold=True, size=9)
        _fill_cell(cells[1], "", size=9)
        _fill_cell(cells[2], _money(total), bold=True, size=9, align=_RIGHT)
        _fill_cell(cells[3], "", size=9)
        for c in cells:
            _shade(c, _GOLD_TINT)
            _cell_top_border(c, _GOLD, "12")

    _table_borders(table, {"insideH": ("single", "4", _ROW_LINE)})


def _bullets_in_cell(cell, title: str, items: list, *, numbered: bool) -> None:
    hp = cell.paragraphs[0]
    _tight(hp)
    hp.paragraph_format.space_after = Pt(2)
    _run(hp, title, bold=True, size=10.5, color=_INK)
    _para_bottom_border(hp, _GOLD, "12")
    style = "List Number" if numbered else "List Bullet"
    for item in items or ["—"]:
        p = cell.add_paragraph(str(item), style=style)
        _tight(p)


def _add_findings_actions(doc, findings: list, actions: list) -> None:
    """Key findings and recommended actions side by side (saves vertical space).

    Capped at 4 items each so an over-long list can't push the report onto a
    second page — the prompt asks for 3-4.
    """
    table = doc.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    left, right = table.rows[0].cells
    _bullets_in_cell(left, "Key findings", (findings or [])[:4], numbered=False)
    _bullets_in_cell(right, "Recommended actions", (actions or [])[:4], numbered=True)


def _build_document(report: dict, generated_at: datetime) -> "Document":
    stamp = generated_at.strftime("%b %d, %Y %H:%M")
    doc = Document()

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)
    # Kill Word's default ~8pt "space after" on every paragraph — the main cause of
    # the layout drifting onto a second page. Sections add their own spacing back.
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1.0
    for section in doc.sections:
        section.top_margin = Inches(0.4)
        section.bottom_margin = Inches(0.4)
        section.left_margin = Inches(0.55)
        section.right_margin = Inches(0.55)
        section.footer_distance = Inches(0.25)

    _add_masthead(doc, stamp)
    _add_title(
        doc,
        report.get("title") or "AI Usage & Cost Weekly Briefing",
        report.get("period_label") or "",
        report.get("prior_period_label") or "",
    )
    _add_tiles(doc, report.get("kpis") or {})
    _add_spend_table(
        doc,
        "Spend by provider — week over week",
        report.get("providers") or [],
        label_header="Provider",
        total_label="Total",
    )
    _add_spend_table(
        doc,
        "Spend by model — week over week",
        (report.get("models") or [])[:_MAX_MODELS],
        label_header="Model",
        total_label="Top models total",
    )
    _add_top_spenders_table(
        doc,
        "Top spenders this week",
        (report.get("top_users") or [])[:_MAX_USERS],
        total_label="Total",
    )
    _add_findings_actions(
        doc,
        report.get("key_findings") or [],
        report.get("recommended_actions") or [],
    )

    footer = doc.sections[0].footer
    fp = footer.paragraphs[0]
    _run(
        fp,
        "Generated by the Usage Agent from the Power BI semantic model  ·  "
        f"figures reconcile with the dashboard's measures  ·  generated {stamp}.",
        size=7.5,
        color=_FAINT,
    )
    return doc


def render_one_pager_docx(
    report: dict,
    output_path: str | Path,
    *,
    generated_at: datetime | None = None,
) -> Path:
    """Render a structured *report* dict to a branded ``.docx`` at *output_path*.

    Returns the resolved output path.
    """
    generated_at = generated_at or datetime.now()
    doc = _build_document(report, generated_at)

    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))

    logger.info("Wrote one-pager: %s", output_path)
    return output_path


def default_output_path(generated_on: date | None = None) -> Path:
    """Default filename for a one-pager, e.g. ``ai_usage_one_pager_2026-07-09.docx``."""
    generated_on = generated_on or date.today()
    return Path(f"ai_usage_one_pager_{generated_on.isoformat()}.docx")
