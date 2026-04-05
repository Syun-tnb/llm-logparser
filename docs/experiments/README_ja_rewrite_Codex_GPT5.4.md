# llm-logparser

LLM の会話エクスポートを、そのまま読める Markdown と、あとから扱いやすい `parsed.jsonl` に整形するローカル CLI です。

1 回コマンドを実行すると、会話ごとのフォルダができて、

- 読むための Markdown
- 集計の元になる `parsed.jsonl`
- 追加分析に使う `thread_stats.json` や `message_windows.jsonl`

が手元に残ります。クラウド送信やテレメトリはありません。

---

## 目次

- [このツールでできること](#このツールでできること)
- [実行すると何が手に入るか](#実行すると何が手に入るか)
- [クイックスタート](#クイックスタート)
- [実行すると中で何が起きるか](#実行すると中で何が起きるか)
- [よくある使い方](#よくある使い方)
- [分析を使い分ける](#分析を使い分ける)
- [入力ルール](#入力ルール)
- [CLI リファレンス](#cli-リファレンス)
- [設定ファイル](#設定ファイル)
- [補足](#補足)

---

## このツールでできること

このツールは、ChatGPT などの会話エクスポートを次の形に変えます。

- 人がすぐ読める Markdown
- サービス差分をならした `parsed.jsonl`
- 件数や時系列を見られる分析用ファイル

向いている用途:

- 会話ログを読み返したい
- 会話を会話単位で整理したい
- トークン数や傾向を後から見たい
- ローカルだけで扱いたい

対応している元データ:

- OpenAI ChatGPT
- Anthropic Claude
- xAI Grok
- Mistral Le Chat
- Google Gemini My Activity

---

## 実行すると何が手に入るか

まずは、結果の形を先に見たほうが早いです。

```text
artifacts/
  openai/
    manifest.json
    thread-abc123/
      parsed.jsonl
      thread_stats.json
      message_windows.jsonl
      thread-abc123__gpt-4o.md
```

必要に応じて、あとから次のファイルも増やせます。

```text
artifacts/
  openai/
    thread-abc123/
      token_stats.json
      metrics.json

    analysis.db
    l3/semantic-topics/
```

見方:

- `parsed.jsonl`: 整形済みの会話データ本体
- `thread-*.md`: 読みやすい Markdown
- `thread_stats.json`: 会話ごとの件数や期間
- `message_windows.jsonl`: 会話を一定単位でたどるための補助データ
- `token_stats.json`: トークン数
- `metrics.json`: 傾向や指標
- `analysis.db`: SQLite でまとめて見るための任意の索引

実際のイメージは「生のエクスポートが、会話ごとのフォルダに整理される」と思えば十分です。

---

## クイックスタート

まず試すならこれだけで足ります。

```bash
pip install llm-logparser
```

```bash
# Minimal workflow
llm-logparser chain --provider openai --input <export> --outdir artifacts

# Analyze
llm-logparser analyze stats --input artifacts/openai
```

これで起きること:

1. 生のエクスポートを整形する
2. 会話ごとの Markdown を書き出す
3. `artifacts/openai/` に会話単位のファイル群が並ぶ
4. `analyze stats` で全体像をすぐ確認できる

`llp` という短い別名も使えます。

```bash
llp parse ...
llm-logparser analyze stats ...
```

---

## 実行すると中で何が起きるか

`chain` は、よくある「整形して、そのまま読める形にもしたい」を 1 回で済ませるコマンドです。

```bash
llm-logparser chain \
  --provider openai \
  --input path/to/conversations.json \
  --outdir artifacts \
  --timezone Asia/Tokyo
```

中でやっていることは 2 段階です。

### 1. 会話を整形する

- 生のエクスポートを読み込む
- 会話ごとに `thread-*` フォルダを作る
- `parsed.jsonl` を書く
- あわせて `thread_stats.json` と `message_windows.jsonl` も作る
- サービス単位の `manifest.json` も作る

### 2. 読みやすい Markdown を作る

- 各 `parsed.jsonl` から Markdown を生成する
- そのまま読めるログとして残す

つまり、

```text
生のエクスポート
  ↓
整形済みデータ
  ↓
読める Markdown
```

という流れです。

---

## よくある使い方

### 会話をまず読みたい

```bash
llm-logparser chain \
  --provider openai \
  --input <export> \
  --outdir artifacts
```

終わったら `artifacts/openai/thread-*/` の Markdown を開けば読めます。

### すでに整形済みで、Markdown だけ欲しい

```bash
uv run llm-logparser export \
  --input parsed.jsonl \
  [--out <md>] \
  [--split auto|size=N|count=N] \
  [--timezone <IANA>] \
  [--formatting none|light]
```

### まず全体の量だけ見たい

```bash
llm-logparser analyze stats --input artifacts/openai
```

わかること:

- 会話数
- メッセージ総数
- 文字数
- 期間
- 会話ごとの多さ

### 1 件だけ取り出したい

```bash
uv run llm-logparser extract \
  --provider openai \
  --input <file> \
  --conversation-id <id> \
  --outdir artifacts \
  [--dry-run]
```

---

## 分析を使い分ける

最初に覚えるなら、この 4 つで十分です。

| コマンド | 何を見るか | まず使う場面 |
| --- | --- | --- |
| `analyze stats` | 件数・期間・会話ごとの量 | 全体像を見たい |
| `analyze timeline` | 日・週・月ごとの動き | いつ使っていたか見たい |
| `analyze tokens` | トークン数 | コスト感や長さを見たい |
| `analyze metrics` | 傾向や指標 | やり取りの特徴を見たい |

### すぐ使う分析

#### 集計を見る

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

#### 時系列で見る

```bash
uv run llm-logparser analyze timeline \
  --input artifacts/openai \
  --bucket day \
  [--json] \
  [--out <path>]
```

#### トークン数を出す

```bash
uv run llm-logparser analyze tokens \
  --input <parsed.jsonl-or-directory> \
  [--model <model>] \
  [--encoding <tiktoken-encoding>] \
  [--skip-existing] \
  [--dry-run]
```

注意:

- 既定のトークン処理に対応しているのは `openai`、`anthropic`、`xai` です
- それ以外はエラーになります
- ただし `--encoding` を明示すれば上書きできます

#### 指標を出す

```bash
uv run llm-logparser analyze metrics \
  --input <parsed.jsonl-or-directory> \
  [--skip-existing] \
  [--dry-run]
```

これは `token_stats.json` が必要なので、先に `analyze tokens` を実行します。

### もう少し踏み込む分析

#### レポートを作る

```bash
uv run llm-logparser analyze datasheet \
  --input <parsed.jsonl-or-directory> \
  [--json] \
  [--out <path>]
```

#### SQLite でまとめて見る

```bash
uv run llm-logparser analyze sqlite-build \
  --input <artifact-root> \
  --provider <provider-id> \
  [--overwrite]
```

これは会話ごとのファイルを置いたルート、たとえば `artifacts/` を渡します。

#### 意味的な近さを見る

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

ここは任意機能です。最初は後回しでかまいません。

ポイント:

- 既定の `deterministic-hash` なら Ollama は不要です
- Ollama が必要なのは `--backend ollama` を使うときです

#### 保存済みの意味クラスタを眺める

```bash
uv run llm-logparser analyze semantic-preview \
  --input <provider-artifact-root> \
  [--top-clusters <N>] \
  [--min-cluster-size <N>] \
  [--cross-thread-only]
```

#### 話題データを作る

```bash
uv run llm-logparser analyze semantic-topics \
  --input <provider-artifact-root> \
  [--model <ollama-model>] \
  [--min-cluster-size <N>] \
  [--cross-thread-only]
```

#### 話題データをたどる

```bash
uv run llm-logparser analyze semantic-topic-explore \
  --input <artifact-root>
```

#### ローカルモデルで話題名を付ける

```bash
uv run llm-logparser analyze semantic-topic \
  --input <provider-artifact-root> \
  --model <ollama-model> \
  [--top-clusters <N>] \
  [--min-cluster-size <N>] \
  [--cross-thread-only]
```

---

## 入力ルール

どこに何を渡すのかは、ここだけ見れば大丈夫です。

| コマンド | 入力 |
| --- | --- |
| `parse` / `chain` | 生のエクスポート。ファイルでもディレクトリでも可 |
| `export` | 単一の `parsed.jsonl` |
| `extract` | 生のエクスポート。ファイルでもディレクトリでも可 |
| `analyze stats` / `datasheet` / `timeline` / `tokens` / `metrics` | `parsed.jsonl` または `parsed.jsonl` を含むディレクトリ |
| `analyze sqlite-build` | ディレクトリのみ。サービス単位の出力フォルダを含むルート |
| `analyze semantic-prototype` | `message_windows.jsonl` / `parsed.jsonl` / それらを含むディレクトリ |
| `analyze semantic-preview` / `semantic-topic` / `semantic-topics` / `semantic-topic-explore` | ディレクトリのみ |

迷ったときの覚え方:

- 生データなら `parse` / `chain`
- 整形済みデータなら `export` / `analyze`

---

## CLI リファレンス

### `parse`

生のエクスポートを整形済みの会話データに変換します。

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

### `chain`

整形と Markdown 書き出しをまとめて行います。

```bash
uv run llm-logparser chain \
  --provider openai \
  --input <raw> \
  --outdir artifacts \
  [--validate-schema] \
  [other export options...]
```

補足:

- `chain` も `parse` も `artifacts/<provider>/...` に出力します
- 以前の `output/` 中間フォルダは作りません

### `export`

整形済みの 1 会話を Markdown に書き出します。

```bash
uv run llm-logparser export \
  --input parsed.jsonl \
  [--out <md>] \
  [--split auto|size=N|count=N] \
  [--timezone <IANA>] \
  [--formatting none|light]
```

### `extract`

1 会話だけを取り出します。

```bash
uv run llm-logparser extract \
  --provider openai \
  --input <file> \
  --conversation-id <id> \
  --outdir artifacts \
  [--dry-run]
```

---

## 設定ファイル

コマンドだけでも使えますが、毎回同じ指定をするなら `config.yaml` が便利です。

```yaml
schema_version: 1
active_profile: default

profiles:
  default:
    provider: openai

    input:
      path: exports/messages.jsonl

    output:
      path: artifacts/thread.md
      formatting: light
      split: auto

    parse:
      outdir: artifacts
      validate_schema: true
```

優先順位:

```text
CLI の指定 > profile の設定 > 既定値
```

---

## 補足

### ローカル前提

- 基本動作はローカル完結です
- テレメトリはありません
- 機密ログを外に送りません

### 例外的な依存

- `analyze tokens` では `tiktoken` を使います
- 初回だけエンコーディング取得の通信が発生することがあります
- その後はローカルキャッシュが使われます

### どこから始めるべきか

迷ったらこの順番です。

1. `chain` で Markdown を作る
2. `analyze stats` で全体を見る
3. 必要なら `analyze tokens`
4. さらに必要なら `analyze metrics`

---

## 開発時の実行

リポジトリを clone して使う場合:

```bash
uv sync
uv sync --extra dev
uv run pytest
```

