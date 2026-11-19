# BasicViewer

## Start

1. Extract the complete ZIP.
2. Run `start-viewer.cmd`.
3. Open a BasicViewer SQLite dataset.

Python or PySide6 do not need to be installed separately.

## Dataset

Use `Open SQLite...` to open a file created by the BasicViewer data builder.

## Metric

The full-width Metric strip is independent of Selection. Changing the metric updates:

- pattern pointers in Image
- Wafer map colors
- Chip map colors
- per-group statistics

## Grouping

`Edit Groups` is available after dataset loading.

- Open grouping rule: load a rule into the editor
- Editor Apply: activate it in the Viewer
- Save grouping rule: save the active or edited rule
- Viewer `Apply grouping rule...`: open an existing rule and apply it immediately
