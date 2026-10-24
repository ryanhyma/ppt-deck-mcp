# Deck suite — first pass report (2026-10-24)

## What exists now

| Piece | Where | Notes |
|---|---|---|
| 100-case corpus | `tests/cases.json` (`tests/make_cases.py`) | 7 categories, explicit expected invariants per case |
| Runner + validator | `tests/run_suite.py` | one **new conversation per case**, drives the agent API, validates the .pptx with python-pptx, writes JSON + Markdown |
| 5 corporate templates | `templates/*.pptx` + `*.json` manifests | Corporate Teal, Midnight (dark), Ledger (serif/bronze), Sandstone (warm), Grid (cobalt rail) |
| Template gallery | Deck Studio App (`apps/deck_studio/`) | thumbnails come from `list_templates` → `preview`, clicking pre-fills "Use template X for a 5-slide deck about …" |
| Template-aware chrome | `deck_style.py` (Theme) | dark/serif/rail templates render correctly, incl. table and chart colours |

Corpus: basics 15 · charts 20 · tables 12 · visuals 10 · stress 12 · templates 20 · mixed 11.

## Executed this pass: 12 cases (one per class + template variety)

| id | cat | result | slides exp/got | chart | table | pic | s |
|---|---|---|---|---|---|---|---|
| basic-03 | basics | PASS | 5/5 | 0 | 0 | 0 | 70 |
| chart-04 | charts | PASS | 5/5 | 1 | 0 | 0 | 99 |
| chart-07 | charts | PASS | 5/5 | 1 | 0 | 0 | 97 |
| table-01 | tables | PASS | 5/5 | 0 | 1 | 0 | 81 |
| table-04 | tables | PASS | 4/4 | 0 | 1 | 0 | 71 |
| visual-01 | visuals | PASS | 5/5 | 0 | 0 | 1 | 212 |
| stress-01 | stress | PASS | 5/5 | 0 | 0 | 0 | 118 |
| stress-04 | stress | PASS | 10/10 | 1 | 1 | 0 | 131 |
| style-01 | templates | PASS | 5/5 | 1 | 0 | 0 | 98 |
| style-06 | templates | PASS | 5/5 | 0 | 0 | 0 | 76 |
| style-16 | templates | PASS | 5/5 | 0 | 0 | 0 | 74 |
| mixed-01 | mixed | PASS | 7/7 | 2 | 1 | 1 | 140 |

**12/12 pass, median 97 s, 44 tool calls** (build_deck 17, list_templates 12, preview_deck 12,
render_svg 2, skill 1). Every case produced its own reviewable conversation.

## Defects this run exposed (and what changed)

1. **Silent visual skip** — `build_deck` returned `"skipped: table needs headers and rows"` and still
   wrote a deck. `table-04` produced a "Pricing Comparison" slide with no table, and the deck looked
   fine. Fix: `_validate_slides()` now raises a message naming the missing field *before* any file is
   written; the agent retried and passed on re-run.
2. **Session expiry mid-suite** — `SESSION_EXPIRY=15 min`, so cases 11–12 got 401 and were recorded as
   failures. Fix: `AuthError` → re-login → retry the case once.
3. **Slide-count drift** — `chart-07` first produced 6 slides for a "5-slide" request (5 content +
   title). Model-side; instructions already say the count includes the title. Re-run passed.
4. **Harness false-signal risks** (from a local-27B review): terminal-event detection broke on any
   `final` key; `preview_deck not called` was scored as a quality failure; tool errors were detected by
   one hardcoded substring; POST timeout was 60 s while the stream was 600 s. All four fixed
   (`final is True` + `responseMessage`, preview → warning, broader error detection, 120 s POST).
   Known limitations kept: bullets are counted only in the body placeholder; `unwrap()` assumes one
   `{"result": …}` level; >110-char bullets fail by design.

## Findings

- The 27B follows the two-step workflow reliably: 12/12 called `list_templates`, 12/12 called
  `preview_deck`, and `render_svg` only when a diagram was requested.
- Composition is the slow path: `visual-01` (SVG + preview) 212 s vs 70 s for a plain deck. SVG authoring
  plus its extra round trip is the cost centre.
- Template choice is respected when named explicitly ("Use template midnight.pptx"); without a name it
  always takes the default.
- One case invoked the `skill` tool — skills are reachable from the agent, not just configured.
- Extra visuals appear unrequested on number-heavy prompts (stress-04 added a chart and a table); not a
  failure, but worth a policy decision.

## Cost model

~97 s/case median ⇒ **~2 h 40 m for all 100**. Run in category chunks to keep review manageable:

```sh
.venv/bin/python tests/run_suite.py --category charts --limit 20
.venv/bin/python tests/run_suite.py --ids basic-01,basic-02
```

## Next steps

1. Run the remaining 88 cases in category chunks (charts + tables first: they exercise the validation
   path that just changed).
2. Decide the "extra visuals" policy (allow / forbid) and encode it in the skill.
3. Add a `--resume` flag keyed on result JSON so a run can survive a token expiry or a reboot.
4. Add image checks: rendered slide overflow (compare text box height vs placeholder) rather than just
   bullet counts.
5. Wire the suite into the notes workflow: one line per run in `benchmarks.md`.
