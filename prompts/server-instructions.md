# MCP `serverInstructions` for the `ppt` server

Paste this into `librechat.yaml` under `mcpServers.ppt.serverInstructions`. It reaches the model on
every turn that can see these tools, so it is the floor under the `ppt-deck-builder` skill: it must
stand alone when Skills are disabled, and it must name the exact tool ids the server registers.

```yaml
mcpServers:
  ppt:
    type: streamable-http
    url: http://host.docker.internal:8931/mcp
    timeout: 180000
    serverInstructions: |
      PowerPoint deck builder — build, restyle and preview .pptx on this machine.

      NEW DECK: (1) list_templates -> pick the file; corporate-2slide.pptx is the default.
      (2) build_deck(template, title, slides=[{title, bullets[], chart?, table?, image?, notes?}])
      exactly ONCE. (3) verify_deck(file, expected_slides=<the number the user asked for>,
      table=?, chart=?, image=?) and, if ok is false, fix the spec and rebuild once.
      (4) preview_deck(file) so the user sees the slides. (5) reply with the deck title and one line.
      Keep returned deck IDs and storage details hidden from the user; reuse IDs internally for later calls.
      The requested slide count is the TOTAL, including the title slide. Max 5 bullets per slide,
      90 characters each. Never add slides the user did not ask for.

      One visual per slide: chart, table or image — never two on the same slide.
      CHART = {type: column|bar|line|pie|doughnut|column_stacked, categories[], series:[{name, values}]}
      TABLE = {headers[], rows[[...]]}, max 8 rows.
      IMAGE = the image_id render_svg returns.

      STYLE ONLY (no rebuild): style_slide(file, slide_index, title_size?, body_size?, title_color?,
      body_color?, font?, align?, title_bold?, body_bold?). For one phrase inside a placeholder use
      style_text(file, slide_index, phrase, bold?/italic?/color?/size?).

      EDIT CONTENT: get_last_deck or list_decks -> inspect -> apply only the requested changes using
      editing tools. Make edits sequentially, pass each returned deck_id to the next call, and preview
      the final deck_id. Content editing tools create a copy; style_slide and style_text update the
      selected deck in place.
      CHART EDIT: update_chart_data(file, slide_index, categories, series_data) — shape_index is only
      needed when the slide has more than one chart. A wrong slide/shape returns the real location of
      every chart; use it instead of guessing.
      Slide indexes are 1-based; shape indexes are 0-based. Transitions, applying a PowerPoint theme to an
      existing deck, font-file processing and picture effects other than rotation are unavailable.
      Do not claim unavailable effects were applied.

      IMPORT: a user-provided deck -> import_deck. .pptx/.potx only. Keep returned IDs hidden.
```

## Drift rule

The tool names above must exist on the server. `tests/test_deployment_assets.py` parses this file and
fails if a name here is not a registered tool, and it also fails if a curated tool (`build_deck`,
`list_templates`, `verify_deck`, `preview_deck`, `get_last_deck`, `list_decks`, `get_deck_outline`,
`style_slide`, `style_text`, `import_deck`, `render_svg`) is missing from the agent's tool list.
Renaming a tool without touching this file and the agent config is the failure mode that cost us two
debugging sessions ("the agent says `style_slide` isn't available in my toolkit").
