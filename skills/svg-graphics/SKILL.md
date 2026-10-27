---
name: svg-graphics
description: Create simple SVG icons or schematic diagrams and rasterize them to PNG for insertion into slides. Use when a deck needs a visual that is not a chart or a table, or when the user asks for an icon, logo shape, or diagram.
---

# SVG graphics for slides

`render_svg(svg, name, width_px=1600)` converts SVG markup to a transparent PNG and returns an
opaque `image_id`. Use it as `image` in `build_deck` or as `image_id` in `add_image`; use
`enhance_image` for image adjustments. Keep image IDs hidden from the user.

This toolchain inserts the generated PNG into the deck. The user can use the original SVG separately
when editing the deck in PowerPoint.

## Rules that keep it looking professional

- **Canvas:** always set `viewBox="0 0 640 400"` (or 800×450) and no `width`/`height`
  attributes; the rasterizer scales it.
- **Palette:** teal `#2A9D8F`, navy `#1F3864`, sand `#E9C46A`, coral `#E76F51`,
  light grey `#F5F7F9`, ink `#333333`. Same colours as the deck.
- **Shapes:** solid fills, no gradients, no filters, no `<script>`, no external
  images or web fonts. `font-family="Calibri"` if you need text.
- **Complexity:** under ~30 elements. For anything richer, use a chart or a table.
- **Background:** leave it transparent (do not paint a full-canvas rect) unless the
  graphic needs a card; then use `#F5F7F9` with `rx="24"`.

## Skeleton

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400">
  <circle cx="320" cy="200" r="120" fill="#2A9D8F"/>
  <rect x="260" y="140" width="120" height="120" rx="16" fill="#1F3864"/>
  <text x="320" y="360" font-family="Calibri" font-size="28"
        fill="#1F3864" text-anchor="middle">Label</text>
</svg>
```

## Workflow

1. Sketch the shape with 5-15 elements; keep one idea per graphic.
2. `render_svg(svg=..., name="fish-anatomy", width_px=1200)`.
3. `build_deck(..., slides=[{"title": ..., "bullets": [...], "image": <image_id>}])`.

If `render_svg` errors on invalid markup, simplify — most failures are unclosed tags
or a missing `xmlns`.
