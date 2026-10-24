"""Shared visual style: the deterministic chrome every generated slide gets.

Chrome and palette come from the template's sidecar manifest (`templates/<name>.json`),
so a dark, serif or rail-edged template renders correctly without touching this module.
"""
from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

# Defaults = the Corporate Teal manifest, used when a template has no sidecar.
DEFAULT_THEME = {
    "label": "Corporate Teal",
    "background": "#FFFFFF",
    "title": "#1F3864",
    "body": "#333333",
    "accent": "#2A9D8F",
    "muted": "#9AA0A6",
    "font": "Calibri",
    "rule_width": 1.1,
    "rail": False,
}

CHART_PALETTE = [
    RGBColor(0x2A, 0x9D, 0x8F),
    RGBColor(0x1F, 0x38, 0x64),
    RGBColor(0xE9, 0xC4, 0x6A),
    RGBColor(0xE7, 0x6F, 0x51),
    RGBColor(0x26, 0x46, 0x53),
    RGBColor(0x8A, 0xB1, 0x7D),
]

WIDTH = Inches(13.333)
HEIGHT = Inches(7.5)
FOOTER = "Acme Corp  |  Confidential"


def hex_rgb(value: str) -> RGBColor:
    h = value.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _luma(color: RGBColor) -> float:
    return (0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]) / 255


def mix(a: RGBColor, b: RGBColor, t: float) -> RGBColor:
    return RGBColor(
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


class Theme:
    """Resolved colours + typography for one template."""

    def __init__(self, data: dict | None = None):
        d = dict(DEFAULT_THEME)
        d.update({k: v for k, v in (data or {}).items() if v is not None})
        self.raw = d
        self.label = d.get("label", "Custom")
        self.font = d["font"]
        self.bg = hex_rgb(d["background"])
        self.title = hex_rgb(d["title"])
        self.body = hex_rgb(d["body"])
        self.accent = hex_rgb(d["accent"])
        self.muted = hex_rgb(d["muted"])
        self.rule_width = float(d.get("rule_width", 1.1))
        self.rail = bool(d.get("rail"))
        self.dark = _luma(self.bg) < 0.5
        # Table / card surface: a hair away from the background, in the title's direction.
        self.panel = mix(self.bg, self.title, 0.10 if self.dark else 0.05)
        self.panel_alt = mix(self.bg, self.title, 0.16 if self.dark else 0.02)
        # Header text colour that survives on the accent fill.
        self.on_accent = RGBColor(0x0B, 0x12, 0x20) if _luma(self.accent) > 0.62 else RGBColor(0xFF, 0xFF, 0xFF)

    def chart_palette(self):
        return [self.accent, self.title, hex_rgb("#E9C46A"), hex_rgb("#E76F51"),
                hex_rgb("#264653"), hex_rgb("#8AB17D")]


def _no_shadow(shape) -> None:
    shape.shadow.inherit = False


def decorate(slide, theme: Theme) -> None:
    """Accent bar + rule + footer wordmark, in the template's palette."""
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, HEIGHT - Inches(0.22), WIDTH, Inches(0.22))
    bar.fill.solid()
    bar.fill.fore_color.rgb = theme.accent
    bar.line.fill.background()
    _no_shadow(bar)

    rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, HEIGHT - Inches(0.26), WIDTH, Inches(0.04))
    rule.fill.solid()
    rule.fill.fore_color.rgb = theme.title
    rule.line.fill.background()
    _no_shadow(rule)

    if theme.rail:
        rail = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.16), HEIGHT - Inches(0.26))
        rail.fill.solid()
        rail.fill.fore_color.rgb = theme.accent
        rail.line.fill.background()
        _no_shadow(rail)

    foot = slide.shapes.add_textbox(Inches(0.55), HEIGHT - Inches(0.60), Inches(7), Inches(0.3))
    tf = foot.text_frame
    tf.text = FOOTER
    for p in tf.paragraphs:
        for r in p.runs:
            r.font.size = Pt(10)
            r.font.color.rgb = theme.muted
            r.font.name = theme.font


def title_rule(slide, theme: Theme, top: float = 1.28, width: float | None = None) -> None:
    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0.92),
        Inches(top),
        Inches(width if width is not None else theme.rule_width),
        Inches(0.055),
    )
    bar.fill.solid()
    bar.fill.fore_color.rgb = theme.accent
    bar.line.fill.background()
    _no_shadow(bar)


def page_number(slide, number: int, theme: Theme) -> None:
    box = slide.shapes.add_textbox(WIDTH - Inches(1.35), HEIGHT - Inches(0.62), Inches(0.8), Inches(0.3))
    tf = box.text_frame
    tf.text = f"{number:02d}"
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.RIGHT
    for r in p.runs:
        r.font.size = Pt(10)
        r.font.bold = True
        r.font.color.rgb = theme.muted
        r.font.name = theme.font
