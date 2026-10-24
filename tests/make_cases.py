#!/usr/bin/env python
"""Generate the 100-case deck corpus.

    .venv/bin/python tests/make_cases.py > tests/cases.json

Every case is a single user instruction plus the invariants the output must satisfy, so the
runner can judge a deck without a human reading it.
"""
from __future__ import annotations

import json

CASES = []


def add(cid, category, prompt, slides, chart=False, table=False, image=False, template="corporate-2slide.pptx", note=""):
    CASES.append(
        {
            "id": cid,
            "category": category,
            "prompt": prompt,
            "expect": {
                "slides": slides,
                "chart": chart,
                "table": table,
                "image": image,
                "template": template,
            },
            "note": note,
        }
    )


TOPICS = [
    "coffee roasting", "the water cycle", "remote work policy", "solar adoption in 2026",
    "onboarding a new engineer", "why sleep matters", "cybersecurity basics",
    "quarterly sales review", "urban gardening", "electric vehicles",
    "hiring plan for next year", "the history of the bicycle",
]
LONG_TOPICS = [
    "supply chain resilience", "customer support metrics", "data governance",
    "office relocation plan", "product launch readiness",
]

# ---------------------------------------------------------------- basics
for i, topic in enumerate(TOPICS):
    n = [2, 3, 5, 5, 6, 8, 4, 5, 3, 7, 5, 4][i]
    add(f"basic-{i+1:02d}", "basics", f"Make a {n}-slide deck about {topic} for a general audience.", n)

add("basic-13", "basics", "Make a 5-slide deck introducing our new analytics dashboard.", 5)
add("basic-14", "basics", "Make a 4-slide deck summarising last quarter's customer feedback.", 4)
add("basic-15", "basics", "Make a 6-slide deck explaining our data retention policy to staff.", 6)

# ---------------------------------------------------------------- charts
CHART_CASES = [
    ("column", "a column chart of revenue by quarter (Q1 40, Q2 52, Q3 61, Q4 78)", 5),
    ("bar", "a bar chart comparing team headcount (Eng 24, Sales 12, Support 9, Design 6)", 5),
    ("line", "a line chart of monthly active users (Jan 1200, Feb 1350, Mar 1500, Apr 1720, May 1900)", 5),
    ("pie", "a pie chart of budget allocation (Product 45, Marketing 25, Ops 20, Other 10) as percentages", 5),
    ("doughnut", "a doughnut chart of device share (Mobile 58, Desktop 34, Tablet 8) as percentages", 4),
    ("stacked", "a stacked column chart of support tickets by channel over 2 months", 6),
    ("multi-series", "a column chart with two series comparing 2025 and 2026 sales across 3 regions", 5),
    ("two charts", "two slides with charts: one column chart of shipments, one line chart of returns", 6),
    ("chart+table", "a chart of churn by month and a table of top 5 accounts", 6),
]
for i, (kind, desc, n) in enumerate(CHART_CASES):
    add(f"chart-{i+1:02d}", "charts", f"Make a {n}-slide deck about our business that includes {desc}.", n,
        chart=True, table=(kind == "chart+table"))
for i, topic in enumerate(LONG_TOPICS):
    n = 5 + (i % 3)
    add(f"chart-{i+10:02d}", "charts",
        f"Make a {n}-slide deck on {topic}; include a chart of plausible example numbers and label it clearly as illustrative.",
        n, chart=True)
for i, topic in enumerate(TOPICS[:6]):
    add(f"chart-{i+15:02d}", "charts",
        f"A {5}-slide deck about {topic} with one chart that supports the main point.", 5, chart=True)

# ---------------------------------------------------------------- tables
TABLE_CASES = [
    ("comparison of 4 vendors", "vendors, price, contract length", 5),
    ("Q3 vs Q4 results", "metric, Q3, Q4, change", 5),
    ("project risks", "risk, likelihood, impact, owner", 6),
    ("pricing tiers", "tier, seats, price, support", 4),
    ("hiring pipeline", "candidate, role, stage, next step", 5),
    ("travel options", "mode, cost, duration, comfort", 5),
]
for i, (what, cols, n) in enumerate(TABLE_CASES):
    add(f"table-{i+1:02d}", "tables",
        f"Make a {n}-slide deck with a table comparing {what} (columns: {cols}).", n, table=True)
for i, topic in enumerate(TOPICS[:6]):
    add(f"table-{i+7:02d}", "tables",
        f"Make a 5-slide deck about {topic} that includes one summary table.", 5, table=True)

# ---------------------------------------------------------------- visuals (SVG)
for i, topic in enumerate(["fish anatomy", "the water cycle", "a laptop stack", "solar panels", "a factory line", "a delivery route"]):
    add(f"visual-{i+1:02d}", "visuals",
        f"Make a 5-slide deck about {topic}. On one slide add a simple diagram you draw yourself as SVG.",
        5, image=True)
for i, topic in enumerate(TOPICS[:4]):
    add(f"visual-{i+7:02d}", "visuals",
        f"Make a 4-slide deck about {topic} with a simple icon graphic on the title slide.", 4, image=True)

# ---------------------------------------------------------------- stress
STRESS = [
    ("5-slide deck about our platform where every bullet is a full sentence of about 85 characters", 5, "long bullets"),
    ("6-slide deck where each content slide has 5 bullets", 6, "many bullets"),
    ("3-slide deck whose slide titles are long, around 55 characters each", 3, "long title"),
    ("10-slide deck about company history", 10, "ten slides"),
    ("4-slide deck where each content slide has exactly one bullet", 4, "one bullet"),
    ("2-slide deck: a title slide and one closing slide", 2, "minimal"),
    ("5-slide deck about energy prices with percentages in most bullets", 5, "numbers heavy"),
    ("6-slide deck explaining how TLS handshakes work, for engineers", 6, "technical"),
]
for i, (prompt, n, kind) in enumerate(STRESS):
    add(f"stress-{i+1:02d}", "stress", f"Make a {prompt}.", n, note=kind)
for i, topic in enumerate(TOPICS[6:10]):
    add(f"stress-{i+9:02d}", "stress", f"Make a 5-slide deck about {topic}, concise bullets, no visuals.", 5, note="plain")

# ---------------------------------------------------------------- templates
STYLES = ["midnight.pptx", "ledger.pptx", "sandstone.pptx", "grid.pptx"]
STYLE_LABEL = {"midnight.pptx": "Midnight", "ledger.pptx": "Ledger", "sandstone.pptx": "Sandstone", "grid.pptx": "Grid"}
n = 1
for style in STYLES:
    for topic in ["our roadmap", "cost reduction", "customer research", "hiring plan", "brand refresh"]:
        add(f"style-{n:02d}", "templates",
            f"Use template {style} for a 5-slide deck about {topic}.", 5, template=style)
        n += 1

# ---------------------------------------------------------------- mixed
MIXED = [
    ("product review", 7, True, True, True),
    ("market entry plan", 6, True, True, False),
    ("annual report summary", 8, True, True, False),
    ("safety briefing", 5, False, True, True),
    ("research findings", 6, True, False, True),
    ("vendor selection", 5, False, True, False),
    ("training overview", 6, False, False, True),
    ("pricing proposal", 5, True, True, False),
]
for i, (topic, n, ch, tb, im) in enumerate(MIXED):
    extras = ", ".join([x for x, on in (("a chart", ch), ("a table", tb), ("a simple SVG diagram", im)) if on])
    add(f"mixed-{i+1:02d}", "mixed", f"Make a {n}-slide deck about {topic} including {extras}.", n,
        chart=ch, table=tb, image=im)
for i, (topic, style) in enumerate(zip(["product review", "hiring plan", "cost audit"], ["ledger.pptx", "grid.pptx", "midnight.pptx"])):
    add(f"mixed-{i+9:02d}", "mixed",
        f"Use template {style} for a 6-slide deck about {topic} with a chart and a table.", 6,
        chart=True, table=True, template=style)

if __name__ == "__main__":
    print(json.dumps({"version": 1, "cases": CASES}, indent=1))
