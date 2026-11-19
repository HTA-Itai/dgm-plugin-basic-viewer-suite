# dgm-plugin-basic-viewer-suite

BasicViewerのSQLite Builderと、単一データセットを表示するQt Viewerを管理するリポジトリです。

## Applications

| Application | Role |
|---|---|
| `applications/basic_viewer_data_builder` | CSV、OVLX、画像を1つのViewer SQLiteへ変換するPlugin Application |
| `applications/basic_viewer` | SQLiteを開き、メトリクス、グループ、画像、統計を表示するWindowsローカルアプリ |

## Branch strategy

- `main`: release-ready stable branch
- `develop`: integration branch for the next release
- `feature/<topic>`: branch from `develop` and merge by PR
- `hotfix/<topic>`: branch from `main`, merge to `main`, then merge to `develop`

Feature PRs use squash merge; release merges use merge commits.

## Initial design

- Candidate C: metrics use a full-width strip above the existing Selection/Map/Image/Statistics layout.
- Metrics update image overlays, Wafer map, Chip map and group statistics without entering the Selection apply state.
- Group rules are applied from the Viewer with `Apply grouping rule...`.
- `Edit Groups` opens a separate editor window only after SQLite loading.
- The grouping editor provides Open, Save, and Apply.
- Builder stores pattern facts and metrics needed for grouping and filtering; compatibility-only `C` is not generated.
