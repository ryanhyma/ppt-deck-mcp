"""Append-only audit log: one JSON object per line.

Protected B needs to answer "who read or changed which deck, and what was refused" for up to a couple of
years (AU-2/AU-3/AU-6/AU-9/AU-12). This module is deliberately small and dependency-free, because it has
to keep working when everything else is degraded.

Rules, all of them pinned by `tests/test_audit.py`:

* the actor is taken from ``auth.current_identity()`` — never from an argument, so a caller cannot claim
  somebody else's identity in the record;
* values are redacted before they are written: filesystem paths become ``[path]``, bearer tokens become
  ``[redacted]``. A log must not become a copy of the data it protects;
* a failed write never raises into the request path (a full disk must not stop somebody building a deck),
  but it is reported through :func:`last_error` and to stderr once;
* the file rotates at ``PPT_AUDIT_MAX_BYTES`` (default 10 MiB) so it cannot fill a Protected B disk;
  ship the files to the SIEM — this is the local buffer, not the system of record.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_LOCK = threading.Lock()
_LAST_ERROR: str | None = None
_WARNED = False
_REDACTED = "[redacted]"
_PATH = "[path]"

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)


def log_path() -> Path:
    explicit = os.environ.get("PPT_AUDIT_LOG")
    if explicit:
        return Path(os.path.expanduser(explicit))
    out = os.environ.get("PPT_OUTPUT_DIR") or str(Path(__file__).resolve().parent / "out")
    return Path(out) / "audit.jsonl"


def _max_bytes() -> int:
    try:
        return max(1024, int(os.environ.get("PPT_AUDIT_MAX_BYTES", DEFAULT_MAX_BYTES)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_BYTES


def last_error() -> str | None:
    """The last write failure, if any. Surfaced by /healthz and the startup log."""
    return _LAST_ERROR


def reset() -> None:
    """Test hook: forget cached state (the path is read per call, so only the error flag matters)."""
    global _LAST_ERROR, _WARNED
    with _LOCK:
        _LAST_ERROR = None
        _WARNED = False


def _looks_like_path(value: str) -> bool:
    if value.startswith("/") or value.startswith("\\\\"):
        return True
    if len(value) > 2 and value[1] == ":" and value[2] in "\\/":  # C:\...
        return True
    return False


def _redact(value: Any, key: str = "") -> Any:
    """Strip the two things that must never be written: paths and credentials."""
    if isinstance(value, str):
        value = _BEARER_RE.sub(_REDACTED, value)
        if _looks_like_path(value):
            return _PATH
        return value
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v, key) for v in value]
    if isinstance(value, Path):
        return _PATH
    return value


def record(event: str, **fields: Any) -> dict:
    """Append one event. Never raises for I/O problems; see :func:`last_error`."""
    try:
        import auth  # noqa: PLC0415 - optional during early startup/tests

        identity = auth.current_identity()
    except Exception:  # noqa: BLE001
        identity = None

    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "event": event,
    }
    if identity is not None:
        payload["subject"] = identity.subject
        if identity.tenant:
            payload["tenant"] = identity.tenant
        if identity.mode:
            payload["auth"] = identity.mode
    payload.update(_redact({k: v for k, v in fields.items() if v is not None}))

    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    _write(line)
    return payload


def _write(line: str) -> None:
    global _LAST_ERROR, _WARNED
    path = log_path()
    with _LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                too_big = path.stat().st_size + len(line.encode()) > _max_bytes()
            except OSError:
                too_big = False
            if too_big:
                rotated = path.with_suffix(path.suffix + ".1")
                os.replace(path, rotated)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            _LAST_ERROR = None
        except OSError as exc:
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"
            if not _WARNED:
                _WARNED = True
                print(f"[audit] cannot write {path}: {exc}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- convenience recorders


def record_tool(tool: str, *, deck_id: str = "", outcome: str = "ok", ms: int | None = None,
                error: str = "") -> None:
    record("tool.call", tool=tool, deck_id=deck_id or None, outcome=outcome, ms=ms,
           error=error or None)


def record_asset(*, deck_id: str, name: str, outcome: str) -> None:
    # Only the name and decision: never the resolved path.
    record("asset.get", deck_id=deck_id, name=Path(name).name, outcome=outcome)


def record_auth_failure(*, path: str, status: int, error: str) -> None:
    record("auth.reject", path=path, status=status, error=error)


def wrap_tool_fns(app) -> int:
    """Record every tool call in one place, so no caller has to remember to.

    Wraps ``tool.fn`` after registration (schemas are already built), which covers our tools and the
    vendored upstream ones alike. Returns how many tools were wrapped.
    """
    import functools
    import time

    wrapped = 0
    for name, tool in getattr(app, "_tool_manager", type("X", (), {"_tools": {}})())._tools.items():
        original = getattr(tool, "fn", None)
        if original is None or getattr(original, "__audit_wrapped__", False):
            continue

        @functools.wraps(original)
        def wrapped_fn(*args, _original=original, _name=name, **kwargs):
            started = time.perf_counter()
            deck_id = ""
            try:
                result = _original(*args, **kwargs)
                if isinstance(result, dict):
                    deck_id = str(result.get("deck_id") or "")
                record_tool(_name, deck_id=deck_id, outcome="ok",
                            ms=round((time.perf_counter() - started) * 1000))
                return result
            except Exception as exc:  # noqa: BLE001 - record, then re-raise untouched
                deck_id = str(kwargs.get("file") or "") if str(kwargs.get("file", "")).startswith("d") else ""
                record_tool(_name, deck_id=deck_id, outcome="error",
                            ms=round((time.perf_counter() - started) * 1000),
                            error=type(exc).__name__)
                raise

        wrapped_fn.__audit_wrapped__ = True
        tool.fn = wrapped_fn
        wrapped += 1
    return wrapped
