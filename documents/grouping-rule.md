# BasicViewer grouping rule

```yaml
format: basic-viewer-grouping
version: 2
name: B781 contacts
applies_to:
  - {pno: 2, ms: 02MS}

coords:
  x: {source: layout, tolerance_nm: 30}
  y: {source: ordinal, origin: top, tolerance_px: 5}

groups:
  C1:
    select:
      - {x: [593805]}
  Spot:
    select:
      - {points: [[594048, 5], [594169, 4]]}
```

## Coordinate sources

- `layout`: use LayoutCoordX/Y
- `ordinal`: number valid rows or columns in on-screen order

## Selection

- `{x: [x1, x2]}`
- `{y: [y1, y2]}`
- `{x: [...], y: [...]}`
- `{points: [[x, y], ...]}`

## Edge-cut patterns

Edge-cut patterns are excluded before group assignment.
