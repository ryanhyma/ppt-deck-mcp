#!/usr/bin/env python
"""Generate the canonical 2-slide corporate .pptx fixture used by build_deck.

Layout 0 = Title Slide, Layout 1 = Title and Content. All other layouts/slides are
dropped so a small model has exactly two choices.
"""
import sys
from pathlib import Path

import sys as _sys; _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from deck_style import decorate, HEIGHT, WIDTH  # noqa: E402
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

NAVY = RGBColor(0x1F, 0x38, 0x64)
ACCENT = RGBColor(0x2A, 0x9D, 0x8F)
GRAY = RGBColor(0x5A, 0x5A, 0x5A)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

W, H = Inches(13.333), Inches(7.5)


def drop_placeholder(shape):
    shape._element.getparent().remove(shape._element)


def style_text(shape, size, bold=False, color=NAVY, align=None):
    tf = shape.text_frame
    tf.word_wrap = True
    for p in tf.paragraphs:
        if align is not None:
            p.alignment = align
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.color.rgb = color
            r.font.name = "Calibri"


def build(out: Path):
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H

    layouts = list(prs.slide_layouts)
    if len(layouts) < 2:
        sys.exit("unexpected template: fewer than 2 slide layouts")

    # --- Title slide layout ---
    title_lay = layouts[0]
    for ph in list(title_lay.placeholders):
        if ph.placeholder_format.idx not in (0, 1):
            drop_placeholder(ph)
    for ph in title_lay.placeholders:
        if ph.placeholder_format.idx == 0:
            ph.left, ph.top, ph.width, ph.height = (
                Inches(0.9), Inches(2.05), Inches(11.5), Inches(1.9),
            )
            style_text(ph, 44, bold=True, color=NAVY, align=PP_ALIGN.LEFT)
        elif ph.placeholder_format.idx == 1:
            ph.left, ph.top, ph.width, ph.height = (
                Inches(0.9), Inches(4.1), Inches(11.5), Inches(1.2),
            )
            style_text(ph, 20, color=GRAY, align=PP_ALIGN.LEFT)

    # --- Content slide layout ---
    body_lay = layouts[1]
    for ph in list(body_lay.placeholders):
        if ph.placeholder_format.idx not in (0, 1):
            drop_placeholder(ph)
    for ph in body_lay.placeholders:
        if ph.placeholder_format.idx == 0:
            ph.left, ph.top, ph.width, ph.height = (
                Inches(0.9), Inches(0.45), Inches(11.5), Inches(0.95),
            )
            style_text(ph, 30, bold=True, color=NAVY, align=PP_ALIGN.LEFT)
        elif ph.placeholder_format.idx == 1:
            ph.left, ph.top, ph.width, ph.height = (
                Inches(0.9), Inches(1.55), Inches(11.5), Inches(4.9),
            )
            style_text(ph, 19, color=RGBColor(0x33, 0x33, 0x33), align=PP_ALIGN.LEFT)

    # --- Example slides (the "ghost" content) ---
    s1 = prs.slides.add_slide(title_lay)
    decorate(s1)
    s1.placeholders[0].text = "Presentation Title"
    s1.placeholders[1].text = "Subtitle or date"

    s2 = prs.slides.add_slide(body_lay)
    decorate(s2)
    s2.placeholders[0].text = "Section Heading"
    tf = s2.placeholders[1].text_frame
    tf.text = "First key point"
    for text in ("Second key point", "Third key point"):
        p = tf.add_paragraph()
        p.text = text
        p.level = 0

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    print(f"wrote {out} ({out.stat().st_size} bytes, {len(prs.slides.__iter__.__self__._sldIdLst)} slides)")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "templates" / "corporate-2slide.pptx"
    build(target)
