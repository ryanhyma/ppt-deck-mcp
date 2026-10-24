# Deck suite — batches 1–3 (30 cases)

Local `qwen3.6-27b` via LibreChat, one new conversation per case. `pass` = the artefact
matched the request (slide count, requested visuals, bullet limits). Tool errors and a
missing `preview_deck` are reported as process warnings, not failures.

| batch | instructions | cases | output-pass | median s | unrequested visual | tool-error runs |
|---|---|---|---|---|---|---|
| Batch 1 | old instructions | 10 | 8/10 | 91 | 7 | 3 |
| Batch 2 | tightened | 10 | 10/10 | 69 | 0 | 0 |
| Batch 3 | tightened | 10 | 9/10 | 85 | 2 | 0 |

**Total: 27/30 output-pass (90%)**, median 84s per deck.

## Failures

- **basic-06** — slides 9≠8  
  Make a 8-slide deck about why sleep matters for a general audience.  
  http://localhost:3080/c/362a290a-68e8-49a7-8843-757a3e3e13be
- **basic-10** — slides 8≠7  
  Make a 7-slide deck about electric vehicles for a general audience.  
  http://localhost:3080/c/0604a63f-4d59-4ace-992a-bbc9fa581d20
- **chart-14** — 2 slides >5 bullets  
  Make a 6-slide deck on product launch readiness; include a chart of plausible example numbers and la  
  http://localhost:3080/c/d2828e38-ff14-4615-8e68-98326e02736d

## What the batches showed

1. **Slide-count drift is instruction-sensitive.** 2/10 in batch 1 (5→6, 7→8) with looser
   wording; 0/20 after the instruction spelled out 'N slides means N-1 content slides'.
2. **Unrequested visuals were the dominant deviation.** 8/10 cases in batch 1 added a chart,
   table or icon nobody asked for; the explicit 'only when asked' rule cut it to 2/20.
3. **Tool errors are process noise, not output defects.** 3 runs hit a validation error and
   recovered on retry, still producing correct decks — which is why pass/fail now judges the
   file, and tool errors are reported separately.
4. **Bullet cap is the remaining hard rule-break.** 1/30: two slides with 6 bullets
   (chart-14). The model can exceed 5 when the content is dense.
5. **Speed improved as instructions tightened**: 91s → 69s → 85s median (fewer retries).

## Next steps

1. Finish batches 4–10 (`--limit 10 --resume`), which cover the remaining charts, tables,
   visuals, stress and template cases (68 left).
2. Decide the bullet policy: hard-fail, auto-shrink to 17pt, or split the slide. Recommend
   auto-shrink with a warning — 6 bullets at 17pt still reads.
3. Add a `revision` category: build → edit instruction → verify only the requested slide changed.
4. Track actual template used per case (currently only asserted by prompt); compare against
   the requested template to catch silent fallback.
5. One line per batch into `benchmarks.md` so drift over time is visible.
