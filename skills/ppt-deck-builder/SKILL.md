---
name: ppt-deck-builder
description: Build a professional .pptx with the configured `ppt-deck` MCP tools — bullets, native editable charts, tables, and images. Use whenever the user asks for a presentation, deck, slides, or PowerPoint.
---

# PPT Deck Builder

Tools come from the configured PPT MCP server: `list_templates`, `render_svg`, `build_deck`,
`verify_deck`, and `preview_deck`.
All are deterministic — you supply content, the tool applies layouts, theme, chart
styling, and slide chrome. Never write layout code and never use the low-level
`add_slide` / `populate_placeholder` tools to build a new deck.

## Workflow

1. `list_templates` → pick a template, note `title_layout` / `content_layout`.
2. `render_svg` → only if a slide needs an icon or simple diagram.
3. `build_deck` → exactly once, with the whole outline. Keep its returned `deck_id`.
4. `verify_deck(file=<deck_id>, expected_slides=<requested total>)`.
5. `preview_deck(file=<same deck_id>)` so the user sees the deck in Deck Studio.
6. Reply with the deck title and one summary line. Keep the deck ID and storage details hidden.

Use the opaque `deck_id` returned by `build_deck` for verify, styling, outline, and preview calls.
For an existing deck, import the user-provided deck and use its returned `deck_id` for later tool calls.
Keep the ID and storage details hidden from the user.

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
- `image`: `image_id` returned by `render_svg` (also accepted for a whole deck). Placed right;
  bullets then use the left half.
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

Several slides named at once → one `style_slide` call per slide. For wording or visual changes, use
the editing tools below.

## Editing an existing deck

Never edit from memory. Use the editing tools to preserve existing slide content:

1. `get_last_deck` (or `list_decks`) → the `deck_id` of the deck.
2. Use `get_slide_info` or `get_deck_outline` to inspect the deck.
3. Apply only the requested change with the appropriate editing tool, such as `manage_text`,
   `populate_placeholder`, `add_shape`, `add_image`, `add_chart`, or `manage_hyperlinks`.
4. Edits return a new `deck_id`. Make edits sequentially and pass the latest returned ID to the
   next tool call; use `preview_deck` with the final ID. Keep IDs hidden from the user.

`style_slide` and `style_text` update the selected deck in place and retain its existing ID.

Rebuilding from an outline can omit content the outline cannot represent. Only rebuild when the user
asks for a regenerated deck or the requested change cannot be made with an editing tool.

Slide numbers are 1-based. Shape indexes are 0-based; `get_slide_info` reports positions in inches.

- Inspect: `get_presentation_info`, `get_slide_info`, `extract_slide_text`, and
  `extract_presentation_text` inspect content beyond the outline.
- Text: `populate_placeholder`, `add_bullet_points`, `manage_text`, and `optimize_slide_text`
  edit or format slide text. `manage_hyperlinks` lists and edits links.
- Visuals: `add_shape`, `add_connector`, `add_image`, `enhance_image`, and `apply_picture_effects`
  add or adjust visuals. Picture effects support rotation only.
- Charts and tables: `add_chart` and `add_table` add visuals to an existing slide;
  `update_chart_data` and `format_table_cell` edit existing visuals.
- Slides and metadata: `add_slide` uses a deck layout; `list_slide_templates`, `get_template_info`,
  `apply_slide_template`, and `create_slide_from_template` use slide templates. `manage_slide_masters`
  inspects masters and layouts; `set_core_properties` updates deck metadata.

Transitions, applying a PowerPoint theme to an existing deck, font-file processing, and picture effects
other than rotation are not supported. Some decorative effects in slide templates are simplified; do
not claim those effects were applied.

## Importing an uploaded deck

`import_deck` accepts a `.pptx` or `.potx` uploaded to the current chat and returns a `deck_id` and
outline. Keep the ID and storage details hidden from the user.

## Dos and don'ts

- At most 5 bullets per slide, at most 90 characters each, no trailing periods.
- Never invent precise statistics you cannot justify; keep claims qualitative when
  the user did not supply data.
- Do not add slides the user did not ask for. Rebuild once only if verification finds a defect.
