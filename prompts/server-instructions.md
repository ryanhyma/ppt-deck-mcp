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
      (4) preview_deck(file) so the user sees the slides. (5) reply with the file path and one line.
      The requested slide count is the TOTAL, including the title slide. Max 5 bullets per slide,
      90 characters each. Never add slides the user did not ask for.

      One visual per slide: chart, table or image — never two on the same slide.
      CHART = {type: column|bar|line|pie|doughnut|column_stacked, categories[], series:[{name, values}]}
      TABLE = {headers[], rows[[...]]}, max 8 rows.
      IMAGE = absolute path of a .png/.jpg (render_svg returns one).

      STYLE ONLY (no rebuild): style_slide(file, slide_index, title_size?, body_size?, title_color?,
      body_color?, font?, align?, title_bold?, body_bold?). For one phrase inside a placeholder use
      style_text(file, slide_index, phrase, bold?/italic?/color?/size?).

      EDIT CONTENT: get_last_deck or list_decks -> get_deck_outline(file) -> change only what was
      asked -> build_deck(..., base=<that file>) so existing per-slide styling survives ->
      verify_deck -> preview_deck.

      IMPORT: an uploaded deck -> import_deck(filename). .pptx/.potx only.
```

## Drift rule

The tool names above must exist on the server. `tests/test_deployment_assets.py` parses this file and
fails if a name here is not a registered tool, and it also fails if a curated tool (`build_deck`,
`list_templates`, `verify_deck`, `preview_deck`, `get_last_deck`, `list_decks`, `get_deck_outline`,
`style_slide`, `style_text`, `import_deck`, `render_svg`) is missing from the agent's tool list.
Renaming a tool without touching this file and the agent config is the failure mode that cost us two
debugging sessions ("the agent says `style_slide` isn't available in my toolkit").
