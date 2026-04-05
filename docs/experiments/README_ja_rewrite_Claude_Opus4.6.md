# llm-logparser

[![PyPI version](https://img.shields.io/pypi/v/llm-logparser)](https://pypi.org/project/llm-logparser/)[![Python versions](https://img.shields.io/pypi/pyversions/llm-logparser)](https://pypi.org/project/llm-logparser/)[![License](https://img.shields.io/github/license/Syun-tnb/llm-logparser)](LICENSE)[![GitHub Sponsors](https://img.shields.io/github/sponsors/Syun-tnb)](https://github.com/sponsors/Syun-tnb)

**ChatGPT や Claude のエクスポートファイルを、1コマンドで "読める・探せる・分析できる" データに変換する CLI。**

すべてローカル処理。クラウド送信ゼロ。テレメトリなし。

---

## 目次

- [こんなことができます](#こんなことができます)
- [できあがるもの](#できあがるもの)
- [すぐに試す](#すぐに試す)
- [処理の流れ](#処理の流れ)
- [よくある使い方](#よくある使い方)
- [分析コマンド一覧](#分析コマンド一覧)
- [発展的な使い方](#発展的な使い方)
- [CLI リファレンス](#cli-リファレンス)
- [設定ファイル](#設定ファイル)
- [その他](#その他)

---

## こんなことができます

LLM サービスからダウンロードしたチャット履歴を渡すと——

1. **構造化** — 会話をスレッドごとに整理し、`parsed.jsonl` として保存
2. **Markdown 化** — そのまま読める Markdown ファイルを生成
3. **分析** — メッセージ数・トークン数・安全性指標・利用傾向をファイルに書き出し

> 対応サービス: OpenAI ChatGPT / Anthropic Claude / xAI Grok / Mistral Le Chat / Google Gemini（My Activity）

---

## できあがるもの

コマンド実行後、次のようなディレクトリが作られます。

```text
artifacts/openai/
  manifest.json                        ← 全会話の一覧
  thread-abc123/
    parsed.jsonl                       ← 構造化された会話データ（正規形式）
    thread_stats.json                  ← メッセージ数・タイムスタンプ等
    message_windows.jsonl              ← メッセージのグループ情報
    thread-abc123__gpt-4o.md           ← 👈 Markdown でそのまま読める
```

生成された `.md` ファイルを開くと、こんな感じの会話が読めます:

```markdown
---
thread: "abc123"
provider: "openai"
messages: 42
models: ["gpt-4o"]
range: "2025-10-01T01:00:00+00:00 〜 2025-10-18T10:15:00+00:00"
---

## [User] 2025-10-18 10:00
おはようございます！

## [Assistant] 2025-10-18 10:01
おはようございます。今日はどんなことをお手伝いしましょうか？
```

GFM 互換で、コードブロック・リンク・テーブル・引用もそのまま保持されます。

---

## すぐに試す

### インストール

```bash
pip install llm-logparser
```

> `uv pip install llm-logparser` でもOK。`llp` は `llm-logparser` の短縮エイリアスです。

### 変換 → Markdown を読む

```bash
llm-logparser chain \
  --provider openai \
  --input path/to/conversations.json \
  --outdir artifacts \
  --timezone Asia/Tokyo
```

これだけで、会話の整形と Markdown 書き出しが完了します。

`artifacts/openai/` 以下の `.md` を開いてみてください。

---

## 処理の流れ

`chain` は内部で 2 つの処理を通しで実行します。

```text
生エクスポート
  → parse（構造化）
    → parsed.jsonl    ← すべての基盤（正規データ）
      → export        → Markdown 文字起こし
      → analyze stats → 集計サマリ
      → analyze tokens → token_stats.json
      → analyze metrics → metrics.json
      → analyze datasheet → レポート（Markdown / JSON）
```

**ポイント:**
- `parsed.jsonl` がすべての起点。ここから各コマンドが派生ファイルを生成します
- 派生ファイルはいつでも再生成可能。削除しても問題ありません
- 各ステップは独立しており、好きなところで止められます

---

## よくある使い方

### 会話を読むだけ

```bash
llm-logparser chain --provider openai --input <export> --outdir artifacts
```

→ `.md` ファイルを開いて読む

### ざっくり全体像を把握

```bash
llm-logparser analyze stats --input artifacts/openai
```

出力例:
```text
threads:    12
messages:   847
  user:     421
  assistant: 426
characters: 312,504
timespan:   2025-01-15 — 2025-03-20
```

`--json` で構造化出力、`--per-thread` で会話ごとの内訳も出せます。

### トークン数を知りたい

```bash
llm-logparser analyze tokens --input artifacts/openai
```

→ 各会話の横に `token_stats.json` が生成され、メッセージごとのトークン数がわかります。コスト計算やコンテキスト長の確認に。

### 安全性・対話パターンを調べたい

```bash
llm-logparser analyze metrics --input artifacts/openai
```

→ `metrics.json` が生成され、拒否応答の回数・ユーザーの修正パターン・返答の長さ比率などが確認できます。

> ⚠️ `analyze metrics` を実行するには、先に `analyze tokens` を済ませてください。

### 利用時間帯を可視化

```bash
llm-logparser analyze timeline --input artifacts/openai --bucket day
```

出力例:
```text
2025-01-15:  48 messages
2025-01-16:  12 messages
2025-01-17:   0 messages
2025-01-18:  93 messages
```

---

## 分析コマンド一覧

| コマンド | 何がわかるか | 出力先 |
|---------|------------|--------|
| `analyze stats` | 会話数・メッセージ数・期間 | ターミナル / JSON |
| `analyze tokens` | メッセージごとのトークン数 | `token_stats.json` |
| `analyze metrics` | 安全性・対話パターン・語彙多様性 | `metrics.json` |
| `analyze datasheet` | データセット全体の研究用サマリ | Markdown / JSON |
| `analyze timeline` | 時間帯別のメッセージ量 | ターミナル / JSON |
| `analyze sqlite-build` | SQL で横断検索するための DB | `analysis.db` |

### 依存関係

```text
parsed.jsonl
  ├── analyze tokens → token_stats.json
  │     └── analyze metrics → metrics.json
  ├── analyze stats（単独で動作）
  ├── analyze timeline（単独で動作）
  └── analyze datasheet（単独で動作、sidecar があれば活用）
```

> `analyze sqlite-build` はオプションです。大量データを SQL で検索したいときにだけ使います。いつでも再構築できる補助的なインデックスです。

---

## 発展的な使い方

### 各分析コマンドの詳細

---

#### `analyze stats` — ターミナル要約

**答えてくれること:**
- 全体の会話数・メッセージ数は？
- 一番長い会話はどれ？
- ユーザーとアシスタントのメッセージ比率は？

```bash
llm-logparser analyze stats --input artifacts/openai
```

`--json` で構造化出力。`--per-thread` で会話ごと。`--top N` で上位 N 件。

---

#### `analyze tokens` — トークン計測

**答えてくれること:**
- この会話は合計何トークン？
- コストの高いメッセージはどれ？

```json
{
  "summary": { "total_tokens": 4821, "total_messages": 42 },
  "by_role": { "user": { "tokens": 1200 }, "assistant": { "tokens": 3621 } },
  "messages": [{ "message_id": "m1", "role": "user", "token_count": 28 }]
}
```

> トークナイザは `tiktoken` を使用。`openai`・`anthropic`・`xai` はサービス既定を自動選択。それ以外は `--encoding` を指定してください。初回のみネットワーク経由でエンコーディングデータを取得し、以後はキャッシュを利用します。

---

#### `analyze metrics` — 対話品質の指標

**答えてくれること:**
- アシスタントが要求を拒否した回数は？
- ユーザーが発言を修正・訂正した回数は？
- 返答の文字数比率は？

```json
{
  "safety": { "refusal_count": 1, "intervention_count": 2 },
  "interaction": { "revision_count": 3, "correction_count": 1 },
  "user_effort": { "rapid_revisions": 2, "response_length_ratio": 3.4 },
  "diversity": { "type_token_ratio": 0.62 }
}
```

> ⚠️ 事前に `analyze tokens` で `token_stats.json` を生成しておく必要があります。

---

#### `analyze datasheet` — 研究用データセット概要

論文付録やデータセットカードに使えるフォーマットで、データセット全体の概要を出力します。

```bash
llm-logparser analyze datasheet --input artifacts/openai
```

Markdown が既定。`--json` で構造化出力。

---

#### `analyze sqlite-build` — SQL 検索インデックス

大量のスレッドを横断して検索したいときに使います。

```bash
llm-logparser analyze sqlite-build \
  --input artifacts \
  --provider openai
```

`analysis.db` が生成されます。`parsed.jsonl` からいつでも再構築可能な補助データです。

---

### セマンティック分析（実験的機能）

> 以下は実験的な機能群です。ローカルで動作し、再構築可能で、正規データには影響しません。

| コマンド | 機能概要 |
|---------|---------|
| `analyze semantic-prototype` | ウィンドウ埋め込み・類似度計算・クラスタリング |
| `analyze semantic-preview` | 保存済みクラスタの閲覧（読み取り専用） |
| `analyze semantic-topics` | トピック構造ファイルの生成 |
| `analyze semantic-topic-explore` | トピック一覧・タイムライン・逆引きの閲覧 |
| `analyze semantic-topic` | Ollama を使った実験的なラベル/要約の生成 |

`semantic-prototype` は既定で `deterministic-hash` バックエンドを使うため、Ollama なしで動作します。Ollama が必要なのは `--backend ollama` を指定した場合と、`semantic-topic` / `semantic-topics --model <model>` のみです。

詳細な設定は `config.yaml` で行えます:

```yaml
analyze:
  semantic_prototype:
    backend: ollama
    model: embeddinggemma
    backend_options:
      base_url: http://localhost:11434
      timeout_seconds: 30.0
    embedding:
      max_input_bytes: 2048
      chunk_overlap_bytes: 128
      aggregate: mean
```

ウィンドウの分割幅は `parse` / `chain` 時に決まります:

```yaml
parse:
  message_windows:
    size: 4
    stride: 2
```

---

## CLI リファレンス

> `uv run` はリポジトリから直接実行する場合の接頭辞です。`pip install` 済みなら不要です。

### parse — 構造化

生エクスポートをスレッドごとの `parsed.jsonl` に変換します。

```bash
uv run llm-logparser parse \
  --provider openai \
  --input <file> \
  --outdir artifacts \
  [--message-window-size <N>] \
  [--message-window-stride <N>] \
  [--dry-run] [--fail-fast] \
  [--validate-schema]
```

`--message-window-stride` を `--message-window-size` より小さくすると、重複するスライディングウィンドウになります。

### export — Markdown 書き出し

`parsed.jsonl` を Markdown に変換します。

```bash
uv run llm-logparser export \
  --input parsed.jsonl \
  [--out <md>] \
  [--split auto|size=N|count=N] \
  [--timezone <IANA>] \
  [--formatting none|light]
```

大きな出力は分割できます:

```text
--split size=4M       サイズで分割
--split count=1500    件数で分割
--split auto          自動分割
```

### extract — 単一会話の抽出

PII マスキングつきで、1つの会話を Gemini 互換 JSON として書き出します。

```bash
uv run llm-logparser extract \
  --provider openai \
  --input <file> \
  --conversation-id <id> \
  --outdir artifacts \
  [--dry-run]
```

### chain — 一括実行（parse → export）

`parse` と `export` を 1 コマンドで実行します。

```bash
uv run llm-logparser chain \
  --provider openai \
  --input <raw> \
  --outdir artifacts \
  [--validate-schema] \
  [other export options...]
```

追加オプション:

```text
--parsed-root       既存の parsed を再利用
--export-outdir     Markdown の出力先を変更
--dry-run           書き込みなし（確認のみ）
--fail-fast         最初のエラーで停止
```

### analyze stats — 集計サマリ

```bash
uv run llm-logparser analyze stats \
  --input <parsed.jsonl-or-directory> \
  [--per-thread] \
  [--top <N>] \
  [--sort messages|chars|span|conversation_id] \
  [--include-role-breakdown] \
  [--json] \
  [--out <path>]
```

### analyze datasheet — データセットレポート

```bash
uv run llm-logparser analyze datasheet \
  --input <parsed.jsonl-or-directory> \
  [--json] \
  [--out <path>]
```

### analyze timeline — 時間帯別集計

```bash
uv run llm-logparser analyze timeline \
  --input artifacts/openai \
  --bucket day \
  [--json] \
  [--out <path>]
```

### analyze tokens — トークン計測

```bash
uv run llm-logparser analyze tokens \
  --input <parsed.jsonl-or-directory> \
  [--model <model>] \
  [--encoding <tiktoken-encoding>] \
  [--skip-existing] \
  [--dry-run]
```

### analyze metrics — 対話品質指標

```bash
uv run llm-logparser analyze metrics \
  --input <parsed.jsonl-or-directory> \
  [--skip-existing] \
  [--dry-run]
```

> 事前に `analyze tokens` で `token_stats.json` を生成してください。

### analyze sqlite-build — SQL インデックス

```bash
uv run llm-logparser analyze sqlite-build \
  --input <artifact-root> \
  --provider <provider-id> \
  [--overwrite]
```

### analyze semantic-prototype — 埋め込み・クラスタリング

```bash
uv run llm-logparser analyze semantic-prototype \
  --input <provider-artifact-root> \
  [--backend deterministic-hash|ollama] \
  [--model <local-embedding-model>] \
  [--top-k <N>] \
  [--min-score <float>] \
  [--sqlite-db <path/to/analysis.db>] \
  [--candidate-window-days <N>] \
  [--candidate-min-chars <N>] \
  [--candidate-min-assistant-ratio <float>] \
  [--candidate-same-thread allow|prefer|only|exclude] \
  [--overwrite]
```

出力ファイル: `window_embeddings.jsonl` / `window_neighbors.jsonl` / `window_clusters.jsonl`

### analyze semantic-preview — クラスタ閲覧

```bash
# クラスタ一覧（大きいものから）
uv run llm-logparser analyze semantic-preview \
  --input <provider-artifact-root> \
  [--top-clusters <N>] [--min-cluster-size <N>] [--cross-thread-only]

# 特定クラスタの詳細
uv run llm-logparser analyze semantic-preview \
  --input <provider-artifact-root> \
  --cluster-id <cluster_id> [--top-k <N>]

# 特定会話のクラスタ
uv run llm-logparser analyze semantic-preview \
  --input <provider-artifact-root> \
  --conversation-id <conversation_id> [--cross-thread-only]
```

### analyze semantic-topics — トピック構造ファイル生成

```bash
uv run llm-logparser analyze semantic-topics \
  --input <provider-artifact-root> \
  [--model <ollama-model>] \
  [--min-cluster-size <N>] \
  [--cross-thread-only]

# 特定クラスタだけ
uv run llm-logparser analyze semantic-topics \
  --input <provider-artifact-root> \
  --cluster-id <cluster_id> \
  [--model <ollama-model>]
```

出力ファイル:
- `topics.json` — トピックの本体（クラスタ・ウィンドウ・メッセージの参照つき）
- `topic_membership.jsonl` — 逆引きインデックス

### analyze semantic-topic-explore — トピック閲覧

```bash
# トピック一覧
uv run llm-logparser analyze semantic-topic-explore \
  --input <artifact-root>

# トピック詳細
uv run llm-logparser analyze semantic-topic-explore \
  --input <artifact-root> \
  --topic-id <topic_id>

# メッセージからトピックを逆引き
uv run llm-logparser analyze semantic-topic-explore \
  --input <artifact-root> \
  --message-id <message_id>

# 会話単位の表示
uv run llm-logparser analyze semantic-topic-explore \
  --input <artifact-root> \
  --conversation-id <conversation_id> \
  [--hide-single-window] \
  [--min-window-count <N>] \
  [--min-conversation-count <N>]
```

おすすめの始め方:

```bash
# ノイズの少ない一覧（シングルウィンドウを非表示）
llm-logparser analyze semantic-topic-explore --input <root> --hide-single-window

# 複数会話にまたがるトピックだけ
llm-logparser analyze semantic-topic-explore --input <root> --min-conversation-count 2
```

### analyze semantic-topic — 実験的ラベル生成

```bash
uv run llm-logparser analyze semantic-topic \
  --input <provider-artifact-root> \
  --model <ollama-model> \
  [--top-clusters <N>] \
  [--min-cluster-size <N>] \
  [--cross-thread-only]
```

読み取り専用。ファイルは書き出しません（ファイルに保存したい場合は `semantic-topics` を使ってください）。

---

## 入力の指定方法

| コマンド | 入力に渡すもの |
|---------|--------------|
| `parse` / `chain` | 生エクスポートファイル（またはディレクトリ） |
| `export` | `parsed.jsonl`（単体） |
| `extract` | 生エクスポート + `--conversation-id` |
| `analyze stats` / `tokens` / `metrics` / `datasheet` / `timeline` | `parsed.jsonl` またはそれを含むディレクトリ |
| `analyze sqlite-build` | ディレクトリ + `--provider` |
| `analyze semantic-*` | ディレクトリ（サービス別の artifacts ルート） |

ざっくりまとめると:
- **`parse` / `chain`** = 生データを渡す
- **`export`** = `parsed.jsonl` を渡す
- **`analyze`** = `parsed.jsonl` またはディレクトリを渡す

---

## 設定ファイル

> 設定ファイルはオプションです。CLI フラグだけで動きます。

`config.yaml` を置くと、既定値として使われます（CLI フラグのほうが常に優先）。

### 設定ファイルの探索順

1. `--config <path>` で明示指定
2. 環境変数 `LLM_LOGPARSER_CONFIG`
3. カレントディレクトリの `config.yaml`
4. 親ディレクトリを遡って最初に見つかる `config.yaml`
5. `~/.config/llm-logparser/config.yaml`

### プロファイル

複数の入力元を管理するとき便利です。

```yaml
schema_version: 1
active_profile: default

profiles:
  default:
    provider: openai

    input:
      path: exports/messages.jsonl

    sanitize:
      enabled: true
      replacement: REDACTED
      scope: content_parts

    output:
      path: artifacts/thread.md
      formatting: light
      split: auto

    parse:
      outdir: artifacts
      validate_schema: true
```

選択の優先順位:

```text
--profile > active_profile > 唯一定義されたプロファイル
```

値の優先順位:

```text
CLI フラグ > プロファイルの値 > 組み込みの既定値
```

### 設定の確認

```bash
llm-logparser config path        # 使われている設定ファイルのパス
llm-logparser config show        # 現在の設定内容
llm-logparser config validate    # 設定の妥当性チェック
```

### 個人情報の除去（extract 用）

```yaml
sanitize:
  enabled: true
  replacement: REDACTED
  scope: content_parts   # or: all_strings
  extra_keywords: [credential]
  mask_patterns:
    - acct-\d+
```

`sanitize` を省略しても、`extract` は既定で個人情報除去が有効です（メールアドレス・電話番号パターンを検出）。

### 非対話モード

CI / 自動化で使うとき：

```bash
--non-interactive
# または
LLM_LOGPARSER_NON_INTERACTIVE=1
```

必須入力が不足している場合、終了コード `2` で停止します。

---

## その他

### 多言語対応

出力の言語とタイムゾーンを変更できます:

```text
--locale   ja-JP
--timezone Asia/Tokyo
```

対象:
- CLI のメッセージとヘルプテキスト
- 分析ヒューリスティックの判定フレーズ（拒否パターン、修正パターン等）

> `analyze stats` 等のレポート出力と JSON スキーマキーは英語固定です（ツール安定性のため）。

### フレーズのカスタマイズ

`src/llm_logparser/i18n/*.yaml` の YAML ファイルで、分析に使われる判定フレーズを調整できます:

- `analysis.refusal.indicators` — 拒否応答の判定フレーズ
- `analysis.revision.cues` — ユーザー修正の判定フレーズ
- `analysis.correction.cues` — 訂正の判定フレーズ
- `analysis.clarification.cues` — 明確化の判定フレーズ

組織固有の言い回しがある場合は、コードを変更する前にまず YAML を調整してください。

### セキュリティとプライバシー

- オフラインで動作（`tiktoken` が初回のみデータを取得する以外）
- テレメトリなし
- 出力は決定的（同じ入力なら同じ結果）
- `extract` の個人情報除去は既定で有効

### VS Code 拡張

VS Code 拡張は別リポジトリに移動しました: https://github.com/Syun-tnb/llm-logparser-vscode

### ディレクトリ構造（リファレンス）

> 覚える必要はありません。ツールが自動で作成します。

```text
artifacts/
  openai/
    manifest.json
    thread-<conversation_id>/
      parsed.jsonl
      thread_stats.json
      message_windows.jsonl
      token_stats.json
      metrics.json
      thread-<conversation_id>__*.md
      meta.json（オプション）
    analysis.db（オプション・L2）
    l3/semantic-topics/（オプション・L3）
```

### 内部アーキテクチャ

```text
┌─────────────────────────────────────────────────────────┐
│  L1: 決定的分析                                          │
│  stats / timeline / tokens / metrics / datasheet         │
├─────────────────────────────────────────────────────────┤
│  L2: SQLite インデックス（オプション）                     │
│  sqlite-build → analysis.db                              │
├─────────────────────────────────────────────────────────┤
│  L3: セマンティック分析（実験的）                          │
│  semantic-prototype / preview / topics / topic-explore    │
├─────────────────────────────────────────────────────────┤
│  L4: モデル/API 連携（将来）                              │
└─────────────────────────────────────────────────────────┘
         ↑ すべての起点: parsed.jsonl（正規データ）
```

- L1 の出力はすべて `parsed.jsonl` から決定的に再現可能
- L2 / L3 は補助データであり、削除しても正規データには影響しません

---

## コントリビュート

PR を歓迎します。

始めやすい領域: **アダプタ追加** / **書き出し改善** / **多言語フレーズ追加**

原則:
- 決定的な処理を中心に
- サービス固有の処理はアダプタに閉じ込める
- 既定でオフライン動作

```bash
uv run pytest
```

---

## ライセンス

MIT

---

## 著者

```text
The words you weave are not mere echoes;
they carry weight,
and may they never be lost to the tide of time.
```

© 2025 **Ashes Division - Reyz Laboratory**
