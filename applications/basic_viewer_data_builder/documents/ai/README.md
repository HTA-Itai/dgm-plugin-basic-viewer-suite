# comparison_viewer_data_builder: AI開発ガイド

comparison_viewerで読むデータを生成するBuilder。Viewerとの対応が分かる名称へ統一した。
旧名viewer_data_builder / comparison_viewer_builderの互換ディレクトリは残さない。ソース正本は本リポジトリの`applications/comparison_viewer_data_builder/`。
正式配備は固定Viewer内包リリースのインストーラー、ソース配備は開発用のみ（[配備手順](../../../../documents/deployment.md)）。

## 現行版: 単一SQLite v4

既定出力を**1データセット1SQLite**へ更新した。
元CSV/OVLXに加えてウェーハMap用の元MSRをBuilderへ渡す。
実行・schema・エラー・旧版との区別は[SQLite v4契約](sqlite-v4.md)を正本とする。
[Viewer](../../../comparison_viewer/documents/ai/README.md)は2ファイルをBase/Comparisonとして選択する。
Viewer配下にあったデモ変換処理は削除し、Producerを本Applicationへ統合した。
Plugin triggerの既定出力は`output/<Msr>.sqlite`。Msrがない場合は警告して従来の
`output/dataset.sqlite`を使う。既存Workflowの追加/変更はしていない。
正式SQLite triggerは固定Viewerと`result.json`も必須とする。
未配備ソースでSQLiteだけを検証するtriggerには`--data-only`を明示する（配備済みリリースでは拒否）。
同梱・版・成功条件は[リリース仕様](../../../../documents/release.md)を正本とする。

以下は**初期Parquet版1.0の記録**。
この旧ディレクトリ形式が必要な場合のみ`--format parquet`を明示する。
以下のViewer未実装という記載や5倍の初期希望は当時の記録で、現行の10倍・2SQLite Viewer仕様ではない。

確認日: 2026-10-28。新規Application、初期版1.0.0。
共通実行契約（Plugin本体リポジトリの`documents/ai/application-contract.md`）も確認する。
Registry自体はPlugin本体のGit管理外。本Application・設定・wheel（Git LFS）・文書は本リポジトリで一緒に管理し、Registryへ配備する。

## 目的と範囲

1データセットのCSV・OVLXを、Windowsローカルの比較Viewer向けに整えるBuilder。
初期用途は2データの輪郭重畳と中心ズレベクトル。将来は3データ以上と、
basic_viewer_light相当の散布図・箱・ヒストグラム・ヒートマップを目指す。

**Viewer本体、画像間位置合わせ、描画、既存Workflowへの組込みは未実装。**
既存のbasic_viewer_light・ovlx_extractorは変更しない。
比較相手・表示列・表示方法・矢印倍率には依存せず、1データ単位で出力を完結させる。

- 実装: [main.py](../../main.py)、[builder.py](../../builder.py)
- 設定: [vector.yml](../../config/vector.yml)
- 契約: [出力形式1.0](format.md)
- 検証・実測・未検証事項: [検証記録](validation.md)

## 合意したデータ・表示の前提

- 対応キーは`IMAGENAME + LayoutCoordX + LayoutCoordY`。
  意味のあるデータ同士を比較する前提でも、名前だけで対応させない。
- CSVはSID空白行のみ採用し、1パターン1行を保証する。重複は先頭採用や平均で解消せずエラー。
- LayoutCoordはデザイン座標、Centerは同じ座標系の実輪郭中心、単位はnm。
  `Center - LayoutCoord`が中心ズレ。PixelX/Yは画像上のデザイン位置。
- Builderは中心欠測・Normal以外も状態付きで残す。欠測をゼロ補完しない。
  0.000は欠測と決めつけない。実データにはNeckingで中心が0の例がある。
- Viewerで表示する矢印はデザイン中心→各データの実輪郭中心。
  初期表示倍率の希望は5倍、将来設定変更可能。倍率は実ズレ数値とは分離する。
- 画像・デザイン・各データのContour・ベクトルを独立レイヤーとして切替できるViewerを想定。
  画像全体のSVGを保持し、パターンごとの切り出しやPNGへの焼込みはしない。
- ovlx_extractorの描画を参考に、原画・SVG・cond.jsonを保持する。
  原画には`image.jpeg`を使い、既存の合成済みPNGを代用しない。
- 代表データではnm/pixel=0.659、X同方向・Y反転を観測したが、
  全入力共通の確定仕様にせず、Viewer側で条件情報と座標対応を確認する。

### 座標残差の保留方針

2026-10-28、ユーザは代表2データが同一半導体パターンの異なる工程の結果であり、
約1.20pixelの残差には工程によるパターン形成誤差の可能性もあると指摘。
原因を確定したものではない。

この残差はCSVの対応Pixel位置に共通平行移動を適用した後の値で、
実Contour同士や中心ズレを直接測定した値ではない。
原因究明・追加補正を現段階の前提にせず、まずViewerで見て違和感があれば検討する。
観察したい工程間ズレを消す補正は行わない。Builder自体は位置合わせを行わない。
共通の観測事実は入力データ記録（Plugin本体リポジトリの`documents/ai/input-data.md`）を参照。

## 実行

実行環境はPython 3.12、Linux x86_64。同梱wheelはこの環境向け。
Windowsは成果物を閲覧する将来Viewerの対象であり、
このLinux用wheelをWindowsへインストールすることはできない。

基盤は`requirements/`内のwheelをオフライン導入する。
スタンドアロンで依存を入れる場合も、同梱wheelと
[requirements.txt](../../requirements.txt)を使用する。

```bash
python -m pip install --no-index --find-links applications/comparison_viewer_data_builder/requirements \
  -r applications/comparison_viewer_data_builder/requirements.txt
```

### 単体実行

```bash
python applications/comparison_viewer_data_builder/main.py \
  --source /path/to/one-result-directory \
  --output /path/to/new-dataset \
  --dataset-id one-msr-id \
  --config applications/comparison_viewer_data_builder/config/vector.yml \
  --zip
```

sourceは`report/`と`viewer/`を含む**一つの結果ディレクトリ**。
親のMsrディレクトリから複数結果を自動選択しない。
`--dataset-id`省略時はsourceディレクトリ名を使う。
出力フォルダと任意ZIPの既存ファイルは上書きしない。別出力先を指定する。
source配下への出力は禁止し、元CSV・OVLXには書き込まない。

### Plugin起動

```bash
python main.py /app/work/trigger.json
```

Application作業ディレクトリで実行する。
`input/<TargetData>/report/<TargetCSV>`と`input/<TargetData>/viewer/*.ovlx`を読み、
`output/dataset/`へ出力。`CreateZip: true`なら`output/dataset.zip`も生成。
`TargetConfig`既定値は`vector.yml`、データ識別は`Msr`、なければ`TargetData`。
triggerの共通`Config`との自動マージはしない。
`TargetData`はinput内、`TargetConfig`はconfig内に制限する。
共有Inputの更新・InputPush通知・Workflow変更は行わない。

## 設定

```yaml
TargetCSV: result_Size_Area.csv
AdditionalColumns:
  SizeX:
    type: float64
    unit: nm
  Area:
    type: float64
    unit: nm^2
CreateZip: false
```

- 初期設定の追加列は空。必須の識別・座標・品質列は必ず保持する。
- 追加列は`float64`、`int64`、`string`、`bool`と単位を明示する。
  単位不明のままnmなどを推測しない。文字列等で単位がない場合は`unit: ""`。
- 削除した列はViewerからは選べない。必要な指標を追加指定して再生成する。
  必須列・派生列の上書き、指定列の欠落、未知の設定キーはエラー。
- `CreateZip`はbool。CLIの`--zip`があればtrueとして扱う。

## 失敗時と性能上の注意

- 必須列欠落、採用キー重複、無効な数値、非有限値、欠けたOVLX・必須メンバー、
  JPEG・SVG・座標条件の構造不正は非ゼロ終了。正常な空成果物を装わない。
- CSVにはヘッダにない末尾の空フィールドがあるため、空の場合に限り許容して記録する。
  名前のない非空値や短い行はエラー。
- 同一親ディレクトリの一時領域で生成・検証後、最終名へ移動する。
  通常の入力検証失敗では最終フォルダ・ZIPを残さない。
  最終フォルダとZIPを移動する2操作の間のOS障害まで原子的には保証しない。
- 追加列を含む**採用データ全体はメモリへ保持してソート**する。
  Parquetは画像ごとの部分読込を可能にするが、Builderのメモリが常に一定になるわけではない。
  超大規模データの外部ソート・ストリーミング化は未実装。
- 生のJPEG/SVGを無損失で保持するため、画像・輪郭の容量は残る。
  配布ZIPと、展開後の部分読込の目的を区別する。
