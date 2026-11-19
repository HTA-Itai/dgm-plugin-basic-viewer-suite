# 閲覧用データセット形式 1.0.0

これは初期Parquet形式の契約。現在の既定出力は[単一SQLite v4](sqlite-v4.md)。
この旧形式は`--format parquet`で明示生成する。現在の比較ViewerはこのParquetフォルダを直接開かない。

生成者: comparison_viewer_data_builder 1.0.0（当時の初期版）。利用者: 今後作成するローカル比較Viewer。
現時点で既存basic_viewer_lightが直接読める形式ではない。

## 配置

```text
dataset/
  manifest.json
  images.json
  measurements.parquet
  images/
    000000/
      image.jpeg
      design.svg
      contour.svg
      cond.json
    000001/
      ...
```

任意の配布ZIPは兄弟パス`dataset.zip`に生成し、内部は`dataset/`をルートとする。
ZIPは閲覧前に全展開する想定。ZIPのままのランダムアクセスは初期契約に含めない。
パスはすべてデータセットルートからの相対パス。ホストの絶対パスは記録しない。

## manifest.json

- `format`: `plugin-viewer-dataset`
- `format_version`／`builder_version`: `1.0.0`
- `dataset_id`: triggerのMsrまたはCLI指定の識別子
- `created_utc`: UTCの生成日時
- `source`: 対象CSV名と元CSV全体のSHA-256
- `configuration`: 追加列設定とSID空白フィルタ
- `statistics`: 元行数、除外SID行数、採用行数、画像数、中心欠測数、
  Normal以外の行数、`vector_valid`行数、末尾空フィールド許容行数、除外列名
- `pattern_key`: `IMAGENAME, LayoutCoordX, LayoutCoordY`
- `columns`: 列名・Arrow型・単位・nullable情報
- `measurements`: Parquetパス、圧縮方式、row group数、SHA-256
- `image_index`: 画像索引パスとSHA-256
- `coordinates`／`quality`: 座標・中心ズレ・品質列の定義
- `build_seconds`: 生成開始からmanifest作成前まで。ZIP作成時間は含まない。

Consumerは形式名と対応するバージョンを検証する。将来の互換性を無条件に仮定しない。
ファイルのハッシュは破損・版違い確認用で、電子署名や真正性保証ではない。

## measurements.parquet

1行はSID空白行から採用した1パターン。キーの一致は数値化後のLayoutCoordで比較する。
`10`と`10.000`のような表記差で別パターンにしない。

| 列 | 型 | 単位・意味 |
|---|---|---|
| IMAGENAME | string | 元の画像名。識別キーとして保持 |
| SID | string nullable | 採用時は空白のためnull |
| ContourSuccess | string | 元の状態を保持 |
| DefectFlag | string | 元の品質フラグを保持 |
| LayoutCoordX/Y | float64 | デザイン中心、nm |
| PixelX/Y | float64 | 画像上のデザイン位置、pixel |
| CenterX/Y | float64 nullable | 実輪郭中心、Layoutと同じ座標系、nm |
| image_id | int32 | images.jsonへの参照 |
| center_available | bool | CenterXとCenterYが両方非null |
| quality_normal | bool | DefectFlagがNormalと完全一致 |
| vector_valid | bool | center_availableかつquality_normalかつContourSuccessがYes |

追加列は設定した型・単位で保持する。型と単位はParquetのfield metadataにも記録する。
文字列以外の空欄もnull。元の0値は0として保持する。
有限でない数値は値として採用せずエラーとし、欠測へ黙って置換しない。
`vector_valid`は描画候補の保守的な判定で、物理的妥当性や異常値なしを保証しない。
欠測・品質不良も行は残し、Viewer側の表示選択と切り離す。

画像名、LayoutCoordX、LayoutCoordYの順にソート。
**1画像につき1 row group**でZstd圧縮する。
1枚分の必要列だけ読める一方、多数のrow groupによるメタデータの容量・読込コストは残る。

## images.json

画像名のソート順で付番した画像情報の配列。
元画像名を出力のパスに直接使わず、数字のディレクトリへ格納する。

- `image_id`／`IMAGENAME`
- `directory`: `images/000000`等
- `row_group`: Parquetの0始まり行グループ番号
- `row_offset`／`rows`: 全体の行位置・当該画像の採用パターン数
- `size_pixels`／`nm_per_pixel`
- `source_ovlx_sha256`
- `assets`: 4つの抽出ファイルそれぞれのバイト数・SHA-256

CSVに参照されないOVLXは出力しない。選択された全画像にはOVLXが必要。
`IMAGENAME`の拡張子を`.ovlx`へ変えて`viewer/`直下を参照する。
画像名のディレクトリ成分・Windowsドライブ・パストラバーサルは許容しない。
OVLX全体の汎用展開はせず、既定の4ファイルだけを直接読む。
必須メンバーの欠落・重複、JPEG寸法と条件の不一致はエラー。
OVLX内の画像はPNGではなくJPEG原画を保持する。

## 座標と輪郭

4ファイルはOVLX内部のバイト列をそのまま保存する。
design.svgとcontour.svgが同じキャンバス・倍率であるとは仮定しない。
cond.jsonのnm/pixel、matching_shift、manual_shiftを変更せず保持する。
デザイン種類やSVGの重複レイヤーもこの段階では削除・統合しない。

中心ズレは`CenterX/Y - LayoutCoordX/Y`。Builderは数値を保持し、
ベクトルの画像座標への変換・表示倍率・工程間位置合わせは行わない。
特に実中心を揃えて工程差を消す処理はしない。
デザイン座標系の原点は元データ定義のまま。
今回の代表データから得たY反転を全入力へ無検証で適用しない。

## 部分読込例

```python
import json
from pathlib import Path
import pyarrow.parquet as pq

root = Path("/path/to/dataset")
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
if manifest["format"] != "plugin-viewer-dataset" or manifest["format_version"] != "1.0.0":
    raise ValueError("Unsupported dataset format")
images = json.loads((root / manifest["image_index"]["path"]).read_text(encoding="utf-8"))
image = images[0]
file = pq.ParquetFile(root / manifest["measurements"]["path"])
targets = file.read_row_group(image["row_group"], columns=[
    "LayoutCoordX", "LayoutCoordY", "PixelX", "PixelY", "CenterX", "CenterY", "vector_valid",
])
raw_image = root / image["directory"] / "image.jpeg"
```

これは読込例であり、Viewer UIや座標補正・品質不良の表示方針を実装したものではない。
