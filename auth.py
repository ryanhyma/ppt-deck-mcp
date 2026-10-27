"""Authentication for the HTTP surface: Entra ID access tokens, or a shared service token.

Why this exists: every route (``/mcp``, ``/decks/…``, ``/apps/…``) used to be open, so anything that could
reach the port could call tools and download decks. LibreChat's own RBAC is not a boundary here, because a
direct caller never goes through LibreChat.

Three modes (``PPT_AUTH_MODE``):

``none``    development only. Refused when ``PPT_ENV=prod``.
``token``   one shared bearer (``PPT_MCP_TOKEN``), constant-time compared. For a single-user/internal hop.
``entra``   Entra ID access tokens: RS256 signature against the tenant JWKS, exact ``iss``, ``aud`` =
            this API, ``tid`` = our tenant, plus a delegated scope or app role.

Entra notes that shaped the implementation:

* Entra **does not support the RFC 8707 ``resource`` parameter**; audience binding arrives in the ``aud``
  claim (v2 tokens carry the API's client id, v1 tokens the app ID URI). So ``aud`` is compared exactly.
* ``tid`` must be checked as well: a token from another tenant is otherwise perfectly valid.
* ``scp`` (delegated) or ``roles`` (app-only) decides whether this caller may use the deck tools at all.
* Never authorise on ``preferred_username``/``upn``/``email``; the subject is ``oid`` (falling back to
  ``sub``) and that is what owns decks.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

ALLOWED_ALGS = ("RS256", "RS384", "RS512")
CLOCK_SKEW_S = 300
JWKS_TTL_S = 6 * 3600


class AuthError(Exception):
    """A rejected credential. ``status`` is the HTTP status the surface should return."""

    def __init__(self, status: int, error: str, description: str = "", scope: str = ""):
        super().__init__(description or error)
        self.status = status
        self.error = error
        self.description = description
        self.scope = scope

    def www_authenticate(self, resource_metadata: str = "") -> str:
        parts = [f'Bearer error="{self.error}"']
        if self.description:
            parts.append(f'error_description="{self.description}"')
        if self.scope:
            parts.append(f'scope="{self.scope}"')
        if resource_metadata:
            parts.append(f'resource_metadata="{resource_metadata}"')
        return ", ".join(parts)


@dataclass(frozen=True)
class Identity:
    subject: str
    tenant: str = ""
    client: str = ""
    scopes: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    mode: str = "none"
    claims: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def scope_hint(self) -> str:
        """Short, stable digest used to namespace ownership and the render cache."""
        raw = f"{self.tenant}|{self.subject}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    @property
    def anonymous(self) -> bool:
        return self.mode == "none"


ANONYMOUS = Identity(subject="anonymous", mode="none")
_CURRENT: contextvars.ContextVar[Identity] = contextvars.ContextVar("deck_identity", default=ANONYMOUS)


def current_identity() -> Identity:
    """The identity of the in-flight request (anonymous in ``none`` mode)."""
    return _CURRENT.get()


def set_current(identity: Identity):
    return _CURRENT.set(identity)


def reset_current(token) -> None:
    _CURRENT.reset(token)


# --------------------------------------------------------------------------- configuration


def _setting(env: str, cfg_key: str, default: Any = None) -> Any:
    value = os.environ.get(env)
    if value not in (None, ""):
        return value
    try:
        from deck_preview import load_config  # noqa: PLC0415 - avoid a cycle at import time

        value = load_config().get(cfg_key)
    except Exception:  # noqa: BLE001
        value = None
    return default if value in (None, "") else value


def setting(env: str, cfg_key: str, default: Any = None) -> Any:
    """Read a setting the way the rest of the server does (env first, then ppt-mcp.config.json)."""
    return _setting(env, cfg_key, default)


def authorization_server() -> str:
    """The issuer an MCP client should authenticate against (used in the RFC 9728 document)."""
    explicit = _setting("PPT_ENTRA_ISSUER", "entra_issuer", "")
    if explicit:
        return str(explicit)
    tenant = _setting("PPT_ENTRA_TENANT_ID", "entra_tenant_id", "")
    return f"https://login.microsoftonline.com/{tenant}/v2.0" if tenant else ""


def advertises_authorization_server() -> bool:
    """Whether protected-resource metadata should name an authorization server."""
    if str(_setting("PPT_ADVERTISE_AS", "advertise_as", "0")).strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(authorization_server())


def required_scopes() -> list[str]:
    return str(_setting("PPT_ENTRA_REQUIRED_SCOPES", "entra_required_scopes", "") or "").replace(",", " ").split()


def auth_mode() -> str:
    mode = str(_setting("PPT_AUTH_MODE", "auth_mode", "none")).strip().lower()
    if mode not in ("none", "token", "entra"):
        raise ValueError(f"PPT_AUTH_MODE must be none|token|entra, not {mode!r}")
    return mode


def is_production() -> bool:
    return str(_setting("PPT_ENV", "env", "")).strip().lower() in ("prod", "production")


def ownership_enabled() -> bool:
    """Deck ownership is implied by authentication; it can be turned off for a single-user box."""
    explicit = _setting("PPT_OWNERSHIP", "ownership", None)
    if explicit is not None:
        return str(explicit).strip().lower() not in ("0", "false", "no", "off")
    return auth_mode() != "none"


# --------------------------------------------------------------------------- verifiers


class SharedTokenVerifier:
    """One bearer for the whole service. Fine for a single-user box or a same-host hop."""

    mode = "token"

    def __init__(self, token: str, subject: str = "service-token"):
        if not token:
            raise ValueError("PPT_AUTH_MODE=token needs PPT_MCP_TOKEN")
        self._token = token.encode()
        self._subject = subject

    def verify(self, bearer: str) -> Identity:
        if not hmac.compare_digest(bearer.encode(), self._token):
            raise AuthError(401, "invalid_token", "shared token mismatch")
        return Identity(subject=self._subject, mode="token")


class EntraVerifier:
    """Validates Entra ID access tokens for this API."""

    mode = "entra"

    def __init__(self, tenant_id: str, audience: str, required_scopes: Iterable[str] = (),
                 required_roles: Iterable[str] = (), jwks_url: str = "", jwks: Mapping | None = None,
                 issuer: str = "", leeway: int = CLOCK_SKEW_S):
        if not tenant_id:
            raise ValueError("PPT_AUTH_MODE=entra needs PPT_ENTRA_TENANT_ID")
        if not audience:
            raise ValueError("PPT_AUTH_MODE=entra needs PPT_ENTRA_AUDIENCE (the API's client id)")
        self.tenant = tenant_id
        self.audience = audience
        self.required_scopes = tuple(s for s in required_scopes if s)
        self.required_roles = tuple(r for r in required_roles if r)
        self.issuer = issuer or f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.leeway = int(leeway)
        self._static_jwks = jwks is not None  # injected keys never trigger a network fetch
        self._jwks: dict[str, Any] | None = dict(jwks) if jwks else None
        self._jwks_fetched_at = time.time() if jwks else 0.0
        self._jwks_url = jwks_url or f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        self._lock = threading.Lock()

    # -- keys
    def _load_jwks(self, force: bool = False) -> dict:
        if self._static_jwks:
            return self._jwks or {"keys": []}
        with self._lock:
            fresh = self._jwks and (time.time() - self._jwks_fetched_at) < JWKS_TTL_S
            if fresh and not force:
                return self._jwks  # type: ignore[return-value]
            try:
                with urllib.request.urlopen(self._jwks_url, timeout=10) as resp:  # noqa: S310 - https URL
                    self._jwks = json.loads(resp.read().decode("utf-8"))
                self._jwks_fetched_at = time.time()
            except Exception as exc:  # noqa: BLE001
                if self._jwks:  # keep serving with the keys we have rather than failing closed
                    return self._jwks
                raise AuthError(401, "invalid_token", f"cannot load signing keys: {exc}") from exc
            return self._jwks  # type: ignore[return-value]

    def _key_for(self, token: str):
        import jwt  # noqa: PLC0415

        header = jwt.get_unverified_header(token)
        if (header.get("alg") or "") not in ALLOWED_ALGS:
            raise AuthError(401, "invalid_token", f"unsupported algorithm {header.get('alg')!r}")
        kid = header.get("kid")
        jwks = self._load_jwks()
        key = self._find(jwks, kid)
        if key is None:  # key rotation: refetch once before refusing
            key = self._find(self._load_jwks(force=True), kid)
        if key is None:
            raise AuthError(401, "invalid_token", "signing key not found in the tenant JWKS")
        return key

    @staticmethod
    def _find(jwks: Mapping, kid: str | None):
        from jwt.algorithms import RSAAlgorithm  # noqa: PLC0415

        for entry in (jwks or {}).get("keys", []):
            if kid and entry.get("kid") != kid:
                continue
            if entry.get("kty") != "RSA":
                continue
            try:
                return RSAAlgorithm.from_jwk(json.dumps(entry))
            except Exception:  # noqa: BLE001
                continue
        return None

    # -- verification
    def verify(self, bearer: str) -> Identity:
        import jwt  # noqa: PLC0415

        try:
            key = self._key_for(bearer)
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - malformed token
            raise AuthError(401, "invalid_token", f"malformed token: {exc}") from exc

        try:
            claims = jwt.decode(
                bearer, key=key, algorithms=list(ALLOWED_ALGS),
                audience=self.audience, issuer=self.issuer, leeway=self.leeway,
                options={"require": ["exp", "iat", "aud", "iss"], "verify_aud": True,
                         "verify_iss": True, "verify_exp": True},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError(401, "invalid_token", "token expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthError(401, "invalid_token", "token was issued for a different API") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthError(401, "invalid_token", "token was issued by a different issuer") from exc
        except Exception as exc:  # noqa: BLE001
            raise AuthError(401, "invalid_token", f"token rejected: {exc}") from exc

        if claims.get("tid") and claims["tid"] != self.tenant:
            raise AuthError(403, "invalid_token", "token belongs to another tenant")
        if claims.get("ver") and claims["ver"] != "2.0":
            raise AuthError(401, "invalid_token", "v1 tokens are not accepted; request v2.0 tokens")

        subject = claims.get("oid") or claims.get("sub")
        if not subject:
            raise AuthError(401, "invalid_token", "token has no stable subject (oid/sub)")

        # Delegated tokens carry `scp`; app-only tokens carry `roles`. Either satisfies the gate — a
        # caller needs permission, not both kinds of permission.
        scopes = tuple(str(claims.get("scp") or "").split())
        roles = tuple(claims.get("roles") or ())
        if self.required_scopes or self.required_roles:
            scope_ok = bool(self.required_scopes) and set(self.required_scopes).issubset(scopes)
            role_ok = bool(self.required_roles) and bool(set(self.required_roles).intersection(roles))
            if not (scope_ok or role_ok):
                wanted = " ".join(self.required_scopes or self.required_roles)
                raise AuthError(403, "insufficient_scope",
                                f"needs scope {' '.join(self.required_scopes)} or app role "
                                f"{' '.join(self.required_roles)}", scope=wanted)

        return Identity(subject=subject, tenant=str(claims.get("tid") or self.tenant),
                        client=str(claims.get("azp") or claims.get("appid") or ""),
                        scopes=scopes, roles=roles, mode="entra", claims=claims)


_VERIFIER: Any = None
_VERIFIER_GUARD = threading.Lock()


def verifier():
    """The process-wide verifier for the configured mode (None in ``none`` mode)."""
    global _VERIFIER
    mode = auth_mode()
    if mode == "none":
        if is_production():
            raise RuntimeError(
                "PPT_AUTH_MODE=none is refused when PPT_ENV=prod: set ppt token or entra settings")
        return None
    with _VERIFIER_GUARD:
        if _VERIFIER is not None and getattr(_VERIFIER, "mode", None) == mode:
            return _VERIFIER
        if mode == "token":
            _VERIFIER = SharedTokenVerifier(str(_setting("PPT_MCP_TOKEN", "mcp_token", "") or ""),
                                            subject=str(_setting("PPT_TOKEN_SUBJECT", "token_subject",
                                                                 "service-token")))
        else:
            raw_jwks = _setting("PPT_ENTRA_JWKS_JSON", "entra_jwks_json", "")
            jwks = json.loads(raw_jwks) if raw_jwks else None
            _VERIFIER = EntraVerifier(
                tenant_id=str(_setting("PPT_ENTRA_TENANT_ID", "entra_tenant_id", "") or ""),
                audience=str(_setting("PPT_ENTRA_AUDIENCE", "entra_audience", "") or ""),
                # A sovereign/mock issuer is not login.microsoftonline.com; without this the verifier
                # silently defaults to the commercial endpoint and rejects every valid token.
                issuer=str(_setting("PPT_ENTRA_ISSUER", "entra_issuer", "") or ""),
                required_scopes=str(_setting("PPT_ENTRA_REQUIRED_SCOPES", "entra_required_scopes", "") or "").replace(",", " ").split(),
                required_roles=str(_setting("PPT_ENTRA_REQUIRED_ROLES", "entra_required_roles", "") or "").replace(",", " ").split(),
                jwks_url=str(_setting("PPT_ENTRA_JWKS_URL", "entra_jwks_url", "") or ""),
                jwks=jwks,
            )
        return _VERIFIER


def reset_verifier() -> None:
    """Test hook."""
    global _VERIFIER
    with _VERIFIER_GUARD:
        _VERIFIER = None


def bearer_from_header(value: str | None) -> str:
    if not value:
        raise AuthError(401, "invalid_token", "missing Authorization header")
    parts = value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AuthError(401, "invalid_token", "expected 'Authorization: Bearer <token>'")
    return parts[1].strip()
