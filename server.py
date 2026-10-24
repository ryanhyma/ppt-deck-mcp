#!/usr/bin/env python
"""Entry point: vendored Office-PowerPoint server + our two small-model deck tools.

Run (host, no container):
    .venv/bin/python server.py --transport http --host 172.17.0.1 --port 8931
    .venv/bin/python server.py --transport stdio          # Windows / native prod

Env:
    PPT_TEMPLATE_PATH  template dirs (colon-separated)
    PPT_OUTPUT_DIR     where generated decks are written
"""
import argparse
import os
import sys

from mcp.server.transport_security import TransportSecuritySettings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "upstream"))

from ppt_mcp_server import app, get_template_search_directories  # noqa: E402
from deck_tools import register_deck_tools  # noqa: E402

register_deck_tools(app, get_template_search_directories)


def main() -> None:
    parser = argparse.ArgumentParser(description="PPT deck MCP server (upstream + build_deck)")
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

    app.run(transport="streamable-http" if args.transport == "http" else "sse")


if __name__ == "__main__":
    main()
