#!/usr/bin/env python
"""Warm LibreOffice render bridge: one soffice process, page-level PNG export over UNO.

This file must run under a Python that can ``import uno`` — the system Python on Linux, or
LibreOffice's own ``python.exe`` on Windows. The MCP server (a venv without pyuno) launches it
as a child process and talks to it with one JSON object per line on stdin/stdout.

Protocol (one request per line, one reply per line):

    -> {"id": 1, "op": "render", "pptx": "/abs/deck.pptx", "outdir": "/abs/dir",
        "indices": [1, 3], "width": 1600}
    <- {"id": 1, "ok": true, "pages": [{"index": 1, "path": "...", "width": 1600, "height": 900}],
        "errors": [], "ms": 412}

    -> {"id": 2, "op": "ping"}
    <- {"id": 2, "ok": true, "pid": 1234, "docs": 0}

Rendering a subset of slides is the point: the caller caches unchanged slides and asks for the
misses only. Slides are exported by LibreOffice's own ``impress_png_Export`` filter, so no
intermediate PDF and no second rasteriser are involved.

If the UNO connection dies the bridge exits non-zero; the parent restarts it (a fresh process
with a fresh profile is the only reliable recovery for a wedged soffice).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LOG = sys.stderr


def log(msg: str) -> None:
    LOG.write(f"[render_bridge] {msg}\n")
    LOG.flush()


# --------------------------------------------------------------------------- soffice supervision
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Soffice:
    """A headless soffice listening on a UNO socket, started once and reused."""

    def __init__(self, binary: str, profile: Path | None = None, port: int = 0,
                 ready_timeout: float = 60.0, soffice_args: list[str] | None = None):
        self.binary = binary
        self.port = port or free_port()
        self.ready_timeout = ready_timeout
        self.soffice_args = soffice_args or []
        self.profile = Path(profile) if profile else Path(tempfile.mkdtemp(prefix="lo-warm-"))
        self.profile.mkdir(parents=True, exist_ok=True)
        self.proc: subprocess.Popen | None = None
        self.ctx = None
        self.desktop = None

    # -- lifecycle
    def start(self):
        self.proc = subprocess.Popen(
            [
                self.binary,
                f"-env:UserInstallation={self._profile_url()}",
                "--headless", "--invisible", "--nologo", "--nodefault",
                "--nofirststartwizard", "--norestore", "--nolockcheck",
                f"--accept=socket,host=127.0.0.1,port={self.port};urp;StarOffice.ComponentContext",
                *self.soffice_args,
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            # No new session: soffice joins the bridge's process group, so one killpg by the parent
            # reaps both if the bridge is killed abruptly.
        )
        self._connect_when_ready()
        return self

    def _profile_url(self) -> str:
        p = str(self.profile.resolve())
        if os.name == "nt":  # file:///C:/Users/... => three slashes before the drive letter
            return "file:///" + p.replace("\\", "/").lstrip("/")
        return "file://" + p

    def _connect_when_ready(self):
        import uno  # noqa: PLC0415  (only available in the bridge interpreter)

        url = f"uno:socket,host=127.0.0.1,port={self.port};urp;StarOffice.ComponentContext"
        deadline = time.time() + self.ready_timeout
        last = None
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f"soffice exited early with code {self.proc.returncode}")
            try:
                local = uno.getComponentContext()
                resolver = local.ServiceManager.createInstanceWithContext(
                    "com.sun.star.bridge.UnoUrlResolver", local)
                self.ctx = resolver.resolve(url)
                self.desktop = self.ctx.ServiceManager.createInstanceWithContext(
                    "com.sun.star.frame.Desktop", self.ctx)
                log(f"connected on port {self.port} (pid {self.proc.pid if self.proc else '?'})")
                return
            except Exception as exc:  # noqa: BLE001 - retry until the deadline
                last = exc
                time.sleep(0.25)
        raise RuntimeError(f"soffice did not accept a UNO connection in {self.ready_timeout:.0f}s: {last}")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - stubborn soffice
                self.proc.kill()
                self.proc.wait(timeout=10)
        _reap_children_of(self.profile)
        if self.profile and self.profile.is_dir():
            shutil.rmtree(self.profile, ignore_errors=True)


# --------------------------------------------------------------------------- process hygiene
def _reap_children_of(profile: Path | None) -> None:
    """Last resort: a soffice.bin still carrying our throwaway profile must not survive us."""
    if not profile or os.name == "nt":
        return
    try:
        subprocess.run(["pkill", "-f", str(profile)], capture_output=True)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- page rendering
def _prop(uno, name, value):
    p = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    p.Name = name
    p.Value = value
    return p


def render_pages(soffice: Soffice, pptx: Path, outdir: Path, indices: list[int],
                 width: int) -> list[dict]:
    """Export the requested 1-based page indices to ``outdir/slide-<index>.png``."""
    import uno  # noqa: PLC0415

    outdir.mkdir(parents=True, exist_ok=True)
    src_url = uno.systemPathToFileUrl(str(Path(pptx).resolve()))
    doc = soffice.desktop.loadComponentFromURL(
        src_url, "_blank", 0,
        (_prop(uno, "Hidden", True), _prop(uno, "ReadOnly", True)),
    )
    pages_out: list[dict] = []
    try:
        draw_pages = doc.getDrawPages()
        total = draw_pages.getCount()
        smgr = soffice.ctx.ServiceManager
        for index in indices:
            if index < 1 or index > total:
                raise IndexError(f"slide {index} out of range (deck has {total})")
            page = draw_pages.getByIndex(index - 1)
            pw, ph = float(page.Width), float(page.Height)
            height = int(round(width * ph / pw)) if pw else width
            path = outdir / f"slide-{index}.png"
            gef = smgr.createInstanceWithContext("com.sun.star.drawing.GraphicExportFilter", soffice.ctx)
            gef.setSourceDocument(page)
            gef.filter((
                _prop(uno, "URL", uno.systemPathToFileUrl(str(path.resolve()))),
                _prop(uno, "MediaType", "image/png"),
                _prop(uno, "FilterData", uno.Any("[]com.sun.star.beans.PropertyValue", (
                    _prop(uno, "PixelWidth", int(width)),
                    _prop(uno, "PixelHeight", int(height)),
                ))),
            ))
            if not path.is_file():
                raise RuntimeError(f"LibreOffice wrote no PNG for slide {index}")
            pages_out.append({"index": index, "path": str(path), "width": int(width),
                             "height": int(height)})
        return pages_out
    finally:
        try:
            doc.close(True)
        except Exception:  # noqa: BLE001 - closing a broken doc must not mask the real error
            pass


# --------------------------------------------------------------------------- main loop
def handle(soffice: Soffice, req: dict) -> dict:
    op = req.get("op") or "render"
    if op == "ping":
        return {"id": req.get("id"), "ok": True, "pid": soffice.proc.pid if soffice.proc else None,
                "port": soffice.port, "alive": soffice.alive()}
    if op != "render":
        return {"id": req.get("id"), "ok": False, "error": f"unknown op {op!r}"}

    pptx = Path(req["pptx"])
    if not pptx.is_file():
        return {"id": req.get("id"), "ok": False, "error": f"deck not found: {pptx}"}
    indices = [int(i) for i in (req.get("indices") or [])]
    if not indices:
        return {"id": req.get("id"), "ok": True, "pages": [], "errors": [], "ms": 0}
    width = int(req.get("width") or 1600)
    started = time.perf_counter()
    try:
        pages = render_pages(soffice, pptx, Path(req["outdir"]), indices, width)
        return {"id": req.get("id"), "ok": True, "pages": pages, "errors": [],
                "ms": round((time.perf_counter() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001 - report, let the parent decide about restarting
        return {"id": req.get("id"), "ok": False, "error": f"{type(exc).__name__}: {exc}",
                "ms": round((time.perf_counter() - started) * 1000)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Warm LibreOffice page renderer (JSON lines on stdin/stdout)")
    ap.add_argument("--soffice", default=os.environ.get("SOFFICE_BIN") or "soffice")
    ap.add_argument("--profile", default=os.environ.get("PPT_UNO_PROFILE") or "")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PPT_UNO_PORT") or 0))
    ap.add_argument("--ready-timeout", type=float, default=60.0)
    args = ap.parse_args()

    if not (Path(args.soffice).is_file() or shutil.which(args.soffice)):
        log(f"soffice not found: {args.soffice}")
        return 2

    soffice = Soffice(args.soffice, profile=Path(args.profile) if args.profile else None,
                      port=args.port, ready_timeout=args.ready_timeout)
    try:
        soffice.start()
    except Exception as exc:  # noqa: BLE001
        log(f"startup failed: {exc}")
        soffice.stop()
        return 3

    # A parent that disappears (SIGTERM/SIGKILL of the server, a killed bridge) must not leave a
    # headless LibreOffice behind: turning these signals into a clean shutdown is what stops it.
    def _shutdown(signum, _frame):
        # SIGTERM is what our own parent sends on close: routine, so keep the log quiet.
        if signum != signal.SIGTERM or os.environ.get("PPT_DEBUG"):
            log(f"signal {signum}: stopping soffice")
        soffice.stop()
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            signal.signal(sig, _shutdown)

    # Tell the parent we are ready (first line of stdout) with the port we actually used, so a
    # random port is discoverable even though the parent did not choose it.
    print(json.dumps({"op": "ready", "port": soffice.port}), flush=True)

    fatal = 0
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"ok": False, "error": f"bad request: {exc}"}), flush=True)
                continue
            if (req.get("op") or "render") == "shutdown":
                print(json.dumps({"id": req.get("id"), "ok": True}), flush=True)
                break
            reply = handle(soffice, req)
            print(json.dumps(reply), flush=True)
            # A dead soffice is not recoverable from inside; exit so the parent starts a new one.
            if not soffice.alive():
                log("soffice died during a request; exiting for a restart")
                fatal = 4
                break
    finally:
        soffice.stop()
    return fatal


if __name__ == "__main__":
    raise SystemExit(main())
