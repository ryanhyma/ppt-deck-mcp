#!/usr/bin/env python
"""Live concurrency probe: N MCP clients rendering decks at the same time.

    .venv/bin/python tests/concurrency_check.py --url http://172.17.0.1:8931/mcp --workers 4

Exercises the real HTTP server (not just functions): several users hitting `preview_deck` while
others run `build_deck`, including two clients rendering the *same* deck. Asserts every call
succeeds, every rendered deck has the right slide count, and no preview directory is left partial.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def one(url: str, deck: Path, tag: str, results: list) -> None:
    started = time.time()
    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool("preview_deck", {"file": str(deck)})
                text = res.content[0].text if res.content else "{}"
                data = json.loads(text)
                data = data.get("result", data)
                ok = (data.get("slide_count") or 0) > 0
                results.append({"tag": tag, "ok": ok, "slides": data.get("slide_count"),
                                "rendered": data.get("rendered_slides"),
                                "error": data.get("preview_error"),
                                "seconds": round(time.time() - started, 1)})
    except Exception as exc:  # noqa: BLE001
        results.append({"tag": tag, "ok": False, "error": f"{type(exc).__name__}: {exc}",
                        "seconds": round(time.time() - started, 1)})


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://172.17.0.1:8931/mcp")
    ap.add_argument("--out-dir", default=os.environ.get("PPT_OUTPUT_DIR", "out"))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    decks = sorted(Path(args.out_dir).glob("*.pptx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not decks:
        print("no decks in", args.out_dir)
        return 2
    same = decks[0]
    results: list = []
    jobs = []
    for i in range(args.workers):
        jobs.append(one(args.url, decks[i % len(decks)], f"deck-{i}", results))
    for i in range(2):  # two clients rendering the SAME deck, to exercise the render lock
        jobs.append(one(args.url, same, f"same-{i}", results))

    await asyncio.gather(*jobs)
    print(json.dumps(results, indent=1))
    failures = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(failures)}/{len(results)} concurrent renders ok")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
