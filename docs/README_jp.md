# llm-logparser

**`llm-logparser` は、LLM チャットのエクスポートデータを、ローカル環境だけで扱いやすい形に変換・整理・解析するための CLI ツールです。** OpenAI のエクスポートを起点に、将来的には Claude / Gemini など複数プロバイダのログも同じ土台で扱えるように設計されています。出力は GitHub Flavored Markdown と canonical JSONL を中心に構成され、監査、アーカイブ、再利用、移行に耐えることを重視しています。

このプロジェクトの中心思想は、**deterministic、offline-first、privacy-first、canonical-first** です。`parsed.jsonl` を唯一の正規データソースとして据え、Parser はデータの正規化に専念し、Exporter / Analyzer はその上に表現や分析を積み上げます。テレメトリはありません。クラウドへの送信もありません。生成される sidecar は再構築可能な派生成果物であり、正しさの起点は常に canonical data model にあります。

---

## ✨ 主な機能

- **Parse → Normalize → JSONL → Export** の一連のワークフローを CLI で実行
- LLM エクスポートを **canonical JSONL (`parsed.jsonl`)** に正規化
- スレッド単位の **Markdown 出力** を生成
- YAML front-matter 付きの **thread-based layout**
- サイズ / 件数 / 自動判定による **Markdown 分割出力**
- **ローカライズ可能な CLI / help / runtime メッセージ**
- IANA タイムゾーンに基づく **時刻変換付き Markdown 出力**
- `chain` による **parse + export の一括実行**
- `analyze stats` / `analyze timeline` による **canonical データからの集計**
- `analyze tokens` による **決定的な `token_stats.json` sidecar** の生成
- `analyze metrics` による **決定的な `metrics.json` sidecar** の生成
- `analyze sqlite-build` による **任意の SQLite 分析インデックス** の生成
- refusal / revision 系ヒューリスティクスを **YAML リソースで調整可能**
- **local-first / deterministic** を維持しやすいアーキテクチャ
- 将来のマルチプロバイダ対応を見据えた **adapter-based design**

> 現在の MVP は主に OpenAI ログを中心に整備されています。  
> Claude / Gemini などは今後の対応対象です。

---

## 🧱 アーキテクチャ

### Canonical Data Model

`llm-logparser` の中心は **`parsed.jsonl`** です。これは各プロバイダ固有のエクスポート形式を、解析しやすく比較しやすい **canonical JSONL** に正規化したものです。

この `parsed.jsonl` が、プロジェクト全体における **single source of truth** です。

以下の機能はすべて、この canonical data model を前提に動作します。

- Markdown Export
- Analyzer
- 将来の Viewer / GUI
- SQLite インデックス
- 下流のアプリケーションや研究用処理

重要なのは、**正しさの基準が sidecar ではなく `parsed.jsonl` にある** という点です。`thread_stats.json`、`token_stats.json`、`metrics.json` などは便利な派生成果物ですが、正規入力ではありません。必要であればいつでも再生成できる、という前提で扱います。

### 責務分離: Parser / Exporter / Analyzer

このプロジェクトでは、責務を明確に分けています。

**Parser**
- プロバイダ固有のデータを canonical 形式に正規化する役割
- `parsed.jsonl` を生成する
- parse 時点で軽量な thread-local artifact を作ることはあるが、あくまで canonical data の補助に留まる

**Exporter**
- canonical data を人間が読みやすい Markdown に整形する役割
- 表示、分割、タイムゾーン変換、front-matter 付与を担う
- データそのものの意味づけや解析はしない

**Analyzer**
- canonical data に対して deterministic な集計や派生 artifact を作る役割
- `stats` / `timeline` は presentation-oriented な集計出力
- `tokens` / `metrics` は per-thread sidecar の生成
- 将来的に `thread_stats.json` のような parse-time metadata を最適化用途で使う可能性はあるが、**正しさは常に `parsed.jsonl` に依存**する

### Canonical First の原則

以下は、このプロジェクトで特に重要な設計ルールです。

- `parsed.jsonl` は唯一の canonical source of truth
- `thread_stats.json` は parse-time の補助メタデータであり、canonical input ではない
- Analyzer は sidecar が無くても正しく動けるべきである
- `token_stats.json` や `metrics.json` は **rebuildable artifact** である
- 将来的に最適化を入れる場合でも、canonical correctness は `parsed.jsonl` から検証可能でなければならない

### `thread_stats.json` の位置づけ

`thread_stats.json` は parse 時に生成される軽量な thread-local summary です。メッセージ数、文字数、タイムスタンプ範囲などを素早く参照するには便利ですが、**解析の真実源ではありません**。

将来的に Analyzer がこれをキャッシュや最適化のために参照する実装はあり得ます。ただしその場合でも、以下の原則は変わりません。

- `thread_stats.json` が欠けていても Analyzer は成立する
- `thread_stats.json` が古くても、正しさの最終判定は `parsed.jsonl` に戻れる
- canonical data と sidecar は混同しない

---

## 🚀 クイックスタート

### 1. セットアップ

依存管理には [`uv`](https://docs.astral.sh/uv/getting-started/installation/) を利用します。

```bash
uv sync
uv sync --extra dev
```

コマンドエイリアス:

`llp` は `llm-logparser` の簡易エイリアスです。既存のコマンドはどちらの実行名でも同じように利用でき、たとえば `llp parse ...` や `llp analyze ...` のように実行できます。

### 2. エクスポートを parse する

```bash
uv run llm-logparser parse \
  --provider openai \
  --input examples/messages.jsonl \
  --outdir artifacts
```

このコマンドは、プロバイダの生データを canonical thread records に正規化し、`artifacts/output/<provider>/thread-*/parsed.jsonl` を生成します。

### 3. 正規化済みスレッドを Markdown に出力する

```bash
uv run llm-logparser export \
  --input artifacts/output/openai/thread-abc123/parsed.jsonl \
  --timezone Asia/Tokyo \
  --formatting light
```

`export` は canonical data をもとに Markdown を生成します。タイムゾーン変換や分割出力は Exporter の責務です。

### 4. parse と export をまとめて実行する

```bash
uv run llm-logparser chain \
  --provider openai \
  --input examples/messages.jsonl \
  --outdir artifacts \
  --timezone Asia/Tokyo
```

`chain` は **parse → export** を一度に実行したい場合のためのコマンドです。

### 5. Analyze を実行する

まず canonical thread 全体に対する統計を確認できます。

```bash
uv run llm-logparser analyze stats \
  --input artifacts/output/openai
```

Analyze 系サブコマンドは、意図的に出力クラスが分かれています。

- `stats` / `timeline` は端末表示やファイル保存を前提とした **presentation output**
- `tokens` / `metrics` は各スレッド横に作られる **JSON sidecar**
- `sqlite-build` は単一の **SQLite database artifact**

Analyzer のレイヤーは次のように整理されています。

- L1 は deterministic analysis: `stats` / `timeline` / `tokens` / `metrics` / `datasheet`
- L2 は `sqlite-build`: **任意の deterministic な SQLite インデックス**
- L3 / L4 は将来の model-based layer（local / API）であり、現時点の CLI 機能ではありません

### 6. 推奨 sidecar ワークフロー

`metrics.json` は `token_stats.json` に依存するため、先に `analyze tokens` を実行してください。

```bash
uv run llm-logparser analyze tokens \
  --input artifacts/output/openai
```

書き込む前に sidecar 生成をプレビューしたい場合は、`--dry-run` を使います。

```bash
uv run llm-logparser analyze tokens \
  --input artifacts/output/openai \
  --dry-run
```

続いて `metrics.json` を生成します。

```bash
uv run llm-logparser analyze metrics \
  --input artifacts/output/openai
```

### 7. よく使う補助オプション

Markdown 分割は次のように指定できます。

```bash
--split size=4M
--split count=1500
--split auto
```

追加調整も可能です。

```bash
--split-soft-overflow 0.20
--split-hard
--tiny-tail-threshold 20
```

`chain` では以下のようなオプションが利用できます。

```text
--parsed-root       既存の parsed thread を再利用
--export-outdir     Markdown の出力先を別ルートに変更
--dry-run           parse フェーズのみ試行して書き込みを行わない
--fail-fast         export エラーで即停止
```

---

## 📁 ディレクトリ構造

標準的な出力構造は次のようになります。

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
        meta.json (optional)
```

### 各ファイルの役割

`manifest.json`
- provider 単位の軽量インデックス
- スレッド一覧、パス、件数などの把握に使える

`parsed.jsonl`
- canonical thread data
- parser が生成する正規データ
- 本プロジェクト全体の source of truth

`thread_stats.json`
- parse-time の thread-local summary
- 便利な補助メタデータだが canonical input ではない

`message_windows.jsonl`
- メッセージウィンドウ単位の deterministic artifact
- 下流処理や将来機能の足場となる

`token_stats.json`
- `analyze tokens` が生成する per-thread sidecar
- tokenizer ベースの集計情報を保持

`metrics.json`
- `analyze metrics` が生成する per-thread sidecar
- `token_stats.json` と canonical message text を使って派生指標を計算

`thread-<conversation_id>__*.md`
- Exporter が生成する Markdown ファイル
- 分割が有効な場合は複数ファイルになる

> `--outdir` には **ルートだけ** を渡してください。  
> 実際の `output/<provider>/...` 配下はツールが自動で構成します。

---

## 📝 Markdownフォーマット説明

出力される Markdown は **GitHub Flavored Markdown (GFM)** を前提にしています。

### Front Matter

各 Markdown ファイルの先頭には YAML front-matter が付きます。

```yaml
---
thread: "abc123"
provider: "openai"
messages: 42
models: ["gpt-4o"]
range: "2025-10-01T01:00:00+00:00 〜 2025-10-18T10:15:00+00:00"
---
```

この front-matter には、スレッド識別子、プロバイダ、メッセージ数、モデル情報、時刻範囲など、人間とツールの両方が参照しやすい最小限のメタデータを入れます。

### 本文構造

メッセージ本文は時系列順に並びます。

```markdown
## [User] 2025-10-18 10:00
Good morning!

## [Assistant] 2025-10-18 10:01
Good morning — how can I help today?
```

### 保持される表現

Markdown 出力では、会話内容の可読性を損なわないことを重視しています。

- fenced code block
- link
- table
- quote
- 基本的な段落構造

### 日時と整形

- 日時は `--timezone` に基づいて変換されます
- Markdown 自体は locale 依存で再整形されません
- `formatting` は最小限の整形ポリシーです
- 出力は GFM 互換を保つことを意図しています

Exporter の役割は、canonical data の意味を変えることではなく、**人間に読みやすく表現すること**です。

---

## 📊 Analyze機能（stats / timeline / tokens / metrics）

Analyze 系は、canonical data に基づく deterministic analysis のための機能群です。ここで最も重要なのは、**すべての正しさの起点が `parsed.jsonl` である** という点です。

### 共通ポリシー

- `parsed.jsonl` が canonical source である
- `thread_stats.json` は補助メタデータであり、canonical input ではない
- `token_stats.json` と `metrics.json` は rebuildable sidecar である
- sidecar が欠けていても、Analyzer の設計上の正しさは canonical data に戻れる必要がある

### `analyze stats`

`stats` は canonical `parsed.jsonl` を走査して、スレッド数、メッセージ数、文字数、ロール別件数、時刻範囲などを deterministic に集計します。

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

ポイント:

- 現在の実装は `thread_stats.json` を入力として使いません
- canonical `parsed.jsonl` から直接値を導出します
- `--json` は machine-readable な presentation output を得たい場合に使います
- `--out` は描画済み結果をファイルに保存したい場合に使います

### `analyze timeline`

`timeline` は canonical `parsed.jsonl` の時刻付きメッセージを UTC ベースでバケット集計します。

```bash
uv run llm-logparser analyze timeline \
  --input <parsed.jsonl-or-directory> \
  --bucket day \
  [--json] \
  [--out <path>]
```

利用できるバケットは以下です。

- `hour`
- `day`
- `week`
- `month`

`stats` と同様に、`timeline` も presentation-oriented なコマンドであり、結果を画面表示またはファイル出力します。

### `analyze tokens`

`tokens` は各スレッドの横に `token_stats.json` を生成します。

```bash
uv run llm-logparser analyze tokens \
  --input <parsed.jsonl-or-directory> \
  [--model <model>] \
  [--encoding <tiktoken-encoding>] \
  [--skip-existing] \
  [--dry-run]
```

このコマンドの特徴は次の通りです。

- `parsed.jsonl` を入力にして per-thread sidecar を生成する
- 出力は presentation text ではなく `token_stats.json`
- `--json` や `--out` ではなく、`--skip-existing` を持つ
- sidecar は既定で再生成・上書きされる
- `--skip-existing` を付けると既存 sidecar を温存し、欠けているものだけ作る

Tokenizer の現在のバックエンドは `tiktoken` です。

- `openai` / `anthropic` / `xai` には provider default がある
- `--encoding` は provider / model 解決を上書きする
- 初回利用時のみエンコーディング資産を取得するため、**一度だけネットワークアクセスが発生する可能性**がある
- 取得後はローカルキャッシュが使われるため、それ以降の token counting 自体は local / deterministic に動作する
- `--dry-run` を付けると、ファイルは書き込まずに sidecar 生成件数を確認できる

### `analyze metrics`

`metrics` は `parsed.jsonl` と `token_stats.json` をもとに `metrics.json` を生成します。

```bash
uv run llm-logparser analyze metrics \
  --input <parsed.jsonl-or-directory> \
  [--skip-existing] \
  [--dry-run]
```

重要:

- `metrics.json` の生成には、各スレッド横の `token_stats.json` が必要です
- そのため、通常は先に `analyze tokens` を実行します
- これも sidecar builder であり、presentation output ではありません
- `--dry-run` を付けると、書き込み前に sidecar 生成プレビューだけを実行できます

含まれる代表的な内容:

- ratio / token / character / distribution / diversity 指標
- `safety.refusal`
- `interaction.revision`
- `correction` / `clarification` / `retry` の subtype count

既定の挙動:

- 既存の `metrics.json` は再生成・上書きされます
- `--skip-existing` は既存 artifact を温存し、欠けたものだけ補います
- ただし、`metrics.json` が無いスレッドについては、必要な `token_stats.json` が存在しなければなりません

### Analyze の出力クラスの違い

Analyze サブコマンドは、見た目を統一するよりも、**生成物の性質をそのまま CLI に反映する** 方針です。

- `stats` / `timeline`
  - 端末表示または `--out` への保存
  - `--json` による機械可読な presentation output が利用可能
- `tokens` / `metrics`
  - スレッドディレクトリ内に sidecar JSON を生成
  - `--skip-existing` によって sidecar 再生成ポリシーを制御
- `sqlite-build`
  - 単一の `analysis.db` を作る L2 の補助コマンド
  - `--overwrite` で DB 再構築を制御

### Sidecar の位置づけ

`token_stats.json` と `metrics.json` は便利な機械可読 artifact ですが、canonical source ではありません。

- canonical input は `parsed.jsonl`
- sidecar は再構築可能
- sidecar の欠落はアーキテクチャ上の破綻を意味しない
- correctness を確認したいときは canonical data に戻れるべき

`analysis.db` も同様に、canonical source ではなく、L2 のための
**任意の deterministic / rebuildable なインデックス**です。

- `parsed.jsonl` を置き換えない
- `token_stats.json` / `metrics.json` を置き換えない
- 汎用の分析エンジンや、あらゆる派生成果物の保存先として扱わない
- より大きなデータセットでの問い合わせや探索を補助するための index layer として扱う

---

## 🌍 ローカライゼーション

`llm-logparser` の i18n は、完全翻訳を強制するモデルではなく、**best-effort で安全にフォールバックするモデル** です。locale file の欠落やキー不足は異常ではなく、通常の運用範囲として扱われます。

### 何がローカライズされるか

ローカライズ対象:

- CLI の help / runtime / error メッセージ
- Analyzer の heuristic phrase resource

ローカライズ対象外:

- `analyze stats` と `analyze timeline` の英語テキスト要約
- JSON artifact のキー名
- 安定した machine-readable schema
- argparse 由来の `usage:` や built-in boilerplate
- Markdown の日時表現そのもの

### 指定方法

```text
--locale   en-US | ja-JP | …
--timezone Asia/Tokyo | UTC | …
```

### locale file の配置

```text
src/llm_logparser/i18n/*.yaml
```

locale YAML は次のようなトップレベル構造を持ちます。

- `messages:`  
  CLI / help / runtime / error 用の文字列
- `analysis:`  
  refusal / revision などのヒューリスティクスに使う構造化リソース

### 優先順位

通常の locale 解決順序は次の通りです。

1. CLI `--locale` / `--lang`
2. 環境変数 `LLP_LOCALE`
3. 選択された profile の `locale`
4. `en-US`

### フォールバック動作

- 未知の locale は `en-US` に解決される
- `messages:` のキー欠落は `en-US`、それでも無ければ raw key にフォールバックする
- `analysis:` のキー欠落は `en-US` にフォールバックする
- `en` や `ja` のような短縮名は、一意に解決できる場合に自動導出される
- 同じ言語プレフィックスを複数 locale file が共有する場合は、完全な locale tag を使う

### Analyzer 用 YAML カスタマイズ

Analyzer の phrase-based heuristic は YAML から調整できます。代表的なキーは以下です。

- `analysis.refusal.indicators`
- `analysis.revision.cues`
- `analysis.correction.cues`
- `analysis.clarification.cues`

方針:

- CLI 文言を変えたいなら `messages:` を編集する
- 組織固有の言い回しやドメイン表現を調整したいなら `analysis:` を編集する
- phrase list は大きくしすぎず、保守的に増やす方が誤検出を避けやすい
- まず YAML を調整し、それでも足りなければコード変更を検討する

### 現状の制約

- top-level config `locale` はまだない
- argparse built-in の完全な多言語化は未対応
- system locale の自動利用は行わない

---

## ⚙️ 設定（config.yaml）

`llm-logparser` は、CLI の既定値を YAML で与えるための `config.yaml` をサポートしています。これは実行時の利便性を上げる仕組みであり、**CLI 引数が常に最優先**です。

### 基本原則

- CLI flags が最優先
- profile の値は「足りない引数を埋める」ために使われる
- built-in CLI defaults は最後に適用される

優先順位:

```text
CLI flags > selected profile values > built-in CLI defaults
```

locale には追加ルールがあります。

```text
CLI --locale/--lang > LLP_LOCALE > selected profile locale > en-US
```

### config の探索順

`--config` を渡さない場合、設定ファイルは次の順で探索されます。

1. `--config <path>`
2. 環境変数 `LLM_LOGPARSER_CONFIG=<path>`
3. カレントディレクトリの `config.yaml`
4. 親ディレクトリ方向にたどって最初に見つかる `config.yaml`
5. `~/.config/llm-logparser/config.yaml`

設定ファイルが見つからなくても CLI は通常通り動作します。

### 最小構成例

```yaml
schema_version: 1
active_profile: default

profiles:
  default:
    provider: openai
    timezone: Asia/Tokyo
    locale: ja-JP

    input:
      path: exports/messages.jsonl

    sanitize:
      enabled: true
      replacement: REDACTED
      scope: content_parts

    parse:
      outdir: artifacts
      validate_schema: true

    output:
      path: artifacts/thread.md
      formatting: light
      split: auto
```

### Profile

複数 profile を定義し、`--profile <name>` で選択できます。

選択優先順位:

```text
--profile > active_profile > ひとつだけ定義されている profile
```

複数 profile が存在し、自動選択も明示指定もない場合:

- interactive mode では選択プロンプトが出る
- non-interactive mode では profile default は適用されない

### `input`

`input` は config-aware な各コマンドで共有されます。

```yaml
input:
  path: exports/messages.json
  parsed: artifacts/output/openai/thread-123/parsed.jsonl
```

意味:

- `input.path` / `input.paths` は `parse` / `chain` / `extract` 向け
- `input.parsed` は `export` 向け

### `output`

Exporter の既定値をまとめるセクションです。

```yaml
output:
  path: artifacts/thread.md
  formatting: light
  split: auto
```

補足:

- `output.path` が canonical な出力先キー
- `output.dir` はサポートしていない

### `parse` / `chain` / `extract`

コマンド別の既定値は、それぞれ対応するセクションに置きます。

```yaml
parse:
  outdir: artifacts
  validate_schema: true

chain:
  outdir: artifacts
  export_outdir: artifacts/markdown

extract:
  outdir: artifacts
  conversation_id: conv-123
```

### 後方互換キー

`schema_version: 1` では、古い profile-level key も後方互換のために受け付けています。ただし今後の cleanup で削除予定です。

代表例:

- `outdir` → `parse.outdir`, `chain.outdir`, `extract.outdir`
- `dry_run` → `parse.dry_run`, `chain.dry_run`, `extract.dry_run`
- `fail_fast` → `parse.fail_fast`, `chain.fail_fast`
- `validate_schema` → `parse.validate_schema`, `chain.validate_schema`
- `export_outdir` → `chain.export_outdir`
- `parsed_root` → `chain.parsed_root`
- `conversation_id` → `extract.conversation_id`

新規設定では section-based shape を使うことを推奨します。

### `sanitize`

`extract` のマスキング動作は `sanitize` セクションで制御します。

```yaml
sanitize:
  enabled: true
  replacement: REDACTED
  scope: content_parts
  extra_keywords: [credential]
  mask_patterns:
    - acct-\d+
```

`scope` は以下をサポートします。

- `content_parts`
- `all_strings`

既定動作:

- `sanitize` を省略しても `extract` の安全寄り既定値は有効
- フィールド名ベースの秘匿と、内蔵 email / phone パターンが適用される
- `mask_patterns` を指定すると、そのリストが実際の string masking に使われる
- `mask_patterns: []` にすると regex ベースの masking を止めつつ、field-name redaction は維持できる

### 相対パスの解決

`config.yaml` に書いた相対パスは、**その `config.yaml` が置かれているディレクトリ基準**で解決されます。カレントディレクトリ依存ではありません。

これは次のような運用で重要です。

```bash
LLM_LOGPARSER_CONFIG=/etc/llm/config.yaml
```

### config サブコマンド

設定解決を確認したいときは、軽量なサブコマンドを使えます。

```bash
uv run llm-logparser config path
uv run llm-logparser config show [--profile work]
uv run llm-logparser config validate
```

### Non-Interactive Mode

CI や自動処理向けにプロンプトを止めたい場合は、以下を使います。

```bash
--non-interactive
```

または:

```bash
LLM_LOGPARSER_NON_INTERACTIVE=1
```

non-interactive mode では、以下の条件で exit code `2` になります。

- 必須オプションが不足している
- 複数候補があり入力が曖昧である

---

## 🔒 セキュリティとプライバシー

`llm-logparser` は、対話ログという機密性の高いデータを扱う前提で設計されています。そのため、privacy-first と local-first を強く意識しています。

### 基本方針

- parse / export / 大半の analyzer workflow は offline-first
- テレメトリなし
- ログデータを外部送信しない
- 監査に向いた deterministic output
- 秘匿処理が必要な `extract` では sanitize が config-driven で既定有効

### `extract` のサニタイズ

`extract` 実行時には、適用した sanitize policy の概要を `extract.meta.json` に記録します。これにより、後から以下を確認できます。

- sanitize が有効だったか
- どの scope を使ったか
- どの replacement token を使ったか
- custom keyword / pattern を指定していたか

### `tiktoken` に関する注意

`analyze tokens` / `analyze metrics` は基本的にローカル処理ですが、`tiktoken` は初回使用時に encoding data を取得するため、一度だけネットワークアクセスが発生する可能性があります。取得後はローカルキャッシュが使われます。

### 監査可能性

このプロジェクトは「便利さのために正規データを曖昧にする」設計を避けています。

- canonical data は `parsed.jsonl`
- sidecar は再生成可能
- parser と analyzer の責務が分離されている
- data lineage を説明しやすい

これにより、ログ保管、検証、再解析、将来の移行がしやすくなります。

---

## 🗺 ロードマップ

### すでに実装済み

- [x] CLI MVP (`parse` / `export` / `extract` / `chain` / `analyze`)
- [x] スレッド分割対応 Markdown exporter
- [x] JSON Schema validation
- [x] 設定ファイル読み込み（auto-discovery + profiles）
- [x] Analyzer (`stats` / `timeline` / `tokens` / `metrics`)

### 近い将来の候補

- [ ] Anthropic / Claude 対応
- [ ] xAI / Grok 対応
- [ ] 正規化ログを閲覧するための VS Code Extension

### 中長期・探索的テーマ

- [ ] Gemini 対応
- [ ] GUI アプリケーション
- [ ] Viewer / downstream toolchain の拡張
- [ ] canonical data を土台にした研究・分析ユースケースの強化

---

## 🤝 コントリビューション

Issue、Discussion、Pull Request を歓迎します。特に次の領域は貢献しやすいポイントです。

- provider adapter
- exporter の改善
- localization
- analyzer の deterministic な改良
- ドキュメント整備

### コントリビューション時の原則

- deterministic core を壊さない
- provider-specific behavior は adapter に閉じ込める
- offline-first を既定方針とする
- canonical source と sidecar を混同しない
- parser / exporter / analyzer の責務分離を維持する

### テスト

変更前後の確認には、少なくともローカルでテストを回してください。

```bash
uv run pytest
```

ドキュメントだけの変更であっても、設計方針との整合は意識してください。特に `parsed.jsonl` を中心とした canonical-first の説明は、実装とずれないことが重要です。

---

## 📄 ライセンス

このプロジェクトは **MIT License** のもとで公開されています。  
利用、改変、再配布は比較的自由ですが、詳細はリポジトリ直下の `LICENSE` を確認してください。

---

## Author

> "The words you weave are not mere echoes;  
> they carry weight,  
> and may they never be lost to the tide of time."

© 2025 **Ashes Division — Reyz Laboratory**  
