"""A tiny Entra-shaped identity provider for tests and offline validation.

Purpose: prove the verifier rejects what it must, without a cloud tenant and without the network. It
mints RS256 JWTs with the same *shape* Entra uses — ``iss`` = ``https://login.microsoftonline.com/<tid>/v2.0``,
``aud`` = the API's client id, plus ``tid``, ``oid``, ``scp``/``roles``, ``ver``, ``azp`` — and exposes the
matching JWKS so :class:`auth.EntraVerifier` can validate against it.

It is a test double, not an IdP:

* it never authenticates a user (tests mint whatever claims they need);
* it does not run in the server: the server either talks to a real Entra tenant or reads a static JWKS
  (``PPT_ENTRA_JWKS_JSON``) for an air-gapped dry run;
* ``tools/entra_stub_server.py`` wraps it in a JWKS endpoint for manual end-to-end checks.

Use it directly in tests::

    idp = StubIdP(tenant="1111…", audience="2222…")
    token = idp.token(oid="user-1", scopes=("Decks.Read",))
    auth.EntraVerifier(tenant_id=idp.tenant, audience=idp.audience, jwks=idp.jwks()).verify(token)
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

DEFAULT_SCOPE = "Decks.Read"
DEFAULT_ROLE = "Decks.Access"


@dataclass
class StubIdP:
    tenant: str = "11111111-1111-1111-1111-111111111111"
    audience: str = "22222222-2222-2222-2222-222222222222"
    kid: str = "stub-key-1"
    _key: Any = field(default=None, repr=False)

    def __post_init__(self):
        if self._key is None:
            self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # -- keys
    def _private_pem(self) -> bytes:
        return self._key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def jwks(self) -> dict:
        """The tenant JWKS document, as Entra would serve it."""
        from jwt.algorithms import RSAAlgorithm

        jwk = json.loads(RSAAlgorithm.to_jwk(self._key.public_key()))
        jwk.update({"kid": self.kid, "use": "sig", "kty": "RSA"})
        return {"keys": [jwk]}

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant}/v2.0"

    def jwks_json(self) -> str:
        return json.dumps(self.jwks())

    # -- tokens
    def token(self, *, oid: str | None = "user-0001", sub: str | None = "__derived__",
              scopes: Iterable[str] = (DEFAULT_SCOPE,),
              roles: Iterable[str] = (), audience: str | None = None, tenant: str | None = None,
              issuer: str | None = None, expires_in: int = 3600, issued_at: int | None = None,
              ver: str | None = "2.0", alg: str = "RS256", key=None, kid: str | None = None,
              azp: str = "librechat-client-id", **extra_claims) -> str:
        """Mint an access token. Every argument exists so a test can make it *wrong*."""
        import jwt

        now = int(time.time()) if issued_at is None else int(issued_at)
        claims: dict[str, Any] = {
            "iss": issuer or self.issuer,
            "aud": audience if audience is not None else self.audience,
            "tid": tenant or self.tenant,
            "oid": oid,
            "sub": (f"sub-{oid}" if sub == "__derived__" else sub),
            "azp": azp,
            "iat": now,
            "nbf": now,
            "exp": now + int(expires_in),
            "jti": uuid.uuid4().hex,
        }
        for claim in ("oid", "sub"):
            if claims.get(claim) is None:
                claims.pop(claim, None)
        if scopes:
            claims["scp"] = " ".join(scopes)
        if roles:
            claims["roles"] = list(roles)
        if ver:
            claims["ver"] = ver
        claims.update(extra_claims)

        pem = self._private_pem()
        headers = {"kid": kid if kid is not None else self.kid}
        if alg == "none":
            return jwt.encode(claims, key="", algorithm="none", headers=headers)
        return jwt.encode(claims, key=pem if key is None else key, algorithm=alg, headers=headers)

    # -- convenience wrongness
    def expired(self, **kwargs) -> str:
        kwargs.setdefault("expires_in", -3600)
        kwargs.setdefault("issued_at", int(time.time()) - 7200)
        return self.token(**kwargs)

    def for_graph(self, **kwargs) -> str:
        kwargs.setdefault("audience", "https://graph.microsoft.com")
        return self.token(**kwargs)

    def other_tenant(self, **kwargs) -> str:
        other = str(uuid.uuid4())
        kwargs.setdefault("tenant", other)
        kwargs.setdefault("issuer", f"https://login.microsoftonline.com/{other}/v2.0")
        return self.token(**kwargs)
