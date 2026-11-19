# Comparison Viewer：2ファイル読込・空間絞込・統計

> **For users:** see the latest **[User Guide (USER_GUIDE.md)](USER_GUIDE.md)**.
>
> 以下は開発者向けの仕様・検証記録（日本語）です。Windows配布ZIPには含めず、英語のユーザガイドだけを同梱します。
> 操作仕様を変更した場合はユーザガイドも同時に更新します。

開発再開時は[AI開発ガイド](documents/ai/README.md)、
データを生成する場合は[Builder SQLite契約](../comparison_viewer_data_builder/documents/ai/sqlite-v4.md)を参照。

## Windowsで起動

Windows 10/11 x64でZIPをすべて展開し、`start-viewer.cmd`をダブルクリックします。
Python 3.12.10、PySide6 6.8.3 / Qt WebEngine、NumPy 2.2.6、Shapely 2.1.2を同梱します。
開発環境は`python -m pip install -r requirements.txt`でViewerの依存を導入します。
NumPy/ShapelyはArea Diffのみで遅延importし、既存のengine/Builder結合処理には要求しません。
Python/pip/WebView2の追加インストール、管理者権限、閲覧時の外部接続は不要な構成です。
システムへのPython登録は行いません。社内のexe/cmd実行ポリシーは適用されます。
今回版のWindows起動は未検証です。前回版の同梱ランタイムは利用者確認済みですが、
今回は未使用のQtモジュールを削減しているため、改めてWindows実機確認が必要です。
エラー時は黒いウィンドウに残るエラーを報告してください。

起動後に **Open Base...** でBuilderが生成したSQLiteを選ぶとBase単独で表示し、
**Open Comp...** で比較対象を追加します（Comp単独は不可）。同じファイルの自己比較や左右を入れ替えた比較も可能です。
本体のZIPにデータは固定収録しません。SQLiteは任意のローカルフォルダに保存できます。
JPEG等の再展開や2つのDBのマージはせず、読み取り専用で必要な情報だけ参照します。
元CSV・OVLX・MSRをViewerへ渡す必要はありません。利用者によるPython導入も不要です。
Replace Base... / Replace Comp... で片側を差し替え、Compの×でCompを閉じます。
選択をキャンセル、または形式検証に失敗した場合は元のファイル・表示状態を保持します。
形式検証では`manifest.csv_sha256`など必須情報の有無も確認し、2ファイルを実際に接続できることを確かめてから
セッションを切り替えます。成功した場合は選択条件と結果を初期化し、全体を一度Applyします。
読込中の旧セッションの応答は新しいセッションには適用しません。
対応形式は`plugin-viewer-sqlite` version 4。旧SQLite v2/v3はBuilderで再生成する。旧Parquetフォルダや固定2データDBは開けない。

### 別配布：Chip番号を合わせた重ね表示テスト

利用者の明示的な指定による、別チップ間での表示動作確認専用のコピーです。
正式な同一点対応とは認めず、本来一致すべきかの判断は利用者が後日行います。
旧`temp/comparison-viewer-chip-remap-test-windows.zip`は重ね表示だけの確認版です。
旧固定データ版は`temp/comparison-viewer-apply-a-windows.zip`です。
現在の分離配布版は **`temp/comparison-viewer-two-files-windows.zip`** です。
英語GUI・Map矢印・固定グラフ等を維持したうえで、承認されたA案の共通条件バー・
Apply・Ctrl＋ドラッグ操作・注目Chip表示・対応画像集合の統一を実装しています。
旧feedback-v2／Chip補正テスト版のZIP・展開フォルダは上書きしません。

開発者は以下で元DBを変更せず、別のDBを生成します（出力先の上書きは禁止）。

```sh
python3 applications/comparison_viewer/prepare_test_overlay.py \
  temp/viewer-datasets-v4/comparison.sqlite \
  temp/viewer-datasets-v4/comparison-TEST-ONLY-chip-remap.sqlite \
  --base temp/viewer-datasets-v4/base.sqlite
```

- PNO＋SEQUENCENO＋MSの共通画像から、一貫した1対1のチップ対応を検証する。
  Comparisonの(3,5)→(6,5)、(4,2)→(5,2)、(6,4)→(8,7)、
  (7,7)→(3,6)、(9,3)→(8,3)をテスト専用コピーに適用する。
- Comparisonの画像索引chip_x/yと計測行XINDEX/YINDEXのみ補正する。
  元番号はTEST_ORIGINAL_XINDEX/YINDEX、変更行にはTEST_ONLY_CHIP_OVERRIDEを保存する。
  XREL/YREL・Layout・Pixel・Center・計測値・画像・輪郭は変更しない。
- 元DBのSHA256、対応表、テスト警告をmetadata.test_chip_overrideへ保存。
  テストDBを開いたときだけBase単独でも常時警告を表示し、画像矢印は10倍で開始する。
  比較時はmetadataで指定した画像を選択し、Base／Comparisonを重ねる。
- 代表データはBase 1,290画像中、対応画像1,288、対応画像なし2。対応条件・位置合わせは通常版と同じ。
  2026-11-12にパターン対応をLayout座標からPIDへ変更し、対応画像1,288枚すべてで20 PIDが対応する
  （変更前は共通Layoutあり918枚で、その一部は別パターン同士の対応だった）。
- 統計はテスト用のChip対応に従う。表示・統計を正式な同一点の工程比較に使用しない。
  元のMSR／CSV／添付スナップショットと通常版DBは変更しない。
- 検証: `test_test_overlay.py`は隔離生成・元DB不変・対応拒否条件、
  `test_overlay_browser.cjs`は実データの両輪郭・ベクトル描画と警告・統計を確認する。

## 操作

画面はワークベンチ構成です。左レールに上からSelection（条件）、Wafer map、Chip map、
中央上段にImage list（Imageの左、同じ高さ、幅はドラッグ・◂で折りたたみ）とImage、下段に統計タブ（間のsplitterで高さ調整）、右に元計測値ドロワー（画面右端・Image上端付近の縦タブ **Raw measurement data** で開閉、既定は非表示）。
CSV出力はヘッダの **Export ▾** にまとめています。合意内容は[decisions.md](../../documents/decisions.md)の
「ワークベンチレイアウト」「GUIの表記ルール」節を参照。

1. SelectionパネルのImage typeでAll／01MS／02MSに絞り込みます。
   候補は選択中のPNOにBase・Comparisonどちらかで存在するMSだけです。PNOを変えて
   そのPNOにないMSが選ばれていた場合はAllに戻ります（戻す操作でも同じ）。
   PNOとは別の画像種別です。PNOは混ぜず、複数ある場合のみ **PNo** 選択を表示。
2. Mapの表示datasetは各Map見出しの **Show Base | Comp**（Base既定）。Wafer mapとChip mapで連動します。
   画像の背景JPEGもこの切替に連動します（Comparisonは表示時だけ読み込み、PID位置合わせのshift分ずらして描画。
   同じChipのComparison画像や共通PIDがなければBaseのまま理由を表示）。輪郭・ベクトル・統計・グラフはBOTHのまま。元計測値ドロワーのBase/ComparisonタブはMapと独立です。
   色はMap右の縦カラーバー（ツールチップに矢印のnm→screen px校正と表示datasetを表示）。
3. ウェーハMapでチップをクリック。Ctrl+クリックで複数選択。
   未選択状態は全計測チップ。これは分析条件の編集であり、まだ再計算しません。
4. Chip MapはFocus Chipの1枚だけを表示します。Focus Chipの切替は閲覧専用で、
   分析対象の複数Chip選択は変えません。画像一覧で別Chipの画像を開くとFocusも追従。
   通常ドラッグはPan、Ctrlを押してドラッグ開始するとチップ内矩形の設定です。
   この矩形は注目Chipだけでなく分析対象の全Chipのチップ内座標へ適用します。
   Fitはパネル見出しに配置し、表示位置だけを戻します。矩形・分析条件は変えません。
5. Chip Mapの画像位置をクリックして閲覧。
   同じ座標の複数MS画像は繰り返しクリックで巡回します。Map表示がBase・Comparisonの
   どちらでも同じく巡回し、選択中の画像位置に選択リングを表示します。
   未対応の画像位置には集計矢印を出さず、別チップの画像で代用しません。
6. Imageの左の画像一覧から閲覧画像を変更。
   列はImage name／Seq／Chip／Image type。仮想スクロールで全件を1リスト表示し（ページなし）、上下キーで前後の画像へ（Imageにフォーカスがあるときも同じ）。
   一覧の幅は右端の境界線をドラッグ（ダブルクリックで初期値）、◂で縦帯に折りたたみ。幅と折りたたみ状態は保存。
   検索欄は画像名の部分一致（大小無視）、Chip（`8,7`や`(8, 7)`）、Seq番号の完全一致で一覧だけを絞り、統計は変えません。
   一覧は編集途中の条件を満たす対応Base画像のみ。未対応画像は集計から除外し、
   適用済み結果には未対応画像数をBase／Comparison別で表示します。
   画像の閲覧変更だけでは集計対象をその1枚へ限定しません。
7. 画像は常にImage＋Overlayです。Pan／ROIやPattern distributionのモード切替はありません。
   通常ドラッグはPan、Ctrlを押してドラッグ開始すると画像ROIを設定、
   ホイールは拡大縮小です。共有ROIは両データの元Pixel座標へ適用します。
   画像のFitもChip Mapと同じ見出し右上にあり、画像をパネルに収めてPan/Zoomを戻します。
   ROI・分析条件・統計結果は変更しません。両Fitはツールチップで動作を説明します。
   ドラッグ中にCtrlを離しても開始時に選んだ操作を維持します。
   **Expand** は画像を別ウィンドウで大きく表示します。選択画像・レイヤ・ROI・PID強調は本窓と共有し、
   Pan/Zoomだけ独立。別窓でもCtrl+ドラッグで共有ROIを設定でき、上下キーで前後の画像、Escで閉じます。
8. 起動時は全体を一度自動Apply。以後はSelectionパネルの **Apply selection** で
   両Mapの矢印・数値表・グラフを、同じ条件の1スナップショットとして更新します。
   編集途中の条件はPending changes、図表は前回のAPPLIED結果と明示。
   画像閲覧・選択枠はすぐ反映しますが、旧結果を新条件の値とは扱いません。
   Apply処理中の追加編集は未適用のまま残り、エラー時は旧結果を保って明示通知します。
9. 統計一覧は両データを常設。グラフはHistogram／Box plot／Scatter。
   グラフ領域のMetricでOffset X／Offset Y／|Offset|を選択。散布図はOffset X×Offset Y固定なので選択を無効化。
10. 右ドロワーは選択画像の元計測値で、集計スナップショットとは別です。
   表示列を選択（既定はPID先頭、`#`連番は任意列）、見出しクリックでソート、スクロールできます。
   CSV出力は **Export ▾ → Raw measurement data - Base/Comparison (CSV)**（ドロワーで選んだdatasetと列。メニュー名と`dataset`列でdatasetを明示し、`#`は出力しない）。
11. Image見出しの **Show contours** で元Imageを左、Contourを右に併置する。境界dragで幅比率を変更・保存できる。
    This image、Same PID across selection、All selected imagesを切替。Hide contoursは右だけを閉じる。
    This imageは現在の1枚だけを対象に、ROI内（未指定なら全体）の各パターンを各design図形のbbox中心で揃える。
    clickで1 PID、Ctrl+clickで追加/解除し、Clicked PIDsを選択対象にする。元Image上に黄色で対象を示す。
    Ctrl+dragで従来のROIを設定しROI選択へ戻る。PID選択は統計フィルタを変えない。
    同一PIDは元Imageのクリックでも指定でき、選択PIDの輪郭範囲へFitする。こちらの座標は画像中心のまま。
    Same PIDは線表示を維持してopacityを0.30から0.85へ上げ、Base / Comparison / Bothを再取得なしで切り替える。
    Base=赤、Comparison=シアンは他グラフと統一して維持し、Bothで重なる線だけ紫に強調して凡例を出す。
    各roleのrasterのalpha積で共通strokeを特定する。自然な混色、内部の塗り領域・重なり率を示すものではない。
    他scope・元Image・密度表示の配色は変更しない。
    ホイール・＋/−で拡大縮小、dragでpan、Contour自身のFitで初期表示へ戻す。元ImageのFit/Expand/layersは独立して使える。
    全画像は各画像のdesign layout中心（Builderの`design_x/design_y`）を原点に揃え、個別PIDシフトは加えない。
    画像ごとのmatching shiftでdesign位置は最大数nm動くため、画像中心で揃えると現在画像のDesignと密度がずれる。
    FocusはBase/Comparisonを主表示に切替え、反対側の密度も薄く表示する。
    Base vs ComparisonはE2方式で色相が優勢側（赤=Base、シアン=Comparison）、明るさが合計密度。
    密度値は対応画像ペア数で割り、共通の密度スケールを使う。これはPID単位の移動量ではなく、集団の空間密度比較。
    密度は各セルを通る輪郭長[nm]の画像ペア平均（1024×1024、平滑化なし）。Builderの`density_cells`を加算する。
    点サンプル計数の旧形式はrebuildエラー。
    密度は1024固定（2048はJPEG状のブロックが目立ち、利用者判断で不採用）。輪郭CanvasはHiDPI対応、密度の拡縮は高品質補間。
    密度表示にはX/Y両方のnm目盛を出す。
    SEM / Designは独立ON/OFF、SEM opacityで背景の濃さを調整する。参照は左Imageが実際に表示中のBase/Compの現在1枚で、平均像ではない。
    roleごとのdesign layout中心・pixel校正で配置し（密度と同じ原点）、左ImageのPID平均平行移動は適用しない。参照datasetと画像名をstatusに出す。
    高精細化しても母集団の形状・位置ばらつきは消さない。細部確認にはzoomかパネル拡大を使う。
    読込中はspinnerと経過秒を表示し、「対象なし」と区別する。失敗時は理由とRetryを表示。
    要求は同時に1件、待機は最新条件だけ。直近4結果と描画Pathを保持し、密度は1条件まで保持する（Base+Comp配列で16 MiB）。

チップ・チップ内矩形・画像ROIはSelectionパネルのChips／Area／ROIの各ピルの×で個別解除できます（条件がAllなら×は無効）。
Reset selectionはPNOとImage typeを維持して空間条件を解除。
Undoは直前の条件を復元します。
画像一覧、元計測値、統計一覧は数値をテーブルで扱います。
各表は横/縦スクロール。Imageと統計タブの境界をドラッグして高さを調整（ダブルクリックで既定値、キーボードの上下でも可）。
統計タブ右端の▁／▢で最小化／最大化、各Map見出しの▼で折りたたみ。これらは次回起動時も保持します
（QtWebEngineの名前付きprofile`ComparisonViewer`のlocalStorage）。
小さい画面では横スクロールが必要です。最小横幅1270px相当。

## 比較と座標

- **チップ番号の一致が必須**です。
  同じPNO、SEQUENCENO、MS種別、ChipX/Yで画像を対応させ、
  重複候補はエラー。画像内のパターン対応と輪郭位置合わせには同じPIDを使います
  （ADI/ACIでLayout座標系が異なり得るため。PIDが空・画像内重複のパターンは対応させません）。
- 同一チップは対応の必要条件です。SequenceNoだけで別チップを結び付けません。
- 対応画像がある場合、両Contourと両ベクトルを同じBase背景に重ねます。
  Base/ComparisonのContourとベクトルは個別にON/OFF可能です。
  Mapの表示dataset切替とは独立しており、切替でベクトルを片方にしません。
  Contour／Designはズームによらず2.5 screen px、画像ベクトルの既定倍率は10倍です。
- 共通PIDがなければ比較側の重ね合わせを表示せず、理由を表示します。
- `PID`チップで各パターンのPIDをBase位置に表示します（PPEタブで選択中のPIDは黄色）。
- 元Image上のBase/Comparison位置合わせは対応Pixelの平均平行移動のみ。実中心を揃えません。
  既知の最大約1.20pixelの残差を精密補正したものではありません。
- 代表BaseとComparisonは**共通する計測済みチップがありません**。
  そのため通常DBの対応画像一覧は空で、両データのMap集計・統計・グラフも有効N=0です。
  これは対応画像集合に統一したA案の意図した挙動です。
  不足分を別チップから補いません。0件の統計値は欠測表示「—」であり値0ではありません。
- 実データの重ね表示は別配布のChip補正テストDBでも確認。
  これは別チップ間の人工的な対応であり、元データの正式な同一点対応とは区別します。
- 画像ROI判定は各画像の元のPixelX/Yに同じ矩形を適用します。
  比較側を平行移動した後の表示座標では判定しません。
- Pattern distribution表示は利用者の指示により撤去しました。
- Contour overlayの輪郭対応はBuilderがSQLite v4の`patterns.contour_index`へ保存する。
  Viewerは`contour_data`のfloat64輪郭と事前計算gridサンプルを直接読む。
  全画像scopeでは輪郭のPoint-in-Polygon再対応や全geometryのJSON展開を行わない。
  This imageだけは当該画像ペアのdesignを読み、各PixelX/Yを含む最小のdesign図形で中心を求める。
  全画像を囲むframeは除外し、無対応・曖昧対応をエラーにする。丸めPixelや計測中心への代用はしない。
  同一PIDの座標転送はzlib圧縮float64のbase64とpolygon長配列。ブラウザで一度だけ復元してPath2Dを作る。
  SQLite v2/v3との後方互換性はないため、Builderで再生成する。無対応はNULL、曖昧対応はBuilderエラー。
- 今回の512pxデータではLayout→画像変換はX正・Y反転。
  SVGはpolygonのみ、Designはdata-design-type=0のみ。この試作を汎用SVGレンダラと扱わないでください。

## 統計と計算タイミング

- ΔX/Y = CenterX/Y − LayoutCoordX/Y [nm]。
  大きさは各行のhypot(ΔX,ΔY)。表示ベクトル倍率は集計値に影響しません。
- 中心値あり、Normal、ContourSuccess=Yesの行だけを計算。
  中心欠測と品質除外の件数を区別。0中心値を欠測にしません。
- 成分・大きさ: 平均、中央値、母標準偏差（ddof=0）、最小、最大。
- 方向指標は画面・計測値の追加列・統計・グラフ・CSVの統計指標から削除。
  元DBのdirection列は互換性と元値保全のため残しますが使用しません。
- ウェーハMapはチップ内全有効パターン、チップ内Mapは各画像内有効パターンについて
  median(dx),median(dy)の矢印を描画。色は各パターンの大きさの中央値です。
  成分の中央値のhypotとは別の量。両Mapの色範囲は起動時全体の共通範囲で固定。
  Map・数値統計・グラフはすべて同じ対応画像集合を使います。
  PNO＋SEQ＋MS＋Chipが一致する両画像がチップ・チップ内矩形をともに満たす場合だけ採用。
  未対応画像数と、相手画像が範囲外のため採用できない件数を別に表示します。
  ROI・品質判定は各画像内パターンへ独立適用するため、画像数が同じでも有効Nは異なります。
  同じ条件を適用しても、測定欠損をゼロで埋めません。
- MSRの存在セルを使用し、計算前も実測チップを青色表示。未計測は斜線です。
  矢印凡例は1nmが何画面pxかを数値表示。ウェーハ側はセルサイズに応じて校正、
  チップ内側は32screen px/nm。画像の10倍とは異なり、ウェーハ距離の実縮尺ではありません。
  ホバーでOffset X／Offset Y／median |Offset|／有効Nを確認できます。
- ヒストグラムは両データ共通の30等幅ビン・件数。
  箱ひげは25/50/75%分位・1.5 IQRひげ・外側の点。
  散布図はOffset X対Offset Yの全有効点、間引きなし。
  横軸・ビン境界・散布図の両軸は、現在のPNOの全画像種別・全チップ・ROIなしの
  データで決定し、MS／空間絞込後も固定。ヒストグラムの件数縦軸だけはApply済みの選択に合わせる
  （ROIなどで件数が少ないと棒が潰れるため。2026-11-12変更）。ゼロ軸・数値目盛・補助線・単位を表示します。
  既存Basic Viewer Lightの3σドット制限を新Viewerへ無断で転用していません。
- Center offset (Center − Layout) タブは、同一取得点の画像内パターン全体を独立集計します。
- PPEタブ（2026-11-12追加）はパターン対応の差分統計です。
  PPE X/Y = Offset X/Y_Base − Offset X/Y_Comparison、|PPE| = hypot。対応画像ペア内の同じPIDで、両方validの組だけを集計。
  表はAllとPIDごとのN、Mean、SD（母SD）をX・Y・|PPE|で表示（3σは2026-11-12に削除）。行クリックでPIDを選択。
  ヒストグラムはPIDごとの色の積み上げ（PID選択時はそのPIDのみ）、ビン幅は自動または入力（400ビン超は拒否）。
  横軸は全データ（現在のPNO、絞込なし）で固定し、件数縦軸は表示中の棒（Apply済み選択、PID選択時はそのPID）に合わせる。
  箱ひげはPIDごとに1本。値軸は全データで固定。
  散布図はPIDごとに1点（N>0のPID）。Statisticで Mean（横=Mean X、縦=Mean Y、0中心）／SD（横=SD X、縦=SD Y、0始まり）を切替。
  縦横同スケール、軸はApply済み選択に合わせる（点が少ないため）。点クリックでPID選択（再クリック・空白で解除）。
  散布図ではComponent・Bin widthを使わないため非表示。
  画像ROIはBase側パターン位置で判定。相手のいないPID数を注記に表示。
  `Export statistics CSV`は表、`Export PPE values CSV`はパターン組ごとの値（無効な組は値が空）を保存し、Pending時は不可。
- 起動時は全体を一度だけ計算。条件変更時に自動集計せずPending changesと表示。
  旧結果とその条件・時刻は保持。CSV出力は最新条件へ更新するまで不可。
  指標/グラフ切替は計算済み配列を再利用し、DB再集計をしません。
- 統計の計算はワーカースレッド。画像読込の古い結果が新選択を上書きしないようにします。
  同時ワーカー2本の上限、要求タイムアウトは120秒。極端な大容量・キャンセルは今後の課題。

## 展開時間の改善

以前は画像・JS・サムネイル等で数千ファイルを配布していました。
今回は画像・輪郭・計測値を1個のSQLiteに収録し、サムネイルを廃止しました。
一時ディレクトリへ画像を再展開せず、選択画像だけDBから直接読みます。

Windows Qtも全モジュールではなく使用するバインディング、
PE依存DLL、必要プラグイン、WebEngine資源、日本語/英語ロケールに削減。
ヘッダ、型スタブ、開発ツール、未使用Qtモジュールは配布しません。
動的ロードするQtプラグインも明示収録します。
依存DLLの静的検査は実行検証を代替しません。
ファイル数削減は展開/ウイルス検査の負荷低減を狙ったもので、Windows展開速度は未測定。
Defender等を無効にする方法は推奨しません。展開先は可能ならローカルSSDを使用してください。
現在はViewer本体（同梱ランタイム込み）とデータを別配布にしています。
本体ZIPは最初とViewer更新時のみ展開し、以後は各Builder結果のSQLiteだけ取得します。

## 開発・検証

ソース正本: 本リポジトリの`applications/comparison_viewer/`。Plugin Registryの複製は`scripts/deploy_to_registry.py`で更新し、直接編集しません。
Registryは現在Git管理対象外のため、Builderと共に別途保存・配布してください。
ローカルGUI製品としてRegistryに置いており、Plugin RunnerからGUIを起動するApplicationではありません。
Plugin Workflowには接続していません。

- 元CSV/OVLX/MSR→SQLiteは`applications/comparison_viewer_data_builder/main.py`だけが担当します。
  Viewer配下の重複変換処理`prepare_demo.py`は削除しました。
- `engine.py`: 同一チップ判定・画像部分読込・統計。標準ライブラリのみ。
- `main.py`: Qt WebChannel、ワーカースレッド、2ファイル選択、CSV保存ダイアログ。
  開発時は`--base PATH --comparison PATH`でも起動できます。
- `test_two_files.py`: ローカルID衝突、読取専用、形式拒否（manifest必須キー欠落を含む）、自己比較、左右交換、TEST警告。
- `test_export.py`: CSV出力で見出し・値の式起動文字（`=`、`+`、`-`、`@`、タブ、CR）を無害化し、`-45.190`のような符号付き数値文字列は数値のまま残すこと。
- `test_selection_browser.cjs`: 合成データでPNO変更時のImage type候補、Base/Comparison両方のMS巡回と選択リング。
  `NODE_PATH`にPlaywright、環境変数`VIEWER_PYTHON`・`CHROME`で実行環境を指定。
- `test_open_qt.py BASE COMPARISON SYNTHETIC`: 実Qtで空起動、Base先行の制約、Base単独、Comp追加・差し替え・閉じる、
  取消、無効ファイル時の状態保持、PPE値CSVのQt経由保存、再読込、旧セッション応答の隔離を検証。
- `test_single_browser.cjs BASE OUTPUT_DIR [APP_DIR] [synthetic]`: Base単独でComp系操作・PPEタブの無効化、Base行のみの統計、
  By PID表とCSV、Contour overlayを確認しスクリーンショットを保存。合成座標データをBaseに使う場合は
  最後に`synthetic`を指定し、警告とCSVのフラグも検証する。
- `test_engine.py`: 正常な両ベクトル、異なるチップ、ROI、品質/欠測、方向削除、MS絞込、
  共通母集団、未対応除外、片側範囲外、各データの有効N差、重複エラー、
  Layout座標が異なってもPIDで重なること、PID別PPE・N=0行、ROIとPPE値一覧、PIDの空・重複。
- `test_ppe_browser.cjs BASE SYNTHETIC OUTPUT_DIR`: 実データでPPEタブ、PID色、PID選択、ビン幅、箱ひげ、
  散布図（Mean/SD切替、点クリックのPID選択）、PIDラベル、値一覧とNの一致、Pending時のExport禁止を確認しスクリーンショットを保存。
- `test_qt.py BASE --comparison COMPARISON`: 実際のQt WebEngine/Channel/SQLite統合テスト。
- `test_browser.cjs DATABASE`: 実ポインタによるMap選択、矩形、ROI、解除/戻す、タブ、統計。
- `test_source_data.py BASE --comparison COMPARISON`（`--source`または環境変数`PLUGIN_ROOT`で元入力を指定）: 全51,600行・全2,580JPEGと全SVG/条件を元入力と照合。
- `test_workflow_browser.cjs DATABASE OUTPUT_DIR [APP_DIR]`: A案の実データ・実ポインタ検証。
  初期統計待機中の着色、Ctrl ROI／通常Pan／取消、Focusの閲覧独立性、共通データ選択、
  条件編集と適用結果の分離、Apply中の追加編集、失敗・再試行、母集団一致、
  英語・改行・2.5px輪郭・MS絞込・固定軸・値選択・ゼロ軸を確認。
  `test_browser.cjs`／`test_overlay_browser.cjs`は同テストへの互換入口です。
  2ファイル試験では`DATABASE`にBaseを渡し、環境変数`VIEWER_COMPARISON`でComparisonを指定します。
- `package_windows.py --downloads DIR --output DIR --guide-python PYTHON`: 公式Windowsパッケージと依存DLLから配布。
  `PYTHON`（開発機のPySide6入りPython）で`build_user_guide_pdf.py`を実行し、USER_GUIDE.mdから作った
  USER_GUIDE.pdfをZIP最上位に入れる（mdは同梱しない）。
  objdumpが開発環境に必要。CSV出力のWindowsダイアログは未検証。

MSRの配列・ピッチ・原点等の読み取りはBuilderの責務です。既存msr_parserの設定に対応する項目を検証します。
chip_existは行番号をY、文字位置をXとして0を不存在、非0を存在セルとして扱い、
SHOT_ARRAYと寸法一致を検証。存在コード1/2の測定意味を推測しません。
今回のIN_SHOT_CHIP_ARRAY=1×1のみ対応。他の配列は明示エラー。
Waferの原点を持つ物理座標図ではなく、XINDEX/YINDEXに対応するチップ配置図です。

## 今回の検証結果

- 単体テストはengine 10件＋旧Chip補正4件＋2ファイル6件。
  独立した2つのSQLiteについて全51,600計測行・全2,580JPEG・全SVG/条件の元入力照合で合格。
  A版も元DBと補正DBは変更せず、データSHA256を確認して配布します。
- Base有効22,246行、Comparison有効20,684行をDBに保持。
  元DBは共通チップがないので両データを分析対象から除外します。
  補正テストDBの全体は1,288組で、有効Base=22,214、Comparison=20,652です。
  元DB保持数・旧版の独立Map集計数と、今回の対応画像だけの統計数を区別してください。
- 実Qt WebEngine/WebChannel/ワーカー/SQLiteの統合と、
  Chromeでのポインタ操作、空の選択、条件変更、ROI、解除/戻すを確認。
- 同一チップの合成データでは双方のベクトルを生成することを単体検証し、
  描画用の独立した合成fixtureで赤・青両方を描くこと、
  Mapの表示datasetを変えても画像重ね描画が変わらないことも検証します。
- 旧版の統計のみのLinux参考実測は全1,290画像約0.93秒、1チップ258画像約0.29秒。
  v2は独立Mapの集計も追加しているため、同じ性能値をv2の値とは扱いません。
- ZIPのCRC、全ファイルのSHA-256、Windows用DLLの依存収集を検査。
  **Windows起動・ネイティブ保存ダイアログ・展開所要時間は未検証**。

## 同梱ライセンス

公式Python embeddable 3.12.10 / PySide6 6.8.3のWindows x64配布物を使用。
Pythonライセンス: `runtime/LICENSE.txt`。
Qt/PySide/Shibokenの元メタデータ・ライセンス関連ファイルは
`runtime/Lib/site-packages/*.dist-info/`等に保持。
NumPy/ShapelyのWindows wheelも同じ場所へ展開し、付属のライセンス、
OpenBLASおよびGEOSのDLLと第三者ライセンスを保持します。
QtのLGPL/GPL/商用等および第三者ライセンス、対応ソース提供義務は
製品化・社外配布前に別途確認してください。

- https://doc.qt.io/qtforpython-6/licenses.html
- https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/
- https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip

配布元ハッシュ・Windows実行未検証の表示は`distribution.json`、
各ファイルのSHA-256は`SHA256SUMS.txt`にあります。
