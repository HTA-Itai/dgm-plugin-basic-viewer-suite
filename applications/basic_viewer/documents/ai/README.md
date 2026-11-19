# Comparison Viewer: AI開発・現行仕様ガイド

## 最初に読むもの

1. 本書: 責務・現行契約・壊してはいけない振る舞い。
2. [操作と統計の詳細](../../README.md): Apply A、GUI、数値・座標の定義。
3. [Builder SQLite v4契約](../../../comparison_viewer_data_builder/documents/ai/sqlite-v4.md): 入力形式の正本。
4. 共通の検討履歴（Plugin本体リポジトリの`documents/ai/local-comparison-viewer.md`）: 過去の試作・利用者指摘・未決の経緯。

ソース正本は本リポジトリの`applications/comparison_viewer/`（旧Tools→Registryから移管）。Registryの複製は直接編集しない（[配備手順](../../../../documents/deployment.md)）。
Registryは現状Git管理外なので本体のコミットだけでは保存されない。BuilderとViewerのソース・文書・
テストは明示的にバックアップ/配布する。Registryに置くこととPlugin RunnerでGUIを起動することは別。
既存Workflowは変更していない。

## 利用者向けガイドの更新

- [USER_GUIDE.md](../../USER_GUIDE.md)は操作の早見表と結果の読み方に絞り、内部実装の詳細は本書・READMEへ記載する。
- 図の近くに「1本/1点/1セルが表す対象」「軸・色・単位・集計の分母」を短く記す。
  Center offsetの各パターン散布図とPPEのPID集計散布図、輪郭線・輪郭長密度・面積差を区別する。
- ガイドの図は画像・輪郭・計測値・データセット名を含めて完全な架空データを使う。
  実SEMの加工・ぼかしでは代用しない。画像内に`DUMMY IMAGE - NOT SEM`、本文に架空データである旨を明記する。
- mdと図を変更したらPDFを再生成し、図・表・改ページを確認する。PDF自体はGitへ追加せず、配布物へ同梱する。

## 合意したProducer/Consumer分離

```text
extensionsの1結果(CSV/OVLX) + 元MSR
  -> comparison_viewer_data_builder -> 1つの<MSR名>.sqlite -> ダウンロード

Viewerをローカルで起動（Python/Qtを同梱）
  -> Open Base...（Base単独で閲覧可）-> Open Comp...（任意）
  -> 段階的に範囲を編集 -> Apply -> 両Map/統計/グラフ更新
```

Viewerは元入力を変換しない。以前の固定2データ変換prepare_demo.pyは削除した。
通常GUI起動はデータなし。Baseを開き、Compは任意で追加する。
片側の取消・形式不正は旧セッションを保持。形式検証はmanifest必須キー（`csv_sha256`含む）と
選択ファイルの試験接続まで行い、失敗を旧セッション置換前に検出する。成功時のみroleを割り当て、
ページ再初期化で条件・画像・結果を消去し、全体Applyを一度実施する。
不正ファイルはログとダイアログで明示する。
旧セッション応答はページごとに異なるrequest ID prefixで無視し、新条件の結果に混ぜない。
CLIの`--base`と`--comparison`は開発/検証用で、Base単独も指定できる（Comp単独は禁止）。

engine.Storeのv1形式は`allow_legacy=True`を明示する過去の回帰テスト用としてのみ残す。
本番のfile pickerはv4のみを受け付け、旧固定DB・Parquetは開けない。

## 維持するUI・解析契約

- GUIは英語。Base/Comparisonの名前は各1行。10倍ベクトル、2.5 screen pxの輪郭/Design。
- ImageのContour / Vectorsは各Base/Compの一括ボタン。操作可能な子がすべてONのときだけ一括OFF、
  それ以外（片側ONを含む）は一括ON。無効なCompを有効化せず、Base単独ではBaseだけを切り替える。
  `syncOverlayGroups`で個別操作も`aria-pressed`のtrue/false/mixedへ反映し、Expandも再描画する。
- Image typeの01MS/02MSはPNOとは別。異なるPNOを混ぜない。
  候補は現在のPNOに存在するMSだけ。PNO変更・Undoで無効になったMSはAllへ戻す。
- Chip Mapの選択判定は`isSelected`（Comparison entryは`pair_id`でBase選択と対応）を使い、
  MS巡回と選択リングはMapの表示dataset（Show Base | Comp）に依存せず動く。
- Draftの絞込・選択枠・画像一覧は即時、分析結果はApplied snapshot。
  Apply中の追加編集はPendingに残す。失敗時は旧結果保持＋明示エラー、再試行可能。
- Mapの表示datasetは各Map見出しの`Show Base | Comp`で切り替え、Wafer MapとChip Mapで連動する（`state.mapRole`）。
  元計測値ドロワーのBase/Comparisonタブ（`state.rawRole`）はMapと独立。画像の背景JPEGは`state.mapRole`に連動（Comparisonは`comparison_image`コマンドで遅延取得・画像ごとにキャッシュし、
  `image.shift`の位置に描くのでROI・輪郭はBase座標のまま。表示中の背景は`#imageBackground`）。輪郭・ベクトルと統計は常にBOTH。
- Wafer Mapはチップ配置図で、物理原点付きウェーハ座標図ではない。
  計測済みのチップは初回Apply完了前から着色する。
- Chip MapはFocus Chip 1枚だけを表示。Focus・画像閲覧・Pan/Fitは分析条件を変えない。
- Chip MapとImage overlayのFitは各パネル見出しの右上に統一し、ROI解除から離す。
  ChipはPanリセット、ImageはPan/Zoomをリセットして全体表示。ツールチップで選択・解析不変を明示する。
- 画像とChip Mapとも通常dragはPan、Ctrlを押してpointerdownしたdragだけ矩形ROI。
  Ctrlを途中で離しても開始時の操作を維持。cancelは確定しない。
- 画像対応キーは(PNO,SEQ,MS,chip_x,chip_y)。別チップで代用しない。
  両画像がチップ/取得位置条件を満たす**共通の対応画像集合**で、Map/統計/graphを統一する。
  unpairedとcounterpart_outsideを表示する。
- Center offset統計のPattern ROIは両元画像のPixel座標へ独立適用し、品質も独立判定するため、有効Nは異なってよい。
  パターン対応の差分はPPEタブだけが扱う。
- Builder v4は各patternのPixelX/Yを含む`geometry.contour`polygon indexを`patterns.contour_index`へ保存する。
  NULLは未対応、複数polygon一致はBuilderで拒否。ViewerのContour overlayはこのindexで輪郭を引き、
  PNo・Chip・Area・Image typeのApply済み対応画像ペアを母集団として扱う。
- 全画像密度はimage frame中心のデザイン座標(nm)へ変換し、個々のPID位置シフトを行わない。
  輪郭polygonの各辺をセル境界で分割し、セル内の輪郭長[nm]を共通1024×1024 gridへ加算して、対応画像pair数で平均化する。
  点サンプル計数は0.5 px相当のセルを飛ばして市松状の偽模様を作るため使わない。表示を良く見せるための平滑化もしない。
  Base/Comparisonは共通のdensity scaleを使う。E2は色相が密度の優勢側、明るさが両者の合計密度。
  密度差はPID対応の移動量を意味しない。
- `contour_data`はfloat64輪郭・uint32 offsets・事前計算した輪郭長セル（density_offsets/density_cells/density_lengths）を持つ。
  全画像scopeを全geometryのJSON展開へ戻さない。This imageだけは当該画像ペアのdesignを読み、中心を特定する。
  共通grid範囲が異なるframeは元pointsから`length_cells`（Builderと同一実装）で再計算する。
  旧形式（density/density_1024の点サンプル）は「rebuild」エラー。
  密度の原点は画像中心ではなく`contour_data.design_x/design_y`（design layout中心）。再投影もこの原点を使う。
  `design_x/design_y`のない初期v4試作は「rebuild」エラーにする。表示は1024固定で2048は提供しない（利用者判断）。
  密度配列2枚で16 MiB。gridは最大2結果（計32 MiB）、全体最大4結果をcacheする。
  同じscope/measureの旧gridは置換し、上限時は安価な線/単一画像から先にLRUで破棄する。
  全画像Canvasは上下余白を抑え、HiDPIと高品質補間に対応し、X/Y両方のnm目盛を出す。輪郭Canvas以外の既存pixel座標系は変更しない。
  SEM / Designは独立ON/OFFで、SEM opacityを持つ。参照は`backgroundRole()`の現在1枚であり、集団の平均像ではない。
  `image.reference_frames[role]`のwidth/height/nm_x/nm_y/design_centerとroleごとのdesignで、密度と同じdesign layout中心へ置く。
  左ImageのPID平行移動を参照へ流用しない。SEM decode完了・背景role変更時も再描画し、参照datasetと画像名をstatusに明示する。
  密度0セルは透明、画像領域は黒地、その上にSEM→密度→Designの順で描く。
  高解像度密度・同一PID応答はzlib圧縮float64をbase64で転送。
  ブラウザではDecompressionStreamで展開し、Path2Dを一度だけ構築する。
  現viewportのrasterも保持し、pan/zoom操作中は高速プレビュー、操作停止150 ms後に元Pathから再描画する。
  status文言でパネル高さが変わるため、文言更新→ResizeObserverの寸法反映後にraster化する。
- Show contoursで元Imageを左、Contourを右に表示する。境界のdrag/keyboard/double-clickで幅調整し比率を保存。
  元画像のFit/Expand/layers/ROIは常に使え、右のFitとは独立する。Hide contoursは右だけを閉じる。
  This imageは現在の1画像ペアのみ。ROI内（未指定なら全体）、またはクリック選択PID群の各design中心を原点へ揃える。
  PixelX/Yは選択用。位置合わせ中心はその点を含む最小のdesign polygonのbbox中心。全画像を囲むframeを除外し、
  無対応・同面積の曖昧対応はエラー。元データ51,520輪郭で対応を確認済み。計測中心や丸めpixelへ置換しない。
  clickで単一PID、Ctrl+clickで追加/解除。This imageのPID群はROIと排他的で統計条件を変えない。
  Ctrl+dragは従来どおり共通ROIを設定し、This imageをROI選択へ戻す。元画像上に対象を黄色で示す。
  Same PIDは画像クリックでも1 PIDを指定でき、その輪郭範囲へFit。座標は従来の画像中心のまま。
  This imageと同じ`paintSelectionDots`で、Same PIDの選択PIDにも元Imageのdesign位置へ黄色の発光点を描く。
  PID selector/画像click・Density/Area Diff・Expandに追従し、PIDラベルやContourのON/OFFには依存しない。
  Apply後のPID自動選び直しも`syncContourPids`内で変更を検出して元Imageを再描画する。
  Same PIDはheatmapではなく明るい線（alpha .85）。Base / Comparison / Bothはbackend要求なしに描画対象だけを切り替える。
  Contour線・vectorは共通のBase=赤#ff6767/Comparison=青#4687ffとし、datasetボタン・専用ラベル・統計表のdataset名も合わせる。
  Same PIDのBothだけ各roleを一時rasterへ1回strokeし、
  alpha積の共通strokeを紫で強調する。自然な混色・塗り面積の重なり率ではない。一時layerをcacheへ保持しない。
  All selected密度の既存paletteは維持し、PID用凡例をCanvas下部に表示する。
  All selected imagesも画像中心を維持する。複数画像にまたがるSame ROIという別scopeは追加しない。
  Loading（spinner・経過秒）、空結果、失敗（Retry）を区別。要求は1件処理中＋最新1件だけ、直近4結果をLRU保持。
  同じkeyの処理中再発行や、破棄予定のscopeをworkerへ積み上げる実装をしない。
- パターンの対応は**PID**（`patterns.raw`の`PID`）で取る。Layout座標はADI/ACIで座標系が違い得るため対応キーにしない。
  PIDが空・画像内重複のパターンは対応させない。
- Area differenceは輪郭**面積差**。内側=Base \ Comp、外側=Comp \ Baseを一意なPIDごとに計算する。
  Imageの`image_alignment`を共用し、個々の計測輪郭/中心での位置合わせは禁止。異なるpixel校正は明示拒否。
  Imageの全パターンへの塗りはSEMと元strokeの間にeven-odd fillで描く。元輪郭色・Expand・既存scopeを保つ。
  Inward=淡いピンク#ff9eae、Outward=シアン#55c8ff。Imageのopacityは.78。
  Inwardの選択ボタンも同じピンクで塗り、非選択時はoutlineとする。
  `areaColors`からgridのRGBも導出し、Image/This imageの面・ヒートマップ・凡例で色が乖離しないようにする。
  Contour overlayの上位モードContour Density / Area Diffは全scopeに適用する。
  Area Diff / This imageは同じBaseデザイン中心で差分両領域を重ね、独立した計測輪郭合わせはしない。
  Area Diff / Same PIDは画像中心座標を保持した指定PIDの平均面積ヒートマップ（差分範囲にFit）。
  Area Diff / All selectedはBaseのdesign layout中心基準nmへ差分を移し、
  Greenの定理による部分セル面積を加算し、1パターン以上比較できた画像ペア数で割る。
  ROIはBaseのPixel位置で選び、除外理由/使用ペア数/比較Baseパターン数を明示する（CompのみのPIDは候補外）。
  2チャンネルは非負のまま別保存し、相殺しない。線形共通尺度、数値hover、補間なし、ゼロ差分も正常描画。
  参照SEM/DesignはAll selectedのみで現在のBase1枚に固定。他scopeへ隠れた設定を適用しない。Base単独では操作無効。
  NumPy/Shapely（同梱wheel）はこの処理だけ遅延importし、既存engine経路の標準ライブラリ互換を維持する。
  256領域ずつのバッチ積分でメモリを抑え、全辺イベントをglobal sortしない。不要な密度BLOBも読まない。
  セルへの加算は`np.add.at`で行い、バッチごとの全gridサイズの`bincount`を避ける。
  All selectedで2画像ペア以上・ROI内Base候補4,096件以上の場合のみ、`ProcessPoolExecutor`で2分割する。
  `spawn`と既存engine内のtop-level `_area_chunk`を使い、Qtからforkしない。新たな依存や配布ソースは不要。
  SQLite Rowではなくplain dictを送り、子は元DBをread-onlyで検証・接続する。
  親のextentを共有し、子は圧縮float64の未平均gridと使用ペア数を返す。
  必ず未平均値を合算して全使用ペア数で一度だけ割る（worker平均の平均は禁止）。合算後も保存則を検証する。
  `_AREA_POOL_LOCK`で重複要求の同時子数を2に制限し、executorのcontext managerで成功/失敗時ともjoinする。
  失敗は既存Qtエラー/Retryへ伝え、黙った直列fallbackはしない。終了時は既存Qt pool待機を経て子も終了する。
  小選択・Same PID・This imageは直列。差分geometryの選択間cacheや永続cacheは今回追加していない。
  旧応答は画像ID/keyで排除し、最大4件cache（大gridは2件・同scope/measureは1件）。
  area要求は10分、他は従来どおり2分。
  詳細な分母・品質・除外規則は[面積差の判断記録](../../../../documents/decisions.md#contour面積差2026-11-17)。
- 位置合わせは同じPIDのPixelの平均平行移動だけ。実Contour/実Centerへの補正は禁止。
  共通PIDなしは理由を表示してComparison overlayを出さない。
- PPE = dx_Base − dx_Comparison（Y同様）、|PPE|=hypot。両方validの組だけ。PID別とAllでN/Mean/SD（母SD）。3σは画面・CSVとも出さない。
  画像ROIはBase側パターン位置で判定。PIDの色は全データのPID順で固定し、表・ヒストグラム・箱ひげ・散布図で一致させる。
  PPE散布図はPIDごと1点（横X・縦Y、Mean/SD切替、縦横同スケール、Meanは0中心）。軸はApply済み選択に追従。
  定義と判断の経緯は[decisions.md](../../../../documents/decisions.md)の「PPE」節。
- Center offset（内部キー`dx`/`dy`/`magnitude`、表示Offset X/Offset Y/|Offset|）はCenter−Layout、screen Yは反転。
  MapはOffset X/Y成分中央値と|Offset|中央値。
  色と矢印長を分離し、nm→screen pxの長さ校正を明示する。
- graphはOffset X/Offset Y/|Offset|を選択、scatterはOffset X×Offset Y固定。全体PNOの固定横軸/共通binと0軸/補助軸を保持。
  ヒストグラムの件数（縦）軸だけはApply済み選択の表示中の棒に合わせる（`niceCount`で丸め）。ROIで件数が減っても棒が潰れないため。
- CSVはPending時の統計出力を禁止し、見出しと値の両方で文字列の式起動文字を無害化する（元CSV由来の列名も対象）。ただし符号付き数値の文字列（例: `-45.190`、`+1e-3`）は数値として残す。
- 画面はワークベンチ構成（合意内容は[decisions.md](../../../../documents/decisions.md)の「ワークベンチレイアウト」節）。
  左レール=Selection→Wafer map→Chip map（Mapは残り高さを分け合う）、中央上段=Image list＋`#listSplitter`＋Image、中央下段=splitter＋統計タブ、右=元計測値ドロワー（既定は非表示、右端の縦タブ`#rawToggle`「Raw measurement data」で開閉。開くとタブはドロワー左端に付く）。
  Exportはヘッダの`Export ▾`メニューに集約。GUIの略称は全部品で統一する（同節の前の「GUIの表記ルール」）。
- Image listは仮想スクロール（行高`ROW_HEIGHT`固定）でページングしない。`state.matched`=分析条件で絞った一覧、
  `state.visible`=さらに検索で絞った表示行。検索（名前部分一致・chip・Seq完全一致）は一覧だけに効き、統計・Mapを変えない。
- 元計測値ドロワーが閉じている間は`renderRaw`が表を作らない。CSV出力（`#rawCsv`）は閉じていても選択画像を出す。
- Expandは`window.open("")`のabout:blankへopener側からDOMを組み立てる。選択・レイヤ・ROI・PID強調は本窓と共有、
  Pan/Zoomだけ独立（`imageViews()`の各viewが`transform`を持つ）。Qtでは`ViewerView.createWindow`が同一profileのpopupを作る。
- レイアウト（splitter高さ・統計の最小/最大・Map/Image list折りたたみ・Image list幅`listWidth`）は`localStorage`の`comparisonViewer.layout.v1`に保存する。
  QtのdefaultProfileはoff-the-recordで保存されないため、名前付きprofile`ComparisonViewer`（`viewer_profile()`）を使う。
  Windowsでは`%LOCALAPPDATA%\ComparisonViewer\QtWebEngine\ComparisonViewer`に保存される想定（Linux offscreenで2プロセス保持を確認済み、Windows実機は未検証）。
- SQLiteには書き込まない。外部HTTPアクセス、画像全件decodeや一時asset展開を追加しない。

## 実装とテストの地図

| 実装 | 責務 | 主な検証 |
|---|---|---|
| [main.py](../../main.py) | Qt、2 worker、Open/CSV保存、セッション切替、名前付きprofile、Expand用popup | [test_open_qt.py](../../test_open_qt.py)、[test_qt.py](../../test_qt.py)、[test_export.py](../../test_export.py) |
| [engine.py](../../engine.py) | v4検証、read-only ATTACH、メモリ内ID索引、pair/image/statistics、PID対応とPPE（`ppe_pairs`、`ppe_values`）、面積差と2プロセス集計 | [test_engine.py](../../test_engine.py)、[test_two_files.py](../../test_two_files.py)（実spawnの全セル比較・ROI・分母・例外・子終了）、[test_area_qt.py](../../test_area_qt.py)（Qtからのspawn・処理中close） |
| [contour_area.py](../../contour_area.py) | Shapely/GEOSベクトル差集合、NumPyの保存則付きバッチセル面積積分（area要求だけで使用） | [test_contour_area.py](../../test_contour_area.py)、[test_two_files.py](../../test_two_files.py)、[test_area_browser.cjs](../../test_area_browser.cjs) |
| [viewer.js](../../viewer.js)、[web.html](../../web.html)、[viewer.css](../../viewer.css) | draft/applied、Canvas、表、graph、共通操作、仮想一覧・検索、Expand、レイアウト保存 | [test_workflow_browser.cjs](../../test_workflow_browser.cjs)、[test_single_browser.cjs](../../test_single_browser.cjs)、[test_selection_browser.cjs](../../test_selection_browser.cjs)、[test_ppe_browser.cjs](../../test_ppe_browser.cjs) |
| [prepare_test_overlay.py](../../prepare_test_overlay.py) | 明示的なテスト用コピーだけのchip補正 | [test_test_overlay.py](../../test_test_overlay.py)、実データ回帰 |
| [package_windows.py](../../package_windows.py) | 固定データなしのWindows同梱版、PE依存、CRC/SHA、USER_GUIDE.pdf同梱 | 実staged appのQt/browser検証＋ZIP全件照合 |
| [build_user_guide_pdf.py](../../build_user_guide_pdf.py) | USER_GUIDE.md→A4 PDF（PySide6、Creatorに元mdのSHA-256）。md内の画像はmd基準の相対パス（`documents/screenshots/`）で埋め込み、本文幅へ縮小し、欠落時は失敗させる | `test_user_guide_pdf.py` |

Viewerのrequirements.txtを導入済みの開発環境で、以下をリポジトリルートから実行する。
Windows利用者にはこの開発環境は不要。

```sh
python3 -m unittest discover -s applications/comparison_viewer -p 'test_*.py' -v
python3 applications/comparison_viewer/test_qt.py BASE.sqlite --comparison COMPARISON.sqlite
python3 applications/comparison_viewer/test_open_qt.py BASE.sqlite COMPARISON.sqlite SYNTHETIC.sqlite
VIEWER_COMPARISON=/path/COMPARISON.sqlite node applications/comparison_viewer/test_workflow_browser.cjs /path/BASE.sqlite /path/results
PLUGIN_ROOT=/path/to/plugin python3 applications/comparison_viewer/test_source_data.py BASE.sqlite --comparison COMPARISON.sqlite
NODE_PATH=/path/to/node_modules node applications/comparison_viewer/test_selection_browser.cjs
NODE_PATH=/path/to/node_modules node applications/comparison_viewer/test_ppe_browser.cjs BASE.sqlite SYNTHETIC.sqlite /path/results
NODE_PATH=/path/to/node_modules node applications/comparison_viewer/test_area_browser.cjs BASE.sqlite SYNTHETIC.sqlite /path/results
python3 applications/comparison_viewer/test_area_qt.py BASE.sqlite SYNTHETIC.sqlite /path/results
```

Browserテストには既存のPlaywrightとChromeが必要。QtテストはLinux offscreenで実行する。
開発者環境のPySideを利用者PCへ要求する設計に変えない。

## 実データとテスト補正を区別

代表データは両1,290画像/25,800行で、元の共通chipがない。
したがって元データ同士では対応0、両側unpaired=1,290が正しい。
ユーザ承認の動作確認コピーだけ、Comparison chipをBaseの番号に補正する。
SQLiteの`metadata.test_chip_override`に補正の対応表と元SHAを記録し、ViewerはBaseまたはComparisonに
この記録があれば合成chip座標の警告を表示する。座標から補正の有無を自動検出しているわけではなく、
記録がないことは未加工や同一サイトの証明にはならない。元chip/raw・元SHAを維持する。元座標や原画・計測値は変更しない。
本来的に同じchipと扱えるかはユーザ判断待ちで、正式な工程比較として認めていない。
補正コピーでは1,288組、valid Base=22,214/Comparison=20,652、代表重ね20pattern・17/16vector。
元DBに保持する有効数22,246/20,684とは区別する。

## 性能と配布

Linuxローカル、cache未消去、3回中央値（Windowsやcold-cacheの保証ではない）:

| 操作 | 独立した2SQLite | 従来固定1SQLite |
|---|---:|---:|
| 形式/件数検証 | 約0.016秒 | 約0.001秒 |
| 2,580画像の索引・対応付け | 約0.096秒 | 約0.114秒 |
| 全体統計（補正テスト1,288組） | 約2.88秒 | 約2.65秒 |
| 代表1画像のpayload生成 | 約0.037秒 | 約0.017秒 |

データ読取の測定であり、Qt起動・ネイティブdialog・画像decode/Canvas描画時間を含まない。
新形式は原SVG/condも保存し各約540MB。JPEG等の全件をstartupで読む必要はない。
[benchmark.py](../../benchmark.py)で再測定し、環境・回数・SHAをJSONへ記録できる。

推奨: Viewerは版付き独立配布、BuilderはSQLiteのみ出力。
Pluginの結果一覧に対応Viewer版とリンクを加える方式が第一候補。
閉域/初回導入向けだけ任意のViewer＋データ同梱版とすると、毎回のダウンロード/展開を避けられる。
この結果一覧への組込み時点・release方法は未決で、自動実装していない。

Windows起動・native Open/Save・展開所要時間は未検証。Linux Qtのdialog呼出しはmockで検証済み。
Windows実機確認は配布後に必要。ライセンス/対応source提供義務は[README](../../README.md)参照。
