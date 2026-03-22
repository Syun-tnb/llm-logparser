# llm-logparser

[![PyPI version](https://img.shields.io/pypi/v/llm-logparser)](https://pypi.org/project/llm-logparser/)[![Python versions](https://img.shields.io/pypi/pyversions/llm-logparser)](https://pypi.org/project/llm-logparser/)[![License](https://img.shields.io/github/license/Syun-tnb/llm-logparser)](LICENSE)[![GitHub Sponsors](https://img.shields.io/github/sponsors/Syun-tnb)](https://github.com/sponsors/Syun-tnb)

LLM のチャットエクスポートを、読みやすく・検索しやすく・分析しやすい形に整える local-first CLI です。
データをクラウドへ送信することはありません。

**できること:**

1. ChatGPT（または他の LLM）のエクスポートを、きれいで構造化されたデータに **Parse**
2. 会話を読みやすい Markdown ファイルとして **Export**
3. メッセージ数、トークン使用量、安全性メトリクス、タイムラインなどを会話ごとに **Analyze**

すべてローカルで動作します。クラウドなし、テレメトリなし。データは手元の環境に保存されます。

> 現在サポートしているのは OpenAI 、Claude 、Grok のエクスポートです。Gemini には今後対応予定です。

---

## インストール

> **ほとんどの場合:** `pip install llm-logparser` だけで使い始められます。

`pip` または `uv` を使って、PyPI からインストールできます:

```bash
pip install llm-logparser
```

```bash
uv pip install llm-logparser
```

リポジトリを clone して開発する場合は、次のようにセットアップします:

```bash
uv sync
uv sync --extra dev
```

コマンドエイリアス:

`llp` は `llm-logparser` の簡易エイリアスです。どちらを使っても同じように動作します。

例:

```bash
llp parse ...
llm-logparser analyze stats ...
```

実行方法の違い:

* パッケージとしてインストールした場合 → `llm-logparser ...`
* リポジトリから実行する場合 → `uv run ...`

---

## クイックスタート

生のエクスポートから読みやすい出力まで最短で進む方法:

```bash
llm-logparser chain \
  --provider openai \
  --input path/to/conversations.json \
  --outdir artifacts \
  --timezone Asia/Tokyo
```

これは 2 つのことを行います:
1. 生のエクスポートを、会話ごとの構造化データへ **Parse** します
2. 各会話を Markdown ファイルとして **Export** します

完了すると、次のようなディレクトリが表示されます:

```text
artifacts/output/openai/
  manifest.json                        ← 解析済み会話すべてのインデックス
  thread-abc123/
    parsed.jsonl                       ← 構造化された会話データ
    thread_stats.json                  ← メッセージ数、タイムスタンプなど
    message_windows.jsonl              ← グループ化されたメッセージセグメント
    thread-abc123__gpt-4o.md           ← 読みやすい Markdown 文字起こし
```

任意の `.md` ファイルを開けば会話を読めます。これで終わりです。基本は
これで完了です。

**さらに進みたいですか？** [First Steps After Setup](#first-steps-after-setup)
を見て、analyze コマンドから会話について何が分かるか確認してください。

---

## セットアップ後の最初のステップ

`chain`（または `parse`）を実行した後、データを最大限活用するための
推奨手順は次のとおりです。各ステップは前のステップの上に積み上がります。

### Step 1 — 会話を閲覧する

生成された `.md` ファイルを開きます。これらはタイムスタンプ、ロールラベル、
保持された書式を備えた標準的な Markdown です。

### Step 2 — すばやく要約を確認する

```bash
llm-logparser analyze stats --input artifacts/output/openai
```

これにより、会話の要約が出力されます。スレッド数、総メッセージ数、
文字数、期間です。機械可読な出力が必要なら `--json` を追加してください。

### Step 3 — トークンを数える

```bash
llm-logparser analyze tokens --input artifacts/output/openai
```

これにより、各会話の横に `token_stats.json` ファイルが書き込まれ、
メッセージごとのトークン数が入ります。コストやコンテキストウィンドウの使用量を理解したい場合に有用です。

### Step 4 — 完全なメトリクスを構築する

```bash
llm-logparser analyze metrics --input artifacts/output/openai
```

これにより、各会話の横に `metrics.json` ファイルが書き込まれ、安全性シグナル、
相互作用パターン、文字数/トークン比が含まれます。**先に Step 3 が必要です。**

> **どのステップでも止められます。** それぞれ単独で有用です。Step 3–4 では
> 解析済みデータの横に置かれるファイルが生成され、いつでも再構築できます。

---

## Analyzer は実際に何を出力するのか？

各 analyze コマンドは異なる種類の出力を生成します。ここでは、実際に
何が得られ、何に答えるのかを示します。

---

### `analyze stats` → ターミナル要約（または JSON）

**含まれるもの:** 会話全体にまたがる集計済みの件数と分布。

**答える問い:**
- 会話はいくつあるのか？ 総メッセージ数はいくつか？
- 最も長い会話はどれか？
- user と assistant のメッセージ比率はどれくらいか？

**出力例（テキスト）:**
```text
threads:    12
messages:   847
  user:     421
  assistant: 426
characters: 312,504
timespan:   2025-01-15 — 2025-03-20
```

構造化出力が必要なら `--json` を追加してください。会話ごとの内訳が必要なら
`--per-thread` を追加してください。

---

### `analyze tokens` → `token_stats.json`（会話ごと）

**含まれるもの:** すべてのメッセージのトークン数。ロール別の内訳と、
tokenizer メタデータを含みます。

**答える問い:**
- この会話では何トークン使われたか？
- 最もコストの高いメッセージはどれか？
- user と assistant のトークン配分はどうなっているか？

**フィールド例:**
```json
{
  "summary": { "total_tokens": 4821, "total_messages": 42 },
  "by_role": { "user": { "tokens": 1200 }, "assistant": { "tokens": 3621 } },
  "messages": [{ "message_id": "m1", "role": "user", "token_count": 28 }]
}
```

---

### `analyze metrics` → `metrics.json`（会話ごと）

**含まれるもの:** 文字数/トークン比、語彙多様性、安全性シグナル、
相互作用パターンを含む派生メトリクス。

**答える問い:**
- assistant は何かの要求を拒否したか？ どのくらいの頻度か？
- user は自分の発言を修正または訂正したか？
- assistant 対 user の文字数比はどれくらいか？
- assistant のメッセージ後、user はどれくらい早く応答したか？

**フィールド例:**
```json
{
  "safety": { "refusal_count": 1, "intervention_count": 2 },
  "interaction": { "revision_count": 3, "correction_count": 1 },
  "user_effort": { "rapid_revisions": 2, "response_length_ratio": 3.4 },
  "diversity": { "type_token_ratio": 0.62 }
}
```

**`token_stats.json` が必要です** — 先に `analyze tokens` を実行してください。

---

### `analyze datasheet` → レポート（Markdown または JSON）

**含まれるもの:** 構成、時間的範囲、主要統計をカバーする、
簡潔で付録向けのデータセット要約。

**答える問い:**
- このデータセット全体はどのような見た目か？
- 研究論文の付録には何を書けばよいか？

**出力:** 既定では Markdown です。構造化データが必要なら `--json` を追加してください。

---

### `analyze timeline` → 時間バケット化された活動量（テキストまたは JSON）

**含まれるもの:** 時間単位（時間、日、週、または月）ごとにまとめられたメッセージ数。

**答える問い:**
- もっとも活動していたのはいつか？
- 利用に空白期間はあるか？

**出力例:**
```text
2025-01-15:  48 messages
2025-01-16:  12 messages
2025-01-17:   0 messages
2025-01-18:  93 messages
```

---

### `analyze sqlite-build` → `analysis.db`（任意）

**含まれるもの:** thread stats、messages、message windows を統合し、
問い合わせ可能なテーブルにした SQLite データベース。

**答える問い:**
- 特定のトピックに言及する会話はどれか？
- ある日付範囲にある assistant の全メッセージは何か？
- SQL による会話横断の集計

**これは完全にスキップできます。** これは、SQL クエリを実行したい大規模データセットのユーザー向けの任意の高速化レイヤーです。データベースはいつでも解析済みデータから完全に
再構築できます。

```bash
llm-logparser analyze sqlite-build \
  --input artifacts/output \
  --provider openai
```

---

## どれを使うべきか

| コマンド | 使う場面 |
|---------|---------------|
| `analyze stats` | 会話のすばやい要約を得る |
| `analyze tokens` | メッセージごとのトークン数を数える（コスト/コンテキスト分析向け） |
| `analyze metrics` | 安全性、相互作用、労力のパターンを理解する |
| `analyze datasheet` | 研究用にそのまま使えるデータセット要約を生成する |
| `analyze timeline` | いつ最も活動していたかを確認する |
| `analyze sqlite-build` | SQL で大規模データセットを問い合わせる（任意） |

---

## CLI リファレンス

以下のセクションでは、ソースチェックアウトから作業する際にも便利なため `uv run` を使っています。PyPI からインストールした場合は、`uv run` の接頭辞を外してください。

### Parse

生の provider エクスポートを canonical thread artifact に正規化します:

```bash
uv run llm-logparser parse \
  --provider openai \
  --input <file> \
  --outdir artifacts \
  [--dry-run] [--fail-fast] \
  [--validate-schema]
```

### Export

canonical `parsed.jsonl` を Markdown にレンダリングします:

```bash
uv run llm-logparser export \
  --input parsed.jsonl \
  [--out <md>] \
  [--split auto|size=N|count=N] \
  [--timezone <IANA>] \
  [--formatting none|light]
```

### Extract

PII マスキング付きで、単一の会話を Gemini 互換 JSON として抽出します:

```bash
uv run llm-logparser extract \
  --provider openai \
  --input <file> \
  --conversation-id <id> \
  --outdir artifacts \
  [--dry-run]
```

### Chain

単一コマンドで parse と export を行います。すでに `parse` を別で実行していて export だけ行いたい場合は、`export` を直接使ってください。`chain` は一般的な parse → export ワークフローのための簡易コマンドです。

```bash
uv run llm-logparser chain \
  --provider openai \
  --input <raw> \
  --outdir artifacts \
  [--validate-schema] \
  [other export options...]
```

便利なオプション:

```text
--parsed-root       既存の parsed threads を再利用
--export-outdir     Markdown を別の場所に配置
--dry-run           parse のみ実行（書き込みなし）
--fail-fast         最初の export エラーで停止
```

### Analyze Stats

canonical `parsed.jsonl` ファイルから、決定的な thread および message 統計を計算します:

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

`analyze stats` は、1 つの thread または thread ディレクトリ全体に対する集計や探索的要約が欲しいときに使います。canonical `parsed.jsonl` から直接テキストまたは JSON をレンダリングでき、その加算的な `research_summary` セクションは、決定的な時間軸、発話交代、安全性、軽量な構造集計を提供します。

### Analyze Datasheet

canonical parsed artifact から、簡潔で付録向けのデータセット要約を構築します:

```bash
uv run llm-logparser analyze datasheet \
  --input <parsed.jsonl-or-directory> \
  [--json] \
  [--out <path>]
```

`analyze datasheet` は、探索的要約ではなく安定したレポートレイヤーが欲しいときに使います。既定の出力は Markdown です。`--json` は同じ内容を機械可読な要約オブジェクトとして返します。

### Analyze Timeline

canonical `parsed.jsonl` ファイルからタイムスタンプ付きメッセージ活動を集計します:

```bash
uv run llm-logparser analyze timeline \
  --input artifacts/output/openai \
  --bucket day \
  [--json] \
  [--out <path>]
```

### Analyze Tokens

canonical `parsed.jsonl` から、決定的なスレッドごとの `token_stats.json` sidecar を構築します:

```bash
uv run llm-logparser analyze tokens \
  --input <parsed.jsonl-or-directory> \
  [--model <model>] \
  [--encoding <tiktoken-encoding>] \
  [--skip-existing] \
  [--dry-run]
```

現在の tokenizer バックエンド:

* `tiktoken`
* `openai`、`anthropic`、`xai` 向けの provider 既定値
* `--encoding` は provider と model の解決を上書きします

実行時の注意点:

* `tiktoken` は初回利用時にエンコーディングデータをダウンロードするため、一度だけネットワーク取得を行う可能性があります
* ダウンロードされたエンコーディングデータは、その後ローカルにキャッシュされます
* それ以降の token analysis 実行ではローカルキャッシュが使われます
* 既存の `token_stats.json` sidecar は既定で再構築されます。`--skip-existing` は不足している sidecar のみを埋めます
* `--dry-run` はファイルを書き込まずに sidecar 生成をプレビューします

### Analyze Metrics

`parsed.jsonl` と `token_stats.json` から、決定的なスレッドごとの `metrics.json` sidecar を構築します:

```bash
uv run llm-logparser analyze metrics \
  --input <parsed.jsonl-or-directory> \
  [--skip-existing] \
  [--dry-run]
```

各 thread が隣接する `token_stats.json` をすでに持っているよう、先に `analyze tokens` を実行してください。

現在のメトリクスには次が含まれます:

* ratio、token、character、distribution、diversity の各メトリクス
* `safety.refusal`
* `safety.intervention_count`
* `correction`、`clarification`、`retry` の subtype count を伴う `interaction.revision`
* `user_effort` メトリクス

追加の挙動メモ:

* 既存の `metrics.json` sidecar は既定で再構築されます
* `--skip-existing` は不足している sidecar のみを埋めます
* `--dry-run` は書き込み前に sidecar 生成をプレビューします

### Analyze SQLite Build

canonical かつ決定的な thread artifact から、任意の provider ごとの SQLite analysis index を構築します:

```bash
uv run llm-logparser analyze sqlite-build \
  --input <provider-artifact-root> \
  --provider <provider-id> \
  [--overwrite]
```

`analysis.db` は、クエリ高速化のための任意の、決定的で、再構築可能で、
non-canonical なインデックスです。これは `parsed.jsonl` を置き換えず、将来のあらゆる派生成果物のストレージレイヤーでもありません。

---

## Markdown フォーマット

エクスポートされる各 Markdown ファイルは、YAML front matter から始まります:

```yaml
---
thread: "abc123"
provider: "openai"
messages: 42
models: ["gpt-4o"]
range: "2025-10-01T01:00:00+00:00 〜 2025-10-18T10:15:00+00:00"
---
```

メッセージはタイムスタンプ順に続きます:

```markdown
## [User] 2025-10-18 10:00
Good morning!

## [Assistant] 2025-10-18 10:01
Good morning - how can I help today?
```

Markdown は GFM 互換で、次を保持します:

* fenced code blocks
* links
* tables
* quotes

---

## 分割

大きな Markdown 出力は、サイズ、件数、または自動プリセットで分割できます:

```text
--split size=4M
--split count=1500
--split auto
```

追加の調整:

```text
--split-soft-overflow 0.20
--split-hard
--tiny-tail-threshold 20
```

---

## アーキテクチャ

> **このセクションは、ツールが内部でどう動くかを理解するためのものです。** ツールを使うだけなら
> 必要ありません。代わりに [Quick Start](#quick-start) を参照してください。

### パイプライン

`llm-logparser` は、1 つの canonical source of truth を中心に構築されています:

```text
raw export
  -> parse
  -> canonical parsed.jsonl
     -> export        -> Markdown 文字起こし
     -> analyze stats -> 集計と探索的要約
     -> analyze tokens -> token_stats.json
     -> analyze metrics -> metrics.json
     -> analyze datasheet -> 付録向けレポート（Markdown または JSON）
```

Analyzer コマンドは、既存の sidecar artifact がすでに存在する場合はそれらを再利用し、
欠けている場合は canonical `parsed.jsonl` にフォールバックします。どちらでも
出力は決定的で local-first のままです。

### Canonical Data Model

parser は provider 固有のエクスポートを安定した JSONL schema に正規化します。その JSONL が、このプロジェクトにおける canonical な中間フォーマットです。

下流の機能はその canonical layer を利用します:

* Markdown export
* HTML または GUI viewer
* analyzer
* 将来のアプリケーション

Parser の責務は、決定的な JSONL 生成で終わります。presentation、export formatting、analysis は、別個に扱われる下流の関心事です。

### Analyzer Layering

概要:

* canonical `parsed.jsonl` が source of truth です
* `thread_stats.json`、`token_stats.json`、`metrics.json` は sidecar artifact です
* Layer 1（L1）は決定的 analysis です: `stats`、`timeline`、`tokens`、`metrics`、`datasheet`
* Layer 2（L2）は `analyze sqlite-build` です: 任意の決定的 SQLite index
* Layer 3 / Layer 4 は将来の model-based layer（local / API）であり、現在の CLI 機能ではありません
* `analyze sqlite-build` は再構築可能な non-canonical index であり、汎用 analysis engine や、あらゆる派生データをまとめて置くストアではありません

Sidecar artifact は canonical `parsed.jsonl` から再構築可能で、実行時タイムスタンプを含みません。`analysis.db` も同様に再構築可能で non-canonical です。

データソース方針:

- canonical correctness は `parsed.jsonl` に固定されます
- `analyze metrics` には隣接する `token_stats.json` が必要です
- `analyze stats` は canonical `parsed.jsonl` から計算します
- `analyze datasheet` は、存在する場合は既存の `thread_stats.json`
  と `metrics.json` sidecar artifact を便宜的に再利用することがあります
- それらの sidecar が欠けているか使えない場合、`analyze datasheet` は
  決定的に canonical `parsed.jsonl` へフォールバックします
- `analyze stats` の研究指向の安全性集計も、存在する場合は
  `metrics.json` を再利用することがありますが、それがなくても動作します

CLI 一貫性メモ:

- `analyze stats` と `analyze timeline` は presentation command です: ターミナル出力をレンダリングし、`--json` をサポートし、レンダリング結果を `--out` 経由で書き出せます
- `analyze datasheet` も presentation command です: 既定では Markdown を
  レンダリングし、`--json` をサポートし、レンダリング結果を `--out` 経由で書き出せます
- `analyze tokens` と `analyze metrics` は sidecar builder です: 各 `parsed.jsonl` の横にスレッドごとの JSON artifact を書き込み、presentation flag の代わりに `--skip-existing` を使います
- `analyze sqlite-build` は単一の `analysis.db` index artifact を書き込み、再構築の制御に `--overwrite` を使います

段階的 sidecar 方針:

- 既定の挙動: 既存の `token_stats.json` と `metrics.json` sidecar は再構築されて上書きされます
- `--skip-existing`: 既存の sidecar はそのままにして、不足している sidecar だけを構築します
- `--dry-run`: ファイルを書き込まず、検出された thread と予定されている create/rebuild/skip 件数をプレビューします
- `analyze metrics --skip-existing` でも、`metrics.json` が欠けている thread については、既存の `token_stats.json` が依然として必要です

Analyzer の i18n は意図的に狭くしています:

- locale に支えられた YAML resource は、refusal や revision cue などのヒューリスティック入力にのみ影響します
- `analyze stats`、`analyze datasheet`、
  `analyze timeline` の human-readable text renderer は意図的に英語のみです
- 構造化 JSON 出力と schema key は、ツールの安定性のため英語のままです

### ディレクトリ構造

> **これを覚える必要はありません。** ツールがこの構造を
> 自動で作成します。このリファレンスは必要なときのためにあります。

```text
artifacts/
  output/
    openai/
      manifest.json
      thread-<conversation_id>/
        parsed.jsonl
        thread_stats.json
        message_windows.jsonl
        token_stats.json
        metrics.json
        thread-<conversation_id>__*.md
        meta.json (任意)
```

`--outdir` にはルートパスだけを渡してください。ツールが `output/<provider>/...` を自動で作成します。

---

## 設定 (`config.yaml`)

> **設定は任意です。** CLI はコマンドラインフラグだけでも動作します。
> `config.yaml` は、既定設定を保存したいときや、
> 複数のエクスポート元を管理したいときに便利です。

`llm-logparser` は、YAML `config.yaml` を通じた任意の runtime 既定値をサポートします。CLI フラグが常に優先されます。profile の値は、不足しているオプションを埋めるためにのみ使われます。

外部 provider mapping YAML は、まだ runtime では使われていません。現在の正規化は `src/llm_logparser/core/providers/` 配下の adapter ベースです。

### Config Discovery Order

`--config` フラグが指定されていない場合、ツールは次の順で探索します:

1. 明示的な `--config <path>`
2. 環境変数 `LLM_LOGPARSER_CONFIG=<path>`
3. カレントディレクトリの `config.yaml`
4. `config.yaml` を含む最も近い親ディレクトリ
5. 該当する場合は `~/.config/llm-logparser/config.yaml`

設定ファイルが見つからなくても、CLI は通常どおり動作します。

---

### Profiles

複数の profile を定義し、`--profile <name>` で 1 つ選択できます:

```yaml
schema_version: 1
active_profile: default

profiles:
  default:
    provider: openai

    input:
      path: exports/messages.jsonl
      # or:
      # paths: [exports/a.jsonl, exports/b.jsonl]
      # export uses:
      # parsed: artifacts/output/openai/thread-123/parsed.jsonl

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

Profile 選択の優先順位:

```text
--profile > active_profile > the only defined profile
```

サポートされている config 由来オプションの値の優先順位:

```text
CLI flags > selected profile values > built-in CLI defaults
```

section ベースの形が canonical です。`outdir`、`dry_run`、`fail_fast`、`validate_schema`、`export_outdir`、`parsed_root`、`conversation_id` のような古い profile-level 互換キーも `schema_version: 1` では引き続き受け付けられますが、loader は警告を出し、section ベースの置き換え先を示します。この互換性は、将来の schema-version-2 cleanup で削除される想定です。

複数の `input.paths` が定義されていて、明示的な `--input` が指定されていない場合:

* interactive mode では、プロンプトが表示されます
* non-interactive mode では、プログラムはコード `2` で終了します

複数の profile が存在し、`--profile` でも `active_profile` でも選ばれない場合:

* interactive mode では、profile を選ぶよう促されます
* non-interactive mode では、profile 既定値は適用されません

---

### Relative Path Resolution

`config.yaml` で定義された相対パスは、見つかった `config.yaml` が存在するディレクトリを基準に解決されます。

これにより、次のような使い方でも挙動が安定します:

```bash
LLM_LOGPARSER_CONFIG=/etc/llm/config.yaml
```

また、意図しない CWD 依存のパス解決を避けられます。

---

### Config Subcommands

config 解決の確認やデバッグには、次のヘルパーを使います:

```bash
uv run llm-logparser config path
uv run llm-logparser config show [--profile work]
uv run llm-logparser config validate
```

`config show` は、1 つ選ばれた profile が解決された場合は正規化済みのその profile を表示します。そうでない場合は、正規化済みの設定全体構造を表示します。

`extract` に対する canonical な sanitize セクションは次のとおりです:

```yaml
sanitize:
  enabled: true
  replacement: REDACTED
  scope: content_parts   # or: all_strings
  extra_keywords: [credential]
  mask_patterns:
    - acct-\d+
```

`sanitize` が省略されても、`extract` は現在の安全寄りの既定挙動を維持します:

* sanitization は有効のままです
* 機微なフィールド名は redact されます
* 組み込みの email および phone パターンが `content.parts` に適用されます

---

### Non-Interactive Mode

次でプロンプトを無効化できます:

```bash
--non-interactive
```

または:

```bash
LLM_LOGPARSER_NON_INTERACTIVE=1
```

non-interactive mode では、次の場合にプログラムはコード `2` で終了します:

* 必須オプションが不足している
* 複数の入力候補が曖昧である

これにより、CLI は CI や自動化ワークフローで安全に使えます。

---

## ローカライゼーション

> **このセクションは無視してかまいません**。英語以外のヒューリスティック句リストが必要な場合や、
> 日本語の CLI 出力が欲しい場合を除きます。

`llm-logparser` は best-effort な i18n モデルを採用しています。locale file は任意で、ユーザーが拡張可能な YAML resource であり、キー欠落は実行を妨げるのではなく安全にフォールバックすることが想定されています。

出力フォーマットは次で制御できます:

```text
--locale   en-US | ja-JP | …
--timezone Asia/Tokyo | UTC | …
```

Locale file は `src/llm_logparser/i18n/*.yaml` 配下にあり、次を含められます:

* `messages:` はスカラーな CLI、help、runtime、error テキスト用
* `analysis:` は構造化された analyzer phrase resource 用

ローカライズされるもの:

* `messages:` に由来する CLI、help、runtime メッセージ
* `analysis:` に由来する analyzer のヒューリスティック phrase resource

設計上ローカライズされないもの:

* `analyze stats`、`analyze datasheet`、`analyze timeline` のレンダリング済み要約
* JSON artifact と安定した schema key
* `usage:` などの argparse 組み込みや parser 生成 boilerplate
* タイムゾーン変換を除く Markdown タイムスタンプ整形

Locale の優先順位:

1. CLI `--locale` または `--lang`
2. 環境変数 `LLP_LOCALE`
3. 該当する場合は選択された profile locale `profiles.<name>.locale`
4. `en-US`

メモ:

* まだすべてのコマンドが profile-level locale を完全には尊重しません。CLI と環境設定が優先されます
* parser と help 出力は、生の argv スキャンにより早い段階で CLI locale を取り込めます
* 未知の locale は `en-US` に解決されます
* message key が欠けている場合は `en-US` にフォールバックし、それでも無ければ生の key にフォールバックします
* analyzer resource key は `en-US` にフォールバックします
* `en` や `ja` のような短い別名は、言語接頭辞が曖昧でない場合に locale filename から自動導出されます
* 複数の locale file が同じ言語接頭辞を共有する場合は、完全な locale tag を使ってください

---

## YAML カスタマイズ

Locale データは YAML 駆動です。`src/llm_logparser/i18n/` 配下の locale file は、厳密な契約ではなく best-effort な拡張です。部分的な file でも受け入れられ、`en-US` へのフォールバックは通常挙動です。

スカラーな CLI、help、runtime メッセージは `messages:` 配下にあり、analyzer の phrase 調整は `analysis:` 配下にあります。

キー:

* `analysis.refusal.indicators`
  assistant メッセージに対する `metrics.json` の refusal 検出で使われる phrase list。

* `analysis.revision.cues`
  user メッセージに対する `metrics.json` の revision 検出で使われる phrase list。

* `analysis.correction.cues`
  検出された revision の correction subtype 判定で `metrics.json` が使う phrase list。

* `analysis.clarification.cues`
  検出された revision の clarification subtype 判定で `metrics.json` が使う phrase list。

ガイダンス:

* user 向け CLI、help、runtime テキストを変更するときだけ `messages:` を編集してください
* ドメイン固有の表現、方言、口語表現は YAML に直接追加してください
* 明らかな偽陽性を避けるため、小さく保守的な phrase list を推奨します
* ログが組織固有の言い回しを使っている場合は、コードを変える前にまず YAML を調整してください
* section や key が欠けている場合、locale 固有の挙動は `en-US` にフォールバックします
* revision ヒューリスティックは、cue や類似度マッチングの前に非常に短い user メッセージを無視します

これが、phrase ベースのヒューリスティック調整のために想定されているカスタマイズ経路です。

---

## セキュリティとプライバシー

* parse、export、ほとんどの analyzer workflow は offline-first です
* テレメトリはありません
* 機微なログはローカルに留まります
* 監査向けの決定的な出力です
* `extract` の sanitization は config 駆動で、互換性のため既定で有効です
* `extract.meta.json` には、sanitization が有効だったか、どの scope が実行されたか、どの replacement token が使われたか、custom keyword や pattern が与えられたかが記録されます
* `analyze tokens` と `analyze metrics` は通常ローカルですが、`tiktoken` は初回利用時に一度だけ encoding data を取得し、その後はローカルキャッシュを使う可能性があります

---

## 依存関係とクレジット

現在の analyze と tokenizer 関連の実装は、主に次に依存しています:

* 決定的 analysis とヒューリスティックのための Python 標準ライブラリユーティリティ
* tokenizer ベース analysis のための [`tiktoken`](https://github.com/openai/tiktoken)

refusal と revision ヒューリスティックの phrase resource は、`src/llm_logparser/i18n/` 配下のプロジェクト YAML file にあり、ユーザーが調整できるよう意図されています。

---

## ロードマップ

- [x] CLI MVP (parse / export / extract / chain / analyze)
- [x] thread 分割付き Markdown exporter
- [x] JSON Schema validation
- [x] 設定ファイル読み込み（auto-discovery + profiles）
- [x] Analyzer stats / timeline / tokens / metrics

Near term:

- [ ] Anthropic / Claude support
- [ ] xAI / Grok support
- [ ] 正規化済みログを閲覧するための VS Code Extension

Later / exploratory:

- [ ] Gemini support（形式は評価中）
- [ ] GUI applications

---

## コントリビュート

PR を歓迎します。

始めやすい場所:

* adapters
* exporter 改善
* localization

原則:

* deterministic core
* provider 固有の振る舞いは adapters に置く
* 既定で offline

次でローカルのテストスイートを実行できます:

```bash
uv run pytest
```

---

## ライセンス

MIT - シンプルで制約の少ないライセンスです。

---

## 著者

```text
The words you weave are not mere echoes;
they carry weight,
and may they never be lost to the tide of time.
```

© 2025 **Ashes Division - Reyz Laboratory**
