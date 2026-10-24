"""Shared visual style: the deterministic chrome every generated slide gets.

Kept as code (not slide-master XML) so it works the same with python-pptx on Linux
and Windows and cannot be broken by template edits.
"""
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

NAVY = RGBColor(0x1F, 0x38, 0x64)
ACCENT = RGBColor(0x2A, 0x9D, 0x8F)
GRAY = RGBColor(0x5A, 0x5A, 0x5A)
INK = RGBColor(0x33, 0x33, 0x33)
MUTED = RGBColor(0x9A, 0xA0, 0xA6)

WIDTH = Inches(13.333)
HEIGHT = Inches(7.5)

FOOTER = "Acme Corp  |  Confidential"


def decorate(slide) -> None:
    """Bottom rule + accent bar + footer wordmark. Purely additive, no placeholders."""
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, HEIGHT - Inches(0.22), WIDTH, Inches(0.22))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()
    bar.shadow.inherit = False

    rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, HEIGHT - Inches(0.26), WIDTH, Inches(0.04))
    rule.fill.solid()
    rule.fill.fore_color.rgb = NAVY
    rule.line.fill.background()
    rule.shadow.inherit = False

    foot = slide.shapes.add_textbox(Inches(0.55), HEIGHT - Inches(0.60), Inches(7), Inches(0.3))
    ftf = foot.text_frame
    ftf.text = FOOTER
    for p in ftf.paragraphs:
        for r in p.runs:
            r.font.size = Pt(10)
            r.font.color.rgb = MUTED
            r.font.name = "Calibri"
