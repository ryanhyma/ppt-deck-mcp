"""Security regressions: what a caller may read, and what the asset route may serve.

Two findings from the 2026-10-26 review are pinned here:

1. `preview_deck` accepted **any** existing `.pptx` path, then copied it into the previews directory
   where it could be downloaded — an arbitrary file-read primitive.
2. `/decks/<id>/<name>` served every file in the preview directory, including `deck.json` (absolute
   host paths) and `deck.pptx` (the user's content), with a filename-derived, guessable deck id.

Nothing in this file needs a renderer.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(HERE))
from _env import TMP as _TMP  # noqa: E402

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "upstream"))

import deck_preview  # noqa: E402
import paths  # noqa: E402
import server  # noqa: E402

TOOLS = {name: tool.fn for name, tool in server.app._tool_manager._tools.items()}
TEMPLATE = ROOT / "templates" / "corporate-2slide.pptx"


def _outside_deck() -> Path:
    """A real .pptx that lives outside every allowed root."""
    outside_dir = Path(tempfile.mkdtemp(prefix="outside-"))  # /tmp/outside-XXXX, not under _TMP
    target = outside_dir / "secret.pptx"
    shutil.copyfile(TEMPLATE, target)
    return target


class PathScopeRegressions(unittest.TestCase):
    def test_a_deck_outside_the_allowed_roots_is_refused(self):
        with self.assertRaises(paths.PathNotAllowed):
            paths.resolve_deck(_outside_deck())

    def test_the_error_says_where_decks_may_come_from(self):
        try:
            paths.resolve_deck(_outside_deck())
        except paths.PathNotAllowed as exc:
            self.assertIn(str(_TMP), str(exc))
        else:
            self.fail("expected PathNotAllowed")

    def test_a_missing_path_is_a_not_found_not_a_permission_error(self):
        with self.assertRaises(FileNotFoundError):
            paths.resolve_deck(str(_TMP / "out" / "nope.pptx"))

    def test_a_symlink_cannot_escape_an_allowed_root(self):
        """Resolution happens before the containment check, so a link inside uploads is still out of bounds."""
        link = _TMP / "uploads" / "user" / "innocent.pptx"
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(_outside_deck())
        with self.assertRaises(paths.PathNotAllowed):
            paths.resolve_deck(link)

    def test_preview_deck_does_not_copy_an_outside_file(self):
        outside = _outside_deck()
        before = {p.name for p in deck_preview.preview_root().iterdir()}
        with self.assertRaises(paths.PathNotAllowed):
            TOOLS["preview_deck"](file=str(outside))
        after = {p.name for p in deck_preview.preview_root().iterdir()}
        self.assertEqual(before, after, "a rejected path must not create a preview directory")

    def test_import_deck_refuses_an_outside_file(self):
        with self.assertRaises(paths.PathNotAllowed):
            TOOLS["import_deck"](file=str(_outside_deck()))

    def test_tools_that_edit_a_deck_use_the_same_scope(self):
        """get_deck_outline / style_slide / style_text / verify_deck all take a path."""
        outside = str(_outside_deck())
        for tool, extra in (("get_deck_outline", {}), ("style_slide", {"slide_index": 1, "title_size": 30}),
                            ("style_text", {"slide_index": 1, "phrase": "x"}),
                            ("verify_deck", {"expected_slides": 2})):
            with self.subTest(tool=tool), self.assertRaises(paths.PathNotAllowed):
                TOOLS[tool](file=outside, **extra)

    def test_an_outside_image_cannot_be_embedded_in_a_deck(self):
        outside_png = Path(tempfile.mkdtemp(prefix="outside-img-")) / "x.png"
        outside_png.write_bytes(b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(paths.PathNotAllowed):
            TOOLS["build_deck"](template="corporate-2slide.pptx", title="t",
                                slides=[{"title": "a", "image": str(outside_png)}])


class DeckIdRegressions(unittest.TestCase):
    def _deck(self, name: str) -> Path:
        folder = _TMP / "out" / "made"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        shutil.copyfile(TEMPLATE, target)
        return target

    def test_ids_are_opaque_and_stable(self):
        deck = self._deck("2025-hiring-plan.pptx")
        first, second = deck_preview._deck_id(deck), deck_preview._deck_id(deck)
        self.assertEqual(first, second, "the id must be stable: the cache and gallery depend on it")
        self.assertNotIn("hiring", first.lower())
        self.assertRegex(first, r"^d[0-9a-f]{16}$")

    def test_same_filename_in_different_folders_gets_different_ids(self):
        a = self._deck("deck.pptx")
        sub = _TMP / "out" / "made2"
        sub.mkdir(parents=True, exist_ok=True)
        b = sub / "deck.pptx"
        shutil.copyfile(TEMPLATE, b)
        self.assertNotEqual(deck_preview._deck_id(a), deck_preview._deck_id(b))


class AssetRouteRegressions(unittest.TestCase):
    """`deck_asset_path` is the whole policy for /decks/<deck_id>/<name>."""

    def setUp(self):
        deck = _TMP / "out" / "asset.pptx"
        shutil.copyfile(TEMPLATE, deck)
        self.data = TOOLS["preview_deck"](file=str(deck))
        self.deck_id = self.data["deck_id"]
        self.directory = deck_preview.preview_root() / self.deck_id
        (self.directory / "slide-1.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # renderer may be absent

    def test_slide_images_are_served_by_name(self):
        self.assertIsNotNone(deck_preview.deck_asset_path(self.deck_id, "slide-1.png"))

    def test_metadata_file_is_not_served(self):
        """deck.json leaked absolute host paths."""
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.json"))

    def test_deck_file_requires_a_signature_and_serves_with_one(self):
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.pptx"))
        url = urlsplit(self.data["download"])
        query = parse_qs(url.query)
        served = deck_preview.deck_asset_path(self.deck_id, "deck.pptx", query["exp"][0], query["sig"][0])
        self.assertIsNotNone(served)
        self.assertEqual(served.name, "deck.pptx")

    def test_expired_and_tampered_signatures_are_refused(self):
        import time as _time

        past = int(_time.time()) - 10
        sig = deck_preview.sign_asset(self.deck_id, "deck.pptx", past)
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.pptx", str(past), sig))
        future = int(_time.time()) + 600
        good = deck_preview.sign_asset(self.deck_id, "deck.pptx", future)
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.pptx", str(future), good[:-1] + "0"))
        # A signature minted for one deck must not unlock another file.
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.json", str(future), good))

    def test_traversal_and_unknown_ids_are_refused(self):
        for deck_id, name in (("../../etc", "passwd"), (self.deck_id, "../../../etc/passwd"),
                              (self.deck_id, ".cache"), ("not-a-deck", "slide-1.png"),
                              (self.deck_id, "slide-1.png.bak")):
            with self.subTest(deck_id=deck_id, name=name):
                self.assertIsNone(deck_preview.deck_asset_path(deck_id, name))

    def test_the_signature_key_is_configurable(self):
        url = urlsplit(self.data["download"])
        query = parse_qs(url.query)
        original = os.environ.get("PPT_URL_SECRET")
        try:
            os.environ["PPT_URL_SECRET"] = "a-different-key"
            self.assertIsNone(deck_preview.deck_asset_path(
                self.deck_id, "deck.pptx", query["exp"][0], query["sig"][0]))
        finally:
            if original is None:
                os.environ.pop("PPT_URL_SECRET", None)
            else:
                os.environ["PPT_URL_SECRET"] = original


class PreviewMetadataRegressions(unittest.TestCase):
    def test_deck_json_on_disk_carries_no_open_download_path(self):
        deck = _TMP / "out" / "meta.pptx"
        shutil.copyfile(TEMPLATE, deck)
        data = TOOLS["preview_deck"](file=str(deck))
        on_disk = json.loads((deck_preview.preview_root() / data["deck_id"] / "deck.json").read_text())
        self.assertIn("sig=", on_disk["download"])
        for slide in on_disk["slides"]:
            self.assertRegex(slide["url"], r"^http://test\.local:8931/decks/d[0-9a-f]{16}/slide-\d+\.png$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
