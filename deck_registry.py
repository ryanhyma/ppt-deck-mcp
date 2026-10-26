"""Deck ids: the only handle the MCP surface hands out.

The server must never return a filesystem path to a caller. A path tells the client where the box keeps
its data, names the deck whether or not it wanted to, and invites a follow-up call with that path (which
is how `preview_deck` became a file-read primitive before 2026-10-26).

So each deck gets an opaque id, and the mapping id -> source file lives on disk next to the previews, in
a dotfile the asset route will not serve:

    previews/.index/<deck_id>      one line: the resolved path of that deck
    previews/<deck_id>/deck.json   the metadata the tools return (no paths in it)

The index lives *outside* the per-deck directory on purpose: a preview wipes and rebuilds its own
directory, and an id a caller is holding must never become unresolvable in the middle of that.

"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

DECK_ID_RE = re.compile(r"^d[0-9a-f]{16}$")
INDEX_DIRNAME = ".index"  # dot-directory: not matched by deck_asset_path(), so never served


def deck_id(path: str | os.PathLike[str]) -> str:
    """Stable, opaque id for a deck file (16 hex chars ≈ 64 bits: enumeration is not a threat model)."""
    try:
        resolved = str(Path(path).resolve())
    except OSError:  # pragma: no cover - a path that cannot be resolved still needs an id
        resolved = str(path)
    return "d" + hashlib.sha256(resolved.encode("utf-8", "surrogatepass")).hexdigest()[:16]


IMAGE_ID_RE = re.compile(r"^i[0-9a-f]{16}$")


def new_image_id() -> str:
    """A fresh opaque id for a rendered image; the filename carries it (``…-i<16 hex>.png``)."""
    return "i" + os.urandom(8).hex()


def _index_file(previews: Path, deck_id: str) -> Path:
    return Path(previews) / INDEX_DIRNAME / deck_id


def record(previews: Path, deck_id: str, source: Path) -> None:
    """Remember which file a deck id refers to. Survives previews wiping their own directory."""
    target = _index_file(previews, deck_id)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(str(Path(source).resolve()), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:  # pragma: no cover - read-only workspace: previews still work, edits by id may not
        pass


def source_for(previews: Path, deck_id: str) -> Path | None:
    """The source path recorded for a deck id, if the record is still there."""
    try:
        raw = _index_file(previews, deck_id).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(raw) if raw else None
