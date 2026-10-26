# Windows setup (native, no containers)

Target: LibreChat on Windows Server, decks built by this MCP server, slide previews rendered by a
**portable** LibreOffice — no installer, no registry, no admin rights.

## 1. Python side

```powershell
git clone https://github.com/ryanhyma/ppt-deck-mcp C:\deck-mcp
cd C:\deck-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

`requirements.txt` pulls everything else as wheels — including PyMuPDF, which rasterises the PDF, so
**no poppler / pdftoppm binary is needed** on Windows.

> Previews normally use the **warm renderer** instead: one LibreOffice process exports changed slides
directly to PNG (`import uno` from LibreOffice's `python.exe`, auto-detected), and unchanged slides come
from the image cache. The PyMuPDF/PDF chain above is the fallback when pyuno is not available — nothing
extra to install either way.

## 2. Portable LibreOffice (one unzip)

1. Get a portable build (LibreOfficePortable) or the normal installer pointed at `C:\tools\LibreOfficePortable`.
2. Unzip so that `soffice.exe` sits at:
   `C:\tools\LibreOfficePortable\App\libreoffice\program\soffice.exe`
3. That path is **auto-detected** (also `C:\LibreOfficePortable\…`, `%ProgramFiles%\LibreOffice\…`,
   `%LOCALAPPDATA%\Programs\LibreOffice\…`) — nothing else to configure. Any other location works via
   `soffice_bin` in the config file below.

Impress alone is enough; Writer/Calc/Base are not needed. LibreOffice's own user profile is bypassed
per render (`-env:UserInstallation=…`), so a left-open LibreOffice window won't deadlock the server.

## 3. Config

```powershell
copy ppt-mcp.config.example.json ppt-mcp.config.json
```

```json
{
  "renderer": "soffice",
  "soffice_bin": "C:\\tools\\LibreOfficePortable\\App\\libreoffice\\program\\soffice.exe",
  "template_path": "C:\\deck-mcp\\templates",
  "output_dir": "C:\\deck-mcp\\out",
  "upload_dir": "C:\\LibreChat\\uploads",
  "public_base": "http://decks.internal:8931"
}
```

Environment variables override the file, so CI and mixed setups can still win.

## 4. Run

```powershell
.\.venv\Scripts\python server.py --transport stdio     # LibreChat spawns it (recommended on Windows)
.\.venv\Scripts\python server.py --transport http --host 127.0.0.1 --port 8931   # or run it as a service
```

Register it in LibreChat's `librechat.yaml` (stdio):

```yaml
mcpServers:
  ppt:
    type: stdio
    command: C:\deck-mcp\.venv\Scripts\python.exe
    args: ["C:\\deck-mcp\\server.py", "--transport", "stdio"]
    timeout: 180000
```

## 5. Verify

```powershell
.\.venv\Scripts\python -c "from deck_preview import _pick_renderer, _soffice_bin; print(_pick_renderer(), _soffice_bin())"
# -> soffice C:\tools\LibreOfficePortable\App\libreoffice\program\soffice.exe

.\.venv\Scripts\python -m unittest discover -s tests -v
```

If previews fail, the deck is still built and saved: the tool returns `preview_error` naming the
missing binary. Alternative backends: `renderer: "powerpoint"` (COM, if Office is installed) or
`"none"` (no thumbnails). See `docs/renderers.md`.

## Gotchas

- LibreOffice locks its user profile. The server passes a throwaway
  `-env:UserInstallation=file:///…` per render, so concurrent runs don't collide — but a manually
  opened LibreOffice on the same profile can still block; close it before testing.
- Fonts matter: install the Office fonts (Calibri, Georgia) on the server, or rendered text will be
  substituted and may look wrong.
- Long paths: keep the unzip path short (`C:\tools\...`), Windows MAX_PATH still bites LibreOffice.
