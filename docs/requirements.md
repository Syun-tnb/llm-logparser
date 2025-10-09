# LLM Log Parser — 要件定義（MVP）

## 1. ゴール / スコープ

* 各LLMサービスのエクスポート（JSON/JSONL/NDJSON）を読み込み、**重複のない会話ログ**を**スレッド単位のMarkdown**へ出力する。
* **CLIで完結するMVP**（Parser → Exporter）。  
  Viewerは別途GUIで提供予定だが、**軽量HTMLビューア（検索窓＋一覧＋詳細）をMVP範囲に追加**。
* 将来的な拡張を見据え、**マルチプロバイダ対応**・**多言語対応（i18n/L10n）**・**例外契約（JSONエラー）**・**ランタイム設定の永続化**を設計に含める。
* **Apps SDK統合を見据えた関数/API設計**（入出力をJSON Schema化）を行う。

---

## 2. 入力

* 対応フォーマット：`json` / `jsonl` / `ndjson`（UTF-8, BOMなし推奨）。
* エクスポートの性質：**毎回“全会話”**が出力される（差分ではない）。
* 識別キー：`conversation_id` と `message_id`（※プロバイダ差は後述の正規化で吸収）。
* 巨大ファイル・壊れ行・制御文字・多言語本文（絵文字含む）を想定。

---

## 3. アーキテクチャ（MVP）

* **Core（共通）**
  * ストリーム読取、正規化スキーマ、重複排除、スレッド分割、Markdown整形/出力。
  * **Apps SDK用に純粋関数化（例：`parse_logs()`）を想定**。

* **Providers（薄い層）**
  * 入力差異の吸収（フィールドマッピング、日時/モデル名正規化、添付抽出等）。
  * 追加は**設定ファイル＋最小限のprovider実装**で拡張。

* **Config（外部定義）**
  * JSON/YAMLでマッピング・ルールを保持。**コード改変なしで調整可能**。

* **Viewer（簡易HTML）**
  * 固定テンプレート `index.html` ＋ `menu.html` ＋ `page.html`。
  * シンプル検索窓とページング。
  * SDK統合時にそのままUIウィジェット化できる構成にする。

---

## 4. マルチプロバイダ要件

* 初期想定：ChatGPT（OpenAI）。将来：Claude, Gemini, Perplexity等。
* **正規化スキーマ（Coreに渡す統一形）**
  * `conversation_id: str`
  * `message_id: str`
  * `ts: int`（epoch ms）
  * `author_role: "user"|"assistant"|"system"|"tool"`
  * `author_name: str|null`
  * `model: str|null`（可能な範囲で正規化）
  * `content: str`
  * `attachments: []`（任意）, `extra: {}`（任意）
* 重複キーは `{provider_id}:{conversation_id}`, `{provider_id}:{message_id}` を内部合成し衝突回避。
* プロバイダ設定（YAML/JSON）に**fields/map/フォールバック/欠落ポリシー**等を定義。

---

## 5. 処理フロー

1. **パーサー（Parser）**
   * ストリームで入力読取（巨大ファイル対応）。
   * プロバイダ設定に基づき正規化 → 共通スキーマへ変換。
   * `(provider_id, conversation_id, message_id)` の組で**重複排除**。
   * 日時・文字コードの揺れを吸収（可能な範囲で）。

2. **エクスポーター（Exporter）**
   * スレッド単位にグルーピングし、時系列ソート。
   * **分割出力**：
     * サイズ：例 `20MB`
     * 件数：例 `8,000件`
     * 日付：`none/day/week/month`
       （複合条件OK。閾値は設定で変更可能）
   * Markdownへ整形して出力。

---

## 6. 出力（ファイル/フォーマット）

* 出力ルート：`artifacts/output/{provider_id}/thread-{conversation_id}/…`
* ファイル命名：`thread-{conversation_id}__{chunk_key}.md`
  * `chunk_key` 例：`2025-10-08_part01` / `size20mb_p03` 等（ASCII準拠）
* **Markdown先頭メタ情報（固定文言はi18n対応）**
  * Thread / provider / messages / range / model(s) など
* 1メッセージ=1セクション（日時・役割・名前・本文）。
* コードブロック/URL/画像リンクは原文維持。
* 付帯 `meta.json`（任意）：件数・期間・分割情報など（キーは英語固定）。

---

## 7. CLI 仕様（要点のみ）

* サブコマンド：`parse` / `config <ops>`
* 主オプション例：
  * `--provider <id>` / `--config <path>`
  * `--input <path...>` / `--outdir <dir>`
  * `--split-by {{size,count,date,none}}`
  * `--split-size-mb <int>` / `--max-msgs-per-file <int>` / `--split-by-date {{none,day,week,month}}`
  * `--cache <path>`（既処理ID永続化）
  * `--locale <lang-REGION>`（既定 `en-US`）
  * `--timezone <IANA>`（既定 `Asia/Tokyo`）
  * `--dry-run`（出力なしで統計表示）
  * `--offline`（既定：ON。ネットワーク通信を禁止。将来 `--enable-network` を明示した場合のみ外部通信を許可）
* すべてのヘルプ/出力メッセージは**多言語化**。

---

## 8. 重複管理 / キャッシュ

* 既処理の `(provider_id, conversation_id, message_id)` を**キャッシュ**（JSON/将来SQLite）。
* 次回以降は差分のみ追加処理。
* キャッシュ破損時はロールバック or 再生成（警告発報）。

---

## 9. 多言語対応（i18n/L10n）

* 対象：CLIヘルプ、進行ログ、警告/エラー、統計出力、Markdown固定文言。
* ロケール：`--locale`優先 → 環境 → 既定 `en-US`。
* メッセージは**キー＋プレースホルダ**で管理（未訳は英語フォールバック＆WARN）。
* 日時/数値/単位/曜日/月名はロケール整形。
* ファイル名は**ASCII固定**（互換性優先）。

---

## 10. 例外処理・エラー契約（Viewer一元ハンドリング前提）

* パーサー/CLIは**例外をスロー**し、**JSONペイロード**でエラーを通知。Viewerが解釈・表示・再試行等を行う。
* **必須項目（例）**：
  * `version, severity(FATAL|ERROR|WARN), code(LPxxxx), message_key, params, exit_code`
  * `provider_id, correlation_id, context, retryable, partial(processed/skipped等), timestamp`
* **エラーコード範囲**
  * LP1xxx: 起動/環境（引数・I/O権限）
  * LP2xxx: 入力フォーマット（JSON破損・エンコーディング）
  * LP3xxx: プロバイダ設定/コンフィグ
  * LP4xxx: 正規化/スキーマ
  * LP5xxx: 出力/分割
  * LP6xxx: i18n/ロケール
  * LP9xxx: 予期せぬ内部
* 終了コード規約：`0=成功（WARN/ERRORは集計返却）`, `1/2/3/4/5/9=各FATAL`。
* **部分成功**：ERROR/WARNは継続、最後に集約JSONで件数/代表エラー等を返す。

---

## 11. ランタイム設定の永続化（引数⇄コンフィグ）

* 目的：起動引数の多さを回避。**初回は引数→実行後に自動保存**、2回目以降は**コンフィグ既定**で動く。
* **優先順位**：CLI引数 ＞ 環境変数 ＞ ユーザー設定ファイル（プロファイル） ＞ 既定値。
* 保存：成功終了時に**オートセーブ**（`--no-save-config`で無効可）。
* 形式/配置：YAML推奨。
  * macOS/Linux：`~/.config/llm-logparser/config.yaml`
  * Windows：`%APPDATA%\llm-logparser\config.yaml`
  * ローカル：`./.llp/config.yaml`（任意）
* プロファイル：`--profile <name>`で切替（`schema_version`, `active_profile`, `profiles`）。
* 競合解決：最終決定値を採用し**上書き保存**（バックアップ・ロック・アトミック更新）。
* GUIはこのコンフィグを**直接参照/編集**できる（双方向同期）。

---

## 12. セキュリティ / プライバシー

* 入出力はローカル前提。**外部送信なし（デフォルト）**。
* 起動直後にソケット層を無効化する仕組みを導入し、**ネットワーク通信を物理的に封じる**。
* `--enable-network` オプション指定時のみ外部連携を許可し、その際は**送信内容をログに記録**。
* 依存パッケージはバージョン固定＋ハッシュ付きで管理し、CIで監査する。
* リリース成果物にはSHA256＋署名を添付し、再現可能ビルドを保証する。
* PII/機密混在の可能性あり：**テストデータは合成のみ**を推奨。
* 秘密情報は保存しない設計（混入時はマスク表示）。
* ディレクトリトラバーサル対策、パス正規化、書込権限チェック。

---

## 13. パフォーマンス / 上限制約

* ストリーム処理必須（GB級でも動作）。
* メモリはウィンドウ保持（全件保持しない）。
* 既定分割：**サイズ=20MB / 件数=8,000**（実運用に応じて調整）。
* I/O並列は任意（MVPではシングルで可）。

---

## 14. テスト方針

* ゴールデンテスト（プロバイダ設定YAML×サンプル→期待Markdown）。
* 壊れJSON/欠落/巨大行/多言語/時差のフェイルセーフ。
* i18n：各ロケールのスナップショット整合、未訳キー検出（lint）。
* コンフィグ：ロード/マージ/スキーマ不一致/ロック/バックアップ復旧。
* **ネットワーク禁止テスト**（ソケット封鎖を有効にしてユニットテスト）。

---

## 15. バージョニング / 互換

* **SemVer**準拠。`0.x`は互換破壊の可能性あり。
* 出力Markdown/エラーペイロードに `schema_version` を付与。
* 破壊的変更はCHANGELOGで宣言、Deprecationポリシーに従う。

---

## 16. GitHub公開チェック（突っ込まれ防止）

* `LICENSE`（MIT or Apache-2.0）、`SECURITY.md`、`CODE_OF_CONDUCT.md`、`CONTRIBUTING.md`、`CHANGELOG.md`。
* `docs/error-codes.md`（コードと例JSON）、`docs/provider-guide.md`、`docs/output-contract.md`。
* サンプルは**合成データのみ**。依存ライセンスは `THIRD_PARTY_NOTICES.md` へ。
* Python/OSサポート、CI Matrix、スタイル/型方針の明記。
* READMEには必ず以下を記載：
  * 「完全オフライン動作」「Apps SDK連携予定」
  * 使用例（CLI＋HTMLビューアのスクショ）
  * 将来の拡張余地（マルチプロバイダ／SDK統合）
* CIに以下を追加：
  * ネットワーク禁止テスト
  * 依存監査（pip-audit / SBOM）
  * リリース時の自動署名＆ハッシュ生成

---

## 17. 非対象（MVP範囲外）

* 高度マージ（複数エクスポートの時間軸統合）。
* **フル機能GUI**（ドラッグ&ドロップ編集や複雑フィルタビルダー）。
* 異プロバイダ会話の同一性推定・統合。
* 自動マスキング（将来オプトイン機能として検討）。
* **Apps SDK公開アプリ化そのもの**（設計の芽は残すがMVP外）。

---

## 18. 将来拡張メモ

* 全文検索インデックス、要約/タグ自動付与（API連携）。
* HTML/TXTなど出力追加、埋め込みメタの強化。
* `--fail-fast`、再試行ヒント、ヘルスチェック（`--self-test`）。
* Apps SDK対応ブランチを切り、JSON Schemaを基にしたツール化。
* ローカル専用→外部連携切替のUX検討（例：レポート出力に広告リンク追加）。
* HTMLビューアをSDK UI部品へリプレース。
* GUIはElectron/Tauriベースで後続開発。

---

### 一文サマリ

> **「フルエクスポートでも、差分をきれいにMarkdownへ。MVPは完全ローカル＋簡易HTMLビュー、Apps SDK対応の芽を残す。」**
