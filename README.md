# ppt-mcp — deck builder for LibreChat (and any MCP client)

Vendored fork of [GongRzhe/Office-PowerPoint-MCP-Server](https://github.com/GongRzhe/Office-PowerPoint-MCP-Server)
(MIT, archived upstream) plus two small-model-facing tools.

## Provenance / pinning

- `upstream/` = unmodified copy of the upstream repo at its last commit (v2.0.7, 37 tools, MIT).
  Upstream is archived but feature-complete; we own this copy and do not track it.
- Runtime is local only: `python-pptx` + `Pillow` + `fonttools`, no network calls.
- `mcp<2` is pinned (1.30.0). Upstream imports `mcp.server.fastmcp`, which the 2.x SDK removed.

## What we add

| File | Role |
|---|---|
| `deck_tools.py` | `list_templates` (catalog + layout indices) and `build_deck` (JSON outline → .pptx, one call) |
| `deck_style.py` | deterministic slide chrome (navy title, teal accent bar, footer) |
| `server.py` | entry point: upstream app + our tools, `--transport http\|stdio` |
| `tools/make_template.py` | regenerates the 2-slide corporate fixture |

## Run

```sh
# dev (host process, reachable from the LibreChat container)
.venv/bin/python server.py --transport http --host 172.17.0.1 --port 8931

# production (native Windows Server, LibreChat spawns it)
.venv/bin/python server.py --transport stdio
```

Env: `PPT_TEMPLATE_PATH` (template dirs), `PPT_OUTPUT_DIR` (output dir),
`PPT_RENDERER` (`auto` | `soffice` | `powerpoint` | `none`).

### Preview renderer (optional)

Building and editing decks needs **no** office suite — python-pptx writes OOXML directly. A renderer is
only needed to show slide thumbnails in the chat pane:

| `PPT_RENDERER` | Uses | Notes |
|---|---|---|
| `auto` (default) | PowerPoint on Windows, else `soffice`, else `none` | picks what the host can do |
| `soffice` | `libreoffice-impress` + `poppler-utils` | `--no-install-recommends` is enough (~200 MB), no GUI needed |
| `powerpoint` | COM via `pywin32` | exact PowerPoint rendering, no extra install where Office exists |
| `none` | — | decks, edits and downloads still work; `preview_error` explains the missing pictures |

Deployed on this box as systemd user unit `ppt-mcp.service`; template fixture is
`templates/corporate-2slide.pptx`.

## Test

```sh
PPT_TEMPLATE_PATH=$PWD/templates PPT_OUTPUT_DIR=/tmp/out .venv/bin/python - <<'EOF'
...
EOF
```

Or call `list_templates` / `build_deck` from any MCP client.
