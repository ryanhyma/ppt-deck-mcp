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
from pathlib import Path
from typing import Any, Dict, List

from pptx import Presentation

APP_URI = "ui://deck-studio/index.html"
PREVIEW_ROOT = "previews"


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
        "deck_id": deck_id,
        "title": title or labels[0] if labels else (title or src.stem),
        "file": str(src),
        "slide_count": count,
        "slides": slides,
    }
    (outdir / "deck.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


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


def register_preview_tools(app) -> None:
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
        return data or {"slides": [], "deck_id": None, "title": "No deck yet"}

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
