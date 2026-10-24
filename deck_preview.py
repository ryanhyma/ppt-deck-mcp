"""Deck preview: render a .pptx to PNGs and expose them to the Deck Studio MCP App.

The App lives in `apps/deck_studio/`. It never reads the filesystem; it gets slide URLs in
the tool result and loads them from this server's `/decks/...` HTTP route.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from pptx import Presentation

APP_URI = "ui://deck-studio/index.html"
PREVIEW_ROOT = "previews"

# Set by register_preview_tools(): lets the view show the template gallery.
_TEMPLATE_DIRS_FN = None


def public_base() -> str:
    return os.environ.get("PPT_PUBLIC_BASE", "http://host.docker.internal:8931").rstrip("/")


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


def _render(pptx: Path, outdir: Path) -> int:
    """pptx -> pdf -> png. Returns slide count. Raises on failure."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmp, str(pptx)],
            check=True,
            capture_output=True,
            timeout=180,
        )
        pdf = next(Path(tmp).glob("*.pdf"), None)
        if pdf is None:
            raise RuntimeError("soffice produced no PDF")
        subprocess.run(
            ["pdftoppm", "-png", "-r", "90", str(pdf), str(outdir / "slide")],
            check=True,
            capture_output=True,
            timeout=180,
        )
    return len(sorted(outdir.glob("slide-*.png")))


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
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)

    # Copy the deck next to its previews so the browser can download it from this server.
    shipped = outdir / "deck.pptx"
    shutil.copyfile(src, shipped)

    count = _render(src, outdir)
    labels = _slide_labels(src)
    base = public_base()

    slides = [
        {
            "index": i,
            "label": labels[i - 1] if i - 1 < len(labels) else "",
            "url": f"{base}/decks/{deck_id}/slide-{i}.png",
        }
        for i in range(1, count + 1)
    ]
    data = {
        "templates": _template_gallery(),
        "deck_id": deck_id,
        "title": title or labels[0] if labels else (title or src.stem),
        "file": str(src),
        "slide_count": count,
        "download": f"{base}/decks/{deck_id}/deck.pptx",
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
