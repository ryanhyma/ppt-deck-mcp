"""Small-model-facing tools layered on top of the vendored upstream server.

Three tools only:
  list_templates()          -> catalog of template files + their two layout indices
  render_svg(svg)           -> SVG markup to PNG (icons/diagrams to insert)
  build_deck(...)           -> one call: JSON outline in, .pptx out (bullets, charts,
                               tables, images)

Everything is deterministic: the model emits an outline, never layout code.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
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

from deck_style import Theme, decorate, hex_rgb, page_number, title_rule

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
    raw = (text or "").strip().lower()
    for suffix in (".pptx", ".potx", ".ppt", ".pdf"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
    s = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-")
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


def _load_theme(template_path: Path) -> Theme:
    """Sidecar manifest next to the .pptx: templates/<name>.json."""
    manifest = template_path.with_suffix(".json")
    if manifest.is_file():
        try:
            return Theme(json.loads(manifest.read_text(encoding="utf-8")))
        except Exception:
            pass
    return Theme()


def _save_atomic(prs: Presentation, dest: Path) -> None:
    """Write to a sibling temp file then rename, so a concurrent reader never sees half a deck."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.tmp")
    prs.save(str(tmp))
    os.replace(tmp, dest)


def _delete_all_slides(prs: Presentation) -> None:
    sldIdLst = prs.slides._sldIdLst
    for sldId in list(sldIdLst):
        prs.part.drop_rel(sldId.rId)
        sldIdLst.remove(sldId)


# ---------------------------------------------------------------- text styling


def _style_text_frame(shape, size: int, bold: bool, color, font: str = "Calibri") -> None:
    tf = shape.text_frame
    tf.word_wrap = True
    for p in tf.paragraphs:
        p.alignment = PP_ALIGN.LEFT
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.color.rgb = color
            r.font.name = font


def _fill_title_slide(slide, title: str, subtitle: str, theme: Theme) -> None:
    for ph in slide.placeholders:
        idx = ph.placeholder_format.idx
        if idx == 0:
            ph.text = title
            _style_text_frame(ph, 40, True, theme.title, theme.font)
        elif idx == 1:
            ph.text = subtitle
            _style_text_frame(ph, 20, False, theme.body, theme.font)
        else:
            ph._element.getparent().remove(ph._element)


# ---------------------------------------------------------------- visuals


def _add_chart(slide, spec: Dict[str, Any], left, top, width, height, theme: Theme) -> str:
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
    chart.font.name = theme.font
    chart.font.color.rgb = theme.body
    if spec.get("title"):
        chart.has_title = True
        chart.chart_title.text_frame.text = str(spec["title"])
        for r in chart.chart_title.text_frame.paragraphs[0].runs:
            r.font.size = Pt(14)
            r.font.bold = True
            r.font.color.rgb = theme.title
            r.font.name = theme.font
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
        labels.font.name = theme.font
        labels.font.color.rgb = theme.body
        if is_pie:
            labels.show_value = False
            labels.show_percentage = True
            labels.show_category_name = False
        else:
            labels.show_value = True

    palette = theme.chart_palette()
    custom = [c for c in (spec.get("colors") or []) if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(c))]
    for i, series in enumerate(plot.series):
        color = hex_rgb(custom[i]) if i < len(custom) else palette[i % len(palette)]
        if kind == "line":
            series.format.line.color.rgb = color
            series.format.line.width = Pt(2.5)
        else:
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = color

    if is_pie or kind == "doughnut":
        for i, point in enumerate(plot.series[0].points):
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = (
                hex_rgb(custom[i]) if i < len(custom) else palette[i % len(palette)])

    if not is_pie:
        try:
            va = chart.value_axis
            va.has_major_gridlines = True
            va.major_gridlines.format.line.color.rgb = theme.panel_alt
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


def _add_table(slide, spec: Dict[str, Any], left, top, width, theme: Theme) -> str:
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
        cell.fill.fore_color.rgb = theme.title
        for p in cell.text_frame.paragraphs:
            for r in p.runs:
                r.font.size = Pt(12)
                r.font.bold = True
                r.font.color.rgb = theme.bg if theme.dark else RGBColor(0xFF, 0xFF, 0xFF)
                r.font.name = theme.font

    for r_i, row in enumerate(rows, start=1):
        for c_i, text in enumerate(row):
            cell = table.cell(r_i, c_i)
            cell.text = text
            cell.fill.solid()
            cell.fill.fore_color.rgb = theme.panel if r_i % 2 else theme.panel_alt
            cell.margin_top = Inches(0.03)
            cell.margin_bottom = Inches(0.03)
            for p in cell.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(11)
                    r.font.color.rgb = theme.body
                    r.font.name = theme.font
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
    theme: Theme,
    layout: str = "split",
) -> str | None:
    body = None
    for ph in slide.placeholders:
        idx = ph.placeholder_format.idx
        if idx == 0:
            ph.text = title
            _style_text_frame(ph, 28, True, theme.title, theme.font)
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
            # Only shrink when the slide is genuinely dense; 6 short bullets at 17 pt look anaemic.
            dense = len(bullets) > 5 and sum(len(b) for b in bullets) > 260
            for p in tf.paragraphs:
                p.level = 0
                p.alignment = PP_ALIGN.LEFT
                p.space_after = Pt(10)
                for r in p.runs:
                    r.font.size = Pt(17 if dense else 20)
                    r.font.color.rgb = theme.body
                    r.font.name = theme.font

    if visual:
        kind = visual.get("kind")
        layout = (layout or "split").lower()
        if layout == "visual-full" and body is not None:
            body._element.getparent().remove(body._element)
            body = None
        if layout == "image-left" and body is not None:
            body.left, body.width = Inches(6.55), Inches(5.4)
            for p in body.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(18)
            left, width = Inches(0.9), Inches(5.4)
        elif bullets and body is not None:
            body.width = Inches(5.4)
            for p in body.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(18)
            left, width = Inches(6.55), Inches(5.95)
        else:
            left, width = Inches(0.9), Inches(11.5)
        top = Inches(1.55)

        if kind == "chart":
            visual_note = _add_chart(slide, visual["spec"], left, top, width, Inches(4.55), theme)
        elif kind == "table":
            visual_note = _add_table(slide, visual["spec"], left, top, width, theme)
        elif kind == "image":
            visual_note = _add_picture(slide, visual["path"], left, top, width)

    title_rule(slide, theme)
    page_number(slide, number, theme)

    if notes:
        slide.notes_slide.notes_text_frame.text = notes
    if len(bullets) > 5 and sum(len(b) for b in bullets) > 260:
        return f"{len(bullets)} bullets (auto-shrunk to 17pt)" if not visual_note else f"{visual_note}; {len(bullets)} bullets shrunk"
    return visual_note


# ---------------------------------------------------------------- tool registration


def _run_style(shape) -> Dict[str, Any] | None:
    """First run's effective formatting, as a build_deck style object."""
    if not shape.has_text_frame:
        return None
    for para in shape.text_frame.paragraphs:
        align = str(para.alignment).split(".")[-1].lower() if para.alignment else None
        for run in para.runs:
            color = None
            try:
                if run.font.color and run.font.color.type is not None:
                    color = "#" + str(run.font.color.rgb)
            except Exception:
                color = None
            return {
                "size": run.font.size.pt if run.font.size else None,
                "font": run.font.name,
                "color": color,
                "align": align if align in ("left", "center", "right", "justify") else None,
            }
    return None


def _style_for_slide(slide) -> Dict[str, Any]:
    style: Dict[str, Any] = {}
    for ph in slide.placeholders:
        idx = ph.placeholder_format.idx
        run_style = _run_style(ph)
        if not run_style:
            continue
        prefix = "title" if idx == 0 else "body" if idx == 1 else None
        if prefix is None:
            continue
        for key, value in run_style.items():
            if value is None:
                continue
            style[f"{prefix}_{key}" if key in ("size", "color") else key] = value
    return style


def _apply_style(slide, style: Dict[str, Any], theme: Theme) -> List[str]:
    """Apply a saved style object to a freshly built slide. Returns applied keys."""
    applied: List[str] = []
    if not style:
        return applied
    for ph in slide.placeholders:
        idx = ph.placeholder_format.idx
        is_title = idx == 0
        size = style.get("title_size" if is_title else "body_size")
        color = style.get("title_color" if is_title else "body_color")
        font = style.get("font")
        align = style.get("align")
        if not ph.has_text_frame:
            continue
        for para in ph.text_frame.paragraphs:
            if align in ALIGNMENTS:
                para.alignment = ALIGNMENTS[align]
            for run in para.runs:
                if size:
                    run.font.size = Pt(int(size))
                    applied.append(f"idx{idx}:size")
                if color and re.fullmatch(r"#[0-9A-Fa-f]{6}", str(color)):
                    run.font.color.rgb = hex_rgb(color)
                    applied.append(f"idx{idx}:color")
                if font:
                    run.font.name = str(font)
                    applied.append(f"idx{idx}:font")
    return applied


def _validate_slides(slides: List[Dict[str, Any]]) -> None:
    """Fail loudly on an unusable chart/table spec.

    Silently skipping a visual produces a deck that looks fine and is wrong — the exact failure a
    small model cannot notice. An error message naming the missing field gets it to retry.
    """
    for i, item in enumerate(slides or [], start=2):
        if not isinstance(item, dict):
            raise ValueError(f"slide {i}: expected an object, got {type(item).__name__}")
        # One visual per slide. Two used to be "chart wins, table silently vanishes" — which is how a
        # deck could claim a table and ship without one.
        visual_keys = [k for k in ("chart", "table", "image") if item.get(k)]
        if len(visual_keys) > 1:
            raise ValueError(
                f"slide {i} has {', '.join(visual_keys)}; one visual per slide — split them into "
                f"separate slides")
        chart = item.get("chart")
        if chart is not None:
            if not isinstance(chart, dict):
                raise ValueError(f"slide {i}: 'chart' must be an object")
            if not chart.get("categories"):
                raise ValueError(f"slide {i}: chart needs 'categories' (non-empty list)")
            rows = chart.get("series") or []
            if not rows:
                raise ValueError(f"slide {i}: chart needs 'series': [{{'name': str, 'values': [num]}}]")
            for row in rows:
                if not isinstance(row, dict) or not row.get("values"):
                    raise ValueError(f"slide {i}: every chart series needs 'values': [num]")
        table = item.get("table")
        if table is not None:
            if not isinstance(table, dict):
                raise ValueError(f"slide {i}: 'table' must be an object")
            if not table.get("headers"):
                raise ValueError(f"slide {i}: table needs 'headers' (list of column names)")
            if not table.get("rows"):
                raise ValueError(f"slide {i}: table needs 'rows' (list of rows, each a list of cells)")


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
                    theme = _load_theme(f)
                    try:
                        from deck_preview import template_preview_urls

                        preview = template_preview_urls(f)
                    except Exception:
                        preview = []
                    rows.append(
                        {
                            "template": f.name,
                            "label": theme.label,
                            "description": theme.raw.get("description", ""),
                            "theme": {
                                "background": theme.raw.get("background"),
                                "accent": theme.raw.get("accent"),
                                "font": theme.font,
                                "dark": theme.dark,
                            },
                            "preview": preview,
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
        base: str = "",
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
            base: optional .pptx whose per-slide styles (size/colour/font/align) are
                  re-applied to matching slides when a slide spec has no own "style"

        Returns: {"file": <absolute path>, "slide_count": N, "visuals": [...]}
        """
        _validate_slides(slides)
        base_styles: List[Dict[str, Any]] = []
        if base:
            base_path = Path(base)
            if not base_path.is_file():
                raise FileNotFoundError(f"base deck not found: {base}")
            base_prs = Presentation(str(base_path))
            base_styles = [_style_for_slide(sl) for sl in base_prs.slides]
        dirs = _template_dirs(template_dirs_fn)
        tpl = _resolve_template(template, dirs)
        prs = Presentation(str(tpl))
        theme = _load_theme(tpl)
        title_idx, content_idx = _pick_layouts(prs)
        _delete_all_slides(prs)

        title_slide = prs.slides.add_slide(prs.slide_layouts[title_idx])
        _fill_title_slide(title_slide, title, subtitle, theme)
        decorate(title_slide, theme)

        default_image = Path(image) if image and Path(image).is_file() else None

        visuals: List[str] = []
        styled: List[str] = []
        warnings: List[str] = []
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

            layout = str(item.get("layout") or (item.get("style") or {}).get("layout") or "split")
            note = _fill_content_slide(
                slide,
                made + 2,
                str(item.get("title", "")),
                [str(b) for b in (item.get("bullets") or [])],
                (str(item["notes"]) if item.get("notes") else None),
                visual,
                theme,
                layout,
            )
            if visual and note:
                visuals.append(f"slide {made + 2}: {note}")
            elif note:
                warnings.append(f"slide {made + 2}: {note}")
            inherited = item.get("style") or (base_styles[made + 1] if made + 1 < len(base_styles) else None)
            if inherited:
                if _apply_style(slide, inherited, theme):
                    styled.append(f"slide {made + 2}")
            made += 1

        stem = _slug(output_name or title, "deck")
        stamp = datetime.now().strftime("%H%M%S")
        dest = _output_dir() / f"{stem}-{stamp}-{uuid.uuid4().hex[:4]}.pptx"
        _save_atomic(prs, dest)

        return {
            "file": str(dest),
            "slide_count": len(prs.slides._sldIdLst),
            "content_slides": made,
            "template": tpl.name,
            "layouts_used": {"title": title_idx, "content": content_idx},
            "theme": theme.label,
            "visuals": visuals,
            "styled_slides": styled,
            "warnings": warnings,
            "hint": "The deck is saved. Reply with the file path and a one-line summary.",
        }


# ---------------------------------------------------------------- read-back / edit loop


def _outline(path: Path) -> Dict[str, Any]:
    """Read a deck back into the same shape build_deck accepts, so an agent can edit it."""
    prs = Presentation(str(path))
    slides: List[Dict[str, Any]] = []
    title_text = ""
    subtitle_text = ""

    for i, slide in enumerate(prs.slides, start=1):
        entry: Dict[str, Any] = {"index": i}

        # Text comes from placeholders only: the decorated shapes (footer wordmark, page
        # number, title rule) are chrome, and reading them back would smuggle
        # "Acme Corp | Confidential" into the outline as body text.
        for ph in slide.placeholders:
            idx = ph.placeholder_format.idx
            if not ph.has_text_frame or not ph.text_frame.text.strip():
                continue
            if i == 1:
                if idx == 0:
                    title_text = ph.text_frame.text.strip()
                elif idx == 1:
                    subtitle_text = ph.text_frame.text.strip()
            elif idx == 0:
                entry["title"] = ph.text_frame.text.strip().split("\n")[0]
            elif idx == 1:
                entry["bullets"] = [b.strip() for b in ph.text_frame.text.split("\n") if b.strip()]

        # Visuals can live in any shape, placeholder or not.
        for shape in slide.shapes:
            if shape.has_chart:
                chart = shape.chart
                plot = chart.plots[0]
                entry["chart"] = {
                    "type": "column",
                    "title": (chart.chart_title.text_frame.text if chart.has_title else ""),
                    "categories": [str(c) for c in plot.categories],
                    "series": [
                        {"name": s.name, "values": [round(float(v), 4) for v in s.values]}
                        for s in plot.series
                    ],
                }
            elif shape.has_table:
                rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
                entry["table"] = {"headers": rows[0], "rows": rows[1:]} if rows else {}

        entry["role"] = "title" if i == 1 else "content"
        style = _style_for_slide(slide)
        if style:
            entry["style"] = style
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            entry["notes"] = slide.notes_slide.notes_text_frame.text.strip()
        slides.append(entry)

    return {
        "file": str(path),
        "title": title_text,
        "subtitle": subtitle_text,
        "slide_count": len(slides),
        "slides": slides,
        "hint": "Edit the entries you were asked to change, then call build_deck with the same "
                "title and the full slides list (title slide excluded).",
    }


def register_verify_tools(app) -> None:
    @app.tool()
    def verify_deck(
        file: str,
        expected_slides: int | None = None,
        table: bool = False,
        chart: bool = False,
        image: bool = False,
    ) -> Dict[str, Any]:
        """Check a built deck against what the user asked for. Call this right after build_deck.

        This is the safety net for the one failure class a small model keeps repeating: promising a
        table (or the right slide count) and then omitting it. Returns ok=False with a concrete
        `problems` list, so the fix is obvious: correct the slide spec and rebuild **once**.

        Args:
            file: absolute path of the .pptx returned by build_deck
            expected_slides: total slides the user asked for, including the title slide
            table / chart / image: True if the user asked for that visual in the deck
        """
        path = Path(file)
        if not path.is_file():
            raise FileNotFoundError(f"deck not found: {file}")
        prs = Presentation(str(path))
        slides = list(prs.slides)

        charts = tables = pictures = 0
        for slide in slides:
            for shape in slide.shapes:
                if shape.has_chart:
                    charts += 1
                elif shape.has_table:
                    tables += 1
                elif shape.shape_type == 13:
                    pictures += 1

        problems: List[str] = []
        if expected_slides is not None and len(slides) != int(expected_slides):
            problems.append(
                f"slide count is {len(slides)}, expected {expected_slides} "
                f"(the count includes the title slide)")
        if table and tables == 0:
            problems.append("no table in the deck: add a 'table': {'headers': [...], 'rows': [[...]]} "
                            "to one slide spec — bullets that look like a table do not count")
        if chart and charts == 0:
            problems.append("no chart in the deck: add a 'chart' object to one slide spec")
        if image and pictures == 0:
            problems.append("no image in the deck: render_svg then pass 'image': <png path>")

        return {
            "ok": not problems,
            "file": str(path),
            "slide_count": len(slides),
            "charts": charts,
            "tables": tables,
            "pictures": pictures,
            "problems": problems,
            "next": "rebuild once with the corrected slide spec, then call verify_deck again"
                    if problems else "ok — call preview_deck",
        }


def register_readback_tools(app, output_dir_getter) -> None:
    @app.tool()
    def get_deck_outline(file: str) -> Dict[str, Any]:
        """Read an existing .pptx back as an editable outline (titles, bullets, chart data, tables).

        Use this before changing a deck: fetch the outline, modify only what the user asked for,
        then call build_deck (same title + full slides list) and preview_deck. Never guess the
        current content.

        Args:
            file: absolute path of a .pptx (from build_deck or list_decks)
        """
        path = Path(file)
        if not path.is_file():
            raise FileNotFoundError(f"deck not found: {file}")
        return _outline(path)


# ---------------------------------------------------------------- per-slide styling


SAFE_FONTS = {"Calibri", "Georgia", "Arial", "Times New Roman", "Verdana", "Trebuchet MS",
              "Liberation Sans", "Liberation Serif", "Carlito"}
ALIGNMENTS = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
              "right": PP_ALIGN.RIGHT, "justify": PP_ALIGN.JUSTIFY}


def register_style_tools(app) -> None:
    @app.tool()
    def style_slide(
        file: str,
        slide_index: int,
        title_size: int | None = None,
        body_size: int | None = None,
        title_color: str | None = None,
        body_color: str | None = None,
        font: str | None = None,
        align: str | None = None,
        title_bold: bool | None = None,
        body_bold: bool | None = None,
        title_italic: bool | None = None,
        body_italic: bool | None = None,
        bold: bool | None = None,
        italic: bool | None = None,
    ) -> Dict[str, Any]:
        """Restyle ONE slide of an existing deck, in place. No rebuild, no content change.

        Parameterised on purpose: the Deck Studio pane calls this directly for a selected
        slide, so styling never depends on a model interpreting words like "bigger".

        Args:
            file: absolute path of the .pptx (from build_deck / list_decks)
            slide_index: 1-based slide number
            title_size / body_size: points, 8..72
            title_color / body_color: hex like "#1F3864"
            font: one of Calibri, Georgia, Arial, Times New Roman, Verdana, Trebuchet MS,
                  Liberation Sans/Serif, Carlito
            align: left | center | right | justify
            title_bold / body_bold / title_italic / body_italic: per-placeholder emphasis.
                (bold/italic are kept as aliases that apply to both; prefer the explicit ones.)
        """
        path = Path(file)
        if not path.is_file():
            raise FileNotFoundError(f"deck not found: {file}")

        for name, value in (("title_size", title_size), ("body_size", body_size)):
            if value is not None and not 8 <= int(value) <= 72:
                raise ValueError(f"{name} must be 8..72 points")
        for name, value in (("title_color", title_color), ("body_color", body_color)):
            if value is not None and not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                raise ValueError(f"{name} must be a hex colour like #1F3864")
        if font is not None and font not in SAFE_FONTS:
            raise ValueError(f"font must be one of: {', '.join(sorted(SAFE_FONTS))}")
        if align is not None and align.lower() not in ALIGNMENTS:
            raise ValueError("align must be left, center, right or justify")

        prs = Presentation(str(path))
        total = len(prs.slides)
        if not 1 <= int(slide_index) <= total:
            raise ValueError(f"slide_index must be 1..{total}")
        slide = prs.slides[int(slide_index) - 1]

        changed: List[str] = []
        for ph in slide.placeholders:
            idx = ph.placeholder_format.idx
            is_title = idx == 0
            size = title_size if is_title else body_size
            color = title_color if is_title else body_color
            want_bold = (title_bold if is_title else body_bold)
            want_bold = want_bold if want_bold is not None else bold
            want_italic = (title_italic if is_title else body_italic)
            want_italic = want_italic if want_italic is not None else italic
            if not ph.has_text_frame:
                continue
            for para in ph.text_frame.paragraphs:
                if align is not None:
                    para.alignment = ALIGNMENTS[align.lower()]
                for run in para.runs:
                    if want_bold is not None:
                        run.font.bold = bool(want_bold)
                        changed.append(f"idx{idx}:bold={bool(want_bold)}")
                    if want_italic is not None:
                        run.font.italic = bool(want_italic)
                        changed.append(f"idx{idx}:italic={bool(want_italic)}")
                    if size is not None:
                        run.font.size = Pt(int(size))
                        changed.append(f"idx{idx}:size={size}")
                    if color is not None:
                        run.font.color.rgb = hex_rgb(color)
                        changed.append(f"idx{idx}:color={color}")
                    if font is not None:
                        run.font.name = font
                        changed.append(f"idx{idx}:font={font}")

        if not changed:
            raise ValueError("nothing to change: pass at least one of title_size, body_size, "
                             "title_color, body_color, font, align, bold, italic")
        _save_atomic(prs, path)

        from deck_preview import build_preview

        preview = build_preview(path)
        return {
            "file": str(path),
            "slide_index": int(slide_index),
            "applied": sorted(set(changed)),
            "slide_count": preview["slide_count"],
            "slides": preview["slides"],
            "download": preview.get("download"),
        }


# ---------------------------------------------------------------- per-run emphasis


def register_text_tools(app) -> None:
    @app.tool()
    def style_text(
        file: str,
        slide_index: int,
        phrase: str,
        placeholder: str = "any",
        bold: bool | None = None,
        italic: bool | None = None,
        underline: bool | None = None,
        color: str | None = None,
        size: int | None = None,
    ) -> Dict[str, Any]:
        """Emphasise a specific phrase on one slide — bold/italic/underline/colour/size on the runs
        containing it. This is the per-run counterpart to style_slide (which formats a whole
        placeholder).

        Args:
            file: absolute path of the .pptx
            slide_index: 1-based slide number
            phrase: text to match, case-insensitive substring (e.g. "retention"). Keep it short.
            placeholder: "any" (default), "title" or "body"
            bold / italic / underline: emphasis to set on the matched runs
            color: hex like "#C4703C"
            size: points 8..72

        Returns: {"runs_changed": n, "matches": [str, ...]} — an empty match list means the phrase
        spans several runs; use a shorter phrase.
        """
        path = Path(file)
        if not path.is_file():
            raise FileNotFoundError(f"deck not found: {file}")
        if not phrase or not phrase.strip():
            raise ValueError("phrase must not be empty")
        if color is not None and not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise ValueError("color must be a hex colour like #1F3864")
        if size is not None and not 8 <= int(size) <= 72:
            raise ValueError("size must be 8..72 points")
        if all(v is None for v in (bold, italic, underline, color, size)):
            raise ValueError("pass at least one of bold, italic, underline, color, size")

        prs = Presentation(str(path))
        total = len(prs.slides)
        if not 1 <= int(slide_index) <= total:
            raise ValueError(f"slide_index must be 1..{total}")
        slide = prs.slides[int(slide_index) - 1]

        needle = phrase.strip().lower()
        wanted = {"title": 0, "body": 1}.get(placeholder.lower())
        changed, matches = 0, []

        for ph in slide.placeholders:
            idx = ph.placeholder_format.idx
            if wanted is not None and idx != wanted:
                continue
            if idx not in (0, 1) or not ph.has_text_frame:
                continue
            for para in ph.text_frame.paragraphs:
                for run in para.runs:
                    if needle not in run.text.lower():
                        continue
                    if bold is not None:
                        run.font.bold = bool(bold)
                    if italic is not None:
                        run.font.italic = bool(italic)
                    if underline is not None:
                        run.font.underline = bool(underline)
                    if color is not None:
                        run.font.color.rgb = hex_rgb(color)
                    if size is not None:
                        run.font.size = Pt(int(size))
                    changed += 1
                    matches.append(run.text[:60])

        if changed == 0:
            raise ValueError(
                f"'{phrase}' not found as a single run on slide {slide_index}. "
                "Use a shorter phrase, or style the whole placeholder with style_slide."
            )
        _save_atomic(prs, path)

        from deck_preview import build_preview

        preview = build_preview(path)
        return {
            "file": str(path),
            "slide_index": int(slide_index),
            "phrase": phrase,
            "runs_changed": changed,
            "matches": matches,
            "slides": preview["slides"],
            "download": preview.get("download"),
        }
