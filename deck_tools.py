"""Small-model-facing tools layered on top of the vendored upstream server.

Three tools only:
  list_templates()          -> catalog of template files + their two layout indices
  render_svg(svg)           -> SVG markup to PNG (icons/diagrams to insert)
  build_deck(...)           -> one call: JSON outline in, .pptx out (bullets, charts,
                               tables, images)

Everything is deterministic: the model emits an outline, never layout code.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Inches, Pt

from deck_style import CHART_PALETTE, GRAY, INK, LIGHT, NAVY, decorate, page_number, title_rule

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

CHART_TYPES = {
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "column_stacked": XL_CHART_TYPE.COLUMN_STACKED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "pie": XL_CHART_TYPE.PIE,
    "doughnut": XL_CHART_TYPE.DOUGHNUT,
}

FONT = "Calibri"


# ---------------------------------------------------------------- filesystem helpers


def _template_dirs(template_dirs_fn) -> List[str]:
    dirs = template_dirs_fn()
    seen, out = set(), []
    for d in dirs:
        p = str(Path(d).expanduser().resolve())
        if p not in seen and Path(p).is_dir():
            seen.add(p)
            out.append(p)
    return out


def _output_dir() -> Path:
    out = Path(os.environ.get("PPT_OUTPUT_DIR", Path(__file__).resolve().parent / "out"))
    out.mkdir(parents=True, exist_ok=True)
    return out


def _slug(text: str, fallback: str = "deck") -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s or fallback)[:60]


# ---------------------------------------------------------------- template helpers


def _layout_summary(prs: Presentation) -> List[Dict[str, Any]]:
    rows = []
    for i, lay in enumerate(prs.slide_layouts):
        names = []
        for ph in lay.placeholders:
            try:
                names.append(f"idx{ph.placeholder_format.idx}:{ph.placeholder_format.type}")
            except Exception:
                continue
        rows.append({"index": i, "name": lay.name, "placeholders": names})
    return rows


def _pick_layouts(prs: Presentation):
    """Title slide = layout named 'Title Slide', else the first title-only-ish layout.
    Content = layout named 'Title and Content', else the first sane content layout."""
    names = [(i, lay.name.strip().lower()) for i, lay in enumerate(prs.slide_layouts)]

    title_idx = next((i for i, n in names if n == "title slide"), None)
    if title_idx is None:
        title_idx = next((i for i, n in names if "title" in n and "content" not in n), 0)

    content_idx = next((i for i, n in names if n == "title and content"), None)
    if content_idx is None:
        content_idx = next(
            (
                i
                for i, n in names
                if "content" in n and "two content" not in n and "caption" not in n
            ),
            1,
        )
    return title_idx, content_idx


def _resolve_template(template: str, dirs: List[str]) -> Path:
    p = Path(template).expanduser()
    if p.is_absolute() and p.is_file():
        return p
    for d in dirs:
        cand = Path(d) / template
        if cand.is_file():
            return cand
    for d in dirs:
        for cand in Path(d).glob("*.pptx"):
            if cand.stem.lower() == Path(template).stem.lower():
                return cand
    raise FileNotFoundError(
        f"template '{template}' not found in: {', '.join(dirs) or '(no template dirs)'}"
    )


def _delete_all_slides(prs: Presentation) -> None:
    sldIdLst = prs.slides._sldIdLst
    for sldId in list(sldIdLst):
        prs.part.drop_rel(sldId.rId)
        sldIdLst.remove(sldId)


# ---------------------------------------------------------------- text styling


def _style_text_frame(shape, size: int, bold: bool, color) -> None:
    tf = shape.text_frame
    tf.word_wrap = True
    for p in tf.paragraphs:
        p.alignment = PP_ALIGN.LEFT
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.color.rgb = color
            r.font.name = FONT


def _fill_title_slide(slide, title: str, subtitle: str) -> None:
    for ph in slide.placeholders:
        idx = ph.placeholder_format.idx
        if idx == 0:
            ph.text = title
            _style_text_frame(ph, 40, True, NAVY)
        elif idx == 1:
            ph.text = subtitle
            _style_text_frame(ph, 20, False, GRAY)
        else:
            ph._element.getparent().remove(ph._element)


# ---------------------------------------------------------------- visuals


def _add_chart(slide, spec: Dict[str, Any], left, top, width, height) -> str:
    kind = str(spec.get("type", "column")).lower()
    chart_type = CHART_TYPES.get(kind, XL_CHART_TYPE.COLUMN_CLUSTERED)
    categories = [str(c) for c in (spec.get("categories") or [])]
    series_rows = spec.get("series") or []
    if not categories or not series_rows:
        return "skipped: chart needs categories and series"

    data = CategoryChartData()
    data.categories = categories
    for row in series_rows:
        name = str(row.get("name") or f"Series {len(data._series) + 1}")
        values = []
        for v in row.get("values") or []:
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                values.append(0.0)
        values = (values + [0.0] * len(categories))[: len(categories)]
        data.add_series(name, values)

    graphic = slide.shapes.add_chart(chart_type, left, top, width, height, data)
    chart = graphic.chart
    chart.font.size = Pt(12)
    chart.font.name = FONT
    chart.font.color.rgb = INK
    if spec.get("title"):
        chart.has_title = True
        chart.chart_title.text_frame.text = str(spec["title"])
        for r in chart.chart_title.text_frame.paragraphs[0].runs:
            r.font.size = Pt(14)
            r.font.bold = True
            r.font.color.rgb = NAVY
            r.font.name = FONT
    else:
        chart.has_title = False

    multi = len(series_rows) > 1
    chart.has_legend = multi
    if multi:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(11)

    plot = chart.plots[0]
    is_pie = kind in ("pie", "doughnut")
    plot.has_data_labels = bool(spec.get("show_values", True))
    if plot.has_data_labels:
        labels = plot.data_labels
        labels.font.size = Pt(11)
        labels.font.name = FONT
        labels.font.color.rgb = INK
        if is_pie:
            labels.show_value = False
            labels.show_percentage = True
            labels.show_category_name = False
        else:
            labels.show_value = True

    for i, series in enumerate(plot.series):
        color = CHART_PALETTE[i % len(CHART_PALETTE)]
        if kind == "line":
            series.format.line.color.rgb = color
            series.format.line.width = Pt(2.5)
        else:
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = color

    if is_pie or kind == "doughnut":
        for i, point in enumerate(plot.series[0].points):
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = CHART_PALETTE[i % len(CHART_PALETTE)]

    if not is_pie:
        try:
            va = chart.value_axis
            va.has_major_gridlines = True
            va.major_gridlines.format.line.color.rgb = LIGHT
            va.major_gridlines.format.line.width = Pt(0.75)
            va.tick_labels.font.size = Pt(11)
            va.format.line.fill.background()
        except Exception:
            pass
        try:
            chart.category_axis.tick_labels.font.size = Pt(11)
        except Exception:
            pass
    return f"{kind} chart"


def _add_table(slide, spec: Dict[str, Any], left, top, width) -> str:
    headers = [str(h) for h in (spec.get("headers") or [])]
    rows = [[str(c) for c in row] for row in (spec.get("rows") or [])]
    if not headers or not rows:
        return "skipped: table needs headers and rows"

    cols = len(headers)
    rows = [(row + [""] * cols)[:cols] for row in rows][:8]
    height = Inches(min(0.45 * (len(rows) + 1) + 0.15, 4.2))
    shape = slide.shapes.add_table(len(rows) + 1, cols, left, top, width, height)
    table = shape.table
    table.first_row = True
    for row in table.rows:
        row.height = Inches(0.36)

    for c, text in enumerate(headers):
        cell = table.cell(0, c)
        cell.text = text
        cell.fill.solid()
        cell.fill.fore_color.rgb = NAVY
        for p in cell.text_frame.paragraphs:
            for r in p.runs:
                r.font.size = Pt(12)
                r.font.bold = True
                r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                r.font.name = FONT

    for r_i, row in enumerate(rows, start=1):
        for c_i, text in enumerate(row):
            cell = table.cell(r_i, c_i)
            cell.text = text
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF) if r_i % 2 else RGBColor(0xF5, 0xF7, 0xF9)
            cell.margin_top = Inches(0.03)
            cell.margin_bottom = Inches(0.03)
            for p in cell.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(11)
                    r.font.color.rgb = INK
                    r.font.name = FONT
    return f"table {len(rows)}x{cols}"


def _add_picture(slide, path: Path, left, top, width) -> str:
    pic = slide.shapes.add_picture(str(path), left, top)
    # keep aspect ratio, fit inside width
    if pic.width > width:
        ratio = width / pic.width
        pic.width = width
        pic.height = int(pic.height * ratio)
    max_h = Inches(4.9)
    if pic.height > max_h:
        ratio = max_h / pic.height
        pic.height = max_h
        pic.width = int(pic.width * ratio)
    return f"image {path.name}"


# ---------------------------------------------------------------- content slide


def _fill_content_slide(
    slide,
    number: int,
    title: str,
    bullets: List[str],
    notes: str | None,
    visual: Dict[str, Any] | None,
) -> str | None:
    body = None
    for ph in slide.placeholders:
        idx = ph.placeholder_format.idx
        if idx == 0:
            ph.text = title
            _style_text_frame(ph, 28, True, NAVY)
        elif idx == 1:
            body = ph
        else:
            ph._element.getparent().remove(ph._element)

    bullets = [b for b in bullets if str(b).strip()]
    visual_note = None

    if body is not None:
        if not bullets:
            body._element.getparent().remove(body._element)
            body = None
        else:
            # Pin the text box: without this the placeholder's mid-anchor + autofit lets a
            # narrowed (visual-present) column grow upward and collide with the title.
            body.left, body.top = Inches(0.9), Inches(1.55)
            body.width, body.height = Inches(11.5), Inches(4.6)
            tf = body.text_frame
            tf.word_wrap = True
            tf.auto_size = MSO_AUTO_SIZE.NONE
            tf.vertical_anchor = MSO_ANCHOR.TOP
            tf.text = str(bullets[0])
            for extra in bullets[1:]:
                p = tf.add_paragraph()
                p.text = str(extra)
            for p in tf.paragraphs:
                p.level = 0
                p.alignment = PP_ALIGN.LEFT
                p.space_after = Pt(10)
                for r in p.runs:
                    r.font.size = Pt(20)
                    r.font.color.rgb = INK
                    r.font.name = FONT

    if visual:
        kind = visual.get("kind")
        if bullets and body is not None:
            body.width = Inches(5.4)
            for p in body.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(18)
            left, width = Inches(6.55), Inches(5.95)
        else:
            left, width = Inches(0.9), Inches(11.5)
        top = Inches(1.55)

        if kind == "chart":
            visual_note = _add_chart(slide, visual["spec"], left, top, width, Inches(4.55))
        elif kind == "table":
            visual_note = _add_table(slide, visual["spec"], left, top, width)
        elif kind == "image":
            visual_note = _add_picture(slide, visual["path"], left, top, width)

    title_rule(slide)
    page_number(slide, number)

    if notes:
        slide.notes_slide.notes_text_frame.text = notes
    return visual_note


# ---------------------------------------------------------------- tool registration


def register_deck_tools(app, template_dirs_fn) -> None:
    @app.tool()
    def list_templates() -> Dict[str, Any]:
        """List available .pptx templates and their layout indices.

        Call this first. Returns each template's title/content layout indices plus
        an example deck's slide count, so build_deck can be called without guessing.
        """
        dirs = _template_dirs(template_dirs_fn)
        rows = []
        for d in dirs:
            for f in sorted(Path(d).glob("*.pptx")):
                try:
                    prs = Presentation(str(f))
                    t_idx, c_idx = _pick_layouts(prs)
                    rows.append(
                        {
                            "template": f.name,
                            "path": str(f),
                            "title_layout": t_idx,
                            "content_layout": c_idx,
                            "example_slides": len(prs.slides),
                            "layouts": _layout_summary(prs),
                        }
                    )
                except Exception as exc:  # unreadable file: report, don't fail the catalog
                    rows.append({"template": f.name, "error": str(exc)})
        return {
            "templates": rows,
            "search_paths": dirs,
            "usage": "build_deck(template=<template>, title=..., slides=[{title, bullets, chart|table|image}, ...])",
        }

    @app.tool()
    def render_svg(svg: str, name: str = "graphic", width_px: int = 1600) -> Dict[str, Any]:
        """Rasterize SVG markup to a transparent PNG for use as a slide graphic.

        Use for icons, simple diagrams, or schematic drawings that a chart cannot express.
        Keep the SVG simple: solid fills, few paths, system font names, no external
        images or <script>. Then pass the returned `png` path as an image to build_deck.

        Args:
            svg: full SVG markup, including the <svg ...> root element
            name: file stem for the PNG
            width_px: output width in pixels (height follows the SVG aspect ratio)

        Returns: {"png": <absolute path>, "width_px": ..., "height_px": ...}
        """
        try:
            import cairosvg
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("cairosvg is not installed in this server's venv") from exc

        markup = (svg or "").strip()
        if not markup.lower().startswith("<svg"):
            raise ValueError("svg must be SVG markup starting with '<svg'")
        if "<script" in markup.lower():
            raise ValueError("svg must not contain <script>")

        stem = _slug(name, "graphic")
        dest = _output_dir() / f"{stem}-{datetime.now().strftime('%H%M%S')}.png"
        png_bytes = cairosvg.svg2png(bytestring=markup.encode("utf-8"), output_width=int(width_px))

        dest.write_bytes(png_bytes)
        from PIL import Image  # Pillow ships with the upstream server

        with Image.open(dest) as im:
            w, h = im.size
        return {
            "png": str(dest),
            "width_px": w,
            "height_px": h,
            "hint": "Insert with build_deck(slide image=<png>) or pass as image=<png>.",
        }

    @app.tool()
    def build_deck(
        template: str,
        title: str,
        slides: List[Dict[str, Any]],
        subtitle: str = "",
        output_name: str = "",
        image: str = "",
    ) -> Dict[str, Any]:
        """Build a .pptx from a template and a plain outline — one call, no layout code.

        Each entry in `slides` supports:
            title:   str
            bullets: list[str]      (optional when the slide has a chart/table/image)
            chart:   {"type": "column"|"bar"|"line"|"pie"|"doughnut"|"column_stacked",
                      "categories": [str], "series": [{"name": str, "values": [num]}],
                      "title": str (optional), "show_values": bool (default true)}
                     Native, editable PowerPoint chart, styled with the deck palette.
            table:   {"headers": [str], "rows": [[str]]}   (max 8 body rows)
            image:   absolute path to a .png/.jpg (from render_svg() or disk)
            notes:   str            (speaker notes)

        Args:
            template: template file name from list_templates
            title: deck title (title slide)
            slides: list of slide specs, see above
            subtitle: title-slide subtitle, e.g. a date or tagline
            output_name: optional file stem for the result
            image: optional default image placed on every content slide that has no
                   own chart/table/image (right half; bullets then use the left half)

        Returns: {"file": <absolute path>, "slide_count": N, "visuals": [...]}
        """
        dirs = _template_dirs(template_dirs_fn)
        tpl = _resolve_template(template, dirs)
        prs = Presentation(str(tpl))
        title_idx, content_idx = _pick_layouts(prs)
        _delete_all_slides(prs)

        title_slide = prs.slides.add_slide(prs.slide_layouts[title_idx])
        _fill_title_slide(title_slide, title, subtitle)
        decorate(title_slide)

        default_image = Path(image) if image and Path(image).is_file() else None

        visuals: List[str] = []
        made = 0
        for item in slides or []:
            if not isinstance(item, dict):
                continue
            slide = prs.slides.add_slide(prs.slide_layouts[content_idx])

            visual = None
            if isinstance(item.get("chart"), dict):
                visual = {"kind": "chart", "spec": item["chart"]}
            elif isinstance(item.get("table"), dict):
                visual = {"kind": "table", "spec": item["table"]}
            elif item.get("image") and Path(str(item["image"])).is_file():
                visual = {"kind": "image", "path": Path(str(item["image"]))}
            elif default_image is not None:
                visual = {"kind": "image", "path": default_image}

            note = _fill_content_slide(
                slide,
                made + 2,
                str(item.get("title", "")),
                [str(b) for b in (item.get("bullets") or [])],
                (str(item["notes"]) if item.get("notes") else None),
                visual,
            )
            if visual and note:
                visuals.append(f"slide {made + 2}: {note}")
            made += 1

        stem = _slug(output_name or title, "deck")
        stamp = datetime.now().strftime("%H%M%S")
        dest = _output_dir() / f"{stem}-{stamp}.pptx"
        prs.save(dest)

        return {
            "file": str(dest),
            "slide_count": len(prs.slides._sldIdLst),
            "content_slides": made,
            "template": tpl.name,
            "layouts_used": {"title": title_idx, "content": content_idx},
            "visuals": visuals,
            "hint": "The deck is saved. Reply with the file path and a one-line summary.",
        }
