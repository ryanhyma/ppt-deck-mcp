---
name: ppt-deck-builder
description: Build a professional .pptx from the corporate template with the `ppt` MCP tools — bullets, native editable charts, tables, and images. Use whenever the user asks for a presentation, deck, slides, or PowerPoint.
---

# PPT Deck Builder

Tools come from the `ppt` MCP server: `list_templates`, `render_svg`, `build_deck`.
All are deterministic — you supply content, the tool applies layouts, theme, chart
styling, and slide chrome. Never write layout code and never use the low-level
`add_slide` / `populate_placeholder` tools to build a new deck.

## Workflow

1. `list_templates` → pick a template, note `title_layout` / `content_layout`.
2. `render_svg` → only if a slide needs an icon or simple diagram.
3. `build_deck` → **exactly once**, with the whole outline.
4. Reply with the returned `file` path and one summary line.

## Slide count

The number the user asks for is the **total**, including the title slide.
5 slides = 1 title slide + 4 entries in `slides`.

## Slide spec

```json
{"title": "State of Fish Stocks",
 "bullets": ["63% of stocks sustainable", "Recovery takes 10-15 years"],
 "chart": {"type": "column", "title": "Sustainability index",
           "categories": ["Atlantic", "Pacific"],
           "series": [{"name": "2000", "values": [42, 55]},
                      {"name": "2026", "values": [68, 71]}]},
 "notes": "speaker note, optional"}
```

- `chart`: `column` | `bar` | `line` | `pie` | `doughnut` | `column_stacked`.
  Native PowerPoint chart — editable by the user. Colours come from the deck palette.
- `table`: `{"headers": [...], "rows": [[...]]}` — max 8 rows, 2-5 columns.
- `image`: absolute path to a `.png`/`.jpg` (from `render_svg`, or `image=` on the
  whole deck). Placed right; bullets then use the left half.
- Use one visual per slide. Prefer a chart over bullets when the content is numbers,
  a table for comparisons, and bullets for narrative.

## Plain-language formatting (no rebuild)

If the request changes *only* look — size, colour, font, bold/italic, alignment — call
`style_slide(file, slide_index, ...)` and stop. Rebuilding for a style-only change destroys the
current file for no reason.

| The user says | Pass |
|---|---|
| "bigger title" | `title_size=40` |
| "smaller body" | `body_size=17` |
| "brand orange" | `title_color="#C4703C"` |
| "navy / dark blue" | `title_color="#1F3864"` |
| "serif" | `font="Georgia"` |
| "clean sans" | `font="Calibri"` |
| "centred" | `align="center"` |
| "bold the title" | `title_bold=True` |
| "bold the body" | `body_bold=True` |
| "not bold" (either) | `title_bold=False` / `body_bold=False` |

Several slides named at once → one `style_slide` call per slide. If the wording must change too,
that is a content edit: use the rebuild path below and carry styling via `base=` or a `style` object.

## Editing an existing deck

Never edit from memory. The loop is read → change → rebuild → preview:

1. `get_last_deck` (or `list_decks`) → file path.
2. `get_deck_outline(file)` → titles, bullets, chart series and tables as JSON.
3. Apply **only** the requested change; leave every other slide verbatim.
4. `build_deck(template, title, slides=[...], base=<the file you read>)` with the full outline
   (title slide excluded). `base` re-applies each slide's saved size/colour/font/alignment, so a
   content edit does not destroy styling done in the deck pane.
5. `preview_deck(new_file)` so the user sees the result in the deck pane.

Caveat to state if relevant: the deck is regenerated from the outline, so manual edits made in
PowerPoint afterwards are not preserved.

## Importing an uploaded deck

`import_deck(file)` accepts the uploaded filename ("Q3 review.pptx"), a container path or an absolute
path; it copies the deck into the workspace, renders previews and returns the outline. Only `.pptx`
and `.potx` are readable — legacy `.pot`/`.ppt` (97-2003 binary) is not supported.

## Dos and don'ts

- At most 5 bullets per slide, at most 90 characters each, no trailing periods.
- Never invent precise statistics you cannot justify; keep claims qualitative when
  the user did not supply data.
- Do not add slides the user did not ask for, and do not call `build_deck` twice.
- Output goes to `PPT_OUTPUT_DIR` (`/app/uploads/decks` inside LibreChat).
