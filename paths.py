"""Where a deck file may come from.

Every tool that takes a `file` argument used to accept any path that existed, which made
`preview_deck` a file-read primitive: render anything, then download the copy from the asset route.
This module is the single place that decides whether a path is in bounds.

Rules:

* the path is resolved first (symlinks, `..`, Windows short names) and then checked for containment,
  so a symlink inside the upload directory cannot point out of it;
* only these roots are allowed: the deck workspace (`PPT_OUTPUT_DIR`), the upload directory
  (`PPT_UPLOAD_DIR`), the template directories (`PPT_TEMPLATE_PATH`), plus anything explicitly listed
  in `PPT_ALLOWED_ROOTS` (colon/semicolon separated) for corpora that live elsewhere;
* the file must have a supported suffix and be a regular file.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

DECK_SUFFIXES = (".pptx", ".potx")


class PathNotAllowed(PermissionError):
    """The path exists but is outside every configured root (or is the wrong kind of file)."""


def _split(value: str | None) -> Iterable[str]:
    if not value:
        return ()
    return [part for part in value.replace(";", os.pathsep).split(os.pathsep) if part.strip()]


def _configured_roots() -> list[Path]:
    """Roots in priority order, from the same env vars the rest of the server uses."""
    raw: list[str] = []
    raw += list(_split(os.environ.get("PPT_OUTPUT_DIR")))
    raw += list(_split(os.environ.get("PPT_UPLOAD_DIR")))
    raw += list(_split(os.environ.get("PPT_TEMPLATE_PATH")))
    raw += list(_split(os.environ.get("PPT_ALLOWED_ROOTS")))
    roots: list[Path] = []
    for item in raw:
        path = Path(os.path.expanduser(item.strip())).resolve() if item.strip() else None
        if path and path not in roots:
            roots.append(path)
    return roots


def allowed_roots() -> list[Path]:
    """Public list, used by error messages and tests."""
    roots = _configured_roots()
    if not roots:  # nothing configured (bare import in a test): fall back to the module's own dir
        roots = [Path(__file__).resolve().parent]
    return roots


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def describe_roots() -> str:
    return ", ".join(str(r) for r in allowed_roots())


def is_allowed(path: Path) -> bool:
    """True when an already-resolved path is inside an allowed root."""
    return any(_within(path, root) for root in allowed_roots())


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def resolve_image(ref: str | os.PathLike[str]) -> Path:
    """Resolve an image reference: an id returned by render_svg, or a path inside the allowed roots."""
    text = str(ref or "").strip().strip('"')
    if text.startswith("i") and len(text) == 17 and all(c in "0123456789abcdef" for c in text[1:]):
        import auth
        import deck_registry
        entry = deck_registry.image_entry(Path(os.environ.get("PPT_OUTPUT_DIR", Path(__file__).resolve().parent / "out")) / "previews", text)
        if entry:
            owner = auth.current_identity().scope_hint if not auth.current_identity().anonymous else "anonymous"
            if auth.ownership_enabled() and entry["owner"] != owner:
                raise FileNotFoundError(f"unknown image id {text!r}")
            return resolve_deck(entry["source"], suffixes=(".png",))
        if auth.ownership_enabled():
            raise FileNotFoundError(f"unknown image id {text!r}")
        for root in allowed_roots():
            if not root.is_dir():
                continue
            for candidate in root.rglob(f"*-{text}.png"):
                if candidate.is_file():
                    return resolve_deck(candidate, suffixes=(".png",))
        raise FileNotFoundError(f"unknown image id {text!r} — call render_svg first")
    raise ValueError("Use an image ID returned by render_svg or enhance_image.")


def resolve_deck(name: str | os.PathLike[str], *, suffixes: tuple[str, ...] | None = None) -> Path:
    """Resolve a user-supplied deck path, or raise: FileNotFoundError / PathNotAllowed.

    ``suffixes`` defaults to the decks we can read (.pptx/.potx); callers that also accept an
    uploaded image pass their own tuple.
    """
    raw = str(name or "").strip().strip('"')
    if not raw:
        raise FileNotFoundError("empty path")
    candidate = Path(os.path.expanduser(raw))
    try:
        resolved = candidate.resolve(strict=True)  # follows symlinks; raises for a missing path
    except (OSError, RuntimeError) as exc:
        raise FileNotFoundError(f"no file at '{raw}' — check the path, or pass just the filename") from exc
    if not resolved.is_file():
        raise FileNotFoundError(f"'{raw}' is not a file")
    if not is_allowed(resolved):
        raise PathNotAllowed(
            f"'{raw}' is outside the allowed locations ({describe_roots()}). "
            "Pass a deck that was uploaded to the chat or built here."
        )
    wanted = suffixes if suffixes is not None else DECK_SUFFIXES
    if wanted and resolved.suffix.lower() not in wanted:
        raise ValueError(f"unsupported file type {resolved.suffix!r} — expected {' / '.join(wanted)}")
    return resolved
