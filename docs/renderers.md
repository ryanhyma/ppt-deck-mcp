# Preview renderers: what was tested (2026-10-25)

Building/editing a `.pptx` needs **no** office suite — python-pptx writes OOXML directly. A renderer is
only needed to show slide **thumbnails** in the chat pane. This file records what was actually tested
on a real 8-slide deck (charts, tables, SVG, styled text), not what the READMEs claim.

| Candidate | Works headless? | Windows install | Verdict |
|---|---|---|---|
| **LibreOffice Impress** (`soffice`) | yes | portable unzip, **auto-detected** or `soffice_bin` | **best free option**; font substitution is the only visible difference |
| **PowerPoint COM** (`pywin32`) | yes, where Office exists | none | exact rendering; Microsoft does not support server-side Office automation |
| **Aspose.Slides** (`aspose-slides`) | yes, after `libgdiplus` on Linux | **none — GDI+ is native on Windows** | high fidelity; **commercial licence** (evaluation stamps a watermark and truncates text reads) |
| **Spire.Presentation.Free** | installed and ran | none | **unusable**: every rendered image is replaced by the "Free version is limited to 10 slides" banner |
| **`pptx-raster`** ("pure Python, no dependencies") | n/a | n/a | **does not exist on PyPI** — `pip install pptx-raster` → no matching distribution |
| **`pptx2png`** | wraps COM/LibreOffice | needs them anyway | not an independent renderer |
| **HTML/SVG preview from our own deck model** | yes, no dependencies | none | faithful for decks we generate, **approximate for imported decks**; browser paints it |

### PDF → PNG step

`soffice` converts the deck to PDF, then **PyMuPDF** (a pip wheel) rasterises it. That matters on
Windows: the previous implementation shelled out to `pdftoppm`, which meant downloading poppler as a
second binary. Now the only external piece is `soffice` itself.

## Consequences for the code

`PPT_RENDERER` selects the backend: `auto` (PowerPoint on Windows → soffice → none), `soffice`,
`powerpoint`, `aspose`, `none`. Rendering failures never fail a tool call: the deck is saved,
`slide_count` comes from the deck, and `preview_error` explains the missing pictures.

## Notes

- On Linux, `aspose-slides` needs `libgdiplus` (`System.Drawing`); on Windows nothing extra.
- `ASPOSE_LICENSE_PATH` removes the evaluation watermark.
- Fonts matter more than the engine: this box has no Calibri (`fc-match Calibri` → Noto Sans), which
  is why rendered text can look letter-spaced. A Windows server with Office fonts renders correctly.
- The minimal free install is Impress alone:
  `apt-get install --no-install-recommends libreoffice-impress` (poppler is no longer required).
- Windows portable LibreOffice is found automatically in `C:\tools\LibreOfficePortable\…`,
  `%ProgramFiles%\LibreOffice\…` and friends; otherwise set `soffice_bin` in `ppt-mcp.config.json`.
- Each render uses a throwaway `-env:UserInstallation=file:///…` profile, so concurrent renders don't
  fight over LibreOffice's user profile lock.
