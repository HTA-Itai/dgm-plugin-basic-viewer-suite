# Builder初期版の検証記録

確認日: 2026-10-28。[契約](format.md)と[設計前提](README.md)を参照。

## 環境・依存

- Linux x86_64、Python 3.12.3。
- pyarrow 20.0.0、Pillow 11.3.0、PyYAML 6.0.2。
- 依存のバージョンをrequirements.txtへ固定し、cp312/Linux x86_64のwheelを
  Applicationのrequirementsへ用意。専用venvへオフライン導入して検証した。
- pyarrowはParquet読書きに使用。pandas／NumPyの追加依存は不要。
- Pluginの既存runnerはrequirements内のwheelをオフラインインストールする構造と確認したが、
  Dockerイメージビルド・Workflow実行・Windowsの新Viewerは未検証。

## 自動テスト

[tests/test_builder.py](../../tests/test_builder.py)を実行する:

```bash
python -m unittest discover -s applications/comparison_viewer_data_builder/tests -v
```

11テスト成功。対象:

- Parquetの値・型・単位、画像別row groupの部分読込。
- 必須列＋追加列4型、日本語文字列。
- SIDフィルタ、欠測のnull保持、Normal以外・Contour失敗を保持してvector_validを区別。
- 0の中心は欠測としない。片側の中心だけ欠測でも行を維持。
- 空の追加列設定、指定列欠落、不明設定・型・予約列。
- キー数値の表記差も含む重複、必須値欠落、無効・非有限数値、無効な画像パス。
- 原CSVにある名前のない末尾空フィールドを許容し、非空はエラー。
- 欠落・破損OVLX、必須メンバー欠落、不正SVG、無効な座標条件。
- 入力検証失敗時の最終出力なし・一時作業の清掃、既存出力の保持。
- ZIPのCRC、抽出アセットのバイト一致、sourceのCSVハッシュ不変。
- スタンドアロンCLIとPlugin形式trigger起動、input外参照の拒否。

Pylanceの構文検証はMCP接続エラーで実行できなかったため、
Pythonの構文コンパイルと実際の単体・起動テストを使用した。

## 実データ2件の全件検証

入力は`Input_nfs/GT2000/2D_Micron/extensions`配下の次の結果。

| 役割 | Msr | 結果ディレクトリ |
|---|---|---|
| ベース | 20251024_001322_XP420400_EPE_RGDFA_6O | 20261027225319_0001@MultiMeasurement |
| 比較対象 | 20251023_055538_XP420000_EPE_RGDFA_6O | 20261027225341_0001@MultiMeasurement |

`report/result_Size_Area.csv`、`viewer/*.ovlx`を読み、
`temp/viewer-builder-base`と`temp/viewer-builder-comparison`へ通常CLIで出力した。
それぞれ同名.zipも生成。元InputのCSV・OVLXや既存Applicationは変更していない。

| 項目 | ベース | 比較対象 |
|---|---:|---:|
| SID空白の採用行数 | 25,800 | 25,800 |
| 画像・row group数 | 1,290 | 1,290 |
| 中心が欠測の行 | 3,554 | 5,114 |
| Normal以外の行 | 0 | 2 |
| vector_validの行 | 22,246 | 20,684 |
| 元CSV | 12.73 MB | 12.53 MB |
| 最小列のParquet | 3.92 MB | 3.89 MB |
| 画像・SVG・索引等を含む出力フォルダ | 409.93 MB | 411.35 MB |
| 配布ZIP | 348.50 MB | 365.76 MB |
| manifest作成前までの生成時間（ZIPを除く） | 26.77秒 | 37.07秒 |

単位は10進MB。列削減と圧縮の両方を含むので、Parquetの圧縮率だけの比較ではない。
画像・輪郭を保持するため、全体容量は表データほど小さくならない。
共有ストレージの状態にも依存する単発計測であり、時間保証ではない。

独立した検証処理で次を全件照合した:

- 51,600行の元CSVとParquetのキー・全必須数値・品質値・派生状態が一致。
- 2,580画像それぞれで、索引のrow groupを指定して期待する画像の行だけが得られる。
- 2,580 OVLXの各4メンバー、計10,320ファイルが元OVLXのバイト列と一致。
- 各ZIPの全エントリCRC成功。
- Normalでも中心が欠測の行を削除せず、Normal以外で中心0の行も値を保持。

### 部分読込の実測

各7回のウォーム読込の中央値。Pythonローカル読込で、Viewer初期表示時間ではない。
CSVは共有入力ストレージ、Parquetはローカルtempにあり、保存先は同条件ではない。
CSVは全行を解析して7列の文字列を取り出し、Parquetは7列を型付きで読む。
ParquetFileを開く・索引JSONを読む時間はParquet値に含まない。
従って純粋な形式別の速度比や、Windowsでの性能としては扱わない。

| 読込処理 | ベース | 比較対象 |
|---|---:|---:|
| CSV全行を解析し必要7列を抽出 | 679.5 ms | 664.4 ms |
| Parquet全行の必要7列 | 75.9 ms | 61.0 ms |
| Parquetの1画像・必要7列 | 0.47 ms | 0.55 ms |

この実測で確認したのは、**全CSVの繰り返し解析をせずに、
指定画像の必要列だけ取得できること**。画像・SVG描画や工程間補正は含まない。

## 未検証・限界

- 新しいローカルViewerの実装・Windowsでの読込／描画。
- 大容量データでのBuilderピークメモリ。採用列全体をメモリ保持してソートする。
- 型・単位設定の正しさは入力契約に依存する。単位を自動推定していない。
- 工程間画像の位置合わせ、SVGレイヤー選択、5倍ベクトル表示。
- データセット間の対応付けはViewer側。1,290画像全部が工程間で対応するとは限らない。
- Docker基盤での実ビルド・通知・Workflow接続。今回は単体およびtrigger形式のサブプロセス実行。
- フォルダ名変更／ZIP展開後の新Viewerでの動作は、Viewer実装後に検証する。
