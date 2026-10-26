"""Deck preview: render a .pptx to PNGs and expose them to the Deck Studio MCP App.

The App lives in `apps/deck_studio/`. It never reads the filesystem; it gets slide URLs in
the tool result and loads them from this server's `/decks/...` HTTP route.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlsplit

from mcp.types import ToolAnnotations
from pptx import Presentation

import paths as deck_paths
import render_cache
import render_warm

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


def _setting(env_key: str, cfg_key: str, default: Any = None) -> Any:
    value = os.environ.get(env_key)
    if value not in (None, ""):
        return value
    value = load_config().get(cfg_key)
    return default if value in (None, "") else value


def _setting_bool(env_key: str, cfg_key: str, default: bool) -> bool:
    value = _setting(env_key, cfg_key, None)
    if value is None:
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def _setting_int(env_key: str, cfg_key: str, default: int) -> int:
    try:
        return int(_setting(env_key, cfg_key, default))
    except (TypeError, ValueError):
        return default


PREVIEW_WIDTH_DEFAULT = 1600


def preview_width() -> int:
    """Pixel width of a rendered slide. 1600 keeps the enlarged view crisp; the grid scales it down."""
    return max(320, _setting_int("PPT_PREVIEW_WIDTH", "preview_width", PREVIEW_WIDTH_DEFAULT))


def cache_enabled() -> bool:
    """PPT_RENDER_CACHE=0 turns off both the slide cache and warm LibreOffice (cold chain only)."""
    return _setting_bool("PPT_RENDER_CACHE", "render_cache", True)


def warm_enabled() -> bool:
    return _setting_bool("PPT_WARM_RENDER", "warm_render", True)


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


def _render_soffice(pptx: Path, outdir: Path, width: int | None = None) -> int:
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
        _pdf_to_pngs(pdf, outdir, width=width or preview_width())
    return len(sorted(outdir.glob("slide-*.png")))


def _pdf_to_pngs(pdf: Path, outdir: Path, dpi: float | None = None,
                 width: int | None = None) -> int:
    """Rasterise a PDF with PyMuPDF (pure wheel — no poppler binary needed on Windows).

    ``dpi`` pins the resolution explicitly; ``width`` derives it per page so the output matches
    the warm renderer's pixel width. Exactly one of them should be given.
    """
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
            page_dpi = dpi
            if page_dpi is None:
                inches = (page.rect.width / 72.0) if page.rect.width else 0
                page_dpi = max(24, int(round((width or preview_width()) / inches))) if inches else 96
            page.get_pixmap(dpi=int(round(page_dpi))).save(str(outdir / f"slide-{index}.png"))
        return len(doc)
    finally:
        doc.close()


def _render_powerpoint(pptx: Path, outdir: Path, width: int | None = None) -> int:
    """Windows: export through the installed PowerPoint (no extra install, exact rendering)."""
    try:
        import win32com.client  # noqa: F401  (pywin32)
    except ImportError as exc:  # pragma: no cover - Windows only
        raise RendererUnavailable("pywin32 is required for the powerpoint renderer") from exc

    import win32com.client as win32  # pragma: no cover - Windows only

    target_width = int(width or preview_width())
    app = win32.Dispatch("PowerPoint.Application")
    deck = None
    try:  # pragma: no cover - Windows only
        deck = app.Presentations.Open(str(pptx.resolve()), WithWindow=False)
        ratio = float(deck.PageSetup.SlideHeight) / float(deck.PageSetup.SlideWidth)
        deck.Export(str(outdir.resolve()), "PNG", target_width, int(round(target_width * ratio)))
    finally:  # pragma: no cover
        try:
            if deck is not None:
                deck.Close()
            app.Quit()
        except Exception:
            pass
    return len(sorted(outdir.glob("*.PNG")) + sorted(outdir.glob("*.png")))


def _render_aspose(pptx: Path, outdir: Path, width: int | None = None) -> int:
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

    target_width = float(width or preview_width())
    with slides.Presentation(str(pptx)) as pres:  # pragma: no cover - optional dependency
        page_width = pres.slide_size.size.width
        scale = max(1.0, target_width / float(page_width or 960))
        for index, slide in enumerate(pres.slides, start=1):
            image = slide.get_image(scale, scale)
            image.save(str(outdir / f"slide-{index}.png"), slides.ImageFormat.PNG)
    return len(sorted(outdir.glob("slide-*.png")))


def _render(pptx: Path, outdir: Path, width: int | None = None) -> int:
    """pptx -> PNGs for the pane (cold chain: one soffice call, then PDF raster)."""
    renderer = _pick_renderer()
    if renderer == "none":
        raise RendererUnavailable("preview renderer disabled (PPT_RENDERER=none)")
    if renderer == "powerpoint":
        return _render_powerpoint(pptx, outdir, width)
    if renderer == "aspose":
        return _render_aspose(pptx, outdir, width)
    return _render_soffice(pptx, outdir, width)


# ---------------------------------------------------------------- opaque ids + signed asset URLs
DECK_ID_HEX = 16  # 64 bits: far beyond what enumeration could reach, still short in a URL


def _deck_id(path: Path) -> str:
    """An opaque, stable id for a deck file.

    It used to be the filename slug ("2025-hiring-plan-175350"), which told anyone who could reach the
    server what the deck was called and made every preview directory guessable. Now it is derived from
    the resolved path, so ids are unguessable from the outside, stable across previews (the cache and
    the template gallery both rely on that), and unrelated to the title.
    """
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        resolved = str(path)
    digest = hashlib.sha256(resolved.encode("utf-8", "surrogatepass")).hexdigest()
    return "d" + digest[:DECK_ID_HEX]


def url_secret() -> bytes:
    """HMAC key for download URLs. A random per-process key is fine for dev; prod sets PPT_URL_SECRET."""
    explicit = os.environ.get("PPT_URL_SECRET") or load_config().get("url_secret")
    if explicit:
        return str(explicit).encode("utf-8")
    global _FALLBACK_SECRET
    if _FALLBACK_SECRET is None:
        _FALLBACK_SECRET = os.urandom(32)
    return _FALLBACK_SECRET


_FALLBACK_SECRET: bytes | None = None
URL_TTL_S = 3600


def sign_asset(deck_id: str, name: str, expires: int) -> str:
    payload = f"{deck_id}/{name}/{int(expires)}".encode("utf-8")
    return _hmac.new(url_secret(), payload, hashlib.sha256).hexdigest()[:32]


def signed_url(deck_id: str, name: str, ttl: int = URL_TTL_S) -> str:
    expires = int(time.time()) + int(ttl)
    sig = sign_asset(deck_id, name, expires)
    return f"{public_base()}/decks/{deck_id}/{name}?exp={expires}&sig={sig}"


def verify_signature(deck_id: str, name: str, expires: str | None, sig: str | None) -> bool:
    if not expires or not sig:
        return False
    try:
        exp = int(expires)
    except (TypeError, ValueError):
        return False
    if exp < int(time.time()):
        return False
    return _hmac.compare_digest(sign_asset(deck_id, name, exp), sig)


SLIDE_NAME_RE = re.compile(r"^slide-\d{1,3}\.png$")
DECK_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}\.(pptx|potx)$")


def deck_asset_path(deck_id: str, name: str, expires: str | None = None,
                    sig: str | None = None) -> Path | None:
    """The one policy for `/decks/<deck_id>/<name>`.

    Slide images are served by name inside the deck's own directory; everything else — `deck.json`
    (which leaked absolute host paths), `deck.pptx` (the user's content) and any stray file — requires
    a valid, unexpired signature. Nothing outside the deck directory is reachable by construction.
    """
    if not re.fullmatch(r"[A-Za-z0-9]{1,64}", deck_id or ""):
        return None
    directory = preview_root() / deck_id
    try:
        base = directory.resolve(strict=True)
    except OSError:
        return None
    if SLIDE_NAME_RE.match(name or ""):
        candidate = base / name
    elif DECK_NAME_RE.match(name or "") or name == "deck.json":
        if not verify_signature(deck_id, name, expires, sig):
            return None
        candidate = base / name
    else:
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or not str(resolved).startswith(str(base) + os.sep):
        return None
    return resolved


def build_preview(pptx_path: str | Path, title: str = "") -> Dict[str, Any]:
    src = deck_paths.resolve_deck(str(pptx_path))
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
    prs = Presentation(str(src))
    total = len(prs.slides)

    # Rendering is a view, not data: a host without LibreOffice must still get a valid deck and a
    # clear error instead of a failed tool call.
    render_info: Dict[str, Any] = {"renderer": "none", "hits": 0, "misses": 0}
    preview_error = None
    try:
        render_info = _prepare_previews(src, outdir, prs)
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        preview_error = f"{type(exc).__name__}: {str(exc)[:180]}"
    rendered = len(sorted(outdir.glob("slide-*.png")))
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
        "download": signed_url(deck_id, "deck.pptx"),
        "preview_error": preview_error,
        "render": render_info,
        "slides": slides,
    }
    (outdir / "deck.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def _prepare_previews(src: Path, outdir: Path, prs) -> Dict[str, Any]:
    """Fill ``outdir/slide-N.png`` using the cache and the warm renderer.

    Unchanged slides are hard-linked straight from the cache, so an edit that touches one slide
    re-renders one slide. The cold chain (fresh soffice -> PDF -> PyMuPDF) stays as the fallback
    for hosts without pyuno and for the PowerPoint/Aspose renderers.
    """
    width = preview_width()
    info: Dict[str, Any] = {"renderer": "cold", "hits": 0, "misses": 0, "warnings": []}
    started = time.perf_counter()
    outdir.mkdir(parents=True, exist_ok=True)

    if not cache_enabled() or not warm_enabled() or _pick_renderer() != "soffice":
        info["renderer"] = "cold"
        info["misses"] = _render(src, outdir, width)
        info["ms"] = round((time.perf_counter() - started) * 1000)
        return info

    try:
        renderer = render_warm.get_renderer(
            width=width,
            queue_max=_setting_int("PPT_RENDER_QUEUE_MAX", "render_queue_max", 256),
            timeout=float(_setting_int("PPT_RENDER_TIMEOUT_S", "render_timeout_s", 180)),
        )
    except Exception as exc:  # noqa: BLE001 - warm is an optimisation, never a hard requirement
        info["warnings"].append(f"warm renderer unavailable ({exc}); used cold soffice")
        renderer = None

    if renderer is None:
        info["renderer"] = "cold"
        info["misses"] = _render(src, outdir, width)
        info["ms"] = round((time.perf_counter() - started) * 1000)
        return info

    cache_root = render_cache.cache_root(preview_root())
    engine = render_warm.engine_tag()
    keys = render_cache.slide_keys(src, [str(s.part.partname) for s in prs.slides], engine, width)

    missing: List[int] = []
    for index, key in enumerate(keys, start=1):
        if render_cache.fetch(cache_root, key, outdir / f"slide-{index}.png"):
            info["hits"] += 1
        else:
            missing.append(index)

    if missing:
        try:
            paths = renderer.render(src, {i: keys[i - 1] for i in missing}, cache_root)
            for index, path in paths.items():
                if index not in missing:
                    continue
                shutil.copyfile(path, outdir / f"slide-{index}.png")
            info["renderer"] = "libreoffice-warm"
            info["misses"] = len(missing)
        except Exception as exc:  # noqa: BLE001 - degrade to the cold chain, keep the deck
            info["warnings"].append(f"warm render failed ({type(exc).__name__}: {exc}); used cold soffice")
            info["renderer"] = "cold"
            info["misses"] = _render(src, outdir, width)
            for index, key in enumerate(keys, start=1):
                png = outdir / f"slide-{index}.png"
                if png.is_file():
                    try:
                        render_cache.store(cache_root, key, png)
                    except OSError:
                        pass
    else:
        info["renderer"] = "cache"
    info["ms"] = round((time.perf_counter() - started) * 1000)
    info["cache_size"] = render_cache.count(cache_root)
    _maybe_prune(cache_root)
    if not info["warnings"]:
        info.pop("warnings")
    return info


_LAST_PRUNE = [0.0]


def _maybe_prune(cache_root: Path) -> None:
    """Trim old renders occasionally; never on a hot path and never blocking a request."""
    now = time.time()
    if now - _LAST_PRUNE[0] < 600:
        return
    _LAST_PRUNE[0] = now
    try:
        render_cache.prune(cache_root, limit=_setting_int("PPT_RENDER_CACHE_MAX", "render_cache_max", 5000))
    except Exception:  # noqa: BLE001 - pruning is housekeeping, not correctness
        pass


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

    @app.tool(meta=meta, title="Deck preview / Aperçu du diaporama",
              annotations=ToolAnnotations(title="Deck preview / Aperçu du diaporama"))
    def preview_deck(file: str) -> Dict[str, Any]:
        """Open the Deck Studio view for a .pptx on this machine.

        Renders every slide to an image and shows it in an interactive pane in the chat:
        thumbnails, click-to-zoom, slide count. Call this after build_deck to show the
        result. Returns the same data the view renders.

        Args:
            file: absolute path of a .pptx (e.g. the value returned by build_deck)
        """
        return build_preview(file)

    @app.tool(meta=meta, title="Last deck / Dernier diaporama",
              annotations=ToolAnnotations(title="Last deck / Dernier diaporama"))
    def get_last_deck() -> Dict[str, Any]:
        """Return the most recently previewed deck (the view uses this on open)."""
        data = last_preview()
        return data or {"slides": [], "templates": _template_gallery(), "deck_id": None, "title": "No deck yet"}

    @app.tool(title="Deck list / Liste des diaporamas",
              annotations=ToolAnnotations(title="Deck list / Liste des diaporamas"))
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
                _prepare_previews(src, outdir, Presentation(str(src)))
            except Exception:
                return []
    base = public_base()
    files = sorted(outdir.glob("slide-*.png"), key=lambda p: int(p.stem.split("-")[1]))
    return [f"{base}/decks/{outdir.name}/{f.name}" for f in files]


# Resolved per call, not at import time: `server.py --transport http` applies ppt-mcp.config.json
# *after* importing this module, so a module-level constant would silently ignore `upload_dir`.
DEFAULT_UPLOAD_DIR = "/home/hyma/dev/librechat/uploads"
UPLOAD_DIR = os.environ.get("PPT_UPLOAD_DIR", DEFAULT_UPLOAD_DIR)  # kept for callers/tests


def upload_dir() -> Path:
    return Path(os.environ.get("PPT_UPLOAD_DIR") or UPLOAD_DIR or DEFAULT_UPLOAD_DIR)


def _resolve_input(name: str) -> Path:
    """Find a deck by path or by filename inside the uploads directory — and keep it in bounds.

    Three accepted shapes, in this order: a LibreChat container path (translated to the upload dir), an
    absolute/relative path (validated by :mod:`paths`), or a bare filename searched for inside the
    upload directory only. Anything outside the configured roots raises :class:`paths.PathNotAllowed`.
    """
    raw = (name or "").strip().strip('"')
    if not raw:
        raise FileNotFoundError("empty path")

    for prefix in ("/app/uploads/", "/app/client/public/images/"):
        if raw.startswith(prefix):
            return deck_paths.resolve_deck(upload_dir() / raw[len(prefix):], suffixes=())

    direct = Path(os.path.expanduser(raw))
    if direct.is_absolute() or os.sep in raw or "/" in raw:
        return deck_paths.resolve_deck(raw, suffixes=())

    base = upload_dir()
    wanted = Path(raw).name
    if base.is_dir():
        candidates = sorted(base.rglob(wanted), key=lambda p: p.stat().st_mtime, reverse=True)
        candidates += sorted(base.rglob(f"*{wanted}*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for candidate in candidates:
            try:
                return deck_paths.resolve_deck(candidate, suffixes=())
            except (FileNotFoundError, deck_paths.PathNotAllowed):
                continue
    raise FileNotFoundError(
        f"'{wanted}' not found. Pass an absolute path, or the filename of a file uploaded to the chat."
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
