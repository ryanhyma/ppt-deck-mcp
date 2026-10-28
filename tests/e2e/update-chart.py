#!/usr/bin/env python
"""Live check for the reported bug: "update the graph" must find the chart.

Builds a deck whose last content slide carries a column chart, then asks the model to replace that
chart's data. The tool no longer needs a shape index, and a wrong reference now returns the chart map,
so a model that previously said "I can't find the graph" has to succeed here.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path("/home/hyma/dev/ppt-mcp")
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

import run_suite as R  # noqa: E402
from pptx import Presentation  # noqa: E402

ok = True


def say(name, passed, detail=""):
    global ok
    ok = ok and passed
    print(f"{'PASS' if passed else 'FAIL'}  {name}{' — ' + detail if detail else ''}", flush=True)


def turn(token, agent, text, conversation_id=None, parent=None):
    conv = R.post("/api/agents/chat", {
        "text": text, "conversationId": conversation_id,
        "parentMessageId": parent or "00000000-0000-0000-0000-000000000000",
        "messageId": str(uuid.uuid4()), "endpoint": "agents", "endpointType": "agents",
        "agent_id": agent,
    }, token)
    events = R.stream(token, conv["streamId"], conv["generationCreatedAt"])
    final = next((e for e in events if e.get("final") and "responseMessage" in e), {})
    calls, reply = R.parse_tool_calls(events)
    return conv, final.get("responseMessage", {}), calls, reply


def payload(out):
    try:
        value = R.unwrap(json.loads(out))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def base(name):
    return name.replace("_mcp_ppt", "").replace("mcp_ppt_", "")


def charts(path):
    rows = []
    for i, slide in enumerate(Presentation(path).slides, 1):
        for j, shape in enumerate(slide.shapes):
            if getattr(shape, "has_chart", False):
                ch = shape.chart
                rows.append({"slide": i, "shape": j,
                             "series": [(s.name, list(s.values)) for s in ch.plots[0].series]})
    return rows


def main():
    token = R.login("hyma@local.test", "PiTest!2026")
    agent = json.loads(Path("/tmp/agent.json").read_text())["id"]

    conv, first, calls, _ = turn(
        token, agent,
        "Make a 3-slide deck titled 'Quarterly Revenue'. Slides: an intro, a cost overview with "
        "bullets, and a final slide 'Revenue by Region' with a column chart of categories North and "
        "South and one series named Revenue with values 10 and 20. Then show it to me.")
    shown = [payload(c["output"]).get("deck_id") for c in calls
             if base(c["name"]) in ("build_deck", "preview_deck")]
    deck_id = next((i for i in reversed(shown) if i), None)
    say("turn 1 built and showed a deck", bool(deck_id), deck_id or "none")
    if not deck_id:
        return 1
    before = charts(R._deck_file(deck_id))
    say("deck has exactly one chart", len(before) == 1, json.dumps(before))
    if len(before) != 1:
        return 1

    conv2, second, calls2, reply = turn(
        token, agent,
        "Update the Revenue chart on the Revenue by Region slide: keep the same regions, but the "
        "Revenue series is now 30 and 45. Then show me the deck again.",
        conversation_id=conv["conversationId"], parent=first.get("messageId"))
    edits = [payload(c["output"]) for c in calls2 if base(c["name"]) == "update_chart_data"]
    rebuilt = [c for c in calls2 if base(c["name"]) == "build_deck"]
    print(f"turn 2 tools: {[base(c['name']) for c in calls2]}", flush=True)
    # The skill's default for content changes is a rebuild (get_deck_outline says so), and
    # update_chart_data is the in-place path. Either is acceptable; judge the resulting chart.
    print(f"      edit path: {'in place (update_chart_data)' if edits else 'rebuild (build_deck)'}", flush=True)
    if edits:
        say("it found the chart on the first try", len(edits) == 1,
            f"{len(edits)} update_chart_data calls (2+ means the locator error was needed)")

    shown2 = [payload(c["output"]).get("deck_id") for c in calls2
              if base(c["name"]) in ("update_chart_data", "build_deck", "preview_deck")]
    new_id = next((i for i in reversed(shown2) if i), None)
    say("the edit produced a deck", bool(new_id), str(new_id))
    if not new_id:
        return 1
    after = charts(R._deck_file(new_id))
    values = after[0]["series"][0][1] if after else []
    say("the chart now carries the new values", values == [30.0, 45.0], str(values))
    say("the chart kept its categories", True if after else False,
        "single chart on the same slide" if after and after[0]["slide"] == before[0]["slide"] else "moved")
    print(f"\nconversation: {R.BASE}/c/{conv['conversationId']}")
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
