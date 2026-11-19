# Development

## Branches

- `main`: release-ready stable branch
- `develop`: integration branch
- `feature/<topic>`: branch from `develop`; squash merge into `develop`
- `hotfix/<topic>`: branch from `main`; merge to `main`, then merge to `develop`

## Test runner

```bash
python3 scripts/run_tests.py \
  --builder-python /path/to/builder-python \
  --viewer-python /path/to/viewer-python
```

## Real-data tests

- `20260825153709_0001@2DMeasurement`
- `20260825172353_0001@MultiMeasurement`

Both are under:

`/home/itai/work/plugin/Input/GT2000/2D_Micron/extensions/20260824_024221_B78121PEB_4F190S_CF_v02_4_B3NM531_001`
