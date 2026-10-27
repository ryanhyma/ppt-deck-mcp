"""End-to-end authentication of the HTTP surface, through the real ASGI app.

These drive Starlette's TestClient against `server.build_http_app()`, so they cover the middleware,
the route policy and the identity context together — the parts that are easy to wire *almost* right
(a protected route that still answers 200, an image that bypasses the gate, a bearer that is accepted
but ignored).

Modes under test:

* ``none``  — development: everything open, and the server refuses this mode under PPT_ENV=prod.
* ``token`` — shared bearer for tool traffic; images only through signed URLs.
* ``entra`` — the same wiring with a real JWT verifier behind it (minted by the stub IdP).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "upstream"))

from _env import TMP  # noqa: E402,F401

import auth  # noqa: E402
import entra_stub  # noqa: E402

TOKEN = "test-bearer-0123456789"


def _query(url: str) -> tuple[str, str, str, str]:
    """(exp, sig, subject, owner) from a signed URL."""
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(url).query)
    return (query["exp"][0], query["sig"][0], query.get("sub", [""])[0], query.get("owner", [""])[0])
_SHARED_TOKEN = "shared-secret-for-tests"


_CLIENT = None


def client():
    """One shared TestClient for the module.

    The MCP streamable-HTTP session manager may only be started once per process, so the app (and its
    lifespan) is created once. That is enough for these tests because the gate reads the auth mode at
    request time — each test flips the environment, then makes requests.
    """
    global _CLIENT
    if _CLIENT is None:
        from starlette.testclient import TestClient

        import server

        _CLIENT = TestClient(server.build_http_app())
        _CLIENT.__enter__()
    return _CLIENT


def build_deck_and_preview():
    """Create one deck + preview while auth is off, returning (deck_id, download_url, slide_url)."""
    os.environ["PPT_AUTH_MODE"] = "none"
    auth.reset_verifier()
    import server

    tools = {n: t.fn for n, t in server.app._tool_manager._tools.items()}
    built = tools["build_deck"](template="corporate-2slide.pptx", title="Auth test",
                                slides=[{"title": "One", "bullets": ["a", "b"]}])
    data = tools["preview_deck"](file=built["deck_id"])
    return data["deck_id"], data["download"], data["slides"][0]["url"]


class TokenModeRegressions(unittest.TestCase):
    def setUp(self):
        os.environ["PPT_AUTH_MODE"] = "token"
        os.environ["PPT_MCP_TOKEN"] = _SHARED_TOKEN
        os.environ.pop("PPT_ENV", None)
        auth.reset_verifier()
        self.deck_id, _, _ = build_deck_and_preview()
        os.environ["PPT_AUTH_MODE"] = "token"
        self.client = client()

    def tearDown(self):
        for key in ("PPT_AUTH_MODE", "PPT_MCP_TOKEN", "PPT_ENV"):
            os.environ.pop(key, None)
        auth.reset_verifier()

    def test_the_mcp_endpoint_requires_a_bearer(self):
        response = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(response.status_code, 401)
        challenge = response.headers.get("www-authenticate", "")
        self.assertIn('error="invalid_token"', challenge)
        # A shared-token deployment has no authorization server, so the challenge must NOT advertise
        # one: LibreChat reads that as "OAuth available", reports OAuth Required and never lists tools.
        self.assertNotIn("resource_metadata=", challenge)

    def test_a_wrong_bearer_is_refused(self):
        response = self.client.post("/mcp", headers={"Authorization": "Bearer nope"},
                                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(response.status_code, 401)

    def test_a_valid_bearer_gets_through(self):
        response = self.client.post("/mcp", headers={"Authorization": f"Bearer {_SHARED_TOKEN}"},
                                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertNotEqual(response.status_code, 401, response.text)

    def test_slide_images_are_not_open_any_more(self):
        """The browser has no bearer, so an unsigned image URL must be refused."""
        response = self.client.get(f"/decks/{self.deck_id}/slide-1.png")
        self.assertEqual(response.status_code, 401)

    def test_a_signed_image_url_works_without_a_bearer(self):
        """The browser has no bearer: the signature, the expiry and the owner are what authorise it."""
        import deck_preview

        signed = deck_preview.signed_url(self.deck_id, "slide-1.png")
        self.assertIsNotNone(deck_preview.deck_asset_path(
            self.deck_id, "slide-1.png", *_query(signed)))
        response = self.client.get(signed.replace("http://test.local:8931", ""))
        self.assertEqual(response.status_code, 200)

    def test_a_signed_download_link_works_in_the_browser(self):
        """The Download button runs in the browser, which has no bearer: the signature authorises it —
        and a link without one is still refused."""
        import deck_preview

        signed = deck_preview.signed_url(self.deck_id, "deck.pptx")
        self.assertIsNotNone(deck_preview.deck_asset_path(self.deck_id, "deck.pptx", *_query(signed)))
        self.assertEqual(self.client.get(signed.replace("http://test.local:8931", "")).status_code, 200)
        unsigned = f"/decks/{self.deck_id}/deck.pptx"
        self.assertEqual(self.client.get(unsigned).status_code, 401)

    def test_health_stays_open_and_the_metadata_document_is_absent_by_default(self):
        """`/healthz` is liveness. The PRM document exists only when an AS is advertised: in token mode
        there is no browser OAuth, and its mere presence makes LibreChat 0.8.9 fail every tool call."""
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/.well-known/oauth-protected-resource").status_code, 404)

    def test_app_assets_stay_public_by_design(self):
        """The pane is HTML/JS the browser fetches with no bearer; it holds no user data."""
        self.assertEqual(self.client.get("/apps/deck_studio/app.js").status_code, 200)


class EntraModeRegressions(unittest.TestCase):
    """The same wiring with a JWT verifier: proves the gate uses the verifier, not just presence."""

    def setUp(self):
        self.idp = entra_stub.StubIdP()
        os.environ.update({
            "PPT_AUTH_MODE": "entra",
            "PPT_ENTRA_TENANT_ID": self.idp.tenant,
            "PPT_ENTRA_AUDIENCE": self.idp.audience,
            "PPT_ENTRA_REQUIRED_SCOPES": "Decks.Read",
            "PPT_ENTRA_JWKS_JSON": self.idp.jwks_json(),
        })
        os.environ.pop("PPT_ENV", None)
        os.environ.pop("PPT_MCP_TOKEN", None)
        auth.reset_verifier()
        self.deck_id, _, _ = build_deck_and_preview()
        os.environ["PPT_AUTH_MODE"] = "entra"
        auth.reset_verifier()
        self.client = client()

    def tearDown(self):
        for key in ("PPT_AUTH_MODE", "PPT_ENV", "PPT_ENTRA_TENANT_ID", "PPT_ENTRA_AUDIENCE",
                    "PPT_ENTRA_REQUIRED_SCOPES", "PPT_ENTRA_JWKS_JSON"):
            os.environ.pop(key, None)
        auth.reset_verifier()

    def test_prm_advertisement_can_be_disabled_for_legacy_clients(self):
        """LibreChat v0.8.9 treats any 401 with a PRM pointer as "start OAuth" and then fails.

        `PPT_ADVERTISE_PRM=0` keeps Entra-mode validation but stops advertising the authorization
        server, so a client that cannot complete the flow (and does not need to, because a gateway
        supplies the token) keeps working.
        """
        os.environ["PPT_ADVERTISE_PRM"] = "0"
        try:
            response = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("resource_metadata=", response.headers.get("www-authenticate", ""))
        finally:
            os.environ.pop("PPT_ADVERTISE_PRM", None)

    def test_the_challenge_points_at_protected_resource_metadata_in_entra_mode(self):
        response = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("resource_metadata=", response.headers.get("www-authenticate", ""))

    def test_the_metadata_document_only_advertises_an_authorization_server_on_request(self):
        """A client that discovers an AS will try to run an OAuth flow it may be unable to finish.

        LibreChat v0.8.9-rc1 does exactly that and then reports "OAuth authentication failed", so the
        AS is published only when the deployment wants browser-based OAuth clients (PPT_ADVERTISE_AS=1).
        """
        os.environ["PPT_ENTRA_ISSUER"] = "http://idp.example/realms/deck"
        try:
            self.assertEqual(self.client.get("/.well-known/oauth-protected-resource").status_code, 404,
                             "no metadata document when no authorization server is advertised")
            os.environ["PPT_ADVERTISE_AS"] = "1"
            advertised = self.client.get("/.well-known/oauth-protected-resource").json()
            self.assertEqual(advertised.get("authorization_servers"), ["http://idp.example/realms/deck"])
        finally:
            os.environ.pop("PPT_ADVERTISE_AS", None)
            os.environ.pop("PPT_ENTRA_ISSUER", None)

    def test_a_graph_token_cannot_open_the_mcp_endpoint(self):
        response = self.client.post("/mcp", headers={"Authorization": f"Bearer {self.idp.for_graph()}"},
                                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(response.status_code, 401)

    def test_a_real_token_gets_through(self):
        token = self.idp.token(oid="user-1")
        response = self.client.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertNotEqual(response.status_code, 401, response.text)

    def test_a_token_without_the_scope_is_forbidden(self):
        token = self.idp.token(oid="user-1", scopes=("Something.Else",))
        response = self.client.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(response.status_code, 403)


class OwnershipRegressions(unittest.TestCase):
    """A deck id belongs to the subject that created it."""

    def setUp(self):
        self.saved = (os.environ.get("PPT_AUTH_MODE"), os.environ.get("PPT_MCP_TOKEN"))
        os.environ["PPT_AUTH_MODE"] = "token"
        os.environ["PPT_MCP_TOKEN"] = _SHARED_TOKEN
        auth.reset_verifier()
        self.alice = auth.Identity(subject="alice", tenant="t1", mode="entra")
        self.bob = auth.Identity(subject="bob", tenant="t1", mode="entra")
        import server

        self.tools = {n: t.fn for n, t in server.app._tool_manager._tools.items()}

        token = auth.set_current(self.alice)
        try:
            built = self.tools["build_deck"](template="corporate-2slide.pptx", title="Alice deck",
                                             slides=[{"title": "A", "bullets": ["x"]}])
        finally:
            auth.reset_current(token)
        self.deck_id = built["deck_id"]

    def tearDown(self):
        mode, token = self.saved
        if mode is None:
            os.environ.pop("PPT_AUTH_MODE", None)
        else:
            os.environ["PPT_AUTH_MODE"] = mode
        if token is None:
            os.environ.pop("PPT_MCP_TOKEN", None)
        else:
            os.environ["PPT_MCP_TOKEN"] = token
        auth.reset_verifier()

    def test_the_owner_can_use_the_deck(self):
        token = auth.set_current(self.alice)
        try:
            self.assertEqual(self.tools["get_deck_outline"](file=self.deck_id)["slide_count"], 2)
        finally:
            auth.reset_current(token)

    def test_another_subject_cannot(self):
        token = auth.set_current(self.bob)
        try:
            for tool, args in (("get_deck_outline", {}), ("preview_deck", {}),
                              ("style_slide", {"slide_index": 1, "title_size": 30})):
                with self.subTest(tool=tool), self.assertRaises(FileNotFoundError):
                    self.tools[tool](file=self.deck_id, **args)
        finally:
            auth.reset_current(token)

    def test_ownership_is_off_when_authentication_is_off(self):
        """The single-user dev box keeps working without ceremony."""
        os.environ["PPT_AUTH_MODE"] = "none"
        auth.reset_verifier()
        token = auth.set_current(auth.ANONYMOUS)
        try:
            self.assertEqual(self.tools["get_deck_outline"](file=self.deck_id)["slide_count"], 2)
        finally:
            auth.reset_current(token)

    def test_the_render_cache_is_namespaced_per_subject(self):
        """Two subjects holding identical content must not share rendered files."""
        import render_cache

        deck = TMP / "out" / "same.pptx"
        shutil.copyfile(ROOT / "templates" / "corporate-2slide.pptx", deck)
        parts = ["/ppt/slides/slide1.xml", "/ppt/slides/slide2.xml"]
        alice = render_cache.slide_keys(deck, parts, "engine", 1600, scope="alice-scope")
        bob = render_cache.slide_keys(deck, parts, "engine", 1600, scope="bob-scope")
        self.assertNotEqual(alice, bob)
        self.assertEqual(alice, render_cache.slide_keys(deck, parts, "engine", 1600, scope="alice-scope"))


def tearDownModule():
    global _CLIENT
    os.environ.pop("PPT_AUTH_MODE", None)
    auth.reset_verifier()
    if _CLIENT is not None:
        _CLIENT.__exit__(None, None, None)
        _CLIENT = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
