#!/usr/bin/env python
"""Generate the template family: one .pptx + one .json theme manifest per style.

Each template is 16:9 with exactly two usable layouts (Title Slide, Title and Content) and a
sidecar manifest that the deck builder reads for chrome/typography colours.

    .venv/bin/python tools/make_templates.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deck_style import Theme, decorate  # noqa: E402

W, H = Inches(13.333), Inches(7.5)

THEMES = [
    {
        "file": "corporate-2slide.pptx",
        "label": "Corporate Teal",
        "description": "Navy titles, teal accent bar, Calibri. The default.",
        "background": "#FFFFFF",
        "title": "#1F3864",
        "body": "#333333",
        "accent": "#2A9D8F",
        "muted": "#9AA0A6",
        "font": "Calibri",
        "rule_width": 1.1,
        "rail": False,
    },
    {
        "file": "midnight.pptx",
        "label": "Midnight",
        "description": "Dark deck, sky-blue accent. High contrast for screen presentations.",
        "background": "#0B1220",
        "title": "#E8EDF4",
        "body": "#C3CDDB",
        "accent": "#38BDF8",
        "muted": "#64748B",
        "font": "Calibri",
        "rule_width": 1.1,
        "rail": False,
    },
    {
        "file": "ledger.pptx",
        "label": "Ledger",
        "description": "Serif, bronze accent, generous margins. Financial / board tone.",
        "background": "#FFFFFF",
        "title": "#111827",
        "body": "#374151",
        "accent": "#B08968",
        "muted": "#9CA3AF",
        "font": "Georgia",
        "rule_width": 0.7,
        "rail": False,
    },
    {
        "file": "sandstone.pptx",
        "label": "Sandstone",
        "description": "Warm paper background, terracotta accent. Editorial / human tone.",
        "background": "#FBF7F0",
        "title": "#3E2C23",
        "body": "#4B3F36",
        "accent": "#C4703C",
        "muted": "#A1887F",
        "font": "Calibri",
        "rule_width": 1.1,
        "rail": False,
    },
    {
        "file": "grid.pptx",
        "label": "Grid",
        "description": "Cobalt accent rail on the left edge. Modern product / tech tone.",
        "background": "#FFFFFF",
        "title": "#0F172A",
        "body": "#334155",
        "accent": "#2563EB",
        "muted": "#94A3B8",
        "font": "Calibri",
        "rule_width": 1.1,
        "rail": True,
    },
]


def rgb(hex_str: str) -> RGBColor:
    h = hex_str.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def drop(shape) -> None:
    shape._element.getparent().remove(shape._element)


def style(shape, size: int, bold: bool, color: RGBColor, font: str) -> None:
    tf = shape.text_frame
    tf.word_wrap = True
    for p in tf.paragraphs:
        p.alignment = PP_ALIGN.LEFT
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.color.rgb = color
            r.font.name = font


def build(theme: dict, out_dir: Path) -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H

    bg = rgb(theme["background"])
    for slide in list(prs.slides):
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = bg
    prs.slide_master.background.fill.solid()
    prs.slide_master.background.fill.fore_color.rgb = bg

    title_lay, body_lay = prs.slide_layouts[0], prs.slide_layouts[1]

    for lay in (title_lay, body_lay):
        for ph in list(lay.placeholders):
            if ph.placeholder_format.idx not in (0, 1):
                drop(ph)

    for ph in title_lay.placeholders:
        if ph.placeholder_format.idx == 0:
            ph.left, ph.top, ph.width, ph.height = Inches(0.9), Inches(2.05), Inches(11.5), Inches(1.9)
            style(ph, 44, True, rgb(theme["title"]), theme["font"])
        elif ph.placeholder_format.idx == 1:
            ph.left, ph.top, ph.width, ph.height = Inches(0.9), Inches(4.1), Inches(11.5), Inches(1.2)
            style(ph, 20, False, rgb(theme["body"]), theme["font"])

    for ph in body_lay.placeholders:
        if ph.placeholder_format.idx == 0:
            ph.left, ph.top, ph.width, ph.height = Inches(0.9), Inches(0.45), Inches(11.5), Inches(0.95)
            style(ph, 30, True, rgb(theme["title"]), theme["font"])
        elif ph.placeholder_format.idx == 1:
            ph.left, ph.top, ph.width, ph.height = Inches(0.9), Inches(1.55), Inches(11.5), Inches(4.9)
            style(ph, 19, False, rgb(theme["body"]), theme["font"])

    s1 = prs.slides.add_slide(title_lay)
    s1.placeholders[0].text = "Presentation Title"
    s1.placeholders[1].text = "Subtitle or date"

    s2 = prs.slides.add_slide(body_lay)
    s2.placeholders[0].text = "Section Heading"
    tf = s2.placeholders[1].text_frame
    tf.text = "First key point"
    for text in ("Second key point", "Third key point"):
        p = tf.add_paragraph()
        p.text = text

    # Layout-level styling does not survive into an empty placeholder (no runs yet), so paint
    # the example slides explicitly — otherwise a dark theme previews as black-on-black.
    theme_obj = Theme(theme)

    def paint(ph, size, bold):
        lines = [ln for ln in ph.text_frame.text.split("\n")]
        tf = ph.text_frame
        tf.text = ""
        for i, line in enumerate(lines):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            run = para.add_run()
            run.text = line
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.name = theme["font"]
            run.font.color.rgb = rgb(theme["title"]) if bold else rgb(theme["body"])
            para.alignment = PP_ALIGN.LEFT

    for slide, title_size, body_size in ((s1, 40, 20), (s2, 28, 19)):
        for ph in slide.placeholders:
            paint(ph, title_size if ph.placeholder_format.idx == 0 else body_size,
                  ph.placeholder_format.idx == 0)
        decorate(slide, theme_obj)

    out = out_dir / theme["file"]
    out_dir.mkdir(parents=True, exist_ok=True)
    prs.save(out)

    manifest = {k: v for k, v in theme.items() if k != "file"}
    manifest["file"] = theme["file"]
    (out_dir / (out.stem + ".json")).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    out_dir = Path(__file__).resolve().parent.parent / "templates"
    for t in THEMES:
        p = build(t, out_dir)
        print(f"wrote {p.name} + {p.stem}.json  ({t['label']})")
