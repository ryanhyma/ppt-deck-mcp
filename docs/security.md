# Security hardening backlog

Status: **partially hardened (2026-10-26)**. Items 1 and 2 of the 2026-10-26 review are closed — see
"Fixed" below. Everything else is still open: the server remains fine for a single-user dev box on a
trusted host and is not ready for a shared or network-reachable deployment.

Each item states what was actually verified in the code (file reference) rather than what is assumed.

## Fixed 2026-10-26

| Was | Now | Where | Proof |
|---|---|---|---|
| `preview_deck`/`import_deck`/`get_deck_outline`/`style_slide`/`style_text`/`verify_deck`/`build_deck(base=)`/`image=` accepted any existing path, so any `.pptx` (or `.png`) on the host could be read | every path is resolved (`..`, symlinks) then required to sit inside `PPT_OUTPUT_DIR`, `PPT_UPLOAD_DIR`, `PPT_TEMPLATE_PATH` or `PPT_ALLOWED_ROOTS`; wrong suffix rejected | `paths.py` (`resolve_deck`), used by `deck_preview` + `deck_tools` | `tests/test_security.py::PathScopeRegressions`; live: `preview_deck(file="/etc/hostname")` → "outside the allowed locations" |
| Deck ids were filename slugs (`2025-hiring-plan-175350`), so preview directories were guessable | opaque `d<16 hex>` id derived from the resolved path; stable across previews (cache + gallery depend on it) | `deck_preview._deck_id` | `tests/test_security.py::DeckIdRegressions`; live: old slug id → 404 |
| Tool results carried absolute filesystem paths (`"file": "/home/…/deck.pptx"`), which leaked the host layout and invited follow-up calls with a path | every tool returns an opaque `deck_id` (`d<16 hex>`); `render_svg` returns an `image_id` (`i<16 hex>`); the id -> file index lives in `previews/.index/` and is never served. Paths are still accepted as *input* for operator/CLI use | `deck_registry`, `deck_preview.resolve_ref`, `paths.resolve_image`, `deck_tools` | `tests/test_security.py::NoPathLeakRegressions`; live: every tool result scanned, zero paths |
| `/decks/<id>/<name>` served **every** file in the preview dir — `deck.json` (absolute host paths) and `deck.pptx` (user content) with no authentication | slide images are name-served; `deck.json`/`deck.pptx` require an HMAC signature with an expiry (`?exp=&sig=`); anything else, any traversal, any unknown id → 404 | `deck_preview.deck_asset_path` + `server.deck_asset` | `tests/test_security.py::AssetRouteRegressions`; live: `deck.json` 404, unsigned/tampered/expired `deck.pptx` 404, signed 200 |

Operational consequence: download URLs now expire after an hour (`URL_TTL_S`). Set `PPT_URL_SECRET` in
production — without it the signing key is random per process, so every restart invalidates outstanding
links (correct, just annoying).

## 1. The MCP HTTP endpoint has no authentication — *still open*

`server.py` starts uvicorn on `--host` (dev: `172.17.0.1:8931`) with no auth layer, no API key, no
per-request identity. `librechat.yaml` declares the server with no `headers`/`apiKey`. Anything that
can reach that address — every container on the docker bridge, every local process — can call all
tools.

Consequences: build/render/style decks, read the preview directory, and (item 2) read arbitrary files.
Treatment: bind to loopback only, put a shared-secret header in front, or front it with an
authenticating reverse proxy that LibreChat also uses. The `--transport stdio` mode used on the
Windows production box has no network surface at all, which is why it is the recommended prod shape.

## 2. `preview_deck(file=…)` / `import_deck(file=…)` accept any absolute path — **fixed 2026-10-26**

`deck_preview._resolve_input()` returns `Path(raw)` whenever `direct.is_file()` is true, and
`preview_deck(file)` passes its argument straight through. Combined with item 1, a caller can point the
renderer at any `.pptx`/`.potx` on the machine and receive rendered PNGs of it back through
`/decks/<id>/slide-N.png`.

~~Treatment~~ Done: `paths.resolve_deck()` allowlists the roots and resolves first, so a symlink inside
the upload directory cannot point out of it. The asset route is separately restricted by
`deck_asset_path()`.

## 3. Rendering shells out to LibreOffice on untrusted input — *verified (design), unmeasured risk*

`deck_preview._render_*` runs `soffice --headless --convert-to pdf` (or Aspose) on the requested file,
with a throwaway `-env:UserInstallation` profile per render. LibreOffice is not a sandbox: a malicious
`.pptx` reaches a large parser and, historically, parser bugs. Macro execution is off by default, which
is not a security boundary.

Treatment for shared use: render inside a container/VM with no network, a read-only input mount and a
`ulimit`/timeout; run the service as a dedicated low-privilege account; cap render time and output size.

## 4. No per-caller throttling — *verified*

Every `preview_deck` call re-renders all slides (per-deck locks prevent races, not volume), and
`build_deck` writes a new `.pptx` each time. A loop can fill the disk or pin the CPU.

Treatment: rate limit per caller, a max deck count/size with eviction, and a timeout on the render
subprocess.

## 5. Rendered decks are served without authorization — *verified*

`/decks/<deck_id>/<name>` serves any file under `previews/<deck_id>` (`_safe_file()` blocks traversal,
which is good) but performs no auth. Deck ids are timestamp + random suffix, so they are not trivially
sequential, but they are also not secrets and they appear in chat history and logs.

Treatment: serve through an authenticated route, or make the id an unguessable token kept server-side.

The App assets route sends `Access-Control-Allow-Origin: *` because the MCP App iframe has an opaque
srcdoc origin. That exposes `index.html` / `app.js` / the vendored SDK to any web page that can reach
the host — acceptable for static UI code, but do not put anything sensitive in `apps/`.

## 6. Files are written into the LibreChat uploads tree — *worth checking*

`PPT_OUTPUT_DIR` in dev is `/home/hyma/dev/librechat/uploads/decks`, i.e. inside the chat product's
upload directory. Files inherit the process umask. Before shared use: a dedicated `decks/` root outside
any product's data tree, `0700` dirs, `0600` files, and a retention policy.

## 7. Transport hardening — *verified partially*

uvicorn runs plain HTTP (no TLS) with Starlette's `TransportSecuritySettings` on: DNS-rebinding
protection is enabled and `allowed_hosts` includes `host.docker.internal:*`, `127.0.0.1:*`,
`localhost:*`, `[::1]:*` and the bind address. That is a Host-header check, not authentication, and the
allowed-host list is deliberately broad to keep the docker alias working.

## 8. `dev-preview.html` is publicly reachable on the MCP origin — *verified*

`apps/deck_studio/dev-preview.html` is served by the asset route like any other file. It renders the App
against fixture data (`globalThis.__DECK_MOCK__`) and has no filesystem or tool access, so it leaks
nothing but the UI itself. It should still be removed from non-dev builds or gated behind a flag.

## Non-issues (checked, so nobody re-checks them)

- No credentials are stored in this server: no API keys, no tokens, no `.env` requirement (the LLM key
  lives in LibreChat).
- Both HTTP asset routes resolve through `_safe_file()` with `Path.relative_to(base)` — path traversal
  on `/apps/...` and `/decks/...` is blocked.
- Deck writes are atomic (`temp` + `os.replace`) and output names are unique, so concurrent callers
  cannot overwrite each other's file.
- The vendored upstream tools are pure `python-pptx` + `Pillow` file manipulation; no network calls.
