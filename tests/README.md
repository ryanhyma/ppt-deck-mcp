# Tests

Two layers, on purpose.

## 1. Regression unit tests (fast, no services)

```sh
.venv/bin/python -m unittest discover -s tests -v      # 25 tests, ~5 s
```

They import `server.py` (which registers every MCP tool) and call the tool functions directly, then
inspect the produced `.pptx` with python-pptx. No LibreChat, no model, no network.

| Bug that actually happened | Test |
|---|---|
| Content slides used layout 10 ("Content with Caption") | `LayoutRegressions.test_content_layout_is_title_and_content` |
| Narrowed bullet column grew up over the title | `test_narrow_bullet_column_does_not_grow_over_the_title` |
| `layout="image-left"` / `"visual-full"` ignored | `test_visual_left_puts_the_chart_left_of_the_text`, `test_visual_full_drops_the_bullet_placeholder` |
| Invalid table/chart silently skipped → good-looking wrong deck | `ValidationRegressions.*` |
| Footer wordmark read back as the subtitle | `OutlineRegressions.test_outline_ignores_decorative_chrome` |
| Chart data lost on round trip | `test_outline_round_trips_chart_data` |
| "Bold the title" bolded the body too | `StyleRegressions.test_title_bold_does_not_bold_the_body` |
| Styling wiped by a content rebuild | `test_styling_survives_a_content_rebuild_with_base` |
| `style_text` phrase not found → silent no-op | `test_style_text_fails_loudly_when_phrase_is_absent` |
| Dense slides overflowed / short 6-bullet slides looked anaemic | `test_dense_slides_shrink_bullets_and_warn`, `test_short_six_bullets_keep_full_size` |
| Chart palette override ignored | `test_chart_colours_override_the_palette` |
| Dark template rendered black-on-black | `test_dark_template_uses_light_body_text` |
| `output_name="x.pptx"` → `x-pptx-<time>.pptx` | `test_output_name_does_not_gain_a_pptx_suffix` |
| `import_deck` crashed on absolute paths / accepted legacy `.pot` | `ImportRegressions.*` |
| Suite counted tool errors as failures, missed real ones | `HarnessRegressions.*` |

### Correctness + concurrency

`tests/test_regressions.py::VerifyRegressions` pins the safety net (missing table / wrong count are
reported, matching decks pass). `ConcurrencyRegressions` and `OneVisualPerSlideRegressions` pin the
one-visual rule, unique output names and per-deck render locking.

Live probe against the running server (not part of CI):

```sh
.venv/bin/python tests/concurrency_check.py --workers 4 --out-dir <decks dir>
```

## 2. Browser E2E (needs the stack up)

```sh
CONVO=<conversation-id> node tests/e2e/deck-studio.js
```

Requires LibreChat on :3080, the MCP server on :8931, Playwright's Chrome installed, and
`NODE_PATH=/usr/local/lib/node_modules`. It pins the things unit tests cannot see:

- the App iframe loads at all (separate sandbox origin + `frame-ancestors` + CSP)
- slide images load from the MCP origin (CSP `resourceDomains` + CORS on module/asset fetches)
- the template gallery renders 5 styles
- the style panel and per-phrase controls exist (guards against an outdated App snapshot killing the view)

## 3. Behaviour suite (slow, model in the loop)

`tests/run_suite.py` — 100-case corpus against the local 27B, one conversation per case. See
`tests/results/SUITE-REPORT.md`. Not part of the regression gate; it measures model behaviour, not code.
