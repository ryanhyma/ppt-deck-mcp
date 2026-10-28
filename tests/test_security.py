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

import auth  # noqa: E402
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
        with self.assertRaises(FileNotFoundError):
            TOOLS["preview_deck"](file=str(outside))
        after = {p.name for p in deck_preview.preview_root().iterdir()}
        self.assertEqual(before, after, "a rejected path must not create a preview directory")

    def test_import_deck_refuses_an_outside_file(self):
        with self.assertRaises(FileNotFoundError):
            TOOLS["import_deck"](file=str(_outside_deck()))

    def test_tools_that_edit_a_deck_use_the_same_scope(self):
        """get_deck_outline / style_slide / style_text / verify_deck take a deck id, never a path."""
        outside = str(_outside_deck())
        for tool, extra in (("get_deck_outline", {}), ("style_slide", {"slide_index": 1, "title_size": 30}),
                            ("style_text", {"slide_index": 1, "phrase": "x"}),
                            ("verify_deck", {"expected_slides": 2})):
            with self.subTest(tool=tool), self.assertRaises(FileNotFoundError):
                TOOLS[tool](file=outside, **extra)

    def test_an_outside_image_cannot_be_embedded_in_a_deck(self):
        outside_png = Path(tempfile.mkdtemp(prefix="outside-img-")) / "x.png"
        outside_png.write_bytes(b"\x89PNG\r\n\x1a\n")
        # Images are addressed by the id render_svg returns; a filesystem path is refused outright.
        with self.assertRaises(ValueError):
            TOOLS["build_deck"](template="corporate-2slide.pptx", title="t",
                                slides=[{"title": "a", "image": str(outside_png)}])


def signed_query(url):
    """(exp, sig, subject, owner) from a signed URL."""
    query = parse_qs(urlsplit(url).query)
    return (query["exp"][0], query["sig"][0], query.get("sub", [""])[0], query.get("owner", [""])[0])


class StartupAndOwnershipRegressions(unittest.TestCase):
    """Findings from the security review of 2026-10-27 (subagent-led)."""

    def test_none_mode_is_refused_at_startup_in_production(self):
        """The guard lived only inside verifier(), which `none` mode never calls — so a production
        deployment with the default mode ran wide open. It has to fail when the app is built."""
        from starlette.testclient import TestClient

        os.environ["PPT_ENV"] = "prod"
        os.environ["PPT_AUTH_MODE"] = "none"
        try:
            with self.assertRaises(RuntimeError):
                TestClient(server.build_http_app())
        finally:
            os.environ.pop("PPT_ENV", None)
            os.environ.pop("PPT_AUTH_MODE", None)
            auth.reset_verifier()

    def test_list_decks_and_get_last_deck_are_owner_scoped(self):
        """Both used to return every deck in the workspace — titles, slide counts and, for the newest
        deck, per-slide labels — to any authenticated caller."""
        alice = auth.Identity(subject="alice", tenant="t1", mode="entra")
        bob = auth.Identity(subject="bob", tenant="t1", mode="entra")
        os.environ["PPT_AUTH_MODE"] = "token"
        os.environ["PPT_MCP_TOKEN"] = "tok"
        auth.reset_verifier()
        try:
            token = auth.set_current(alice)
            try:
                built = TOOLS["build_deck"](template="corporate-2slide.pptx", title="Alice secret",
                                            slides=[{"title": "Revealing title", "bullets": ["x"]}])
                TOOLS["preview_deck"](file=built["deck_id"])
            finally:
                auth.reset_current(token)

            token = auth.set_current(bob)
            try:
                listed = {row["deck_id"] for row in TOOLS["list_decks"]()["decks"]}
                last = TOOLS["get_last_deck"]()
            finally:
                auth.reset_current(token)
            self.assertNotIn(built["deck_id"], listed, "bob must not see alice's deck in the list")
            self.assertNotEqual(last.get("deck_id"), built["deck_id"],
                                "bob must not receive alice's deck content via get_last_deck")
            self.assertNotIn("Revealing title", json.dumps(last))

            token = auth.set_current(alice)
            try:
                self.assertIn(built["deck_id"], {row["deck_id"] for row in TOOLS["list_decks"]()["decks"]})
            finally:
                auth.reset_current(token)
        finally:
            os.environ.pop("PPT_AUTH_MODE", None)
            os.environ.pop("PPT_MCP_TOKEN", None)
            auth.reset_verifier()

    def test_the_download_url_is_usable_without_a_bearer_but_needs_its_signature(self):
        """The pane's Download button runs in the browser, which has no bearer — the .pptx has to be
        authorised by its signature, and an unsigned request must still be refused."""
        os.environ["PPT_AUTH_MODE"] = "token"
        os.environ["PPT_MCP_TOKEN"] = "tok"
        auth.reset_verifier()
        try:
            deck = _TMP / "out" / "download.pptx"
            shutil.copyfile(TEMPLATE, deck)
            data = TOOLS["preview_deck"](file=deck_preview.remember(deck))
            url = urlsplit(data["download"])
            exp, sig, sub, owner = signed_query(data["download"])
            name = url.path.rsplit("/", 1)[-1]
            served = deck_preview.deck_asset_path(data["deck_id"], name, exp, sig, sub, owner)
            self.assertIsNotNone(served)
            self.assertIsNone(deck_preview.deck_asset_path(data["deck_id"], name))
            # deck.json is not offered at all any more: it duplicates the tool result
            self.assertIsNone(deck_preview.deck_asset_path(data["deck_id"], "deck.json", exp, sig,
                                                           sub, owner))
        finally:
            os.environ.pop("PPT_AUTH_MODE", None)
            os.environ.pop("PPT_MCP_TOKEN", None)
            auth.reset_verifier()

    def test_a_signed_url_is_bound_to_the_full_identity_not_just_the_subject(self):
        """The owner digest (tenant + subject) is signed, so a link cannot be re-aimed at another
        identity — and it is no longer a no-op comparison against a tenant-less subject."""
        os.environ["PPT_AUTH_MODE"] = "token"
        os.environ["PPT_MCP_TOKEN"] = "tok"
        auth.reset_verifier()
        alice = auth.Identity(subject="alice", tenant="t1", mode="entra")
        try:
            token = auth.set_current(alice)
            try:
                deck = _TMP / "out" / "bound.pptx"
                shutil.copyfile(TEMPLATE, deck)
                data = TOOLS["preview_deck"](file=deck_preview.remember(deck))
            finally:
                auth.reset_current(token)
            exp, sig, sub, owner = signed_query(data["download"])
            self.assertEqual(owner, alice.scope_hint)
            self.assertIsNotNone(deck_preview.verify_url(data["deck_id"], "deck.pptx", exp, sig, sub, owner))
            # the same subject in another tenant has a different digest, and cannot use this link
            other = auth.Identity(subject="alice", tenant="t2", mode="entra")
            self.assertNotEqual(other.scope_hint, owner)
            self.assertIsNone(deck_preview.verify_url(data["deck_id"], "deck.pptx", exp, sig, sub,
                                                      other.scope_hint))
        finally:
            os.environ.pop("PPT_AUTH_MODE", None)
            os.environ.pop("PPT_MCP_TOKEN", None)
            auth.reset_verifier()


class NoPathLeakRegressions(unittest.TestCase):
    """The MCP surface hands out opaque ids, never filesystem paths.

    A returned path tells the caller where the box keeps its data, names the deck even when the user did
    not ask for that, and invites a follow-up call with that path — the pattern that made preview_deck a
    file-read primitive. The id -> file mapping lives in ``previews/<id>/.source``, which is not served.
    """

    def setUp(self):
        self.built = TOOLS["build_deck"](template="corporate-2slide.pptx", title="No leak",
                                         slides=[{"title": "One", "bullets": ["a", "b"]}])
        self.deck_id = self.built["deck_id"]

    def _offending_values(self, payload, where=""):
        """Every string in a tool result that looks like a filesystem path."""
        found = []
        if isinstance(payload, str):
            text = payload
            if str(_TMP) in text:
                found.append(f"{where}: {text[:80]}")
            elif text.startswith("/") or (len(text) > 2 and text[1] == ":" and text[2] in "\\/"):
                found.append(f"{where}: {text[:80]}")
        elif isinstance(payload, dict):
            for key, value in payload.items():
                found += self._offending_values(value, f"{where}.{key}")
        elif isinstance(payload, (list, tuple)):
            for i, value in enumerate(payload):
                found += self._offending_values(value, f"{where}[{i}]")
        return found

    def test_no_tool_result_contains_a_path(self):
        preview = TOOLS["preview_deck"](file=self.deck_id)
        results = {
            "build_deck": self.built,
            "preview_deck": preview,
            "get_last_deck": TOOLS["get_last_deck"](),
            "list_decks": TOOLS["list_decks"](),
            "get_deck_outline": TOOLS["get_deck_outline"](file=self.deck_id),
            "style_slide": TOOLS["style_slide"](file=self.deck_id, slide_index=2, title_size=34),
            "style_text": TOOLS["style_text"](file=self.deck_id, slide_index=2, phrase="One", bold=True),
            "verify_deck": TOOLS["verify_deck"](file=self.deck_id, expected_slides=2),
        }
        for name, payload in results.items():
            with self.subTest(tool=name):
                self.assertEqual(self._offending_values(payload, name), [])

    def test_the_metadata_file_on_disk_holds_no_path_either(self):
        """If it is ever served by mistake, it must not leak the host layout."""
        TOOLS["preview_deck"](file=self.deck_id)
        meta = json.loads((deck_preview.preview_root() / self.deck_id / "deck.json").read_text())
        self.assertEqual(self._offending_values(meta, "deck.json"), [])

    def test_the_id_round_trips_through_every_tool_that_takes_a_deck(self):
        TOOLS["style_slide"](file=self.deck_id, slide_index=2, body_size=20)
        self.assertTrue(TOOLS["verify_deck"](file=self.deck_id, expected_slides=2)["ok"])
        self.assertEqual(TOOLS["get_deck_outline"](file=self.deck_id)["slide_count"], 2)
        self.assertEqual(TOOLS["preview_deck"](file=self.deck_id)["deck_id"], self.deck_id)
        again = TOOLS["import_deck"](file=self.deck_id)
        self.assertEqual(again["slide_count"], 2)

    def test_unknown_ids_fail_clearly(self):
        with self.assertRaises(FileNotFoundError):
            TOOLS["get_deck_outline"](file="d0123456789abcdef")

    def test_the_source_record_is_not_reachable_over_http(self):
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, ".index"))
        self.assertTrue((deck_preview.preview_root() / ".index" / self.deck_id).is_file())


def _seed_template_preview(template: Path, _request=None) -> None:
    """Write the preview PNGs a renderer would produce, so gallery tests need no renderer."""
    directory = deck_preview.preview_root() / f"tpl-{deck_preview._deck_id(template)}"
    directory.mkdir(parents=True, exist_ok=True)
    total = len(deck_preview.Presentation(str(template)).slides)
    for index in range(1, total + 1):
        (directory / f"slide-{index}.png").write_bytes(b"\x89PNG\r\n\x1a\n")


class TemplateGalleryRegressions(unittest.TestCase):
    """The gallery renders templates lazily; a partial render must not be served as complete.

    Found by the LibreChat smoke test: a template preview ended up with only `slide-1.png` (another
    process was writing the same directory), and the gallery happily returned a `slide-2.png` URL that
    404'd — broken thumbnails in the pane, with the failure swallowed.
    """

    def setUp(self):
        self.out = deck_preview.preview_root() / f"tpl-{deck_preview._deck_id(TEMPLATE)}"
        if self.out.exists():
            shutil.rmtree(self.out)

    def test_a_partial_preview_is_re_rendered(self):
        """One slide present, two expected: the gallery must notice and render again.

        The renderer is stubbed, so this pins the *detection* logic (which broke live) on any host, with
        or without LibreOffice: the old code saw `slide-1.png` and considered the preview complete.
        """
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "slide-1.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # pretend the render died early
        rendered: list[int] = []
        original = deck_preview._render

        def fake_render(pptx, outdir, width=None):
            rendered.append(1)
            for index in (1, 2):
                (Path(outdir) / f"slide-{index}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            return 2

        original_getter = deck_preview.render_warm.get_renderer
        deck_preview._render = fake_render
        # Force the cold chain so the stub is the only renderer on every host: on a box with
        # LibreOffice the warm renderer would succeed and never call `_render`, making this
        # assertion pass or fail depending on the machine.
        deck_preview.render_warm.get_renderer = lambda **kwargs: None
        try:
            urls = deck_preview.template_preview_urls(TEMPLATE)
        finally:
            deck_preview._render = original
            deck_preview.render_warm.get_renderer = original_getter
        self.assertTrue(rendered, "a partial preview must trigger a re-render")
        self.assertEqual(len(urls), 2, "the gallery must advertise the full set")
        for url in urls:
            name = url.rsplit("/", 1)[-1]
            self.assertTrue((self.out / name).is_file(), f"{name} advertised but absent")

    def _seed(self):
        _seed_template_preview(TEMPLATE, None)

    def test_gallery_urls_are_usable_when_authentication_is_on(self):
        self._seed()
        """With auth enabled, only signed URLs are served — the template gallery must mint them too.

        Found live: the pane showed broken thumbnails because template URLs stayed unsigned while
        image requests started requiring a signature.
        """
        original = os.environ.get("PPT_AUTH_MODE")
        os.environ["PPT_AUTH_MODE"] = "token"
        os.environ["PPT_MCP_TOKEN"] = "test-token"
        import auth

        auth.reset_verifier()
        try:
            urls = deck_preview.template_preview_urls(TEMPLATE)
            self.assertTrue(urls)
            for url in urls:
                query = parse_qs(urlsplit(url).query)
                name = url.split("?")[0].rsplit("/", 1)[-1]
                served = deck_preview.deck_asset_path(
                    f"tpl-{deck_preview._deck_id(TEMPLATE)}", name,
                    query.get("exp", [None])[0], query.get("sig", [None])[0],
                    query.get("sub", [""])[0], query.get("owner", [""])[0])
                self.assertIsNotNone(served, f"unsigned gallery URL under auth: {url}")
        finally:
            if original is None:
                os.environ.pop("PPT_AUTH_MODE", None)
            else:
                os.environ["PPT_AUTH_MODE"] = original
            os.environ.pop("PPT_MCP_TOKEN", None)
            auth.reset_verifier()

    def test_gallery_ids_are_reachable_through_the_asset_route(self):
        self._seed()
        """Template previews live under `tpl-<id>`; the route's id rule must accept that shape."""
        deck_preview.template_preview_urls(TEMPLATE)
        deck_id = f"tpl-{deck_preview._deck_id(TEMPLATE)}"
        served = deck_preview.deck_asset_path(deck_id, "slide-1.png")
        self.assertIsNotNone(served, f"route refuses the gallery id {deck_id!r}")

    def test_every_advertised_url_exists(self):
        self._seed()
        urls = deck_preview.template_preview_urls(TEMPLATE)
        for url in urls:
            name = url.rsplit("/", 1)[-1]
            self.assertTrue((self.out / name).is_file(), f"{name} advertised but absent")


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
        self.data = TOOLS["preview_deck"](file=deck_preview.remember(deck))
        self.deck_id = self.data["deck_id"]
        self.directory = deck_preview.preview_root() / self.deck_id
        (self.directory / "slide-1.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # renderer may be absent

    def test_slide_images_are_served_by_name(self):
        self.assertIsNotNone(deck_preview.deck_asset_path(self.deck_id, "slide-1.png"))

    def test_metadata_file_is_not_served(self):
        """deck.json leaked absolute host paths."""
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.json"))

    def _query(self, url):
        query = parse_qs(urlsplit(url).query)
        return query["exp"][0], query["sig"][0], query.get("sub", [""])[0], query.get("owner", [""])[0]

    def test_deck_file_requires_a_signature_and_serves_with_one(self):
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.pptx"))
        exp, sig, sub, owner = self._query(self.data["download"])
        served = deck_preview.deck_asset_path(self.deck_id, "deck.pptx", exp, sig, sub, owner)
        self.assertIsNotNone(served)
        self.assertEqual(served.name, "deck.pptx")

    def test_a_link_is_bound_to_the_identity_it_was_minted_for(self):
        """The owner digest is inside the HMAC: swapping it (or the subject) breaks the signature."""
        exp, sig, sub, owner = self._query(self.data["download"])
        self.assertIsNotNone(deck_preview.deck_asset_path(self.deck_id, "deck.pptx", exp, sig, sub, owner))
        # a different owner claim no longer matches the HMAC
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.pptx", exp, sig, sub, "deadbeef"))
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.pptx", exp, sig, "someone-else", owner))

    def test_expired_and_tampered_signatures_are_refused(self):
        import time as _time

        exp, sig, sub, owner = self._query(self.data["download"])
        past = int(_time.time()) - 10
        stale = deck_preview.sign_asset(self.deck_id, "deck.pptx", past, sub, owner)
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.pptx", str(past), stale, sub, owner))
        tampered = sig[:-1] + ("1" if sig[-1] != "1" else "2")  # never a no-op
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.pptx", exp, tampered, sub, owner))
        # A signature minted for one file must not unlock another.
        self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.json", exp, sig, sub, owner))

    def test_traversal_and_unknown_ids_are_refused(self):
        for deck_id, name in (("../../etc", "passwd"), (self.deck_id, "../../../etc/passwd"),
                              (self.deck_id, ".cache"), ("not-a-deck", "slide-1.png"),
                              (self.deck_id, "slide-1.png.bak")):
            with self.subTest(deck_id=deck_id, name=name):
                self.assertIsNone(deck_preview.deck_asset_path(deck_id, name))

    def test_the_signature_key_is_configurable(self):
        exp, sig, sub, owner = self._query(self.data["download"])
        original = os.environ.get("PPT_URL_SECRET")
        try:
            os.environ["PPT_URL_SECRET"] = "a-different-key"
            self.assertIsNone(deck_preview.deck_asset_path(self.deck_id, "deck.pptx", exp, sig, sub, owner))
        finally:
            if original is None:
                os.environ.pop("PPT_URL_SECRET", None)
            else:
                os.environ["PPT_URL_SECRET"] = original


class PreviewMetadataRegressions(unittest.TestCase):
    def test_deck_json_on_disk_carries_no_open_download_path(self):
        deck = _TMP / "out" / "meta.pptx"
        shutil.copyfile(TEMPLATE, deck)
        data = TOOLS["preview_deck"](file=deck_preview.remember(deck))
        on_disk = json.loads((deck_preview.preview_root() / data["deck_id"] / "deck.json").read_text())
        self.assertIn("sig=", on_disk["download"])
        for slide in on_disk["slides"]:
            self.assertRegex(slide["url"], r"^http://test\.local:8931/decks/d[0-9a-f]{16}/slide-\d+\.png$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
