# documents: AI・開発者向け索引

このリポジトリで作業を始めるときは、次の順に読む。全コードの再解析を省略し、設計方針から外れた変更を防ぐことが目的。

1. [構成と契約](architecture.md): 2アプリの役割、データの流れ、変更してはいけない約束。
2. [開発手順](development.md): ブランチ運用、環境、テスト、コミット、リリース。
3. [配備手順](deployment.md): Plugin Registryへの配備、確認、切り戻し。
   - [リリース仕様](release.md): Suite版・互換性、固定Viewer、配布構成、公開手順。
4. [前提と判断の記録](decisions.md): 管理方法、レビューで決めた仕様、未決・未検証のこと。
5. 各アプリの詳細:
   - Viewer: [AI開発ガイド](../applications/comparison_viewer/documents/ai/README.md)、[ユーザガイド（英語）](../applications/comparison_viewer/USER_GUIDE.md)、[仕様・検証記録README](../applications/comparison_viewer/README.md)
   - Builder: [AI開発ガイド](../applications/comparison_viewer_data_builder/documents/ai/README.md)、[SQLite v4契約](../applications/comparison_viewer_data_builder/documents/ai/sqlite-v4.md)

Plugin本体（Agent、Runner、Workflow、他Application）の共通文書は、Plugin本体リポジトリの`documents/ai/`にある。
本リポジトリからは相対リンクできないため、パス名で参照する。

## 正本の所在

| 対象 | 正本 | 備考 |
| --- | --- | --- |
| Viewer・Builderのソース、設定、wheel、アプリ文書 | 本リポジトリ`applications/` | Registryの複製は直接編集しない |
| SQLite v4形式 | [sqlite-v4.md](../applications/comparison_viewer_data_builder/documents/ai/sqlite-v4.md) | Builderが生成者、Viewerが利用者 |
| リリースの版・配布構成・公開手順 | [release.md](release.md) | 配備操作はdeployment.md、判断理由はdecisions.md |
| Plugin実行契約（trigger、Registry mount） | Plugin本体`documents/ai/application-contract.md` | 本リポジトリでは変更しない |
| 生成データ（SQLite）、配布ZIP、Windows runtime | Git管理外 | 再生成可能な成果物として扱う |

## 文書の更新ルール

- 仕様や契約を変えたら、同じコミットで該当文書も直す。
- 確認日・測定値・未検証事項は、事実と推測を分けて記録する。
- 過去の経緯は消さず、現行仕様と区別できる見出しを付ける。
