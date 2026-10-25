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
from starlette.responses import FileResponse, PlainTextResponse  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402

from deck_preview import (APP_URI, preview_root, public_base, register_import_tools,  # noqa: E402
                          register_preview_tools)
from deck_tools import (register_deck_tools, register_readback_tools,  # noqa: E402
                        register_style_tools, register_text_tools)

register_deck_tools(app, get_template_search_directories)
register_readback_tools(app, None)
register_style_tools(app)
register_import_tools(app)
register_text_tools(app)
register_preview_tools(app, get_template_search_directories)

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
        p = _safe_file(preview_root() / request.path_params["deck_id"], request.path_params["name"])
        if p is None:
            return PlainTextResponse("not found", status_code=404)
        headers = {"Cache-Control": "no-store"}
        if p.suffix.lower() in {".pptx", ".potx"}:
            headers["Content-Disposition"] = f'attachment; filename="{p.name}"'
        return FileResponse(p, headers=headers)

    @asynccontextmanager
    async def lifespan(root_app):
        async with inner.router.lifespan_context(root_app):
            yield

    return Starlette(
        routes=[
            Route("/apps/deck_studio/{path:path}", app_asset),
            Route("/decks/{deck_id}/{name}", deck_asset),
            Mount("/", app=inner),
        ],
        lifespan=lifespan,
    )


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
