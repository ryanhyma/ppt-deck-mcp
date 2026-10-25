"""Regression tests for every defect found while building the deck pipeline.

Run:  .venv/bin/python -m unittest discover -s tests -v

Each test names the failure it pins down, so a future change that reintroduces it fails here
instead of shipping a good-looking wrong deck.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Env must be set before importing the server (it registers tools against these paths).
_TMP = Path(tempfile.mkdtemp(prefix="deck-tests-"))
_TEMPLATES = ROOT / "templates"
os.environ["PPT_TEMPLATE_PATH"] = str(_TEMPLATES)
os.environ["PPT_OUTPUT_DIR"] = str(_TMP / "out")
os.environ["PPT_UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["PPT_PUBLIC_BASE"] = "http://test.local:8931"
(_TMP / "out").mkdir(parents=True, exist_ok=True)
(_TMP / "uploads" / "user").mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "upstream"))
sys.path.insert(0, str(HERE))

import server  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.util import Inches, Pt  # noqa: E402

from deck_tools import _outline, _pick_layouts  # noqa: E402

TOOLS = {name: tool.fn for name, tool in server.app._tool_manager._tools.items()}


def build(**kwargs):
    base = {"template": "corporate-2slide.pptx", "title": "Regression deck", "slides": []}
    base.update(kwargs)
    return TOOLS["build_deck"](**base)


def bullets(n, length=40):
    return [f"Point {i} " + "x" * max(0, length - 8) for i in range(1, n + 1)]


def body_of(prs, index):
    for ph in prs.slides[index].placeholders:
        if ph.placeholder_format.idx == 1:
            return ph
    return None


class LayoutRegressions(unittest.TestCase):
    def test_content_layout_is_title_and_content(self):
        """Was: content slides used layout 10 ("Content with Caption")."""
        prs = Presentation(str(_TEMPLATES / "corporate-2slide.pptx"))
        title_idx, content_idx = _pick_layouts(prs)
        self.assertEqual(title_idx, 0)
        self.assertEqual(content_idx, 1)

    def test_narrow_bullet_column_does_not_grow_over_the_title(self):
        """Was: with a chart beside it, the narrowed body placeholder grew upward into the title."""
        out = build(slides=[{
            "title": "Overlap check",
            "bullets": bullets(3),
            "chart": {"type": "column", "categories": ["A", "B"], "series": [{"name": "S", "values": [1, 2]}]},
        }])
        prs = Presentation(out["file"])
        slide = prs.slides[1]
        title = next(ph for ph in slide.placeholders if ph.placeholder_format.idx == 0)
        body = body_of(prs, 1)
        self.assertGreaterEqual(body.top, title.top + title.height - Inches(0.15))
        self.assertEqual(body.top, Inches(1.55))
        self.assertEqual(body.width, Inches(5.4))

    def test_visual_left_puts_the_chart_left_of_the_text(self):
        out = build(slides=[{
            "title": "Left visual",
            "bullets": bullets(2),
            "layout": "image-left",
            "chart": {"type": "bar", "categories": ["A"], "series": [{"name": "S", "values": [3]}]},
        }])
        prs = Presentation(out["file"])
        slide = prs.slides[1]
        chart = next(sh for sh in slide.shapes if sh.has_chart)
        body = body_of(prs, 1)
        self.assertLess(chart.left, body.left)

    def test_visual_full_drops_the_bullet_placeholder(self):
        out = build(slides=[{
            "title": "No bullets",
            "bullets": bullets(2),
            "layout": "visual-full",
            "chart": {"type": "line", "categories": ["A", "B"], "series": [{"name": "S", "values": [1, 2]}]},
        }])
        prs = Presentation(out["file"])
        self.assertIsNone(body_of(prs, 1))


class ValidationRegressions(unittest.TestCase):
    def test_table_without_rows_is_rejected(self):
        """Was: silently skipped, producing a deck whose 'Pricing Comparison' slide had no table."""
        with self.assertRaises(ValueError) as ctx:
            build(slides=[{"title": "Table", "table": {"headers": ["a", "b"], "rows": []}}])
        self.assertIn("rows", str(ctx.exception))

    def test_chart_without_series_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            build(slides=[{"title": "Chart", "chart": {"categories": ["a"]}}])
        self.assertIn("series", str(ctx.exception))

    def test_unknown_template_fails_loudly(self):
        with self.assertRaises(FileNotFoundError):
            build(template="corporate", slides=[])


class OutlineRegressions(unittest.TestCase):
    def test_outline_ignores_decorative_chrome(self):
        """Was: the footer wordmark came back as the title-slide subtitle and got rebuilt as content."""
        out = build(title="Deck", subtitle="", slides=[{"title": "Body", "bullets": bullets(2)}])
        data = _outline(Path(out["file"]))
        self.assertEqual(data["title"], "Deck")
        self.assertEqual(data["subtitle"], "")
        self.assertNotIn("Confidential", data["subtitle"])
        self.assertEqual(len(data["slides"][1]["bullets"]), 2)

    def test_outline_round_trips_chart_data(self):
        out = build(slides=[{
            "title": "Chart",
            "chart": {"type": "column", "categories": ["A", "B"], "series": [{"name": "2026", "values": [10, 20]}]},
        }])
        data = _outline(Path(out["file"]))
        chart = data["slides"][1]["chart"]
        self.assertEqual(chart["categories"], ["A", "B"])
        self.assertEqual(chart["series"][0]["values"], [10.0, 20.0])


class StyleRegressions(unittest.TestCase):
    def test_title_bold_does_not_bold_the_body(self):
        """Was: one `bold` flag hit every placeholder, so "bold the title" bolded the bullets too."""
        out = build(slides=[{"title": "T", "bullets": bullets(2)}])
        deck = out["file"]
        TOOLS["style_slide"](file=deck, slide_index=2, title_bold=True)
        prs = Presentation(deck)
        slide = prs.slides[1]
        title = next(ph for ph in slide.placeholders if ph.placeholder_format.idx == 0)
        body = body_of(prs, 1)
        self.assertTrue(title.text_frame.paragraphs[0].runs[0].font.bold)
        self.assertFalse(bool(body.text_frame.paragraphs[0].runs[0].font.bold))

    def test_styling_survives_a_content_rebuild_with_base(self):
        """Was: a content edit rebuilt from the outline and wiped the pane's per-slide styling."""
        out = build(slides=[{"title": "One", "bullets": bullets(2)}, {"title": "Two", "bullets": bullets(2)}])
        deck = out["file"]
        TOOLS["style_slide"](file=deck, slide_index=2, title_size=40, title_color="#C4703C", font="Georgia")

        rebuilt = build(
            title="Regression deck",
            slides=[{"title": "One", "bullets": bullets(2)}, {"title": "Two edited", "bullets": bullets(3)}],
            base=deck,
            output_name="rebuilt",
        )
        prs = Presentation(rebuilt["file"])
        title = next(ph for ph in prs.slides[1].placeholders if ph.placeholder_format.idx == 0)
        run = title.text_frame.paragraphs[0].runs[0]
        self.assertEqual(run.font.size, Pt(40))
        self.assertEqual(str(run.font.color.rgb), "C4703C")
        self.assertEqual(run.font.name, "Georgia")
        self.assertEqual(len(body_of(prs, 2).text_frame.text.split("\n")), 3)

    def test_style_text_emphasises_only_the_matching_run(self):
        out = build(slides=[{"title": "T", "bullets": ["Bundling raises retention", "Other point"]}])
        deck = out["file"]
        result = TOOLS["style_text"](file=deck, slide_index=2, phrase="retention", bold=True, color="#C4703C")
        self.assertEqual(result["runs_changed"], 1)
        prs = Presentation(deck)
        runs = [r for p in body_of(prs, 1).text_frame.paragraphs for r in p.runs]
        target = next(r for r in runs if "retention" in r.text)
        other = next(r for r in runs if "Other" in r.text)
        self.assertTrue(target.font.bold)
        self.assertEqual(str(target.font.color.rgb), "C4703C")
        self.assertFalse(bool(other.font.bold))

    def test_style_text_fails_loudly_when_phrase_is_absent(self):
        """A missing phrase must not be a silent no-op."""
        out = build(slides=[{"title": "T", "bullets": ["Something else"]}])
        with self.assertRaises(ValueError) as ctx:
            TOOLS["style_text"](file=out["file"], slide_index=2, phrase="nonexistent", bold=True)
        self.assertIn("not found", str(ctx.exception))

    def test_dense_slides_shrink_bullets_and_warn(self):
        out = build(slides=[{"title": "Dense", "bullets": bullets(6, length=70)}])
        self.assertTrue(out["warnings"], "expected a shrink warning")
        prs = Presentation(out["file"])
        run = body_of(prs, 1).text_frame.paragraphs[0].runs[0]
        self.assertEqual(run.font.size, Pt(17))

    def test_short_six_bullets_keep_full_size(self):
        """6 short bullets at 17 pt looked anaemic — the shrink is length-gated."""
        out = build(slides=[{"title": "Short", "bullets": ["One", "Two", "Three", "Four", "Five", "Six"]}])
        self.assertEqual(out["warnings"], [])
        prs = Presentation(out["file"])
        self.assertEqual(body_of(prs, 1).text_frame.paragraphs[0].runs[0].font.size, Pt(20))

    def test_chart_colours_override_the_palette(self):
        out = build(slides=[{
            "title": "Colour",
            "chart": {"type": "column", "categories": ["A"], "colors": ["#C4703C"],
                      "series": [{"name": "S", "values": [1]}]},
        }])
        prs = Presentation(out["file"])
        chart = next(sh.chart for sh in prs.slides[1].shapes if sh.has_chart)
        self.assertEqual(str(chart.plots[0].series[0].format.fill.fore_color.rgb), "C4703C")

    def test_dark_template_uses_light_body_text(self):
        """Was: layout-level styling never reached empty placeholders -> black text on a dark deck."""
        out = build(template="midnight.pptx", slides=[{"title": "Dark", "bullets": bullets(1)}])
        prs = Presentation(out["file"])
        run = body_of(prs, 1).text_frame.paragraphs[0].runs[0]
        self.assertEqual(str(run.font.color.rgb), "C3CDDB")

    def test_output_name_does_not_gain_a_pptx_suffix(self):
        out = build(output_name="quarterly.pptx", slides=[])
        self.assertNotIn("pptx-", Path(out["file"]).stem)


class ImportRegressions(unittest.TestCase):
    def test_missing_absolute_path_reports_clearly(self):
        """Was: pathlib raised 'Non-relative patterns are unsupported' for absolute paths."""
        with self.assertRaises(FileNotFoundError) as ctx:
            TOOLS["import_deck"](file="/home/nobody/nope.pptx")
        self.assertIn("no file at", str(ctx.exception))

    def test_bare_filename_resolves_inside_uploads(self):
        src = _TMP / "uploads" / "user" / "Q3 Review.pptx"
        shutil.copyfile(_TEMPLATES / "corporate-2slide.pptx", src)
        result = TOOLS["import_deck"](file="Q3 Review.pptx")
        self.assertEqual(result["imported_from"], str(src))
        self.assertEqual(result["slide_count"], 2)

    def test_legacy_pot_is_rejected(self):
        legacy = _TMP / "uploads" / "user" / "old.pot"
        legacy.write_bytes(b"not a zip")
        with self.assertRaises(ValueError):
            TOOLS["import_deck"](file="old.pot")


class PreviewRegressions(unittest.TestCase):
    """A missing renderer must not fail a styling operation (it did on the GitHub runner)."""

    def _break_renderer(self):
        import deck_preview

        original = deck_preview._render
        deck_preview._render = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("soffice not found"))
        return deck_preview, original

    def test_build_preview_reports_the_error_instead_of_raising(self):
        out = build(slides=[{"title": "T", "bullets": bullets(1)}])
        module, original = self._break_renderer()
        try:
            from deck_preview import build_preview

            data = build_preview(out["file"])
        finally:
            module._render = original
        self.assertEqual(data["slides"], [])
        self.assertIn("soffice not found", data["preview_error"])
        # The deck's size must not depend on whether a renderer exists.
        self.assertEqual(data["slide_count"], 2)
        self.assertEqual(data["rendered_slides"], 0)
        self.assertTrue(data["download"].endswith("deck.pptx"))

    def test_style_slide_still_applies_without_a_renderer(self):
        out = build(slides=[{"title": "T", "bullets": bullets(1)}])
        module, original = self._break_renderer()
        try:
            result = TOOLS["style_slide"](file=out["file"], slide_index=2, title_size=30)
        finally:
            module._render = original
        self.assertEqual(result["slides"], [])
        prs = Presentation(out["file"])
        title = next(ph for ph in prs.slides[1].placeholders if ph.placeholder_format.idx == 0)
        self.assertEqual(title.text_frame.paragraphs[0].runs[0].font.size, Pt(30))


class HarnessRegressions(unittest.TestCase):
    """The suite's judge must not turn process noise into failures, or hide real ones."""

    CASE = {"id": "x", "category": "t", "prompt": "p",
            "expect": {"slides": 5, "chart": False, "table": False, "image": False,
                       "template": "corporate-2slide.pptx"}}
    GOT = {"slides": 5, "charts": 0, "tables": 0, "pictures": 0,
           "max_bullet_chars": 10, "bullet_overflow_slides": []}

    def judge(self, got, calls=(), error=None):
        from run_suite import judge
        return judge(self.CASE, got, list(calls), error)

    def test_tool_error_is_a_warning_not_a_failure(self):
        calls = [{"name": "build_deck_mcp_ppt", "output": "Error executing tool build_deck: boom"},
                 {"name": "preview_deck_mcp_ppt", "output": "{}"}]
        ok, fails, warnings = self.judge(self.GOT, calls)
        self.assertTrue(ok, fails)
        self.assertTrue(any("tool errors" in w for w in warnings))

    def test_missing_preview_is_a_warning(self):
        calls = [{"name": "build_deck_mcp_ppt", "output": "{}"}]
        ok, fails, warnings = self.judge(self.GOT, calls)
        self.assertTrue(ok)
        self.assertIn("preview_deck not called", warnings)

    def test_slide_count_mismatch_fails(self):
        ok, fails, _ = self.judge({**self.GOT, "slides": 6})
        self.assertFalse(ok)
        self.assertTrue(any("slides" in f for f in fails))

    def test_no_deck_fails(self):
        ok, fails, _ = self.judge(None, [])
        self.assertFalse(ok)
        self.assertIn("no deck produced", fails)


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
