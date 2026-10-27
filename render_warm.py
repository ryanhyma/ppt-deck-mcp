"""Warm single-LibreOffice renderer: one soffice process, a serial queue, and coalescing.

Why a child process: this server runs in a venv built with a Python that has no pyuno bindings,
while LibreOffice's UNO bridge ships with the system Python (Linux) or LibreOffice's own
``python.exe`` (Windows). ``render_bridge.py`` runs under *that* interpreter and owns the
LibreOffice connection; this module owns the bridge's lifecycle and the request queue.

Guarantees the rest of the server relies on:

* **One soffice, reused.** Startup costs ~0.8 s per deck today; that cost is paid once.
* **Serial by construction.** LibreOffice is not safe for parallel document operations through a
  single connection, so exactly one worker thread talks to the bridge. Requests queue.
* **Coalescing.** N concurrent previews of the same deck/width/slide-set are one render.
* **Backpressure.** A bounded queue raises :class:`RenderBusy` instead of growing without limit.
* **Self-healing.** A wedged or crashed bridge/soffice is killed and replaced; a render that
  times out fails that request rather than wedging the queue.
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Dict, Optional

import render_cache

BRIDGE = Path(__file__).resolve().parent / "render_bridge.py"


class RenderBusy(RuntimeError):
    """The render queue is full — the caller should surface this, not block forever."""


class BridgeError(RuntimeError):
    """The bridge process could not render (crashed, timed out, or refused)."""


# --------------------------------------------------------------------------- interpreter discovery
_UNO_PYTHON_CACHE: Dict[str, Optional[str]] = {}


def uno_python(explicit: str | None = None) -> Optional[str]:
    """A Python that can ``import uno``, or None. Result is cached per candidate path."""
    candidate = explicit or os.environ.get("PPT_UNO_PYTHON") or ""
    if candidate:
        key = candidate
    elif os.name == "nt":
        key = _windows_uno_python() or ""
    else:
        key = shutil.which("python3") or ""
    if not key:
        return None
    if key not in _UNO_PYTHON_CACHE:
        _UNO_PYTHON_CACHE[key] = _can_import_uno(key)
    return key if _UNO_PYTHON_CACHE[key] else None


def _can_import_uno(python: str) -> bool:
    try:
        # RADIA: UNO discovery must not create a console on Windows.
        done = subprocess.run([python, "-c", "import uno"], capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return done.returncode == 0
    except Exception:  # noqa: BLE001 - a missing interpreter is not an error here
        return False


def _windows_uno_python() -> Optional[str]:
    """LibreOffice ships its own Python with the UNO bindings under ``program\\``."""
    from deck_preview import _soffice_bin  # noqa: PLC0415 - avoid an import cycle at module load

    binary = Path(_soffice_bin())
    if not binary.name:
        return None
    program = Path(os.path.expandvars(str(binary))).parent
    direct = program / "python.exe"
    if direct.is_file():
        return str(direct)
    for core in sorted(program.glob("python-core-*/python.exe"), reverse=True):
        if core.is_file():
            return str(core)
    return None


def soffice_version() -> str:
    """LibreOffice version, folded into the cache key so a LO upgrade invalidates old PNGs."""
    from deck_preview import _soffice_bin  # noqa: PLC0415

    binary = _soffice_bin()
    # RADIA: Windows' console launcher returns --version instead of opening LibreOffice.
    if os.name == "nt" and Path(binary).suffix.lower() == ".exe":
        cli = Path(binary).with_suffix(".com")
        if cli.is_file():
            binary = str(cli)
    try:
        # RADIA: The version probe is a separate LibreOffice process.
        out = subprocess.run([binary, "--version"], capture_output=True, stdin=subprocess.DEVNULL,
                             timeout=30, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except Exception:  # noqa: BLE001
        out = ""
    return out or "unknown"


_ENGINE_CACHE: list[str] = []


def engine_tag() -> str:
    if not _ENGINE_CACHE:
        _ENGINE_CACHE.append(f"lo-png-1|{soffice_version()}")
    return _ENGINE_CACHE[0]


# --------------------------------------------------------------------------- the bridge process
class Bridge:
    """One ``render_bridge.py`` child process, restarted on demand."""

    def __init__(self, python: str, soffice_bin: str, timeout: float = 180.0):
        self.python = python
        self.soffice_bin = soffice_bin
        self.timeout = timeout
        self.profile = Path(tempfile.mkdtemp(prefix="lo-warm-"))
        self.proc: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 0
        self.restarts = 0
        self._pgid: Optional[int] = None

    # -- lifecycle
    def ensure(self) -> None:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return
            self._spawn()

    def _spawn(self) -> None:
        if self.proc is not None:
            self._kill()
            self.restarts += 1
        cmd = [self.python, str(BRIDGE), "--soffice", self.soffice_bin,
               "--profile", str(self.profile)]
        kwargs = {}
        if os.name == "nt":  # pragma: no cover - Windows
            # RADIA: The UNO bridge itself must not create a console either.
            kwargs["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                       | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            kwargs["start_new_session"] = True  # own group: killpg leaves the server alone
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
            text=True, bufsize=1, env=dict(os.environ), **kwargs,
        )
        try:
            self._pgid = os.getpgid(self.proc.pid) if os.name != "nt" else None
        except Exception:  # noqa: BLE001
            self._pgid = None
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self.proc, self._lines),
                         daemon=True, name="render-bridge-reader").start()
        ready = self._next_line(timeout=max(self.timeout, 90))
        if not ready or ready.get("op") != "ready":
            self._kill()
            raise BridgeError(f"render bridge did not become ready: {ready}")

    def _pump(self, proc: subprocess.Popen, sink: "queue.Queue[str]") -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            sink.put(line)
        sink.put("")  # EOF marker
        # The bridge is gone; anything it started (soffice) must not outlive it, even when nobody
        # asks for another render for hours.
        self._reap_group()

    def _next_line(self, timeout: float) -> Optional[dict]:
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            return None
        if not line:
            return None
        try:
            return json.loads(line)
        except Exception:  # noqa: BLE001 - a non-JSON line is noise, keep reading
            return self._next_line(timeout)

    def _kill(self) -> None:
        """Stop the bridge *gracefully* first: a bridge that is killed cannot stop its soffice."""
        if self.proc is None:
            return
        proc = self.proc
        try:
            if proc.poll() is None:
                proc.terminate()  # SIGTERM -> the bridge stops soffice, then exits
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover - wedged bridge
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                       capture_output=True)
                    else:
                        os.killpg(os.getpgid(proc.pid), 9)
                    proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 - already gone
            pass
        self._reap_group()
        for stream in (proc.stdin, proc.stdout):
            try:
                stream and stream.close()
            except Exception:  # noqa: BLE001
                pass
        self.proc = None

    def _reap_group(self) -> None:
        """Kill anything left in the bridge's process group (soffice re-execs itself).

        This is what keeps a SIGKILLed bridge from leaking a headless LibreOffice: the bridge owns
        the group, soffice joins it, and the group dies with the bridge.
        """
        if os.name == "nt" or not self._pgid:
            return
        try:
            os.killpg(self._pgid, signal.SIGKILL)
        except Exception:  # noqa: BLE001 - no such group: already clean
            pass
        self._pgid = None

    def close(self) -> None:
        with self._lock:
            self._kill()

    # -- requests
    def call(self, request: dict, timeout: float | None = None) -> dict:
        timeout = timeout or self.timeout
        with self._lock:
            if self.proc is None or self.proc.poll() is not None:
                self._spawn()
            assert self.proc is not None and self.proc.stdin is not None
            self._next_id += 1
            request = dict(request, id=self._next_id)
            try:
                self.proc.stdin.write(json.dumps(request) + "\n")
                self.proc.stdin.flush()
            except Exception as exc:  # noqa: BLE001 - dead pipe: restart on the next call
                self._kill()
                raise BridgeError(f"render bridge pipe closed: {exc}") from exc
            deadline = time.time() + timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._kill()  # a render that outlives its budget leaves soffice unusable
                    raise BridgeError(f"render timed out after {timeout:.0f}s")
                reply = self._next_line(timeout=remaining)
                if reply is None:
                    self._kill()
                    raise BridgeError("render bridge exited mid-request")
                if reply.get("id") != request["id"]:
                    continue  # stale reply from a previous incarnation
                if not reply.get("ok"):
                    raise BridgeError(reply.get("error") or "render failed")
                return reply

    def stats(self) -> dict:
        assert self.proc is not None
        return {"pid": self.proc.pid, "alive": self.proc.poll() is None,
                "restarts": self.restarts, "python": self.python}


# --------------------------------------------------------------------------- queue + coalescing
class WarmRenderer:
    """Serial render queue over one warm LibreOffice, with per-key coalescing."""

    def __init__(self, python: str, soffice_bin: str, width: int = 1600,
                 queue_max: int = 256, timeout: float = 180.0):
        self.width = width
        self.bridge = Bridge(python, soffice_bin, timeout=timeout)
        self._queue: "queue.Queue[tuple]" = queue.Queue(maxsize=queue_max)
        self._inflight: Dict[tuple, Future] = {}
        self._guard = threading.Lock()
        self._stats = {"hits": 0, "misses": 0, "renders": 0, "coalesced": 0, "errors": 0,
                       "queue_full": 0, "slides": 0}
        self._worker = threading.Thread(target=self._run, daemon=True, name="render-worker")
        self._worker.start()
        atexit.register(self.close)

    # -- public API
    def live(self) -> bool:
        return self._worker.is_alive()

    def stats(self) -> dict:
        out = dict(self._stats)
        out["queued"] = self._queue.qsize()
        out["inflight"] = len(self._inflight)
        try:
            out["bridge"] = self.bridge.stats()
        except Exception:  # noqa: BLE001
            out["bridge"] = {}
        return out

    def render(self, src: Path, wanted: Dict[int, str], root: Path) -> Dict[int, Path]:
        """Render the slides in ``wanted`` (index -> cache key) and return index -> PNG path.

        Blocks until this request's slides exist in the cache. Raises :class:`RenderBusy` when the
        queue is saturated and :class:`BridgeError` when rendering fails.
        """
        key = (str(Path(src).resolve()), Path(src).stat().st_mtime_ns, Path(src).stat().st_size,
               int(self.width), tuple(sorted(wanted)))
        with self._guard:
            existing = self._inflight.get(key)
            if existing is not None:
                self._stats["coalesced"] += 1
                future = existing
                mine = False
            else:
                future = Future()
                self._inflight[key] = future
                mine = True
                try:
                    self._queue.put_nowait((key, Path(src), dict(wanted), Path(root), future))
                except queue.Full:
                    self._inflight.pop(key, None)
                    self._stats["queue_full"] += 1
                    raise RenderBusy(
                        f"render queue full ({self._queue.maxsize} pending); retry in a moment")
        return future.result(timeout=self.bridge.timeout + 30)

    # -- worker
    def _run(self) -> None:
        while True:
            key, src, wanted, root, future = self._queue.get()
            try:
                if not future.set_running_or_notify_cancel():
                    continue
                result = self._render_job(src, wanted, root)
                future.set_result(result)
            except BaseException as exc:  # noqa: BLE001 - never let one job kill the worker
                self._stats["errors"] += 1
                if not future.done():
                    future.set_exception(exc)
            finally:
                with self._guard:
                    self._inflight.pop(key, None)

    def _render_job(self, src: Path, wanted: Dict[int, str], root: Path) -> Dict[int, Path]:
        missing = {i: k for i, k in wanted.items() if not render_cache.has(root, k)}
        for i, k in wanted.items():
            if i not in missing:
                self._stats["hits"] += 1
        if not missing:
            return {i: render_cache.path_for(root, k) for i, k in wanted.items()}

        started = time.perf_counter()
        staging = Path(tempfile.mkdtemp(prefix="lo-render-"))
        try:
            reply = self.bridge.call({
                "op": "render",
                "pptx": str(Path(src).resolve()),
                "outdir": str(staging),
                "indices": sorted(missing),
                "width": int(self.width),
            })
            for page in reply.get("pages", []):
                index = int(page["index"])
                key = missing.get(index)
                if key is None:
                    continue
                render_cache.store(root, key, Path(page["path"]))
                self._stats["misses"] += 1
                self._stats["slides"] += 1
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        self._stats["renders"] += 1
        self._last_ms = round((time.perf_counter() - started) * 1000)
        self._stats["last_render_ms"] = self._last_ms
        return {i: render_cache.path_for(root, k) for i, k in wanted.items()}

    def close(self) -> None:
        try:
            self.bridge.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- singleton
_RENDERER: Optional[WarmRenderer] = None
_RENDERER_GUARD = threading.Lock()


def get_renderer(width: int = 1600, queue_max: int = 256,
                 timeout: float = 180.0) -> Optional[WarmRenderer]:
    """The process-wide warm renderer, or None when this host cannot run one."""
    global _RENDERER
    with _RENDERER_GUARD:
        if _RENDERER is not None and _RENDERER.live():
            return _RENDERER
        _sweep_stale_profiles()
        python = uno_python()
        if not python:
            return None
        from deck_preview import _soffice_bin  # noqa: PLC0415

        binary = _soffice_bin()
        if not (Path(binary).is_file() or shutil.which(binary)):
            return None
        _RENDERER = WarmRenderer(python, binary, width=width, queue_max=queue_max, timeout=timeout)
        return _RENDERER


def _sweep_stale_profiles(max_age_s: float = 3600) -> int:
    """Delete LibreOffice profiles left behind by an unclean shutdown (a SIGKILLed bridge)."""
    removed = 0
    now = time.time()
    for path in Path(tempfile.gettempdir()).glob("lo-warm-*"):
        try:
            if path.is_dir() and now - path.stat().st_mtime > max_age_s:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    return removed


def reset_renderer() -> None:
    """Test hook: drop the singleton (and its bridge) so the next call starts fresh."""
    global _RENDERER
    with _RENDERER_GUARD:
        if _RENDERER is not None:
            _RENDERER.close()
        _RENDERER = None
    _ENGINE_CACHE.clear()


if __name__ == "__main__":  # tiny manual smoke test: render every slide of a deck
    target = Path(sys.argv[1])
    from deck_preview import preview_root
    import render_cache as rc

    renderer = get_renderer()
    if renderer is None:
        raise SystemExit("no warm renderer available on this host")
    from pptx import Presentation

    parts = [str(s.part.partname) for s in Presentation(str(target)).slides]
    keys = rc.slide_keys(target, parts, engine_tag(), renderer.width)
    root = rc.cache_root(preview_root())
    out = renderer.render(target, dict(enumerate(keys, start=1)), root)
    for index, path in sorted(out.items()):
        print(f"slide {index}: {path}")
    print(renderer.stats())
