# Semantic Manifest Pipeline

Use this reference for complex, multi-panel, image-heavy, formula-heavy, or mixed figures.

## Pipeline Contract

The pipeline is model-led:

1. `prepare_measurements.py` creates OCR, OpenCV, and color evidence.
2. The model verifies the source and authors `manifest.json`.
3. `prepare_visio_manifest.py` validates, crops assets, and renders formula SVGs.
4. `visio_manifest_renderer.ps1` draws native Visio content and writes Shape Data.
5. `compose_visio_package.ps1` exports and audits the staging document before replacing the target.

Detector output is never an automatic drawing plan.

## Element Routing

| Source element | Manifest type | Visio result |
|---|---|---|
| panel, card, frame | `rect` | native rectangle |
| ordinary label | `text` | native editable text box |
| straight line | `line` | native line |
| relationship | `connector` | dynamic glued connector |
| node or marker | `circle`, `ellipse`, `polygon` | native geometry |
| multi-segment geometry | `polyline`, `path` | native polyline/Bezier group |
| equation | `math` | imported vector group with LaTeX Shape Data |
| logo, map, screenshot, photo, chart body | `image` + asset | cropped raster asset |

When uncertain whether a visual object is generic or source-specific, crop it. Do not crop a whole card merely to preserve one icon: redraw the shell, retype the label, and crop the icon alone.

## Coordinate Systems

Use source-image pixels by default. The SVG-style top-left coordinate system is mapped to Visio page inches.

For dense panels, set:

```json
{
  "coordinate_space": "panel",
  "panel_id": "panel-a",
  "x": 0.1,
  "y": 0.2,
  "w": 0.4,
  "h": 0.15
}
```

Panel-local values must remain within `0..1`; containment failures stop composition.

## Visio Extensions

The format is a compatible superset of the FigEdit manifest.

- Add `connector` elements with `from_id` and `to_id`.
- Use `layer`, `panel_id`, `group_id`, `z_index`, and `allow_overlap` where needed.
- Use the optional top-level `visio` object for `page_width_in`, `page_height_in`, `page_index`, `page_mode`, and `default_font`.
- Command-line values override `visio` values; `visio` values override defaults.
- Default page width is 16 inches; height follows source aspect ratio.

See `templates/manifest.schema.json` and `examples/manifest.example.json`.

## Raster Policy

Every asset must record a source region, target position, decision reason, text policy, background handling, and crop status when known.

- More than 40% of canvas area triggers review.
- At least 90% of both canvas width and height is rejected as a full-canvas raster.
- `extract-editable` means nearby readable text must become native Visio text.
- `preserve-raster` and `allow-embedded-text` require a defensible reason.

## Formula Policy

Use `math` with normalized LaTeX and an explicit box. The renderer creates a lightweight SVG and imports it as a Visio group. It stores the original LaTeX in Shape Data. If import fails, the renderer creates a visible text fallback and marks the formula gate for review.

## Quality Gates

Composition fails when:

- required fields, IDs, references, files, coordinates, or element types are invalid;
- a full-canvas raster is present;
- an expected element lacks a rendered shape or Manifest ID Shape Data;
- the staging `.vsdx` or requested exports are missing or empty.

Review before delivery when:

- OCR text lift ratio is below `0.45`;
- more than 12 likely-editable OCR boxes remain inside assets;
- an asset exceeds 40% of canvas area;
- crops need padding;
- formulas fall back to text or do not import as Visio groups.

