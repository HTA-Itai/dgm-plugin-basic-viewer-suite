# 単一データセットSQLite契約 v4

## 責務と入口

- 正本の変換処理は[sqlite_builder.py](../../sqlite_builder.py)。
  [main.py](../../main.py)の既定出力はこの形式。1回の処理は1つの結果ディレクトリだけを対象とする。
- CSV・OVLX・MSRを読むのはBuilder。Viewerには元入力を要求しない。
- Base/Comparisonはファイルを開く時のViewerの役割。ファイルにはrole、比較相手、表示倍率を固定しない。
- 従来の[Parquet契約1.0](format.md)は`--format parquet`で明示利用する。v2との直接互換性はない。

```sh
python3 applications/comparison_viewer_data_builder/main.py \
  --source /data/extensions/DATASET/RESULT \
  --dataset-id DATASET --msr /data/msr/DATASET \
  --output /results/DATASET.sqlite
```

`source`はreport/とviewer/を持つ**一つの結果**。CSVは設定のTargetCSV。
MSRはウェーハの存在マスク・配列・ピッチ・原点に必要な補助入力。
Viewerが元MSRを再解析する構成に戻さない。
Plugin triggerでは`input/<TargetData>`、`input/msr/<Msr>`を使用する。
MSRの配置だけ異なる場合は`SourceMsr`にinput相対パスを明示できる。
入力制約は既存Application契約に従い、MSRもinput外へ出るパスを拒否する。
成果物は`output/<Msr>.sqlite`。MSR名をそのまま使い、Base／Comparisonは名前に含めない。
正式リリースのtrigger実行では、同じ`output/`に固定Windows Viewerと完了情報も出力する。
配布構成と成功条件は[リリース仕様](../../../../documents/release.md)を参照する。
`Msr`が未指定または空の場合は警告し、従来どおり`output/dataset.sqlite`を使う。
この場合も`dataset_id`は従来どおり`TargetData`になる。
MSR名がパス、Windowsで使えない文字、予約デバイス名などの場合は変換せずエラーにする。
同じMSRから複数の結果を生成した場合は同じファイル名となる。
CLIの`--output`指定と旧Parquet形式の出力名は変更しない。Workflowへの追加はまだ行っていない。

出力の上書き・source配下への出力・入力変更を禁止する。
一時領域で生成・整合性検証した後、同じファイルシステム上のhard linkで排他的に公開する。
SQLiteは1ファイルなのでCreateZip/`--zip`は使用しない。
追加ライブラリはなく、既存のPython3.12・PyArrow・Pillow・PyYAMLと標準sqlite3を使用する。

## 物理形式

`PRAGMA user_version=4`。整数の形式版とmetadata.manifestの版が一致する必要がある。
読み手は未対応版を拒否する。拡張子を変えるだけの移行は認めない。

### metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)

valueはJSON。

| key | 内容 |
|---|---|
| manifest | format=`plugin-viewer-sqlite`、format_version=4、builder_version、dataset_id、作成UTC、CSV/MSR SHA256、画像/計測数、輪郭対応数、contour_encoding、座標の説明、対応Viewer形式 |
| layout | info（SHOT_ARRAY、SHOT_SIZE_nm、IN_SHOT_CHIP_ARRAY、CHIP_SIZE_nm、CHIP_ORIGIN、ORIGIN_OFFSET_nm）、cells（x,y,code）、MSR SHA256 |
| test_chip_override | 通常Builderは生成しない。明示的なテスト補正コピーだけが追加する警告・元SHA・対応表・初期表示画像名 |

`layout.cells`の0は不存在、非0は存在。コードの測定上の意味は解釈しない。
現対応はIN_SHOT_CHIP_ARRAY=1×1のIDWデータ。別構造を黙って変換しない。

Builderは輪郭SVGのpolygon順に`geometry.contour`を保持し、各パターンのPixelX/Yを包含する輪郭indexを保存する。
Viewerはこのindexで輪郭を引き、画像ごとの再マッチングをしない。
v3試作で毎回全geometryを展開するボトルネックを確認したため、v4では次の直接読込データを必須にした。
v2/v3のSQLiteはBuilderで再生成する。形式番号だけを書き換えてはならない。

### images

`id INTEGER PRIMARY KEY`は**当該ファイル内**のIDで、他ファイルと重複してよい。
その他の列:

| 列 | 内容 |
|---|---|
| name | 元IMAGENAME、`.png`。実背景のJPEG形式とは別 |
| pno, seq, ms | PNO、SEQUENCENO、ファイル名の01MS/02MS等 |
| chip_x, chip_y | 元XINDEX/YINDEX。存在マスク内であることを検証 |
| xrel, yrel | チップ内取得位置、元XREL/YREL、nm |
| jpeg | OVLX image.jpegのバイト列を変更せず格納 |
| geometry | UTF-8 JSONをzlib圧縮。width/height、nm_x/nm_y、contour/designのpolygon配列 |

`UNIQUE INDEX pairing(pno,seq,ms,chip_x,chip_y)`。曖昧な取得画像は拒否。
一画像内のPNO/SEQ/チップ/取得位置の不一致も拒否。
geometryは既存ovlx描画条件の設計位置オフセットを反映した**画像座標**。
工程間位置合わせやチップ番号の補正は通常Builderで行わない。

### contour_data

1画像1行、`image_id INTEGER PRIMARY KEY`。Contour overlay専用の読込経路であり、
`images.geometry`（背景画像表示用）と元assetsは変更しない。

- width/height、nm_x/nm_y: 画像frameと校正。geometryと同じ値。
- design_x/design_y REAL: 密度の原点にするdesign layout中心（画像pixel座標、float64）。
  geometry.designのうち画像全体を囲むframeを除いた各polygon bbox中心の平均。
  画像ごとのmatching shiftでdesignの画像内位置は数nm変わるため、全画像密度は画像中心ではなくこの点で揃える。
  design patternがない画像はBuilderでエラーにする。
- offsets BLOB: polygonごとの先頭点番号と終端。little-endian uint32、先頭0、要素数はpolygon数+1。
- points BLOB: SVG polygon順・頂点順の画像pixel座標X,Y。little-endian float64、量子化・間引きなし。
  polygon iのバイト範囲は`[offsets[i]*16, offsets[i+1]*16)`。
- density_offsets BLOB: polygonごとのdensityレコード先頭番号と終端。little-endian uint32、要素数はpolygon数+1。
- density_cells BLOB / density_lengths BLOB: 同じ長さの並列配列。cellは1024×1024 gridの番号（little-endian uint32）、
  lengthはそのセル内の**輪郭長[nm]**（little-endian float32）。polygon iは`[density_offsets[i], density_offsets[i+1])`。
  同一polygon内の同一セルは合算し、cell昇順。並列配列にするのはViewer（標準ライブラリのみ）が`array.frombytes`で
  一括読込・一括検証し、集計ループを最小化するため（1レコード構造体の逐次unpackは約2倍遅い）。
  原点はdesign_x/design_y（右向きX・上向きYのnm）、半範囲`max(width*nm_x,height*nm_y)/2`、grid外の長さは捨てる。
  各辺をX/Yのセル境界すべてで分割し、区間長を加算する（`length_cells`、Viewer engineと同一実装）。
  点サンプルの計数ではないため、0.5 px相当のセルでも線が通るセルを飛ばさず、合計長は輪郭全長と一致する。
  平滑化はしない。同じframe半範囲の画像は加算だけで集計できる。選択内に異なるframe半範囲がある場合は、
  Viewerが小さいframeの元pointsを共通半範囲で同じ方法により再計算する。
  manifest.density_encoding、manifest.density_origin（design layout中心）を記録する。
- 旧形式（点サンプル数のdensity/density_1024列）は「rebuild」エラーにする。
  v4はdevelop未公開のため形式版は4のまま。design_x/design_yがない初期のv4試作ファイルは
  「rebuild」エラーにし、画像中心基準の密度を黙って流用しない。

`manifest.contour_encoding=xy-f64le-offsets-u32le`。Builderで計算することでViewerのJSON展開と
輪郭の再分割を避ける。元輪郭座標・ROIによる対象選択は変えない。

### patterns

`PRIMARY KEY(image_id,ordinal)`。1行はSID空白の1パターン。元CSVの画像内順序を維持。

- layout_x/layout_y: LayoutCoordX/Y、nm。画像内の重複Layoutキーを拒否。
- px/py: PixelX/Y、元画像pixel。
- dx/dy: Center−LayoutCoord、nm。片側でもCenter欠測なら両方NULL。0を欠測とみなさない。
- magnitude: hypot(dx,dy)、nm。欠測はNULL。
- direction: 旧テスト形式との内部アクセス互換の予約列。v2 BuilderはNULLを格納し、GUIでは使用しない。
- available: 両Center値あり。valid: availableかつDefectFlag=NormalかつContourSuccess=Yes。
- contour_index: `images.geometry`内の輪郭polygon配列の0始まりindex。PixelX/Yを含むpolygonを対応付ける。
  輪郭がないパターンはNULL。複数polygonに含まれる曖昧な対応はBuilderが拒否する。
- raw: 全ての名前付き元CSV列を文字列のJSONとして保持。追加列設定は検証する列・型・単位を指定し、
  v4では未指定列もrawから削除しない。旧Parquetの列削除方針とは異なる。

SID対象外以外は品質不良・中心欠測を含めて保持する。品質による集計除外はViewerの責務。
CSVの列・値・キー・非有限値、OVLX重複/欠落member、JPEG寸法、SVG等を検証し、
正常に見える空ファイルを生成しない。

### assets

`PRIMARY KEY(image_id,name)`、content BLOB。
元design.svg、contour.svg、cond.jsonをバイト一致で保存する。JPEGはimagesだけに格納し重複保存しない。
Viewerは通常preprocessed geometryを参照し、SVG/condの再変換を行わない。

## 読み手・互換性・配布

[Viewer](../../../comparison_viewer/documents/ai/README.md)は2つをSQLite URI mode=roでATTACHする。
メモリ内に画像索引・ローカルID→セッションID対応だけを作り、BLOBをコピー/統合しない。
画像内容は選択時のみ読み出す。統計は数値列だけを読む。
同じデータの自己比較や左右を入れ替えた比較も可能。
利用中のファイルは外部から更新せず、再生成結果は新ファイルにして開き直す。

形式・schema・manifest件数・孤立行・取得キー重複はopen時に検証する。
全JPEGのdecode、全geometry/rawのparseは起動時に行わず、部分読込の失敗を明示する。
ファイルの内容とformat markerの整合性を偽造できないセキュリティ保証を意味しない。

本体を毎回データに同梱せず、Viewerは版付き別配布を推奨。
BuilderのSQLite内に対応形式版を持たせ、結果一覧/配布ページに対応Viewerリンクを置く案。
実際のPlugin結果へのリンク・バンドル生成時点は未決で、自動組込みはしていない。

## 検証

- [Builder tests](../../tests/test_sqlite_builder.py): roundtrip、raw/元asset、品質/欠測、
  不正識別値・マスク外・上書き拒否・CLI既定・標準/明示MSRのtrigger。
- [Viewer tests](../../../comparison_viewer/test_two_files.py): ID衝突、read-only、形式不正、自己比較、左右交換。
- [全件照合](../../../comparison_viewer/test_source_data.py): 代表2件の全51,600行、2,580JPEG、全SVG/条件。
- 性能は[Viewerガイド](../../../comparison_viewer/documents/ai/README.md)参照。Windows性能とネイティブダイアログは未検証。
