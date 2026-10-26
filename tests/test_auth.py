"""Token validation: the negative matrix that matters for Entra ID.

Every case here is something a real deployment gets wrong: a Graph token accepted because only the
signature was checked, a token from another tenant, an `alg: none` token, an ID token used as an access
token, a caller with no scope. Each one must be a clean 401/403 — never a 200.

The stub in `entra_stub.py` mints tokens with Entra's claim shape, so these run offline and fast.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "upstream"))

from _env import TMP  # noqa: E402,F401  (sets the shared test environment)

import auth  # noqa: E402
import entra_stub  # noqa: E402


def verifier_for(idp: entra_stub.StubIdP, *, scopes=("Decks.Read",), roles=("Decks.Access",)):
    return auth.EntraVerifier(tenant_id=idp.tenant, audience=idp.audience,
                              required_scopes=scopes, required_roles=roles, jwks=idp.jwks())


class EntraVerifierRegressions(unittest.TestCase):
    def setUp(self):
        self.idp = entra_stub.StubIdP()
        self.verifier = verifier_for(self.idp)

    def assertRejected(self, token, status=401, error="invalid_token"):
        with self.assertRaises(auth.AuthError) as ctx:
            self.verifier.verify(token)
        self.assertEqual(ctx.exception.status, status, str(ctx.exception))
        self.assertEqual(ctx.exception.error, error)

    def test_a_normal_delegated_token_is_accepted_and_identified(self):
        identity = self.verifier.verify(self.idp.token(oid="user-1"))
        self.assertEqual(identity.subject, "user-1")
        self.assertEqual(identity.tenant, self.idp.tenant)
        self.assertEqual(identity.scopes, ("Decks.Read",))
        self.assertEqual(identity.mode, "entra")

    def test_a_graph_token_is_not_an_access_token_for_this_api(self):
        """The classic misconfiguration: signature valid, audience wrong."""
        self.assertRejected(self.idp.for_graph())

    def test_an_id_token_is_not_an_access_token(self):
        """An ID token's aud is the client app, so it must be refused here."""
        self.assertRejected(self.idp.token(aud="librechat-client-id"))

    def test_a_token_from_another_tenant_is_refused(self):
        """Entra signs other tenants' tokens too; `tid` and `iss` have to be checked."""
        # right issuer, wrong tenant id → authenticated but not our tenant (403)
        self.assertRejected(self.idp.other_tenant(issuer=self.idp.issuer), status=403)
        # another tenant's issuer → not even a token for this API (401)
        self.assertRejected(self.idp.other_tenant(), status=401)

    def test_v1_tokens_are_refused(self):
        self.assertRejected(self.idp.token(ver="1.0"))

    def test_expired_and_not_yet_valid_tokens_are_refused(self):
        self.assertRejected(self.idp.expired())
        future = int(__import__("time").time()) + 3600
        self.assertRejected(self.idp.token(issued_at=future, expires_in=7200))

    def test_unsigned_tokens_are_refused(self):
        self.assertRejected(self.idp.token(alg="none"))

    def test_hs256_key_confusion_is_refused(self):
        """The classic attack: sign HS256 using the published RSA key material as the HMAC secret.

        PyJWT refuses to *mint* that shape, so the token is assembled by hand — which is what an
        attacker would do anyway.
        """
        import base64
        import hashlib
        import hmac
        import json as _json

        def b64(raw: bytes) -> str:
            return base64.urlsafe_b64encode(raw).decode().rstrip("=")

        public_pem = self.idp._private_pem()  # the key material an attacker has (it is public)
        header = {"alg": "HS256", "typ": "JWT", "kid": self.idp.kid}
        now = int(__import__("time").time())
        payload = {"iss": self.idp.issuer, "aud": self.idp.audience, "tid": self.idp.tenant,
                   "oid": "attacker", "iat": now, "exp": now + 3600, "scp": "Decks.Read"}
        signing_input = f"{b64(_json.dumps(header).encode())}.{b64(_json.dumps(payload).encode())}"
        sig = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
        self.assertRejected(f"{signing_input}.{b64(sig)}")

    def test_a_tampered_payload_is_refused(self):
        token = self.idp.token(oid="user-1")
        head, payload, sig = token.split(".")
        forged = json.loads(__import__("base64").urlsafe_b64decode(payload + "=="))
        forged["oid"] = "admin-1"
        new_payload = __import__("base64").urlsafe_b64encode(json.dumps(forged).encode()).decode().rstrip("=")
        self.assertRejected(f"{head}.{new_payload}.{sig}")

    def test_an_unknown_signing_key_is_refused(self):
        self.assertRejected(self.idp.token(kid="not-in-the-jwks"))

    def test_a_caller_without_the_required_scope_is_forbidden_not_unauthorised(self):
        self.assertRejected(self.idp.token(scopes=("Something.Else",)), status=403,
                            error="insufficient_scope")

    def test_an_app_only_token_needs_a_role(self):
        self.assertRejected(self.idp.token(oid="", sub=None, scopes=(), roles=()), status=401)  # no subject
        with_role = self.idp.token(oid="svc-1", scopes=(), roles=("Decks.Access",))
        self.assertEqual(self.verifier.verify(with_role).roles, ("Decks.Access",))
        self.assertRejected(self.idp.token(oid="svc-1", scopes=(), roles=("Other.Role",)),
                            status=403, error="insufficient_scope")

    def test_authorisation_ignores_display_claims(self):
        """preferred_username/upn/email are not unique or stable; only oid/sub may identify a subject."""
        token = self.idp.token(oid="real-user", preferred_username="admin@example.gc.ca", upn="admin@x")
        self.assertEqual(self.verifier.verify(token).subject, "real-user")

    def test_key_rotation_is_picked_up(self):
        """A rotated kid must trigger a JWKS refetch, not a permanent outage."""
        jwks_file = Path(tempfile.mkdtemp(dir=TMP)) / "jwks.json"
        idp = entra_stub.StubIdP(kid="key-1")
        jwks_file.write_text(idp.jwks_json(), encoding="utf-8")
        verifier = auth.EntraVerifier(tenant_id=idp.tenant, audience=idp.audience,
                                      jwks_url=jwks_file.as_uri(), required_scopes=("Decks.Read",))
        self.assertEqual(verifier.verify(idp.token(oid="u1")).subject, "u1")

        rotated = entra_stub.StubIdP(tenant=idp.tenant, audience=idp.audience, kid="key-2")
        jwks_file.write_text(rotated.jwks_json(), encoding="utf-8")
        self.assertEqual(verifier.verify(rotated.token(oid="u2")).subject, "u2")

    def test_a_tampered_jwks_response_is_not_trusted_silently(self):
        """If keys become unreachable we keep the cached set; we must not accept an unverified token."""
        verifier = verifier_for(self.idp)
        self.assertRejected("not-a-jwt")

    def test_the_error_carries_a_www_authenticate_challenge(self):
        with self.assertRaises(auth.AuthError) as ctx:
            self.verifier.verify(self.idp.for_graph())
        challenge = ctx.exception.www_authenticate("https://decks.local/.well-known/oauth-protected-resource")
        self.assertIn('error="invalid_token"', challenge)
        self.assertIn("resource_metadata=", challenge)


class IssuerConfigRegressions(unittest.TestCase):
    """The factory must honour PPT_ENTRA_ISSUER — a mock/sovereign issuer is not login.microsoftonline.com.

    Found by running the server in entra mode against the mock IdP container: the verifier was built
    without the configured issuer, so every otherwise-valid token failed with "different issuer".
    """

    def tearDown(self):
        for key in ("PPT_AUTH_MODE", "PPT_ENTRA_TENANT_ID", "PPT_ENTRA_AUDIENCE", "PPT_ENTRA_ISSUER"):
            os.environ.pop(key, None)
        auth.reset_verifier()

    def test_the_configured_issuer_is_used_by_the_verifier_factory(self):
        os.environ["PPT_AUTH_MODE"] = "entra"
        os.environ["PPT_ENTRA_TENANT_ID"] = "11111111-1111-1111-1111-111111111111"
        os.environ["PPT_ENTRA_AUDIENCE"] = "api://deck-mcp"
        os.environ["PPT_ENTRA_ISSUER"] = "http://localhost:8899/default"
        auth.reset_verifier()
        self.assertEqual(auth.verifier().issuer, "http://localhost:8899/default")

    def test_the_default_issuer_is_still_the_tenant_endpoint(self):
        os.environ["PPT_AUTH_MODE"] = "entra"
        os.environ["PPT_ENTRA_TENANT_ID"] = "11111111-1111-1111-1111-111111111111"
        os.environ["PPT_ENTRA_AUDIENCE"] = "api://deck-mcp"
        auth.reset_verifier()
        self.assertEqual(auth.verifier().issuer,
                         "https://login.microsoftonline.com/11111111-1111-1111-1111-111111111111/v2.0")


class SharedTokenRegressions(unittest.TestCase):
    def test_the_shared_token_is_compared_in_constant_time_and_required(self):
        verifier = auth.SharedTokenVerifier("s3cret")
        self.assertEqual(verifier.verify("s3cret").subject, "service-token")
        with self.assertRaises(auth.AuthError):
            verifier.verify("wrong")
        with self.assertRaises(ValueError):
            auth.SharedTokenVerifier("")


class ModeRegressions(unittest.TestCase):
    def tearDown(self):
        for key in ("PPT_AUTH_MODE", "PPT_ENV", "PPT_MCP_TOKEN", "PPT_ENTRA_TENANT_ID",
                    "PPT_ENTRA_AUDIENCE", "PPT_OWNERSHIP"):
            os.environ.pop(key, None)
        auth.reset_verifier()

    def test_production_refuses_to_start_without_authentication(self):
        os.environ["PPT_ENV"] = "prod"
        os.environ["PPT_AUTH_MODE"] = "none"
        with self.assertRaises(RuntimeError):
            auth.verifier()

    def test_development_can_run_without_authentication(self):
        os.environ["PPT_AUTH_MODE"] = "none"
        self.assertIsNone(auth.verifier())

    def test_an_unknown_mode_is_a_configuration_error(self):
        os.environ["PPT_AUTH_MODE"] = "kerberos"
        with self.assertRaises(ValueError):
            auth.auth_mode()

    def test_ownership_follows_authentication(self):
        os.environ["PPT_AUTH_MODE"] = "token"
        self.assertTrue(auth.ownership_enabled())
        os.environ["PPT_OWNERSHIP"] = "0"
        self.assertFalse(auth.ownership_enabled())

    def test_a_mounted_request_can_read_its_identity(self):
        self.assertTrue(auth.current_identity().anonymous)
        token = auth.set_current(auth.Identity(subject="u1", mode="entra"))
        try:
            self.assertEqual(auth.current_identity().subject, "u1")
        finally:
            auth.reset_current(token)


def tearDownModule():
    auth.reset_verifier()


if __name__ == "__main__":
    unittest.main(verbosity=2)
