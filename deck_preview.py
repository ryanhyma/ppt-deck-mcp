"""Deck preview: render a .pptx to PNGs and expose them to the Deck Studio MCP App.

The App lives in `apps/deck_studio/`. It never reads the filesystem; it gets slide URLs in
the tool result and loads them from this server's `/decks/...` HTTP route.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlsplit

from pptx import Presentation

APP_URI = "ui://deck-studio/index.html"

# Rendering writes into one directory per deck_id; concurrent calls for the same deck must serialise.
_RENDER_LOCKS: Dict[str, "threading.Lock"] = {}
_RENDER_LOCKS_GUARD = threading.Lock()


def _render_lock(deck_id: str):
    with _RENDER_LOCKS_GUARD:
        lock = _RENDER_LOCKS.get(deck_id)
        if lock is None:
            lock = threading.Lock()
            _RENDER_LOCKS[deck_id] = lock
        return lock
PREVIEW_ROOT = "previews"

# Set by register_preview_tools(): lets the view show the template gallery.
_TEMPLATE_DIRS_FN = None


def _is_ip_literal(host: str) -> bool:
    """True when ``host`` is an IP address literal (IPv4 or IPv6)."""
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def public_base() -> str:
    base = os.environ.get("PPT_PUBLIC_BASE", "http://host.docker.internal:8931").rstrip("/")
    # LibreChat's MCP App bridge refuses to open host-opened links whose host is an IP
    # address literal (DNS-rebinding hardening), so a numeric PPT_PUBLIC_BASE silently
    # breaks the Deck Studio "Download .pptx" button. Fail fast with the fix instead.
    if _is_ip_literal(urlsplit(base).hostname or ""):
        raise RuntimeError(
            f"PPT_PUBLIC_BASE={base!r} uses an IP address literal, which LibreChat blocks for "
            "host-opened links (the Download button). Use a hostname instead, e.g. "
            "http://decks.local:8931 mapped to the host bridge IP in /etc/hosts."
        )
    return base


def preview_root() -> Path:
    root = Path(os.environ.get("PPT_OUTPUT_DIR", Path(__file__).resolve().parent / "out"))
    root = root / PREVIEW_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def _slide_labels(pptx: Path) -> List[str]:
    try:
        prs = Presentation(str(pptx))
    except Exception:
        return []
    labels = []
    for slide in prs.slides:
        text = ""
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                text = shape.text_frame.text.strip().split("\n")[0]
                break
        labels.append(text[:60])
    return labels


class RendererUnavailable(RuntimeError):
    """No renderer configured/found — the deck is still valid, the pictures are not."""


def _pick_renderer() -> str:
    """soffice | powerpoint | none. PPT_RENDERER=auto picks what this host can actually do."""
    choice = (os.environ.get("PPT_RENDERER") or "auto").strip().lower()
    if choice in ("soffice", "libreoffice", "powerpoint", "com", "none", "aspose"):
        if choice in ("libreoffice",):
            choice = "soffice"
        if choice in ("com",):
            choice = "powerpoint"
        return choice
    if os.name == "nt":
        return "powerpoint"
    binary = _soffice_bin()
    return "soffice" if (Path(binary).is_file() or shutil.which(binary)) else "none"


CONFIG_NAME = "ppt-mcp.config.json"
_WINDOWS_SOFFICE_HINTS = (
    r"C:\tools\LibreOfficePortable\App\libreoffice\program\soffice.exe",
    r"C:\tools\libreoffice\program\soffice.exe",
    r"C:\LibreOfficePortable\App\libreoffice\program\soffice.exe",
    r"%ProgramFiles%\LibreOffice\program\soffice.exe",
    r"%ProgramFiles(x86)%\LibreOffice\program\soffice.exe",
    r"%LOCALAPPDATA%\Programs\LibreOffice\program\soffice.exe",
)


def load_config() -> Dict[str, Any]:
    """ppt-mcp.config.json next to this file (or in PPT_CONFIG) — a config file beats Windows env vars."""
    path = Path(os.environ.get("PPT_CONFIG") or Path(__file__).resolve().parent / CONFIG_NAME)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _soffice_bin() -> str:
    """SOFFICE_BIN (env or config) -> portable/installed LibreOffice -> PATH."""
    explicit = os.environ.get("SOFFICE_BIN") or load_config().get("soffice_bin")
    if explicit:
        return os.path.expandvars(os.path.expanduser(str(explicit)))
    if os.name == "nt":
        for hint in _WINDOWS_SOFFICE_HINTS:
            candidate = os.path.expandvars(hint)
            if Path(candidate).is_file():
                return candidate
    return "soffice"


def _render_soffice(pptx: Path, outdir: Path) -> int:
    binary = _soffice_bin()
    if not (Path(binary).is_file() or shutil.which(binary)):
        raise RendererUnavailable(
            f"soffice not found ({binary}); install libreoffice-impress or set SOFFICE_BIN")
    with tempfile.TemporaryDirectory() as tmp:
        # A throwaway user profile avoids "another instance is running" locks on Windows.
        profile = Path(tmp) / "lo-profile"
        subprocess.run(
            [binary, f"-env:UserInstallation=file://{profile}", "--headless",
             "--convert-to", "pdf", "--outdir", tmp, str(pptx)],
            check=True, capture_output=True, timeout=180,
        )
        pdf = next((p for p in Path(tmp).glob("*.pdf") if p.is_file()), None)
        if pdf is None:
            raise RuntimeError("soffice produced no PDF")
        _pdf_to_pngs(pdf, outdir, dpi=90)
    return len(sorted(outdir.glob("slide-*.png")))


def _pdf_to_pngs(pdf: Path, outdir: Path, dpi: int = 90) -> int:
    """Rasterise a PDF with PyMuPDF (pure wheel — no poppler binary needed on Windows)."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        try:
            import fitz as pymupdf  # older wheels
        except ImportError as exc:
            raise RendererUnavailable("pymupdf is required to rasterise the PDF") from exc

    doc = pymupdf.open(str(pdf))
    try:
        for index, page in enumerate(doc, start=1):
            page.get_pixmap(dpi=dpi).save(str(outdir / f"slide-{index}.png"))
        return len(doc)
    finally:
        doc.close()


def _render_powerpoint(pptx: Path, outdir: Path) -> int:
    """Windows: export through the installed PowerPoint (no extra install, exact rendering)."""
    try:
        import win32com.client  # noqa: F401  (pywin32)
    except ImportError as exc:  # pragma: no cover - Windows only
        raise RendererUnavailable("pywin32 is required for the powerpoint renderer") from exc

    import win32com.client as win32  # pragma: no cover - Windows only

    app = win32.Dispatch("PowerPoint.Application")
    deck = None
    try:  # pragma: no cover - Windows only
        deck = app.Presentations.Open(str(pptx.resolve()), WithWindow=False)
        deck.Export(str(outdir.resolve()), "PNG", 1600, 900)
    finally:  # pragma: no cover
        try:
            if deck is not None:
                deck.Close()
            app.Quit()
        except Exception:
            pass
    return len(sorted(outdir.glob("*.PNG")) + sorted(outdir.glob("*.png")))


def _render_aspose(pptx: Path, outdir: Path) -> int:
    """Aspose.Slides: no Office, no LibreOffice. Commercial licence; eval adds a watermark."""
    try:
        import aspose.slides as slides
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RendererUnavailable(
            "aspose-slides is not installed (pip install aspose-slides); "
            "it is a commercial library with an evaluation watermark") from exc

    licence_path = os.environ.get("ASPOSE_LICENSE_PATH")
    if licence_path:  # pragma: no cover - licence path is deployment-specific
        slides.License().set_license(licence_path)

    with slides.Presentation(str(pptx)) as pres:  # pragma: no cover - optional dependency
        width = pres.slide_size.size.width
        scale = max(1.0, 1600.0 / float(width or 960))
        for index, slide in enumerate(pres.slides, start=1):
            image = slide.get_image(scale, scale)
            image.save(str(outdir / f"slide-{index}.png"), slides.ImageFormat.PNG)
    return len(sorted(outdir.glob("slide-*.png")))


def _render(pptx: Path, outdir: Path) -> int:
    """pptx -> PNGs for the pane. Returns the number of images written."""
    renderer = _pick_renderer()
    if renderer == "none":
        raise RendererUnavailable("preview renderer disabled (PPT_RENDERER=none)")
    if renderer == "powerpoint":
        return _render_powerpoint(pptx, outdir)
    if renderer == "aspose":
        return _render_aspose(pptx, outdir)
    return _render_soffice(pptx, outdir)


def _deck_id(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-")[:60] or "deck"
    return stem


def build_preview(pptx_path: str | Path, title: str = "") -> Dict[str, Any]:
    src = Path(pptx_path)
    if not src.is_file():
        raise FileNotFoundError(f"deck not found: {src}")
    if src.suffix.lower() not in (".pptx", ".potx"):
        raise ValueError("preview_deck expects a .pptx/.potx file")

    root = preview_root()
    deck_id = _deck_id(src)
    outdir = root / deck_id
    with _render_lock(deck_id):  # same deck rendered concurrently: serialise, last writer wins
        return _build_preview_locked(src, deck_id, outdir, title)


def _build_preview_locked(src: Path, deck_id: str, outdir: Path, title: str) -> Dict[str, Any]:
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)

    # Copy the deck next to its previews so the browser can download it from this server.
    shipped = outdir / "deck.pptx"
    shutil.copyfile(src, shipped)

    # The slide count belongs to the deck, never to the renderer: with no LibreOffice the PNGs are
    # missing but the deck is still complete and must report its real size.
    total = len(Presentation(str(src)).slides)

    # Rendering is a view, not data: a host without LibreOffice must still get a valid deck and a
    # clear error instead of a failed tool call.
    rendered, preview_error = 0, None
    try:
        rendered = _render(src, outdir)
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        preview_error = f"{type(exc).__name__}: {str(exc)[:180]}"
    labels = _slide_labels(src)
    base = public_base()

    slides = [
        {
            "index": i,
            "label": labels[i - 1] if i - 1 < len(labels) else "",
            "url": f"{base}/decks/{deck_id}/slide-{i}.png",
        }
        for i in range(1, rendered + 1)
    ]
    data = {
        "templates": _template_gallery(),
        "deck_id": deck_id,
        "title": title or labels[0] if labels else (title or src.stem),
        "file": str(src),
        "slide_count": total,
        "rendered_slides": rendered,
        "download": f"{base}/decks/{deck_id}/deck.pptx",
        "preview_error": preview_error,
        "slides": slides,
    }
    (outdir / "deck.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def _template_gallery() -> List[Dict[str, Any]]:
    if _TEMPLATE_DIRS_FN is None:
        return []
    rows: List[Dict[str, Any]] = []
    seen = set()
    for d in _TEMPLATE_DIRS_FN():
        for f in sorted(Path(d).glob("*.pptx")):
            if f.name in seen:
                continue
            seen.add(f.name)
            label, description = f.stem.replace("-", " ").title(), ""
            manifest = f.with_suffix(".json")
            if manifest.is_file():
                try:
                    m = json.loads(manifest.read_text(encoding="utf-8"))
                    label = m.get("label") or label
                    description = m.get("description") or ""
                except Exception:
                    pass
            urls = template_preview_urls(f)
            rows.append(
                {
                    "file": f.name,
                    "label": label,
                    "description": description,
                    "thumb": urls[-1] if urls else "",
                }
            )
    return rows


def last_preview() -> Dict[str, Any] | None:
    root = preview_root()
    candidates = sorted(
        (p for p in root.iterdir() if (p / "deck.json").is_file()),
        key=lambda p: (p / "deck.json").stat().st_mtime,
        reverse=True,
    )
    for c in candidates:
        try:
            return json.loads((c / "deck.json").read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def register_preview_tools(app, template_dirs_fn=None) -> None:
    global _TEMPLATE_DIRS_FN
    _TEMPLATE_DIRS_FN = template_dirs_fn
    meta = {"ui": {"resourceUri": APP_URI}}

    @app.tool(meta=meta)
    def preview_deck(file: str) -> Dict[str, Any]:
        """Open the Deck Studio view for a .pptx on this machine.

        Renders every slide to an image and shows it in an interactive pane in the chat:
        thumbnails, click-to-zoom, slide count. Call this after build_deck to show the
        result. Returns the same data the view renders.

        Args:
            file: absolute path of a .pptx (e.g. the value returned by build_deck)
        """
        return build_preview(file)

    @app.tool(meta=meta)
    def get_last_deck() -> Dict[str, Any]:
        """Return the most recently previewed deck (the view uses this on open)."""
        data = last_preview()
        return data or {"slides": [], "templates": _template_gallery(), "deck_id": None, "title": "No deck yet"}

    @app.tool()
    def list_decks() -> Dict[str, Any]:
        """List decks that already have a rendered preview, newest first."""
        root = preview_root()
        rows = []
        for p in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            meta_file = p / "deck.json"
            if meta_file.is_file():
                try:
                    d = json.loads(meta_file.read_text(encoding="utf-8"))
                    rows.append({"deck_id": d.get("deck_id"), "title": d.get("title"),
                                 "slides": d.get("slide_count"), "file": d.get("file")})
                except Exception:
                    continue
        return {"decks": rows[:20]}


def template_preview_urls(pptx_path: str | Path) -> List[str]:
    """Render a template's example slides once and return URL(s) for the gallery."""
    src = Path(pptx_path)
    outdir = preview_root() / f"tpl-{_deck_id(src)}"
    with _render_lock(outdir.name):  # several gallery loads can race for the same template
        if not (outdir / "slide-1.png").is_file():
            outdir.mkdir(parents=True, exist_ok=True)
            try:
                _render(src, outdir)
            except Exception:
                return []
    base = public_base()
    files = sorted(outdir.glob("slide-*.png"), key=lambda p: int(p.stem.split("-")[1]))
    return [f"{base}/decks/{outdir.name}/{f.name}" for f in files]


# ---------------------------------------------------------------- import an uploaded deck

UPLOAD_DIR = os.environ.get("PPT_UPLOAD_DIR", "/home/hyma/dev/librechat/uploads")


def _resolve_input(name: str) -> Path:
    """Find a deck by absolute path, container path, or a filename inside LibreChat uploads."""
    raw = (name or "").strip().strip('"')
    direct = Path(os.path.expanduser(raw))
    if direct.is_file():
        return direct
    for prefix in ("/app/uploads/", "/app/client/public/images/"):
        if raw.startswith(prefix):
            cand = Path(UPLOAD_DIR) / raw[len(prefix):]
            if cand.is_file():
                return cand
    if Path(raw).is_absolute():
        raise FileNotFoundError(f"no file at '{raw}' — check the path, or pass just the filename")
    base = Path(UPLOAD_DIR)
    if base.is_dir():
        name = Path(raw).name
        exact = sorted(base.rglob(name), key=lambda p: p.stat().st_mtime, reverse=True)
        if exact:
            return exact[0]
        loose = sorted(base.rglob(f"*{name}*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if loose:
            return loose[0]
    raise FileNotFoundError(
        f"'{name}' not found. Pass an absolute path, or the filename of a file uploaded to the chat."
    )


def register_import_tools(app) -> None:
    meta = {"ui": {"resourceUri": APP_URI}}

    @app.tool(meta=meta)
    def import_deck(file: str) -> Dict[str, Any]:
        """Open a .pptx the user uploaded to the chat, so it can be edited or restyled.

        Accepts the uploaded filename (e.g. "Q3 review.pptx"), a container path
        ("/app/uploads/...") or an absolute path. Copies it into the deck workspace, renders
        previews, and returns the outline as well, so a follow-up edit can preserve content.

        Args:
            file: uploaded filename or path to a .pptx
        """
        src = _resolve_input(file)
        if src.suffix.lower() not in (".pptx", ".potx"):
            raise ValueError("only .pptx files can be imported (legacy .pot/.ppt is not supported)")

        from deck_tools import _outline

        root = Path(os.environ.get("PPT_OUTPUT_DIR", Path(__file__).resolve().parent / "out"))
        root.mkdir(parents=True, exist_ok=True)
        stem = _deck_id(src)
        dest = root / f"{stem}-imported-{datetime.now().strftime('%H%M%S')}{src.suffix.lower()}"
        shutil.copyfile(src, dest)

        data = build_preview(dest, title="")
        data["imported_from"] = str(src)
        data["outline"] = _outline(dest)
        return data
