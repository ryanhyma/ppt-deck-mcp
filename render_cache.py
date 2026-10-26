"""Content-addressed cache for rendered slide images.

A slide's picture depends on its own XML plus everything shared with the rest of the deck:
slide size (presentation.xml), masters, layouts, theme, media, charts, diagrams and embedded
objects. Those are hashed once per deck into a "shared" digest; each slide then gets
``sha256(engine, width, shared, slide-xml)``, so:

* re-previewing an untouched deck renders nothing at all;
* an in-place edit (`style_slide`, `style_text`) renders only the slides whose XML changed;
* two decks that share a slide (same template, same content) share one cached PNG.

Anything that could change pixels but is not covered by the key (fonts installed on the host,
LibreOffice version) is folded into ``engine`` by the caller, which is why the engine tag must be
bumped when the renderer changes.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from pathlib import Path
from typing import Iterable, Sequence

CACHE_DIRNAME = ".cache"

# Parts that affect how every slide looks. Missing parts are simply skipped.
SHARED_PART_PREFIXES = (
    "ppt/presentation.xml",
    "ppt/slideMasters/",
    "ppt/slideLayouts/",
    "ppt/theme/",
    "ppt/charts/",
    "ppt/diagrams/",
    "ppt/embeddings/",
    "ppt/media/",
    "ppt/tableStyles.xml",
)


def cache_root(preview_root: Path) -> Path:
    root = Path(preview_root) / CACHE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _is_shared_part(name: str) -> bool:
    return any(name == p or (p.endswith("/") and name.startswith(p)) for p in SHARED_PART_PREFIXES)


def shared_digest(pptx: Path) -> str:
    """Hash every deck-wide part once (cheap: a few hundred KB of XML)."""
    h = hashlib.sha256()
    with zipfile.ZipFile(pptx) as zf:
        for name in sorted(zf.namelist()):
            if not _is_shared_part(name):
                continue
            h.update(name.encode())
            h.update(hashlib.sha256(zf.read(name)).digest())
    return h.hexdigest()


def slide_key(shared: str, engine: str, width: int, slide_xml: bytes, scope: str = "") -> str:
    h = hashlib.sha256()
    h.update(scope.encode())
    h.update(b"|")
    h.update(engine.encode())
    h.update(b"|")
    h.update(str(int(width)).encode())
    h.update(b"|")
    h.update(shared.encode())
    h.update(b"|")
    h.update(hashlib.sha256(slide_xml).digest())
    return h.hexdigest()


def slide_keys(pptx: Path, part_names: Sequence[str], engine: str, width: int,
               scope: str = "") -> list[str]:
    """One cache key per slide, in deck order (``part_names`` from python-pptx).

    ``scope`` namespaces the cache per subject: two people who happen to hold decks with identical
    content must not share rendered files (a shared cache is an access path, not just an optimisation).
    """
    shared = shared_digest(pptx)
    keys: list[str] = []
    with zipfile.ZipFile(pptx) as zf:
        for part in part_names:
            name = part.lstrip("/")
            try:
                xml = zf.read(name)
            except KeyError:  # a part we cannot read must never collide with another slide
                xml = name.encode() + b"\0missing"
            keys.append(slide_key(shared, engine, width, xml, scope))
    return keys


def path_for(root: Path, key: str) -> Path:
    return Path(root) / key[:2] / f"{key}.png"


def has(root: Path, key: str) -> bool:
    try:
        return path_for(root, key).stat().st_size > 0
    except OSError:
        return False


def fetch(root: Path, key: str, dest: Path) -> bool:
    """Copy a cached PNG into ``dest``. Returns False on a miss."""
    src = path_for(root, key)
    try:
        if src.stat().st_size <= 0:
            return False
    except OSError:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)  # same volume: no copy
    except OSError:
        shutil.copyfile(src, dest)
    return True


def store(root: Path, key: str, png: Path) -> None:
    dest = path_for(root, key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(f".{os.getpid()}.{os.urandom(3).hex()}.tmp")
    shutil.copyfile(png, tmp)
    os.replace(tmp, dest)


def count(root: Path) -> int:
    try:
        return sum(1 for _ in Path(root).glob("*/*.png"))
    except OSError:
        return 0


def prune(root: Path, limit: int = 5000) -> int:
    """Keep the newest ``limit`` entries. Returns how many were removed."""
    root = Path(root)
    try:
        files = sorted((p for p in root.glob("*/*.png") if p.is_file()),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return 0
    removed = 0
    for stale in files[max(limit, 0):]:
        try:
            stale.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def clear(root: Path) -> int:
    """Drop every cached render (used by tests and by a manual `?nocache` style recovery)."""
    removed = 0
    for p in Path(root).glob("*/*.png") if Path(root).is_dir() else []:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


__all__: Iterable[str] = [
    "CACHE_DIRNAME", "cache_root", "shared_digest", "slide_key", "slide_keys",
    "path_for", "has", "fetch", "store", "count", "prune", "clear",
]
