# Product decisions

Short, dated, and reversible-with-reason. The long-form trail lives in the operator's notes vault.

## 2026-10-24 — Deck pipeline

- **Native PowerPoint charts, not images.** `build_deck` builds real `ChartData` so the deck stays
  editable. Pictures would look identical and be useless to the analyst.
- **Chrome is drawn in code** (`deck_style.py`), not in slide masters: same output on Linux and
  Windows, no fragile XML surgery.
- **The small model never positions anything.** Layout is a rule (bullets left / visual right / full
  width), so the 27B only emits structure.
- **Style changes bypass the model.** The pane calls `style_slide` / `style_text` directly over the
  MCP Apps bridge; the agent only handles content, where language understanding is actually needed.
- **Style survives content edits** via `get_deck_outline`'s per-slide `style` plus `build_deck(base=…)`.
- **One renderer abstraction, four backends** (`soffice | powerpoint | aspose | none`). Rendering is a
  view dependency: a failure never fails a tool call, and `slide_count` always comes from the deck.
- **Windows default is portable LibreOffice**, auto-detected; PDF→PNG uses the PyMuPDF wheel so no
  poppler binary is required.

## 2026-10-25 — Test/quality

- **`pass` means the artefact is right**, not that the run was clean. Tool errors and a missing
  `preview_deck` are process warnings; the 27B self-corrects often enough that conflating them would
  hide real output defects.
- **Auto-shrink is the overflow policy.** Slides with >5 bullets *and* >260 characters drop to 17 pt
  and the tool reports a warning. No hard failure, no splitting.
- **No HTML/model-based preview fallback.** An approximate preview that can disagree with PowerPoint is
  worse than no preview; if a renderer is unavailable, `preview_error` says so.
- **The browser E2E stays out of CI** (it needs the LibreChat stack); CI runs the 35 unit tests on
  3.12 and 3.13.

## 2026-10-25 — Correctness guards

- **One visual per slide, enforced.** A spec with `chart` + `table` used to keep the chart and drop the
  table silently — the single most misleading output we produced. `build_deck` now raises instead.
- **`verify_deck` is the safety net, not the judge.** It takes what the user asked for (slide count,
  table/chart/image) and returns concrete problems. The agent must call it after `build_deck` and may
  rebuild once. In the re-run, 5 of 11 cases used that retry loop and all ended correct.
- **Concurrency is a product requirement.** Output names carry a random suffix, decks are written
  atomically (temp + rename), and rendering takes a per-deck lock so two users styling/rendering the
  same deck cannot interleave. LibreOffice gets a throwaway `UserInstallation` per call.
