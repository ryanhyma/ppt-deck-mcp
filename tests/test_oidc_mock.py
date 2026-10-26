"""Integration test against the mock Entra ID container (`deploy/oidc/`).

What this adds over `tests/test_auth.py`: the verifier fetches **real keys over HTTP** from a real
discovery + JWKS endpoint, and the token comes from a real token endpoint rather than being minted in
process. That is the difference between "our claim logic is right" and "our claim logic is right when the
Authorization Server is a network service".

Skipped unless the container is reachable (start it with `deploy/oidc/docker-compose.yml`):

    docker compose -f deploy/oidc/docker-compose.yml up -d
    PPT_OIDC_MOCK_URL=http://localhost:8899 .venv/bin/python -m unittest tests.test_oidc_mock

Set `PPT_OIDC_REQUIRED=1` in CI to turn a missing container into a failure instead of a skip.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "upstream"))

from _env import TMP  # noqa: E402,F401

import auth  # noqa: E402

MOCK = os.environ.get("PPT_OIDC_MOCK_URL", "http://localhost:8899").rstrip("/")
ISSUER = f"{MOCK}/default"
TENANT = "11111111-1111-1111-1111-111111111111"
AUDIENCE = "api://deck-mcp"
SCOPE = f"{AUDIENCE}/Decks.Read"


def _get_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - local mock
        return json.loads(resp.read().decode())


def mock_available() -> bool:
    try:
        _get_json(f"{ISSUER}/.well-known/openid-configuration")
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


AVAILABLE = mock_available()
REQUIRED = os.environ.get("PPT_OIDC_REQUIRED") == "1"


def mint(scope: str = SCOPE, client_id: str = "deck-studio") -> str:
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": client_id,
        "client_secret": "x", "scope": scope,
    }).encode()
    request = urllib.request.Request(f"{ISSUER}/token", data=body,
                                     headers={"content-type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=10) as resp:  # noqa: S310
        return json.loads(resp.read().decode())["access_token"]


@unittest.skipUnless(AVAILABLE or REQUIRED, f"mock IdP not reachable at {ISSUER} (see deploy/oidc/)")
class MockEntraIdPRegressions(unittest.TestCase):
    def setUp(self):
        if not AVAILABLE:  # pragma: no cover - only with PPT_OIDC_REQUIRED=1
            self.fail(f"PPT_OIDC_REQUIRED=1 but {ISSUER} is not reachable")
        self.verifier = auth.EntraVerifier(
            tenant_id=TENANT, audience=AUDIENCE, required_scopes=("Decks.Read",),
            issuer=ISSUER, jwks_url=f"{ISSUER}/jwks",
        )

    def test_discovery_serves_the_documents_a_client_needs(self):
        config = _get_json(f"{ISSUER}/.well-known/openid-configuration")
        self.assertEqual(config["issuer"], ISSUER)
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            self.assertIn(key, config)
        self.assertIn("S256", config.get("code_challenge_methods_supported", []))

    def test_a_real_token_from_the_container_is_accepted(self):
        """Signature verified against keys fetched over HTTP; claims must match what we configured."""
        identity = self.verifier.verify(mint())
        self.assertEqual(identity.subject, "svc-deck-builder")
        self.assertEqual(identity.tenant, TENANT)
        self.assertIn("Decks.Read", identity.scopes)

    def test_the_jwks_is_actually_fetched_over_the_network(self):
        """Guard against 'it passes because keys were injected' — this verifier was given only a URL."""
        self.assertIsNone(self.verifier._jwks, "keys must not be preloaded for this test")
        self.verifier.verify(mint())
        self.assertIsNotNone(self.verifier._jwks)

    def test_a_token_for_another_audience_is_refused(self):
        """The mock lets the client pick a scope; a token minted for a different API must not verify."""
        token = mint(scope="api://something-else/Decks.Read")
        with self.assertRaises(auth.AuthError) as ctx:
            self.verifier.verify(token)
        self.assertEqual(ctx.exception.status, 401)

    def test_a_verifier_pointed_at_another_tenant_refuses_the_token(self):
        other = auth.EntraVerifier(tenant_id="22222222-2222-2222-2222-222222222222",
                                   audience=AUDIENCE, required_scopes=("Decks.Read",),
                                   issuer=f"{MOCK}/other-tenant", jwks_url=f"{ISSUER}/jwks")
        with self.assertRaises(auth.AuthError):
            other.verify(mint())

    def test_a_garbage_bearer_is_refused(self):
        with self.assertRaises(auth.AuthError):
            self.verifier.verify("not-a-token")


if __name__ == "__main__":
    unittest.main(verbosity=2)
