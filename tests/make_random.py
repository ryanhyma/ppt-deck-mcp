#!/usr/bin/env python
"""Randomised deck cases: random topic x random visual mix, for capability sampling.

    .venv/bin/python tests/make_random.py --n 10 --seed 7 > /tmp/random-cases.json
"""
from __future__ import annotations

import argparse
import json
import random

TOPICS = [
    "the history of jazz", "coral reef restoration", "Formula 1 race logistics",
    "sourdough fermentation", "lunar geology", "competitive esports economics",
    "urban beekeeping", "parametric insurance", "the coffee supply chain",
    "quantum computing for beginners", "medieval trade routes", "bioluminescence",
    "high-speed rail in Europe", "wildfire prevention", "the science of sleep debt",
    "open-source maintainership", "vertical farming", "glacier monitoring",
    "the economics of streaming", "antibiotic resistance", "city cycling policy",
    "deep-sea mining", "the Voyager probes", "precision fermentation",
]
CHART_TYPES = ["column", "bar", "line", "pie", "doughnut"]
NUMBERS = [
    "2019 32, 2020 28, 2021 41, 2022 57, 2023 66",
    "North 45, South 30, East 15, West 10",
    "Q1 120, Q2 140, Q3 165, Q4 210",
    "Mobile 61, Desktop 33, Tablet 6",
    "Jan 14, Feb 19, Mar 17, Apr 26, May 31, Jun 38",
]
TABLE_HINTS = [
    ("option, cost, time, risk", "comparing 4 options"),
    ("region, volume, growth, note", "comparing 4 regions"),
    ("stage, owner, weeks, status", "with 5 rows"),
    ("metric, 2025, 2026, change", "with 4 rows"),
]
DIAGRAMS = [
    "a simple process diagram with 4 boxes and arrows",
    "a schematic with 3 labelled stages",
    "a simple cycle diagram with 4 nodes",
    "a cross-section sketch with 3 labelled parts",
]
COMBOS = [
    ("chart",), ("table",), ("svg",), ("chart", "table"), ("chart", "svg"), ("chart", "table", "svg"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--min-slides", type=int, default=4)
    ap.add_argument("--max-slides", type=int, default=8)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    topics = rng.sample(TOPICS, min(args.n, len(TOPICS)))
    cases = []
    for i, topic in enumerate(topics, 1):
        combo = COMBOS[(i - 1) % len(COMBOS)]
        rng.shuffle(combo := list(combo))
        slides = rng.randint(args.min_slides, args.max_slides)
        parts, expects = [], {"chart": False, "table": False, "image": False}

        if "chart" in combo:
            ctype = rng.choice(CHART_TYPES)
            nums = rng.choice(NUMBERS)
            parts.append(f"a {ctype} chart using these illustrative numbers ({nums})")
            expects["chart"] = True
        if "table" in combo:
            cols, shape = rng.choice(TABLE_HINTS)
            parts.append(f"a table {shape} (columns: {cols})")
            expects["table"] = True
        if "svg" in combo:
            parts.append(rng.choice(DIAGRAMS) + " drawn as SVG")
            expects["image"] = True

        prompt = f"Make a {slides}-slide deck about {topic} for a general audience"
        prompt += f", including {', '.join(parts)}." if parts else "."
        cases.append(
            {
                "id": f"rand-{i:02d}",
                "category": "random",
                "prompt": prompt,
                "expect": {"slides": slides, **expects, "template": "corporate-2slide.pptx"},
                "note": f"{topic} | {'+'.join(combo) if parts else 'bullets'}",
            }
        )

    print(json.dumps({"version": 1, "cases": cases}, indent=1))


if __name__ == "__main__":
    main()
