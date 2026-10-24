"""Small-model-facing tools layered on top of the vendored upstream server.

Two tools only:
  list_templates()  -> catalog of template files + their two layout indices
  build_deck(...)   -> one call: JSON outline in, .pptx out

Both are deterministic: the model never writes layout code, it emits an outline.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.util import Pt

from deck_style import GRAY, INK, NAVY, decorate

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _template_dirs(template_dirs_fn) -> List[str]:
    dirs = template_dirs_fn()
    seen, out = set(), []
    for d in dirs:
        p = str(Path(d).expanduser().resolve())
        if p not in seen and Path(p).is_dir():
            seen.add(p)
            out.append(p)
    return out


def _layout_summary(prs: Presentation) -> List[Dict[str, Any]]:
    rows = []
    for i, lay in enumerate(prs.slide_layouts):
        names = []
        for ph in lay.placeholders:
            if ph.has_text_frame and ph.text_frame.text.strip():
                pass
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


def _output_dir() -> Path:
    out = Path(os.environ.get("PPT_OUTPUT_DIR", Path(__file__).resolve().parent / "out"))
    out.mkdir(parents=True, exist_ok=True)
    return out


def _slug(text: str, fallback: str = "deck") -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s or fallback)[:60]


def _delete_all_slides(prs: Presentation) -> None:
    sldIdLst = prs.slides._sldIdLst
    for sldId in list(sldIdLst):
        prs.part.drop_rel(sldId.rId)
        sldIdLst.remove(sldId)


def _style_text_frame(shape, size: int, bold: bool, color) -> None:
    """Explicit formatting so rendering does not depend on layout inheritance."""
    tf = shape.text_frame
    tf.word_wrap = True
    for p in tf.paragraphs:
        p.alignment = PP_ALIGN.LEFT
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.color.rgb = color
            r.font.name = "Calibri"


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


def _fill_content_slide(slide, title: str, bullets: List[str], notes: str | None) -> None:
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

    if body is None:
        return
    tf = body.text_frame
    tf.word_wrap = True
    items = [b for b in bullets if str(b).strip()] or [""]
    tf.text = str(items[0])
    for extra in items[1:]:
        p = tf.add_paragraph()
        p.text = str(extra)
    for p in tf.paragraphs:
        p.level = 0
        p.alignment = PP_ALIGN.LEFT
        for r in p.runs:
            r.font.size = Pt(20)
            r.font.color.rgb = INK
            r.font.name = "Calibri"

    if notes:
        slide.notes_slide.notes_text_frame.text = notes


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
            "usage": "build_deck(template=<template>, title=..., slides=[{title, bullets}, ...])",
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

        Args:
            template: template file name from list_templates (e.g. "corporate-2slide.pptx")
            title: deck title (title slide)
            slides: list of {"title": str, "bullets": [str, ...], "notes": str (optional)}
            subtitle: title-slide subtitle, e.g. a date or tagline
            output_name: optional file stem for the result
            image: optional absolute path to an image; it is placed on the right half
                   of every content slide (bullets then use the left half)

        Returns: {"file": <absolute path on this machine>, "slide_count": N, ...}
        """
        dirs = _template_dirs(template_dirs_fn)
        tpl = _resolve_template(template, dirs)
        prs = Presentation(str(tpl))
        title_idx, content_idx = _pick_layouts(prs)

        _delete_all_slides(prs)

        title_slide = prs.slides.add_slide(prs.slide_layouts[title_idx])
        _fill_title_slide(title_slide, title, subtitle)
        decorate(title_slide)

        use_image = bool(image) and Path(image).is_file() and Path(image).suffix.lower() in IMAGE_SUFFIXES

        made = 0
        for item in slides or []:
            if not isinstance(item, dict):
                continue
            slide = prs.slides.add_slide(prs.slide_layouts[content_idx])
            _fill_content_slide(
                slide,
                str(item.get("title", "")),
                [str(b) for b in (item.get("bullets") or [])],
                (str(item["notes"]) if item.get("notes") else None),
            )
            decorate(slide)
            if use_image:
                from pptx.util import Inches

                body = next(
                    (ph for ph in slide.placeholders if ph.placeholder_format.idx == 1), None
                )
                if body is not None:
                    body.left, body.width = Inches(0.9), Inches(6.1)
                slide.shapes.add_picture(
                    image, Inches(7.3), Inches(1.55), width=Inches(5.1)
                )
            made += 1

        stem = _slug(output_name or title, "deck")
        stamp = datetime.now().strftime("%H%M%S")
        dest = _output_dir() / f"{stem}-{stamp}.pptx"
        prs.save(dest)

        return {
            "file": str(dest),
            "slide_count": len(prs.slides.__iter__.__self__._sldIdLst),
            "content_slides": made,
            "template": tpl.name,
            "layouts_used": {"title": title_idx, "content": content_idx},
            "image_placed": use_image,
            "hint": "The deck is saved. Do not add more slides unless asked.",
        }
