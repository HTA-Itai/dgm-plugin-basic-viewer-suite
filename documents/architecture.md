# BasicViewer architecture

## Data flow

```text
Measurement CSV + OVLX assets
  -> basic_viewer_data_builder
  -> basic-viewer-sqlite (one file)
  -> BasicViewer Qt
```

The Viewer does not read source CSV or OVLX files. SQLite is the only runtime dataset contract.

## SQLite format

- `PRAGMA user_version=1`
- format: `basic-viewer-sqlite`
- format version: `1`
- `images`: image identity and wafer/chip coordinates
- `patterns`: layout/pixel/center values, quality and raw CSV facts
- `metrics`: numeric metric names available to the Viewer
- `assets`: JPEG, design SVG, contour SVG and OVLX condition JSON
- `metadata`: manifest and source information

No compatibility-only `C` column is created.

## Viewer surfaces

- Metric strip: changes pattern pointers, map colors and group statistics immediately.
- Selection: PNo, chip and area filters remain separate from Metric selection.
- Wafer map / Chip map: image-level and chip-level navigation.
- Image: source image and selected-metric pointers.
- Statistics: per-group box plots and histograms.
- Box point selection: links to the corresponding Image.

## Grouping

`Grouping Editor` is a separate Qt window and is available only after a SQLite dataset is loaded.

- Open grouping rule: load JSON or YAML into the Editor draft
- Editor Apply: activate the draft in the current Viewer
- Save grouping rule: write the active or edited rule
- Viewer Apply grouping rule: open and immediately activate an existing rule

Viewer rule application and pattern assignment do not modify the SQLite file.
