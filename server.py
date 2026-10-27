#!/usr/bin/env python
"""Entry point: vendored Office-PowerPoint server + our deck tools + the Deck Studio app.

Run (host, no container):
    .venv/bin/python server.py --transport http --host 172.17.0.1 --port 8931
    .venv/bin/python server.py --transport stdio          # Windows / native prod

Env:
    PPT_TEMPLATE_PATH  template dirs (colon-separated)
    PPT_OUTPUT_DIR     where generated decks are written
    PPT_PUBLIC_BASE    origin the LibreChat sandbox can reach (default host.docker.internal:8931)
"""
import argparse
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "upstream"))

import uvicorn  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from ppt_mcp_server import app, get_template_search_directories  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse  # noqa: E402
from starlette.types import ASGIApp, Receive, Scope, Send  # noqa: E402

import auth  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402

from deck_preview import (APP_URI, deck_asset_path, preview_root, public_base,  # noqa: E402
                          register_import_tools, register_preview_tools)
from deck_tools import (register_deck_tools, register_readback_tools,  # noqa: E402
                        register_style_tools, register_text_tools, register_verify_tools)

import audit  # noqa: E402

register_deck_tools(app, get_template_search_directories)
register_readback_tools(app, None)
register_style_tools(app)
register_import_tools(app)
register_text_tools(app)
register_verify_tools(app)
register_preview_tools(app, get_template_search_directories)

# One wrapper records every tool call (ours and the vendored ones) with the request identity.
AUDITED_TOOLS = audit.wrap_tool_fns(app)

APP_DIR = HERE / "apps" / "deck_studio"


# ---------------------------------------------------------------- MCP App resources
@app.resource(
    APP_URI,
    name="Deck Studio",
    title="Deck Studio",
    description="Slide thumbnails and click-to-zoom for the last built deck.",
    mime_type="text/html;profile=mcp-app",
    meta={"ui": {"csp": {"resourceDomains": [public_base()]}}},
)
def deck_studio_html() -> str:
    html = (APP_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace("__APP_JS__", f"{public_base()}/apps/deck_studio/app.js")


# ---------------------------------------------------------------- HTTP assets
def _safe_file(base: Path, rel: str) -> Path | None:
    p = (base / rel).resolve()
    try:
        p.relative_to(base.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def build_http_app():
    inner = app.streamable_http_app()

    async def app_asset(request):
        p = _safe_file(APP_DIR, request.path_params["path"])
        if p is None:
            return PlainTextResponse("not found", status_code=404)
        # The App runs in a srcdoc iframe (opaque origin "null"), so module scripts
        # must be CORS-readable.
        return FileResponse(p, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

    async def deck_asset(request):
        """Slide images by name; data files (deck.json, the .pptx) only with a signed, expiring URL."""
        deck_id = request.path_params["deck_id"]
        name = request.path_params["name"]
        p = deck_asset_path(deck_id, name, request.query_params.get("exp"),
                            request.query_params.get("sig"),
                            auth.current_identity().subject if not auth.current_identity().anonymous else
                            request.query_params.get("sub", ""))
        if p is None:
            audit.record_asset(deck_id=deck_id, name=name, outcome="denied")
            return PlainTextResponse("not found", status_code=404)
        audit.record_asset(deck_id=deck_id, name=name, outcome="ok")
        headers = {"Cache-Control": "no-store"}
        if p.suffix.lower() in {".pptx", ".potx"}:
            headers["Content-Disposition"] = f'attachment; filename="{p.name}"'
        return FileResponse(p, headers=headers)

    async def protected_resource_metadata(request):
        """RFC 9728: where an MCP client learns which authorization server and scopes to use.

        Served only when the deployment advertises an authorization server (`PPT_ADVERTISE_AS=1`).
        Returning the document with an empty `authorization_servers` is not enough for every client:
        LibreChat v0.8.9-rc1 treats the document's presence as "OAuth is available" and then fails the
        tool call, so the honest answer for that deployment is 404 — there is no browser OAuth here.
        """
        if not auth.advertises_authorization_server():
            return PlainTextResponse("not found", status_code=404)
        base = public_base()
        return JSONResponse({
            "resource": f"{base}/mcp",
            # A client that discovers an authorization server will try to run an OAuth flow. Publish it
            # only when the deployment wants browser-based OAuth clients (PPT_ADVERTISE_AS=1); otherwise
            # a client that cannot complete the flow (LibreChat 0.8.9) fails every tool call.
            "authorization_servers": ([auth.authorization_server()]
                                      if auth.advertises_authorization_server() else []),
            "scopes_supported": auth.required_scopes(),
            "bearer_methods_supported": ["header"],
        })

    @asynccontextmanager
    async def lifespan(root_app):
        async with inner.router.lifespan_context(root_app):
            yield

    routes = [
        Route("/.well-known/oauth-protected-resource", protected_resource_metadata),
        # RFC 9728 §3.1 also allows the document under the resource's path; MCP clients probe this form.
        Route("/.well-known/oauth-protected-resource/mcp", protected_resource_metadata),
        Route("/healthz", lambda request: PlainTextResponse("ok")),
        Route("/apps/deck_studio/{path:path}", app_asset),
        Route("/decks/{deck_id}/{name}", deck_asset),
        Mount("/", app=inner),
    ]
    return AuthGate(Starlette(routes=routes, lifespan=lifespan))


class AuthGate:
    """Bearer authentication in front of everything, with signed URLs for browser-fetched images.

    Two kinds of caller reach this server:

    * **tool traffic** (``/mcp``) and app assets — programmatic, so they carry a bearer token;
    * **slide images** — fetched by the *browser*, which has no bearer and belongs to the LibreChat
      origin, so they are authorised by the signed URL minted in the tool result instead.

    A valid signature is still checked against the deck's recorded owner, and every request that has a
    bearer gets its identity bound to a context variable the tools read.
    """

    # `/apps/` stays public: it is the Deck Studio's static HTML/JS, fetched by the browser with no
    # bearer available, and it contains no user data. Tool traffic and deck assets are protected.
    PROTECTED_PREFIXES = ("/mcp", "/decks/")
    IMAGE_SUFFIXES = (".png",)

    def __init__(self, inner: ASGIApp):
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        path = scope["path"]
        if not any(path == p or path.startswith(p) for p in self.PROTECTED_PREFIXES):
            await self.inner(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if os.environ.get("PPT_DEBUG") and path.startswith("/mcp"):
            print(f"[auth] {scope.get('method')} {path} "
                  f"authorization={'present' if headers.get('authorization') else 'MISSING'} "
                  f"ua={headers.get('user-agent','')[:40]}", flush=True)
        query = __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(scope.get("query_string", b"").decode())
        try:
            identity = self._identify(path, headers, query)
        except auth.AuthError as exc:
            audit.record_auth_failure(path=path, status=exc.status, error=exc.error)
            body = {"error": exc.error, "error_description": exc.description}
            # Only advertise protected-resource metadata when there really is an authorization server
            # behind us: a 401 that points at one makes clients start an OAuth flow (LibreChat will
            # report "OAuth Required" and never list the tools) — wrong for the shared-token mode.
            advertises_oauth = (
                auth.auth_mode() == "entra"
                and str(auth.setting("PPT_ADVERTISE_PRM", "advertise_prm", "1")).strip().lower()
                not in ("0", "false", "no", "off")
            )
            challenge = exc.www_authenticate(
                f"{public_base()}/.well-known/oauth-protected-resource" if advertises_oauth else "")
            await send({"type": "http.response.start", "status": exc.status,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"www-authenticate", challenge.encode())]})
            await send({"type": "http.response.body", "body": __import__("json").dumps(body).encode()})
            return

        token = auth.set_current(identity)
        try:
            await self.inner(scope, receive, send)
        finally:
            auth.reset_current(token)

    def _identify(self, path: str, headers: dict, query: dict) -> "auth.Identity":
        mode = auth.auth_mode()
        if mode == "none":
            return auth.ANONYMOUS

        bearer = headers.get("authorization")
        if bearer:
            return auth.verifier().verify(auth.bearer_from_header(bearer))

        # No bearer: only a signed image URL may pass, and only for an image.
        if path.startswith("/decks/") and path.endswith(self.IMAGE_SUFFIXES):
            subject = (query.get("sub") or [""])[0]
            if subject:
                return auth.Identity(subject=subject, mode="signed-url")
        raise auth.AuthError(401, "invalid_token",
                             "this endpoint needs an Authorization: Bearer token")


def _apply_config() -> None:
    """ppt-mcp.config.json supplies defaults on Windows, where env vars are awkward to set."""
    from deck_preview import load_config

    cfg = load_config()
    pairs = (
        ("PPT_OUTPUT_DIR", "output_dir"),
        ("PPT_TEMPLATE_PATH", "template_path"),
        ("PPT_PUBLIC_BASE", "public_base"),
        ("PPT_RENDERER", "renderer"),
        ("PPT_UPLOAD_DIR", "upload_dir"),
        ("SOFFICE_BIN", "soffice_bin"),
        ("ASPOSE_LICENSE_PATH", "aspose_license_path"),
        ("PPT_PREVIEW_WIDTH", "preview_width"),
        ("PPT_WARM_RENDER", "warm_render"),
        ("PPT_RENDER_CACHE", "render_cache"),
        ("PPT_RENDER_CACHE_MAX", "render_cache_max"),
        ("PPT_RENDER_QUEUE_MAX", "render_queue_max"),
        ("PPT_RENDER_TIMEOUT_S", "render_timeout_s"),
        ("PPT_UNO_PYTHON", "uno_python"),
    )
    for env_key, cfg_key in pairs:
        value = cfg.get(cfg_key)
        if value and not os.environ.get(env_key):
            os.environ[env_key] = str(value)


def main() -> None:
    _apply_config()
    parser = argparse.ArgumentParser(description="PPT deck MCP server (upstream + build_deck + Deck Studio app)")
    parser.add_argument("--transport", default="http", choices=["stdio", "http", "sse"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8931)
    args = parser.parse_args()

    if args.transport == "stdio":
        app.run(transport="stdio")
        return

    app.settings.host = args.host
    app.settings.port = args.port
    app.settings.streamable_http_path = "/mcp"

    # DNS-rebinding protection stays on, but the docker host alias must be allowed:
    # the LibreChat container reaches us as http://host.docker.internal:<port>/mcp.
    hosts = [
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        "host.docker.internal:*",
        f"{args.host}:*",
    ]
    app.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"http://{h}" for h in hosts],
    )

    uvicorn.run(build_http_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
