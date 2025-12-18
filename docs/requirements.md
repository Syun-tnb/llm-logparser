# LLM Log Parser — 要件定義（MVP）

## 1. ゴール / スコープ

* 各LLMサービスのエクスポート（JSON/JSONL/NDJSON）を読み込み、
  重複のない会話ログをスレッド単位で **Markdown(GFM)** に出力する。
* **CLIで完結するMVP**（Parser → Exporter）。  
  Viewer は **Exporter が生成した Markdown を読み、HTMLを生成** し、HTMLビューアを提供する。
  更に、将来的に **GUIを含むアプリケーション** として実装することを念頭に置く。
* 将来的な拡張を見据え、**マルチプロバイダ対応**・**多言語対応（i18n/L10n）**・**例外契約（JSONエラー）**・**ランタイム設定の永続化**を設計に含める。
* **Apps SDK統合を見据えた関数/API設計**（入出力をJSON Schema化）を行う。

---

## 2. 入力

### 2.1 共通仕様

#### 対応フォーマット
- JSON / JSONL / NDJSON（UTF-8、BOMなし推奨）
- 各プロバイダが出力する「会話ログ全体のエクスポート」を前提とする。
  差分ではなく **常に全スレッドを含む** 形式を取り扱う。

#### エクスポートの特性
- 巨大ファイル・壊れ行・制御文字・多言語本文（絵文字含む）を想定。
- エクスポート内容にはメタ情報（title, create_time, update_time, model など）が混在する。
- 1回のエクスポートで複数スレッド（conversation）が含まれる。
- parserはこれをスレッド単位に分割して出力する（→ Exporterの入力単位）。

#### 識別キー
| 論理キー | 役割 |
|-----------|------|
| `conversation_id` | スレッド（会話）を一意に識別 |
| `message_id`      | 各発話（メッセージ）を一意に識別 |
| `provider_id`     | プロバイダを識別（例：openai, claude, gemini）|

※ 内部では `{provider_id}:{conversation_id}` / `{provider_id}:{message_id}` の複合キーを用いて衝突を回避。

#### 共通パースポリシー
- **parserは構造抽出のみを行い、値の変換や整形は一切しない。**
- Unicode エスケープ（`\uXXXX`）はデコードせず、そのまま保持する。
- JSON出力時は `ensure_ascii=True` でASCIIエスケープを維持。
- 欠損値は `null` または `{}` のまま保持。
- parserの出力は「1スレッド = 1 JSONLファイル」「1 メッセージ = 1 行」で構成する。
- 各ファイルの1行目はスレッドメタ情報（`record_type: "thread"`）とし、2行目以降にメッセージ行（`record_type: "message"`）を続ける。

#### 設計指針まとめ

* **Parser は「構造の抽出のみ」**
* **Exporter が「text の flatten + 可読整形」に責務集中**
* **adapter で provider 差を吸収**
* **JSONL (canonical schema) が唯一の正式 contract**
* **manifest / meta.json は contract の外側にある補助情報**

これにより：

* Multi-provider が低コストで追加可能
* Exporterの改善が既存パイプラインを壊さない
* Apps SDK / GUI との連携が容易になる

---

## 2.2 Parser / Adapter / Exporter / Viewer の責務分離

本システムは Provider 差異を吸収しつつ将来拡張性を確保するため、
**4層に明確に責務を分離するアーキテクチャ**を採用する：

1. **Provider Adapter 層**
2. **Core Parser 層**
3. **Exporter 層**
4. **Viewer 層（将来統合）**

### (1) Provider Adapter の責務

* Provider 固有の構造（OpenAI の mapping、parts[]、metadata 等）を **展開のみ** する。
* Transformer 的な「内容の変換」「加工」は行わず、**構造を保った dict に正規化**する。
* 統一スキーマ（normalized schema）の **dict を1件ずつ yield** する。

### (2) Core Parser の責務

* Adapter から受け取った normalized dict を **JSONL（thread+messages）** として吐き出す。
* ここは **唯一の中間表現（canonical intermediate format）** を生成する層。
* canonical schema は **v1 系は固定仕様** とし互換性を重視。
* Parser は text を結合しない（parts → text は Exporter の仕事）。

### (3) Exporter の責務

* JSONL を読み込み、Markdown に可読整形する。
* **text 生成（parts の join）** はここで初めて行われる。
* Provider 差の整形はここで吸収する。
* YAML Front Matter などの「出力表現」を担当。

### (4) Viewer の責務

* Exporter の成果物（Markdown）からHTMLを生成しブラウザで表示する。
* Parser や Exporter の構造には干渉しない。

---

### 2-1. OpenAI（ChatGPT エクスポート仕様）

#### 概要
OpenAI ChatGPT のエクスポートJSONは `conversations.json` に全スレッドを含む形式で提供される。  
各スレッドは `"mapping"` キー配下に複数ノードを持ち、各ノードが一つのメッセージを表す。

#### 主な構造例
```json
{
  "id": "<conversation_uuid>",
  "title": "<string>",
  "create_time": <float>,
  "update_time": <float>,
  "mapping": {
    "<message_uuid>": {
      "id": "<message_uuid>",
      "message": { ... },
      "parent": "<parent_uuid>",
      "children": [ ... ]
    }
  }
}
```

#### 特記事項

| 項目                    | 内容                                                                                                                |
| --------------------- | ----------------------------------------------------------------------------------------------------------------- |
| **mapping展開**         | `"mapping"` 配下の各ノードを走査し、`message_id` 単位で正規化。親子関係（`parent` / `children`）は `extra.relations` に格納。                   |
| **content構造**         | 各 message は `"content": {"content_type":"text","parts":[...]}` を持つ。parser はこの構造を改変せず保持し、flatten は exporter が担当する。 |
| **Unicode表現**         | 本文の Unicode エスケープ（`\uXXXX`）はデコードせず保持。parser 出力は ASCII 準拠とする。                                                      |
| **メタ情報**              | `create_time` と `update_time` は thread レベルのみに存在する場合がある。message 側が null のときは thread 値で補完可能。                        |
| **model / author正規化** | `"model_slug"` → `"model"`、`"author.role"` → `"author_role"` に変換。adapter 層で実施。                                    |
| **出力単位**              | 1 conversation = 1 thread として `thread-{conversation_id}/parsed.jsonl` を生成。1 行目に thread メタ、以降に message 行を並べる。      |

#### 注意

ChatGPT のエクスポートは毎回全スレッドを含む。
差分更新は行わず、`update_time` を基準に REPLACE / SKIP 判定を行う（詳細は §8.1 参照）。

---

## 3. アーキテクチャ（MVP）

### 方針：CLI/GUI 分離と Core 共有（リポジトリ方針）

本プロジェクト（本リポジトリ）は **OSS の CLI 版として完結**する。
販売目的の GUI 版（フル機能UI）は **別リポジトリ**として構築し、本リポジトリが提供する **Core 層**を依存して実装する。

> 注：本リポジトリには、必要に応じて **閲覧専用の軽量 Viewer（静的HTMLなど）** を含めてもよい。
> ただし編集機能や課金導線など **GUI固有の責務は含めない**。

---

### リポジトリ境界

#### CLI リポジトリ（本リポジトリ）

* **Core（再利用可能な共通ライブラリ）**
* **CLI（Interface 層）**：引数／設定ロード／起動制御
* Provider Adapter、Config、Cache、Error Contract、i18n
* ※ GUI（リッチUI / Viewerアプリ等）は含めない（閲覧専用の簡易Viewerは任意）

#### GUI リポジトリ（別リポジトリ）

* GUI Interface（Desktop / Web など）
* 本リポジトリの Core を依存として参照し、GUI側で表示／操作／課金導線などを実装する

---

### 層構造（MVP）

```text
┌────────────────────────────┐
│      Interface 層（CLI）     │  ← 引数/設定ロード/起動制御のみ
├────────────────────────────┤
│    Provider Adapter 層       │  ← Provider固有構造の展開・正規化
├────────────────────────────┤
│      Core 層（共通ライブラリ）│  ← parse/exportの純粋処理・契約の中心
└────────────────────────────┘
```

* Core は I/O 境界を明確化し、GUI からも呼び出せる API として提供する。
* CLI は Core API を呼び出す薄いラッパーに徹し、GUI 固有の機能は持たない。

---

### Core API 指針

* Core は “データ変換の中心” とし、GUI/CLI のいずれにも依存しない。
* Core の入出力は JSON Schema（または同等の契約）で固定し、互換性を優先する。
* 文字列化（flatten）や可読整形は Exporter 側の責務とし、Core は構造保持を最優先する。

---

### 3.1 Core（共通層）

#### 役割
- **ストリーム読取**：巨大ファイルでも逐次処理できる構造を維持。
- **正規化スキーマの適用**：Provider Adapterから受け取った各メッセージを共通スキーマへ変換。
- **中間出力（JSONL）生成**：各スレッド単位で `parsed.jsonl` を生成（1行目thread行＋message行×N）。
- **重複排除／スレッド分割**：`(provider_id, conversation_id, message_id)` でユニーク化し、スレッドごとに分割。
- **Apps SDK統合前提の関数設計**：`parse_logs()` のような純粋関数化を想定（I/O副作用を限定）。

#### 出力例
```

artifacts/output/openai/thread-<conversation_id>/parsed.jsonl

```

#### 設計方針
- parserは**構造の抽出のみ**を行う。値の変換・デコードは行わない。
- Unicodeエスケープ（`\uXXXX`）は**デコードせず**そのまま出力。
- JSON出力は `ensure_ascii=True`。
- 正規化後の1レコード = 1メッセージ。  
  各ファイルの1行目は `record_type: "thread"` のメタ行。

---

### 3.2 Provider Adapter 層

#### 役割
- 各プロバイダ固有の構造・命名・階層を吸収し、Coreで扱える形式に変換。
- 実装は「adapterモジュール」として分離（例：`providers/openai/adapter.py`）。
- YAML / JSON のマッピング設定によってフィールド差異を調整可能。

#### OpenAI（ChatGPT）Adapter の特徴
- `mapping` 階層を展開してメッセージノードを抽出。
- 各ノードの `message.content` 構造（`content_type`, `parts[]`）を保持したまま出力。
- `author.role` / `model_slug` などの命名差異を正規化（`author_role`, `model`）。
- `parent` / `children` 関係を `extra.relations` に格納。
- `create_time`, `update_time` は存在しない場合 thread 値で補完。

#### 将来拡張
- Claude / Gemini / Perplexity なども同一インタフェースで実装可能。
- provider追加時は adapter のみ新規追加すればCore側変更不要。

---

### 3.3 Exporter 層

#### 役割
- Coreの出力した `parsed.jsonl`（thread単位）を **Markdown(GFM)** に変換。
- `record_type: "thread"` 行をヘッダー情報として利用し、以降の `message` 行を整形。
- 分割条件（サイズ・件数・日付）を適用して複数ファイルに出力。

#### 主な責務
| 処理 | 内容 |
|------|------|
| グルーピング | conversation_id単位で読込 |
| ソート | ts（時刻）順に整列 |
| 整形 | content.parts[] を join、改行・引用処理などを適用 |
| 出力 | Markdown ファイル生成（UTF-8） |

#### 出力構成例
```

artifacts/output/openai/thread-<conversation_id>/thread-<conversation_id>__2025-10-18_part01.md

```

#### 注意
Exporterは**Coreの正規化結果を改変しない**。  
すべての加工は読み取り時に動的に行う。

---

### 3.4 Config 層

#### 役割
- 各プロバイダのフィールドマッピング、正規化ルール、分割閾値などを外部YAML/JSONで保持。
- CLI引数・環境変数・設定ファイルのマージ順序で最終値を決定。
- コード改変なしで設定変更が可能。

#### 設定例
```yaml
provider: openai
fields:
  author_role: message.author.role
  model: message.metadata.model_slug
content_policy:
  keep_unicode_escape: true
  flatten_parts_in_exporter: true
split_policy:
  by: none
  size_mb: 20
```

---

### 3.5 Viewer（HTMLビューア）

#### 役割

* Exporterで生成された **Markdown(GFM)** からHTMLを生成し、閲覧するビューア。
* index.html + menu.html + page.html のテンプレート構成。
* 検索窓・スレッド一覧・詳細表示を備え、クライアントサイドのみで動作。

#### 位置づけ

* Parser / Exporter とは非同期。生成済みデータを読むだけの層。
* 将来的に Apps SDK へUIウィジェットとして統合可能。

---

### 3.6 データフロー（全体像）

```
[ CLI入力 / Config ]
        │
        ▼
[ Provider Adapter ]
   (mapping展開・正規化)
        │
        ▼
[ Core Parser ]
   (JSONL出力: thread+messages)
        │
        ▼
[ Exporter ]
   (Markdown整形)
        │
        ▼
[ Viewer ]
   (HTML生成・表示)
```

---

### 3.7 設計思想まとめ

* **抽出と整形を明確に分離。**

  * Parser = 「構造理解と正規化」
  * Exporter = 「表現変換とレンダリング」
* **Provider差をAdapterで吸収。**
* **Unicodeや改行などの表現はExporter責務。**
* **スレッド単位出力を徹底し、並列処理・差分更新・キャッシュを容易化。**
* **Apps SDK / GUI統合を前提に、I/O境界を関数化する。**


---

## 4. マルチプロバイダ要件（統一スキーマとAdapter設計）

### 4.1 概要

本システムは複数のLLMサービス（OpenAI, Claude, Gemini, Perplexity 等）からの
エクスポートJSONを統一形式へ正規化することを目的とする。

初期実装は **OpenAI（ChatGPT）** に特化するが、
構造的な差異を「Provider Adapter 層」で吸収する設計とする。

各Adapterは「生データ → 統一スキーマ（Normalized JSON）」への変換のみを担当し、
Parser/Core層はAdapter出力に依存して動作する。

---

### 4.2 統一スキーマ（Normalized Schema）

Core層が受け取る標準スキーマは以下とする。
各Adapterはこれに準拠したdictを返すこと。

| フィールド | 型 / 内容 | 説明 |
|-------------|------------|------|
| `provider_id` | `str` | プロバイダ識別子（例：`openai`, `claude`, `gemini`） |
| `conversation_id` | `str` | スレッド単位の一意ID |
| `message_id` | `str` | メッセージ単位の一意ID |
| `ts` | `int` | UNIX epoch（ミリ秒） |
| `author_role` | `"user" / "assistant" / "system" / "tool"` | 役割識別 |
| `author_name` | `str|null` | 名前（存在すれば） |
| `model` | `str|null` | 使用モデル名（例：`gpt-4o`, `claude-3-opus`） |
| `content` | `object` | 本文構造。`content_type`, `parts[]`, `text` などを保持（flatten禁止）。 |
| `attachments` | `array|null` | 添付情報（URL, ファイルID等）。任意。 |
| `relations` | `object|null` | `parent`, `children` の親子関係情報。 |
| `extra` | `object|null` | provider固有の補助情報（metadata等）。 |
| `record_type` | `"thread" / "message"` | Coreが識別する行種別（threadメタ / メッセージ本文） |

#### スキーマ方針
- `content` は構造体のまま保持。文字列化（`join(parts)`）は Exporter 層で行う。
- Unicode エスケープ（`\uXXXX`）はデコードしない。  
  Parser出力は `ensure_ascii=True` でASCII互換を保証。
- 欠損値は `null` または `{}` を保持し、削除・変換を行わない。
- 1 conversation = 1 JSONLファイル（1行目 thread 行、以降 message 行）。

---

## 4.3 canonical normalized schema（中間表現仕様）

MVP での canonical schema を固定し、将来のバージョニングの基点として扱う。

### thread レコード（record_type="thread"）

```json
{
  "record_type": "thread",
  "provider_id": "openai",
  "conversation_id": "<uuid>",
  "title": "<string|null>",
  "ts_first": 1700000000.0,
  "ts_last": 1700009999.0,
  "message_count": 152,
  "extra": {...}   // provider 固有
}
```

### message レコード（record_type="message"）

```json
{
  "record_type": "message",
  "provider_id": "openai",
  "conversation_id": "<uuid>",
  "message_id": "<uuid>",
  "ts": 1700000123.12,
  "author_role": "user|assistant|system|tool",
  "author_name": "<string|null>",
  "model": "<string|null>",
  "content": {
    "content_type": "text",
    "parts": ["...", "..."],   // flatten禁止
    "text": null               // v1.xではExporter側で生成するためnull固定
  },
  "relations": {
    "parent": "<uuid|null>",
    "children": ["..."]
  },
  "extra": {...}
}
```

### 仕様方針

* **content.parts は必須（providerが持つ場合）**
* **content.text は v1.x では常に null（または absent）**

  * flatten は Exporter の専用責務
* Unicode decode は禁止（`\uXXXX` を保持）
* 1 conversation = 1 JSONL
* JSONL は **中間形式（contract）として全コンポーネントで共有**

---

### 4.4 Provider Adapter 設計

#### Adapterの目的
各Providerの構造差を吸収し、Coreが扱える共通スキーマdictを返す。

#### Adapter 実装規約
- 各Adapterは `providers/<id>/adapter.py` に配置。
- 以下の関数を最低限実装する：

```python
def normalize_thread(raw_thread: dict) -> dict:
    """スレッド（会話単位）のメタ情報を抽出し、record_type='thread'形式で返す。"""

def normalize_messages(raw_thread: dict) -> Iterable[dict]:
    """スレッド配下のメッセージ群を正規化し、record_type='message'形式でyieldする。"""
```

* 各関数は統一スキーマのキー構造を満たすこと。
* AdapterはProvider固有の構造をflattenせず、Core側に委譲できる最小変換を行う。

---

### 4.5 OpenAI（ChatGPT）Adapterの要点

| 項目        | 内容                                                                     |
| --------- | ---------------------------------------------------------------------- |
| 構造        | `mapping` 配下に複数ノードを持つ階層構造。各ノードに `"message"` が含まれる。                     |
| 展開処理      | Adapterが `mapping` を展開し、`message_id` ごとに抽出。                            |
| content   | `"content": {"content_type":"text","parts":[...]}` を保持。`flatten`禁止。    |
| model正規化  | `"model_slug"` → `"model"`、 `"default_model_slug"`をフォールバック。            |
| author正規化 | `"author.role"` → `"author_role"`、 `"name"` → `"author_name"`。         |
| 親子関係      | `"parent"` / `"children"` → `relations.parent` / `relations.children`。 |
| Unicode   | 文字列値は `\uXXXX` エスケープを維持（変換禁止）。                                         |
| 時刻補完      | `message.create_time` が null の場合、thread.create_time を補完可能。             |

---

### 4.6 Provider登録と選択方式

* CLIまたは設定ファイルにより、対象providerを指定する。

  ```bash
  --provider openai
  ```
* Coreは `providers/<id>/adapter.py` を動的ロードし、`normalize_thread` / `normalize_messages` を呼び出す。
* Adapterが存在しない場合は LP3xxx（Provider設定不一致）エラーを返す。
* Providerは独立モジュールとして追加可能。CoreやExporterの修正は不要。

---

### 4.7 責務分離ルール

| 層                    | 役割                                   | 備考              |
| -------------------- | ------------------------------------ | --------------- |
| **Provider Adapter** | 生データの展開・正規化・型整形                      | 値の変換は最小限、構造保持優先 |
| **Core Parser**      | Adapter出力を受け取り、JSONL化・スレッド分割・キャッシュ更新 | 改変禁止、構造の検証のみ    |
| **Exporter**         | contentのflatten（parts結合）とMarkdown整形  | 可読化と表現統一の責務     |
| **Viewer**           | Exporter出力の静的閲覧                      | 構造の編集は行わない      |

---

### 4.8 将来拡張指針

| Provider       | 想定構造                           | 特記事項                                           |
| -------------- | ------------------------------ | ---------------------------------------------- |
| **Claude**     | `conversation.messages[]` 構造   | message-levelでparent_id保持。adapterでrelations構築。 |
| **Gemini**     | `candidates[].content.parts[]` | Markdown・text・inline_image混在を想定。               |
| **Perplexity** | `history[].turns[]`            | 非同期ID・timestamp揺れを吸収。                          |

将来のAdapter追加は **スキーマ定義の変更不要**。
Core / Exporter / Viewer は provider非依存で動作する。

---

## 4.9 schema versioning — 破壊的変更の扱い

canonical schema は将来の互換性のため、明確な versioning を採用する。

### versioning ルール

1. **破壊的変更（breaking change）**
   → `schema_version` メジャーバンプ
   例: 1.x → 2.0
   変更例：

   * content.text を新設して parts と二重保持
   * ts の精度を変更
   * record_type の構造変更

2. **拡張（非破壊的 change）**
   → マイナーバンプ
   例: 1.3 → 1.4
   変更例：

   * extra フィールドに新サブキー追加
   * 新しい provider の追加

3. **ドキュメント整備のみ**
   → パッチバンプ
   例: 1.3 → 1.3.1

### 書き込み場所

* schema_version は
  `parsed.jsonl` 最初の thread レコードか
  `meta.json` に書き込む。

---

## 4.10 将来の content フィールド拡張（vNext）

v1.x の content は構造保持を最優先し、加工はしない。
次期版（vNext 2.x）では、次のような方向性で拡張可能とする：

* `content.text`: flatten 済みテキスト（Exporter と同規則で生成）
* `content.blocks`: ChatGPT Block API に似た階層構造
* `content.annotations`: code / math / quote / image の識別情報

これらは **完全に optional** とし、
v1.x のスキーマに対して互換性を壊さない方針で設計する。

---

### 4.11 設計原則まとめ

* すべてのProviderは「**Adapterで吸収、Coreは構造に依存しない**」方針を徹底。
* 統一スキーマは「可読ではなく、安定であること」を重視。
* flatten・デコード・整形はExporter責務。
* parser出力は常にスレッド単位のJSONL構造で統一する。
* provider固有項目はすべて`extra`または`relations`に格納し、スキーマ拡張を避ける。

---

## 5. 処理フロー

### 5.1 全体概要

本章では、CLI起動からMarkdown出力までの一連の処理フローを定義する。

処理は下記5段階で構成される。

```

[Input File]
↓
[Provider Adapter]
↓
[Core Parser]
↓
[Exporter]
↓
[Viewer]

```

各層の責務を明確に分離し、Provider差やファイル構造差を吸収したうえで、
スレッド単位のJSONL → **Markdown（GFM）** 変換を行う。

---

### 5.2 Provider Adapter フェーズ

#### 目的
各プロバイダ固有の構造差（階層、命名、フィールド仕様）を吸収し、
Core Parser に渡せる統一スキーマへ変換する。

#### 主な処理
- `conversations.json` 等の入力をロード。
- 各スレッド単位で展開（OpenAIの場合は `mapping` 階層を走査）。
- `message` オブジェクトを抽出し、`normalize_thread()` および `normalize_messages()` を適用。
- 構造は保持し、flatten やデコードは行わない。
- 各レコードに `record_type` を付与（`thread` or `message`）。

#### 出力
Provider Adapter の出力は Python dict のストリーム（イテレータ）として Core に渡される。

---

### 5.3 Core Parser フェーズ

#### 目的
Adapter出力を受け取り、スレッド単位の正規化JSONLを生成する。

#### 主な処理
- **ストリーム読取**：巨大JSONでも逐次処理できるように generator ベースで処理。
- **スレッド単位出力**：`conversation_id` ごとに 1ファイルの `parsed.jsonl` を生成。
- **メタ行出力**：1行目に `record_type: "thread"` のスレッドメタ情報を出力。
- **メッセージ行出力**：以降の各messageを 1行ずつ出力。
- **重複排除**： `(provider_id, conversation_id, message_id)` をキーに既処理キャッシュを参照し、重複スキップ。
- **Unicode保持**：`\uXXXX` エスケープを維持。`ensure_ascii=True`。
- **スキーマ検証**：Adapter出力が統一スキーマに準拠しているか検証（LP4xxx範囲で例外化）。

#### 出力例
```

artifacts/output/openai/thread-6811ff1a-2bac-8005-a2ae-5d8e63d7ee3e/parsed.jsonl

```

#### 出力構造
```json
{"record_type": "thread", "provider_id": "openai", "conversation_id": "...", "title": "..."}
{"record_type": "message", "conversation_id": "...", "message_id": "...", "content": {"content_type": "text", "parts": ["..."]}}
...
```

#### キャッシュ連携

* Parserはキャッシュ層を参照し、既処理スレッドをスキップ。
* 新しい `update_time` が確認された場合のみ `REPLACE` 出力を行う。
* キャッシュ更新はスレッド単位で行う（詳細は §8.1 参照）。

---

### 5.4 Exporter フェーズ

#### 目的

Parserが出力したスレッド単位JSONLをMarkdown形式に変換する。

#### 主な処理

* **入力**：`parsed.jsonl` を読み込み、`record_type` でthread/messageを判別。
* **グルーピング**：`conversation_id` ごとにまとめ、`ts`順にソート。
* **flatten**：`content.parts[]` を結合して本文を生成（この段階で初めて文字列化）。
* **分割出力**：分割条件に従い、Markdownを複数ファイルに出力。

  * `--split-by size|count|date|none`
  * `--split-size-mb`, `--max-msgs-per-file`, `--split-by-date` に基づく。
* **ファイル命名規則**：

  ```
  thread-{conversation_id}__{chunk_key}.md
  ```

  例：

  ```
  thread-6811ff1a-2bac-8005-a2ae-5d8e63d7ee3e__2025-10-18_part01.md
  ```
* **メタ情報整形**：各Markdown冒頭にスレッド情報を挿入。
  （title, provider, message_count, date_range, model 等）

#### 出力ディレクトリ構成

```
artifacts/output/{provider_id}/thread-{conversation_id}/
 ├── parsed.jsonl
 ├── thread-{conversation_id}__2025-10-18_part01.md
 └── meta.json（任意）
```

---

### 5.5 Viewer フェーズ（参照）

Exporterの出力したMarkdown（GFM）からHTMLを生成し、HTMLビューアを提供。一覧・検索・閲覧機能を有する。
ViewerはParser/Exporterとは非同期である。

---

### 5.6 エラーと例外ハンドリング

* 各フェーズで発生した例外は構造化エラー（JSONペイロード形式）で伝播。
* Adapter／Parser／Exporterはいずれも例外を握りつぶさず、LPコード体系に準拠してraise。
* WARN / ERROR / FATAL の分類と出力ポリシーは §10 に従う。

---

### 5.7 フローチャート（概略）

```
┌───────────────────────────┐
│         CLI起動・設定読込        │
└──────────────┬────────────┘
               ▼
┌───────────────────────────┐
│     Provider Adapter       │
│  - mapping展開（OpenAI）     │
│  - 正規化dict生成            │
└──────────────┬────────────┘
               ▼
┌───────────────────────────┐
│        Core Parser         │
│  - JSONL出力（thread+msg） │
│  - キャッシュ参照＆更新     │
└──────────────┬────────────┘
               ▼
┌───────────────────────────┐
│         Exporter           │
│  - flatten & sort          │
│  - Markdown出力            │
└──────────────┬────────────┘
               ▼
┌───────────────────────────┐
│           Viewer           │
│  - HTML生成・表示・検索      │
└───────────────────────────┘
```

---

### 5.8 設計方針まとめ

* Adapter → Parser → Exporter → Viewer の責務を明確に分離。
* Parserは**JSON構造の保持と正規化のみ**。整形はExporterに委譲。
* Exporterは**可読性・出力制御**に専念し、構造を改変しない。
* すべての処理はスレッド単位で完結し、差分判定・並列化を容易にする。
* エラーはすべてJSON構造で返却し、CLIまたはViewerで統合的に解釈する。

---

## 6. 出力（ファイル／フォーマット）

### 6.1 概要

本章では、ParserおよびExporterが生成するすべての出力成果物の構造・命名・フォーマット規約を定義する。  
目的は、ViewerやApps SDKなど他コンポーネントが一貫して参照できる「出力契約（Output Contract）」を確立することである。

---

### 6.2 出力ディレクトリ構成

出力ルートは以下の通りとする：

```

artifacts/output/{provider_id}/thread-{conversation_id}/
├── parsed.jsonl
├── thread-{conversation_id}__{chunk_key}.md
├── meta.json（任意）
└── （将来）attachments/...

```

| 要素 | 説明 |
|------|------|
| `parsed.jsonl` | Parserの成果物。スレッド単位で1ファイル。 |
| `thread-...__{chunk_key}.md` | Exporterが生成するMarkdown本体。 |
| `meta.json` | 出力メタ情報（件数・期間・分割情報）。MVPでは任意、将来Viewer必須化予定。 |
| `attachments/` | 将来の拡張用。MVPでは未生成。 |

---

### 6.3 JSONL出力（parsed.jsonl）

#### 構造
Parserは、スレッド単位の正規化JSONLを出力する。

```json
{"record_type": "thread", "provider_id": "openai", "conversation_id": "xxx", "title": "..." }
{"record_type": "message", "conversation_id": "xxx", "message_id": "ef0b3a81-...", "content": {"content_type": "text","parts": ["..."]}, "author_role": "assistant", "model": "gpt-4o", "ts": 1746009883.35}
...
```

#### 特徴

* 1行目にスレッドメタ（record_type: "thread"）を配置。
* `message` はスレッドに属する各発言を1行ずつ格納。
* Unicodeはエスケープ維持（`ensure_ascii=True`）。
* 内容はProvider Adapterが生成した統一スキーマ準拠。
* このJSONLはExporterやViewerが直接読み込める正式成果物とする。

---

### 6.4 Markdown（GFM）出力（Exporter）

#### 目的

JSONLを可読なドキュメント形式に変換する。
本ファイルは最終成果物として人間・Viewer双方から参照される。

#### 命名規則

```
thread-{conversation_id}__{chunk_key}.md
```

| 要素                  | 内容例                                           |
| ------------------- | --------------------------------------------- |
| `{conversation_id}` | 元スレッドID（UUIDなど）                               |
| `{chunk_key}`       | 分割識別子（例：`2025-10-18_part01`、`size20mb_p03`など） |
| 命名原則                | ASCII限定・固定長UUID・日付はISO8601準拠。                 |

#### Markdown構造

```
---
thread: 6811ff1a-2bac-8005-a2ae-5d8e63d7ee3e
provider: openai
messages: 123
models: [gpt-4o]
range: 2025-10-01 〜 2025-10-18
locale: ja-JP
---

## [User] 2025-10-18 10:00
本文...

## [Reyna] 2025-10-18 10:01
本文...
```

##### 仕様詳細

* YAML Front Matter 部にメタ情報を付与（i18n対応可）。
* 1メッセージ＝1セクション（`##` 見出し）。
* `author_role`, `author_name`, `ts`, `content` を整形して出力。
* URL・コードブロック・画像リンクは原文保持（Markdown表記そのまま）。
* 改行・インデント・エスケープはOpenAI仕様に準拠。

---

### 6.4.1 Markdown整形ルール（GFM準拠）

Exporterが生成するMarkdownは、GitHub Flavored Markdown (GFM) に準拠すること。
これにより、GitHub / VSCode / Obsidian など主要エディタで正しく表示・差分比較できることを保証する。

#### 改行・インデント

* 改行は `\n`（LF）固定。
* 複数行メッセージは段落区切りとして扱い、原文の改行を維持する。
* 先頭空白や引用行（`>`）はそのまま保持。

#### コード・引用・リンク

* コードブロックは ``` フェンス記法（GFM標準）を使用。
* インラインコードはバッククォート `` `text` `` を使用。
* URL・画像リンクは自動リンクを保持（正規化・短縮禁止）。

#### テーブル・リスト

* テーブルは `|` 区切りのGFM構文を採用。
* リストは `-` プレフィックス（UL）または `1.`（OL）を使用。
* 入れ子リストは2スペースインデントを基本とする。

#### エスケープ方針

* Markdown構文文字（`*`, `_`, `#`, `>`など）は必要最小限のみエスケープ。
* エスケープ後もGFMパーサで正しく解釈されることを確認する。

#### CI整合性

* 将来的に `markdownlint` による自動検証をCIに組み込む。
* Lintルールセット：`markdownlint-cli2` + `.markdownlint.yaml`（予定）

---

### 6.5 meta.json（任意）

スレッド単位の補助メタファイル。Viewerや統計処理が利用する。

#### 出力例

```json
{
  "conversation_id": "6811ff1a-2bac-8005-a2ae-5d8e63d7ee3e",
  "message_count": 123,
  "models": ["gpt-4o"],
  "date_range": ["2025-10-01", "2025-10-18"],
  "exported_at": "2025-10-18T10:15:00Z",
  "split_policy": "date:week",
  "files": ["thread-..._part01.md", "thread-..._part02.md"]
}
```

#### 補足

* MVPでは任意生成（Exporterのオプションで切替可）。
* 将来Viewer統合時には一覧生成・検索用に必須化予定。

---

### 6.6 i18n / ロケール出力規約

* CLI指定の `--locale` に従い、日付・時刻・固定文言を整形。
* 未訳キーは英語フォールバック＆警告出力。
* ファイル名はASCII固定（互換性優先）。
* Markdown本文は原文（多言語混在可）。

---

### 6.7 付随データの取り扱い

#### 現仕様（MVP）

* `attachments`, `urls`, `images`, `code` などの要素は
  **JSON構造内に参照情報として保持**する。
* 外部ファイル（画像・ドキュメント等）の保存や展開は行わない。
* 出力対象はあくまで構造情報のみであり、本文やリンクを破壊しない。

#### 将来拡張（検討中）

> attachments配下のURLやbase64データを抽出し、
> `artifacts/attachments/{provider_id}/{thread_id}/` に保存する機能を検討中。
> 実装時はセキュリティ・サイズ上限・ライセンス条件を考慮する。

---

### 6.8 Viewer連携仕様

* Viewerは `artifacts/output/{provider_id}/` 以下をルートとして探索。
* `meta.json` に基づきスレッド一覧を構築し、MarkdownをHTMLに変換して表示。
* ファイル間リンク・検索は `conversation_id` をキーとして行う。
* Parser/Exporterとの直接依存は持たない（疎結合設計）。

---

### 6.9 設計方針まとめ

* Parser → Exporter → Viewer の責務分離を厳格に維持。
* JSONLは**構造保存用フォーマット**、Markdownは**可読出力フォーマット**。
* i18n／ファイル命名／添付データの扱いを一貫して明示。
* 外部ファイル出力は将来検討に留め、MVPではテキスト・JSONのみを成果物とする。

---

## 7. CLI仕様

### 7.1 概要

CLIは `llm-logparser` エントリポイントを基点とし、  
**Parser（parse）**, **Exporter（export）**, **Viewer（viewer）**, **Config（config）** の  
4系統サブコマンドを持つ。  
目的は、スクリプト単体で「入力→正規化→出力→閲覧」まで完結できること。

---

### 7.2 サブコマンド構成

| コマンド | 目的 | 説明 |
|-----------|------|------|
| `parse` | パース処理 | 各ProviderのエクスポートJSONを解析・正規化し、スレッド単位の `parsed.jsonl` を出力する。 |
| `export` | 出力処理 | `parsed.jsonl` をMarkdown／HTMLに変換して成果物を生成する。 |
| `viewer` | 簡易ビューア | 生成済み成果物をローカルHTMLサーバーで閲覧する（MVPでは予約）。 |
| `config` | 設定操作 | 設定ファイルの生成・編集・リセットを行う。 |

※ `parse` → `export` を連続実行する簡易チェーン機能として `--chain` オプションを提供。

---

### 7.3 主オプション一覧

| オプション | 概要 | 対応フェーズ |
|-------------|------|----------------|
| `--provider <id>` | 対象プロバイダ指定（例：openai, claude, gemini） | 全体 |
| `--config <path>` | 外部設定ファイルを指定（YAML/JSON） | 全体 |
| `--input <path...>` | 入力ファイルパス（複数可） | parse |
| `--outdir <dir>` | 出力ルート（デフォルト：`artifacts/output`） | 全体 |
| `--export-format {jsonl,md,html}` | 出力フォーマット選択（複数可） | export |
| `--with-meta` | meta.jsonを生成 | export |
| `--split-by {size,count,date,none}` | 分割単位 | export |
| `--split-size-mb <int>` | 分割サイズ上限 | export |
| `--max-msgs-per-file <int>` | メッセージ件数上限 | export |
| `--split-by-date {none,day,week,month}` | 日付単位分割 | export |
| `--cache <path>` | キャッシュファイル保存先 | parse |
| `--locale <lang-REGION>` | ロケール指定（例：ja-JP, en-US） | 全体 |
| `--timezone <IANA>` | タイムゾーン（例：Asia/Tokyo） | 全体 |
| `--dry-run[=parse|export]` | 出力なしで統計表示／シミュレーション | 全体 |
| `--fail-fast` | 例外発生時に即中断 | 全体 |
| `--json-errors` | エラーをJSON構造で出力 | 全体 |
| `--list-threads` | キャッシュまたはparsed.jsonlの一覧を表示 | parse |
| `--chain` | parse→exportを連続実行 | CLI統合 |
| `--offline` | 外部通信禁止（既定ON） | 全体 |
| `--enable-network` | ネットワーク通信を明示的に許可 | 全体 |

---

### 7.4 動作シナリオ例

#### 基本例：OpenAIエクスポートをMarkdownへ
```bash
PYTHONPATH=src python3 -m llm_logparser parse \
  --provider openai \
  --input artifacts/conversations.json \
  --outdir artifacts/output

PYTHONPATH=src python3 -m llm_logparser export \
  --provider openai \
  --outdir artifacts/output \
  --export-format md \
  --split-by date \
  --split-by-date week \
  --with-meta
```

#### チェーン実行

```bash
PYTHONPATH=src python3 -m llm_logparser parse \
  --provider openai \
  --input artifacts/conversations.json \
  --outdir artifacts/output \
  --export-format md \
  --chain
```

（内部的に `export` が続けて呼び出される）

#### 解析済みスレッド一覧を確認

```bash
python3 -m llm_logparser parse --list-threads
```

---

### 7.5 ロケール・タイムゾーン

* `--locale` はすべての出力（CLIメッセージ／Markdown／meta.json）に適用される。
* `--timezone` は時刻表示および日付分割に影響する。
* 未指定時は `locale=en-US`, `timezone=Asia/Tokyo` を既定値とする。
* 翻訳キー未定義の場合は英語フォールバック＆警告出力。

---

### 7.6 ドライラン・診断モード

* `--dry-run`：入力ファイル構造と出力見込みを解析し、生成件数・サイズを表示。
* `--dry-run=parse`：正規化統計のみを表示（出力なし）。
* `--dry-run=export`：分割件数・ファイル見込みを試算。
* `--list-threads`：既処理スレッドIDやキャッシュ内容を一覧出力（JSON）。

---

### 7.7 エラーハンドリング制御

* `--fail-fast`：例外検出時に即時終了。
* `--json-errors`：例外をJSON構造で標準出力（ViewerやSDKで再利用可能）。
* これらの設定は §10（エラー契約）に準拠。

---

### 7.8 Viewer起動（将来機能）

* `viewer` サブコマンドは将来の軽量HTMLサーバー機能を想定。
* MVPでは予約済み（例：`python -m llm_logparser viewer` → artifacts/outputを開くのみ）。
* 将来的に `--serve` / `--port` オプションを追加予定。

---

### 7.9 使用例（複合）

```bash
# すべてをまとめて日本語ロケールで実行
llm-logparser parse \
  --provider openai \
  --input conversations.json \
  --outdir artifacts/output \
  --export-format md \
  --locale ja-JP \
  --split-by size \
  --split-size-mb 20 \
  --with-meta \
  --chain
```

---

### 7.10 設計方針まとめ

* サブコマンド単位で責務を分離（Parser／Exporter／Viewer）。
* オプションはフェーズごとに限定適用し、競合を防止。
* `--chain` により一括実行をサポートしつつ、モジュール的独立性を維持。
* すべての出力は §6（出力仕様）に準拠。
* CLIのすべてのメッセージは i18n／L10n 対応。

---

## 8. 重複管理／キャッシュ

### 8.1 概要

Parserは、各プロバイダのエクスポートを逐次読み取りながら  
重複除外・再生成制御を行うために**キャッシュ層**を利用する。  
目的は、再パース時の無駄な再出力を防ぎ、更新差分のみを安全に反映すること。

キャッシュはCore共通機能（`core/cache_manager.py`）として実装され、  
プロバイダ依存の構造差はAdapter側で吸収する。

---

### 8.2 キャッシュファイル配置

```

artifacts/cache/{provider_id}_cache.json

```

MVPでは1プロバイダ＝1ファイル構成。  
将来的にSQLite等への移行も可能な設計とする。

---

### 8.3 キャッシュスキーマ（共通）

```json
{
  "provider_id": "openai",
  "schema_version": "1.0",
  "threads": {
    "67eddd49-1748-8005-89bb-1ea0e9de21f3": {
      "update_time": 1746009883.35,
      "title": "レイナ、GPUヒートシンク上より出撃",
      "message_count": 132,
      "last_message_id": "ef0b3a81-f55c-4f9d-ab98-32e5dc4d2d8b",
      "messages": [
        "ef0b3a81-f55c-4f9d-ab98-32e5dc4d2d8b",
        "789fb7ef-dd09-4be5-9b1e-1e072e575bde"
      ]
    }
  }
}
```

#### 特徴

* 1スレッド＝1エントリ（key=conversation_id）
* `update_time` はスレッド内メッセージの最大 `create_time`
* `message_count` は総件数
* `messages` は `message_id` 配列（差分判定に使用）
* `schema_version` により将来互換を保証

---

### 8.4 Provider: OpenAI（実装ルール）

OpenAIのエクスポート構造（`mapping`階層）を基準に抽出・更新を行う。

| キャッシュ項目           | 取得元                               | 説明          |
| ----------------- | --------------------------------- | ----------- |
| `conversation_id` | ルートキー                             | スレッド識別子     |
| `message_id`      | `message.id`                      | メッセージ固有ID   |
| `update_time`     | 各 `message.create_time` の最大値      | スレッド更新検出に使用 |
| `title`           | `conversation.title` または先頭メッセージ本文 | スレッドタイトル    |
| `model`           | `message.metadata.model_slug`     | 出力モデル識別     |
| `children`        | `mapping[].children`              | ツリー解析（任意）   |
| `author_role`     | `message.author.role`             | 投稿種別        |
| `status`          | `message.status`                  | 出力可否判定補助    |

#### 備考

* OpenAIでは `message.content.parts` が実本文を保持する。
* `content_type="text"` のみを対象とし、他型（code, system等）はスキップ可。
* Unicodeはデコードせず、`\uXXXX` のまま保存する（正規化責務外）。

---

### 8.5 更新判定ロジック（共通）

| 判定              | 条件                            | 動作                    |
| --------------- | ----------------------------- | --------------------- |
| **NEW**         | キャッシュに存在しない                   | 新規登録・出力実行             |
| **SKIP**        | 新しいupdate_time = 旧update_time | 出力を再生成しない             |
| **REPLACE**     | 新しいupdate_time > 旧update_time | スレッドフォルダを削除し再生成       |
| **WARN & SKIP** | 新しいupdate_time < 旧update_time | 警告を出力しスキップ            |
| **ERROR**       | キャッシュ破損・構造不整合                 | LP8xxx系例外発報（自動ロールバック） |

#### 出力タイミング

* 判定後、Parserは各スレッドの再出力要否をExporterへ通知。
* 処理完了後、正常終了スレッドのみキャッシュ更新。

---

### 8.6 ファイル更新ポリシー

* キャッシュファイルは処理完了後に**アトミック更新**。
* 異常終了時は `.bak` に旧データを保存し、再試行時に復旧可能。
* 更新日時はISO8601形式で `last_updated` に記録。
* `--rebuild-cache` オプション指定時は強制再生成（全スレ再出力）。

---

### 8.7 Parser連携仕様

* Parserは読み取り開始時にキャッシュをロード。
* スレッド単位で更新判定を行い、Exporter呼出可否を返却。
* 処理完了後、Parserがキャッシュを更新し保存。
* キャッシュ破損・不整合は即時警告（`LP8xxx`）を発報。

---

### 8.8 Exporter／Viewerとの関係

| 対象           | キャッシュ参照 | 更新可否             |
| ------------ | ------- | ---------------- |
| **Exporter** | 読み取り専用  | ✖（更新は行わない）       |
| **Viewer**   | 非参照     | ✖（meta.jsonのみ使用） |

Exporterは出力完了後、meta.json生成時にParserからの更新通知を完了扱いとする。

---

### 8.9 エラー処理／再構築

* **破損検出**：構文エラー・バージョン不一致・不正キー。
  → `LP8xxx`（内部I/O）例外として報告。
* **再構築**：`--rebuild-cache` 指定時、全スレ削除→再生成。
* **部分回復**：破損スレッドのみ除外し、正常スレは維持。
* **ロールバック**：異常終了時に `.bak` を自動復元。

---

### 8.10 将来拡張

* SQLiteキャッシュ（インデックス高速化）
* Fingerprint比較（message_id + content hash）
* Provider別差分戦略（Gemini, Claudeなど）
* Apps SDK用API化（キャッシュ操作をSchema化）
* マルチスレッド／非同期更新（I/O分離）

---

### 8.11 設計方針まとめ

* キャッシュは **Parser専用・スレッド単位更新**。
* Provider差はAdapterで吸収し、キャッシュ構造は共通スキーマ化。
* update_timeによる“完全順序付き再生成ポリシー”を採用。
* Exporter／Viewerは参照または非依存で設計。
* JSON形式のまま保持し、外部DB依存を持たない（MVP範囲）。
* 破損検出・ロールバックを標準装備し、安全な再パースを保証する。

---

## 9. 多言語対応（i18n / L10n）

### 9.1 対象範囲

多言語化は以下の3層で共通的に適用される：

| 層 | 対象例 |
|----|---------|
| CLI層 | ヘルプテキスト、進行ログ、統計出力、警告・エラーメッセージ |
| Exporter層 | Markdown冒頭メタ（Thread, Provider, Messages等）／固定見出し文言 |
| Viewer層（将来） | UI要素（メニュー、検索バー、ボタン、フィルタラベル等） |

---

### 9.2 ロケール決定アルゴリズム

優先順位：

1. CLI引数 `--locale <lang-REGION>`  
2. 環境変数 `LLP_LOCALE`  
3. ユーザー設定ファイル `config.yaml`  
4. システムロケール（`locale.getdefaultlocale()`）  
5. 既定：`en-US`

同様にタイムゾーンも：

1. `--timezone`  
2. `LLP_TZ`  
3. `config.yaml`  
4. 既定：`Asia/Tokyo`

CLI起動時に決定したロケールは `context.locale` として全モジュールへ注入される。

---

### 9.3 メッセージキー管理

メッセージは**キー＋プレースホルダ**形式で管理する。

#### 例：
```json
{
  "parser.start": "Parsing started for provider {provider}",
  "parser.complete": "Parsing completed. {count} threads processed.",
  "exporter.writing": "Writing {filename} ({size_mb} MB)...",
  "error.invalid_json": "Input file is not a valid JSON format."
}
```

#### 使用方法：

```python
msg = i18n.t("parser.complete", count=128)
# => "Parsing completed. 128 threads processed."
```

内部的には `i18n.get_text(key, **kwargs)` により解決。

---

### 9.4 翻訳ファイル構成及びCLIエラーメッセージの多言語化

**目的**
CLIのエラーメッセージ・警告・進行ログを外部辞書で管理し、
`--locale` 引数または環境設定に応じて動的に切り替えられるようにする。

**要件**

* すべてのCLI出力メッセージ（例：`invalid argument`, `missing file`, `parse failed` など）を **i18n辞書キー化** する。
* 翻訳リソースは `src/llm_logparser/i18n/{locale}.yaml` に定義し、`parser/exporter/error` キー体系を共通化。
* ロード時にロケールを自動検出（`--locale` > `LLP_LOCALE` > `config.yaml` > `en-US`）。
* 未訳キーは `en-US` にフォールバックし、警告を `[WARN][i18n] Missing key ...` として出力。
* CLI側の例外処理・エラーハンドラは `message_key` と `params` を受け取り、
  ロケール辞書から解決した文字列を出力する。
* 翻訳対象範囲には以下を含む：

  * 引数エラー（argparse / click系メッセージ）
  * ファイルI/Oエラー
  * パース／エクスポート失敗時の要約
  * 成功／統計メッセージ（「Parsed N threads」など）

```
src/llm_logparser/i18n/
 ├── en-US.yaml
 ├── ja-JP.yaml
 ├── fr-FR.yaml（将来）
 └── _schema.yaml（キー定義）
```

**備考**

* 実装は `i18n.get_text(key, **params)` 経由で統一。
* MVP段階では英語固定でも可、locale辞書構造だけ先行定義。
* YAML形式（UTF-8）。
* ルートキーはモジュール単位（parser/exporter/cli/error等）。
* 各ファイルは `_schema.yaml` を基準にキー整合をLintで検査。
* 未訳キーは自動的に英語フォールバック。

> **補足:** CLI層（argparse / logging出力）のi18n適用は本設計に含まれるが、MVP段階では未実装。今後、例外処理層に`i18n.get_text()`を導入してメッセージを外部辞書化する予定。

---

### 9.5 日時・数値・単位の整形規則

* Python `babel` ライブラリ互換を想定。
* 日付整形は `format_datetime(ts, locale, tzinfo)`。
* 数値整形は `format_decimal(value, locale)`。
* ファイル出力（meta.json / Markdown）は常にISO8601で保持し、人間可読部分のみロケール整形。

| 対象 | 出力例（ja-JP）             | 出力例（en-US）             |
| -- | ---------------------- | ---------------------- |
| 日付 | 2025年10月18日 10:15      | Oct 18, 2025, 10:15 AM |
| 数値 | 1,234.5 → 1,234.5      | 同左                     |
| 単位 | MB / 件 / 日付などはロケール依存表記 | "MB" / "records"       |

---

### 9.6 未訳キー／フォールバックポリシー

| 状況         | 動作                         |
| ---------- | -------------------------- |
| 指定キーが存在しない | WARNを出力し、英語(en-US)へフォールバック |
| 翻訳ファイル欠落   | 英語で継続実行、終了はしない             |
| 文字化け検出     | ファイル再ロード試行、失敗時は英語固定        |

CLI上では警告例：

```
[WARN][i18n] Missing key 'parser.summary' in ja-JP. Fallback to en-US.
```

---

### 9.7 CLI統合仕様

* `--locale`, `--timezone` は CLI全体で共有し、Parser/Exporterへ継承。
* `--locale` 変更時は即時反映（キャッシュ不要）。
* i18n層は Lazy Load で初回アクセス時に言語リソースをロード。
* CLIヘルプ・出力メッセージも同一辞書で管理し、翻訳漏れを防ぐ。

---

### 9.8 将来拡張

* 翻訳自動補完（未訳キーを英語からAI翻訳して差分管理）
* Viewer向けJSON辞書（ブラウザで直読み）
* 動的ロケール切替（`llm-logparser viewer --locale fr-FR`）
* サーバーサイドi18n API（Apps SDK統合を想定）

---

### 9.9 設計方針まとめ

* すべてのメッセージはキー化・辞書管理し、文字列リテラルは直接使用しない。
* ロケール・タイムゾーンはCLI引数を最優先し、設定ファイルで永続化。
* 翻訳欠落時は安全側フォールバック（英語）＋警告ログ。
* Exporter／Viewerとも同一辞書を共有（キー整合）。
* ロケール差異をUI・出力に確実に反映し、Apps SDK統合を見据えた拡張性を保つ。


---

## 10. 例外処理・エラー契約（仕様書版）

### 10.1 概要と目的

本章は、`llm-logparser` における**統一的な例外・エラー処理契約**を定義する。  
対象は CLI／Parser／Exporter／Viewer／Apps SDK の全階層であり、  
JSONベースのエラーペイロードを標準化することで、  
UI（Viewer）や外部統合（Apps SDK）での再試行・集約処理を容易にすることを目的とする。

---

### 10.2 適用範囲

| 対象モジュール | 適用内容 |
|----------------|----------|
| Parser | 入力フォーマット解析・正規化中の例外（I/O, JSON構造, config不整合） |
| Exporter | Markdown変換・ファイル出力・分割処理時のエラー |
| CLI | 引数解析・パス権限・ネットワーク制御エラー |
| Viewer | エラー表示・再試行UI・Apps SDK連携 |
| Core／Cache | キャッシュ破損・更新ロック・競合検出 |
| i18n | 翻訳リソース欠落・整形エラー |

---

### 10.3 エラーペイロード構造（JSON Schema）

```json
{
  "version": "1.0",
  "severity": "ERROR",
  "code": "LP2001",
  "message_key": "error.invalid_json",
  "message": "Input file is not a valid JSON format.",
  "params": {
    "filename": "conversations.json",
    "line": 128
  },
  "provider_id": "openai",
  "module": "parser",
  "context": "parse_logs()",
  "exit_code": 2,
  "retryable": false,
  "partial": {
    "processed": 120,
    "skipped": 3
  },
  "timestamp": "2025-10-18T10:15:00Z"
}
```

#### 説明

| フィールド         | 型      | 必須 | 説明                          |
| ------------- | ------ | -- | --------------------------- |
| `version`     | string | ✔  | エラー契約バージョン                  |
| `severity`    | string | ✔  | FATAL / ERROR / WARN / INFO |
| `code`        | string | ✔  | 一意のエラーコード（LP + 4桁）          |
| `message_key` | string | ✔  | i18n辞書キー（翻訳対応）              |
| `message`     | string | ✔  | ロケール解決後の表示文字列               |
| `params`      | object | 任意 | 動的プレースホルダ値                  |
| `provider_id` | string | 任意 | 処理対象プロバイダ                   |
| `module`      | string | ✔  | 発生元モジュール名                   |
| `context`     | string | 任意 | 関数・呼出位置など                   |
| `exit_code`   | int    | ✔  | プロセス終了コード                   |
| `retryable`   | bool   | ✔  | 再試行可能フラグ                    |
| `partial`     | object | 任意 | 処理済み件数等                     |
| `timestamp`   | string | ✔  | ISO8601（UTC）時刻              |

---

### 10.4 エラーコード体系

| 範囲         | 分類         | 説明                               |
| ---------- | ---------- | -------------------------------- |
| **LP1xxx** | 起動・環境      | 引数、ファイル権限、I/Oパス不正                |
| **LP2xxx** | 入力フォーマット   | JSON破損、エンコーディング不正、スキーマ不一致        |
| **LP3xxx** | 設定／プロバイダ   | YAML/JSON設定欠落、Provider Adapter異常 |
| **LP4xxx** | 正規化／構造化    | mapping展開・スキーマ変換・content解析失敗     |
| **LP5xxx** | 出力         | Markdown整形・分割失敗・ファイル書込エラー        |
| **LP6xxx** | i18n／ロケール  | 翻訳辞書欠落、未訳キー警告、数値整形異常             |
| **LP7xxx** | キャッシュ／重複管理 | キャッシュ破損、競合、バージョン不一致              |
| **LP8xxx** | 内部I/O／システム | ファイルロック、権限不足、ディスクフル              |
| **LP9xxx** | 予期せぬ例外     | 捕捉不能・外部ライブラリ異常・AssertionError 等  |

> 詳細は `docs/error-codes.md` を参照（コード・テンプレート例含む）

---

### 10.5 重大度（severity）と終了コード（exit_code）対応表

| severity  | 説明               | exit_code | 処理継続  |
| --------- | ---------------- | --------- | ----- |
| **FATAL** | 致命的エラー。処理中断。     | `1〜9`     | ❌     |
| **ERROR** | 主要タスク失敗（部分成功あり）。 | `0`       | ⭕（継続） |
| **WARN**  | 軽微な警告。結果には影響しない。 | `0`       | ⭕     |
| **INFO**  | 通常ログ。            | `0`       | ⭕     |

CLIは最終的にすべてのエラーを集約し、
終了時に `errors.json` として要約出力する。

---

### 10.6 例外発生フロー

```
Parser → raise LLPError(code="LP2001", severity="ERROR")
      ↓
CoreErrorHandler 捕捉
      ↓
i18n.resolve(message_key)
      ↓
構造体に変換（ErrorPayload）
      ↓
--json-errors 有効時 → JSON出力
--json-errors 無効時 → 標準出力（翻訳済メッセージ）
```

#### 実装例

```python
try:
    parse_logs(input_path)
except LLPError as e:
    handler.emit(e.to_payload(locale="ja-JP"))
except Exception as e:
    handler.emit(LLPError.from_exception(e))
```

---

### 10.7 出力ポリシー（CLI / Viewer連携）

| 出力種別    | 出力先                                  | 説明                   |
| ------- | ------------------------------------ | -------------------- |
| 標準出力    | stdout                               | INFO / WARN（通常メッセージ） |
| 標準エラー   | stderr                               | ERROR / FATAL（即時報告）  |
| 構造化JSON | `artifacts/logs/errors.json`         | すべてのエラーを集約（Viewer用）  |
| 警告ログ    | `artifacts/logs/parser-warnings.log` | 再解析・再出力時の参照用         |

Viewerは `errors.json` をポーリングし、
UI上でメッセージを翻訳・色分け表示する。

---

### 10.8 Apps SDK／HTTP連携（将来仕様）

将来的にApps SDK経由でCLI呼出を行う場合、
このエラーペイロードをHTTPレスポンス（`application/json`) として返却する。

| HTTPコード | LLP severity | 意味            |
| ------- | ------------ | ------------- |
| 200     | INFO/WARN    | 正常／軽警告        |
| 400     | ERROR        | 入力フォーマット／設定ミス |
| 500     | FATAL        | 内部例外・クラッシュ    |
| 503     | RETRYABLE    | キャッシュロック・再試行可 |

---

### 10.9 設計方針まとめ

* すべてのエラーは構造化JSONで表現可能。
* `LPxxxx` コード体系は固定長・永続化を前提とする。
* `--json-errors` フラグによりCLI動作をViewer連携モードに切替。
* i18n対応：`message_key` と `params` による遅延翻訳。
* ViewerおよびApps SDKは同一ペイロードを利用可能。
* 想定外例外も `LP9xxx` として安全にキャッチ・再出力。
* `docs/error-codes.md` に詳細マッピング（例文・テンプレート）を管理。

---

## 11. ランタイム設定の永続化（仕様書版）

### 11.1 概要と目的

本章は、`llm-logparser` の実行時設定（ランタイムコンフィグ）を永続化し、  
CLI／Viewer／Apps SDK 間で**共通に参照可能な設定レイヤー**を定義する。  
目的は以下の通り：

- 起動引数の煩雑さを軽減（初回実行で自動保存、再実行で自動復元）  
- GUI（Viewer）が直接編集できる一貫性のある設定構造を確立  
- CLI・GUI・SDKが同一ソースを参照することで、挙動差異を防ぐ  

---

### 11.2 コンフィグの3階層構造

| レイヤー | 参照優先度 | 保存位置 | 想定編集者 |
|-----------|-------------|-----------|-------------|
| **CLI引数** | 最高 | 実行時（非永続） | ユーザー |
| **ユーザー設定ファイル** | 中 | `~/.config/llm-logparser/config.yaml` | CLI/Viewer両方 |
| **既定値（デフォルト）** | 最低 | 内部YAMLまたは`config/defaults.yaml` | システム |

優先順位ルール：
```

CLI > 環境変数 > ユーザー設定ファイル > 既定値

```

すべての値はロード後にマージされ、最終決定値がセッション構成（`RuntimeConfig`）として使用される。

---

### 11.3 設定スキーマ構造（YAML Schema）

```yaml
schema_version: 1.0
active_profile: default

profiles:
  default:
    provider: openai
    input: artifacts/conversations.json
    outdir: artifacts/output
    split:
      by: none         # none|size|count|date
      size_mb: 20
      max_msgs: 8000
      date_mode: none  # none|day|week|month
    locale: ja-JP
    timezone: Asia/Tokyo
    cache: artifacts/cache/state.json
    enable_network: false
    json_errors: true
    i18n:
      fallback: en-US
      warn_untranslated: true
    viewer:
      theme: light
      autosave_interval: 30
      font_size: 14
    paths:
      logs: artifacts/logs
      temp: artifacts/tmp
```

---

### 11.4 永続化フロー

```
CLI起動
   ↓
設定ファイルロード（ユーザー定義）
   ↓
CLI引数をマージ
   ↓
RuntimeConfig構築
   ↓
実行（Parser/Exporter利用）
   ↓
成功終了時にオートセーブ（--no-save-configで無効化可）
```

#### 実装例（擬似コード）

```python
config = Config.load()
config.merge_args(cli_args)
runtime = RuntimeConfig(config)
run(runtime)
if not args.no_save_config:
    config.save(runtime.active_profile)
```

---

### 11.5 永続化仕様（保存形式と挙動）

| 要素          | 内容                                        |
| ----------- | ----------------------------------------- |
| **形式**      | YAML（UTF-8）                               |
| **保存場所**    | OSごとに標準パスを採用（表11.1参照）                     |
| **保存タイミング** | CLI実行成功時（exit_code==0）                    |
| **競合処理**    | 同時書込を検出し、バックアップファイル（`.bak`）生成             |
| **ロック方式**   | ファイルベースのアトミックロック（排他保証）                    |
| **更新範囲**    | 実行に使用されたプロファイルのみ更新                        |
| **GUI連携**   | Viewerが `config.yaml` を直接編集可能（API経由で再ロード） |

---

### 11.6 ファイル配置（OS別）

| OS             | デフォルトパス                               |
| -------------- | ------------------------------------- |
| macOS / Linux  | `~/.config/llm-logparser/config.yaml` |
| Windows        | `%APPDATA%\llm-logparser\config.yaml` |
| Local Override | `./.llp/config.yaml`（リポジトリ単位）         |

---

### 11.7 プロファイル機能

1つの `config.yaml` 内で複数のプロファイルを保持できる。
`--profile <name>` を指定することで切替可能。

例：

```bash
llm-logparser parse --profile work
llm-logparser export --profile note
```

保存時は `active_profile` に自動記録される。
Viewerはドロップダウンでプロファイルを切り替え可能。

---

### 11.8 GUI連携仕様（Viewer側）

Viewerは以下の動作を通じて設定を同期する：

| 操作     | Viewer側動作                                         |
| ------ | ------------------------------------------------- |
| 設定読み込み | `~/.config/llm-logparser/config.yaml` をJSONとしてロード |
| 設定変更   | フォーム入力 → `config.yaml`書き換え → CLI通知                |
| CLI起動  | 変更検知で再ロード（ホットリロード対応）                              |
| 競合     | CLIが実行中の場合は一時保存→再試行（排他ロック解放後）                     |

設定変更イベントは `llp-config-watcher` モジュールで監視し、
リアルタイムにViewer UIへ反映できる。

---

### 11.9 Apps SDK統合（将来仕様）

Apps SDK からの呼び出し時は、
`GET /config` および `PUT /config` エンドポイントを利用して設定の参照・更新を行う。

| HTTPメソッド | エンドポイント   | 説明                 |
| -------- | --------- | ------------------ |
| `GET`    | `/config` | 現行設定をJSONで取得       |
| `PUT`    | `/config` | 設定を更新し保存（プロファイル単位） |

CLI・Viewer・SDKが同一スキーマを共有するため、
GUIから保存してもCLI起動と矛盾しない。

---

### 11.10 設計方針まとめ

* すべての設定値は**構造化YAMLで永続化**し、GUIとCLIの共通参照を保証。
* CLI終了時に自動保存（`--no-save-config`指定で無効化可）。
* `profiles`構造により複数環境・出力先を柔軟に切替可能。
* Viewerは同ファイルを読み書きし、GUI上で即反映できる。
* Apps SDK連携を見据えたHTTPインターフェースを定義。
* フォーマット変更時は `schema_version` により後方互換を維持。
* バックアップ・ロック・アトミック保存で安全な書込を保証。

---

## 12. セキュリティ / プライバシー（仕様書版）

### 12.1 目的と適用範囲

本章は、`llm-logparser` の利用時における情報保護・安全設計を定義する。  
MVP段階では **完全ローカル実行・外部通信なし** を基本方針とし、  
ユーザーデータ（発話ログ・キャッシュ・設定ファイル）の漏洩防止を保証する。  

Apps SDK / HTTP連携は**非対応（将来定義のみ残置）**とする。  

---

### 12.2 基本設計方針

1. **オフライン・ファースト設計**  
   - デフォルトでネットワーク通信を完全に遮断。  
   - 依存ライブラリによる自動更新・外部リクエストを禁止。  

2. **明示的許可制**  
   - `--enable-network` オプションを指定した場合のみ通信許可。  
   - その際は **全送信ログを artifacts/logs/network.log に記録**。  

3. **データ最小化の原則**  
   - パーサーは入力ファイルを読み込むが、  
     個人情報を外部に送信・共有しない。  
   - 中間生成物（manifest, cache, md）はローカル保存のみ。  

4. **可視化の透明性**  
   - CLIは常に処理ファイル・出力先・警告件数を明示表示。  
   - 非公開ファイル操作（隠しディレクトリ等）は行わない。  

---

### 12.3 ファイル保護ポリシー

| 項目 | 方針 |
|------|------|
| **出力先** | 明示的に指定された`--outdir`以下のみ使用 |
| **権限設定** | 出力ディレクトリに対してユーザー権限確認（600〜700系） |
| **一時ファイル** | `artifacts/tmp` 内で管理し、終了時にクリーンアップ |
| **ログ** | `artifacts/logs` 以下に限定。明示削除可能。 |
| **キャッシュ** | JSON形式で保持。`--reset-cache`で削除可能。 |
| **Viewer** | HTML／JSのみ。外部読み込み・iframe利用なし。 |

---

### 12.4 機密データ・個人情報の扱い

1. **マスク機能（オプション設計）**
   - `config.yaml` に `mask_patterns` セクションを定義可能。
   - 正規表現で一致したテキストを `***` に置換。

  ```yaml
   mask_patterns:
     - "(?i)password[:=]\\S+"
     - "(?i)api[_-]?key[:=]\\S+"
     - "\\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}\\b"
  ```

この機能は **初期状態では無効**。ユーザーが任意に有効化する。

2. **自動削除ポリシー**

   * MVP段階では自動削除を実装しない。
   * 明示的な `--clean` オプションで全ログを削除可能。

3. **暗号化／署名**

   * 現時点では実装しない。
   * 将来リリースビルド時に `SHA256` および `GPG署名` を付与予定。

---

### 12.5 依存パッケージ管理

* 依存ライブラリは `requirements.txt` にバージョン固定で記載。
* `pip install -r requirements.txt` 時にハッシュ検証を行う。
* CIでは `pip-audit` による脆弱性スキャンを実施予定。

---

### 12.6 実行時ガード

| 機能          | 実装内容                           |
| ----------- | ------------------------------ |
| **ソケット無効化** | 起動直後に `socket` モジュールを無効化（例外除外） |
| **パス検証**    | `../` などのディレクトリトラバーサルを禁止       |
| **書込検証**    | 出力先のディスク容量を事前チェック              |
| **サニタイズ**   | ファイル名をASCII限定に変換し、制御文字除外       |
| **セーフモード**  | `--offline` を強制ON（MVP既定）       |

---

### 12.7 テスト・検証ポリシー

1. **ネットワーク禁止テスト**

   * `pytest --disable-socket` により通信呼び出しを検知。
2. **データリークテスト**

   * ログ出力内容に `"password"`, `"token"` が含まれないことを確認。
3. **権限テスト**

   * 出力フォルダ作成時に不正パーミッションが設定されないか検証。
4. **ファイル削除テスト**

   * `--clean` 実行後に一時ファイルが残存しないことを確認。

---

### 12.8 将来対応項目（定義のみ残置）

| 機能              | 状態   | 備考                                  |
| --------------- | ---- | ----------------------------------- |
| **Apps SDK通信**  | 非対応  | 定義は保持。SDK統合時に `enable_network` と連動。 |
| **HTTPエンドポイント** | 非対応  | CLI専用。Viewerはファイル経由で動作。             |
| **暗号化・署名付与**    | 将来対応 | CI/CDでのビルド署名を想定。                    |
| **自動PII検出**     | 将来対応 | LLM支援による本文マスキング（オプトイン）。             |

---

### 12.9 設計方針まとめ

* **完全ローカル動作を最優先**。外部送信・APIアクセスを禁止。
* **安全側ポリシー**：不明な挙動は拒否・スキップで対処。
* **マスク／削除はユーザー主導**。自動処理は行わない。
* **署名・監査対応はCIで追加予定**。MVPでは設計のみ保持。
* **Apps SDKは定義のみ残置、実装対象外**。

これにより、`llm-logparser` は “ローカルで安全に閉じた環境” を保証し、
企業・個人問わず安心して利用できる設計を維持する。

---

## 13. パフォーマンス / 上限制約（仕様書版）

### 13.1 目的

本章は、`llm-logparser` の性能要件と実行上限を定義し、  
MVPにおけるストリーム処理・分割・キャッシュ動作の性能指針を示す。  
目的は「安定稼働」と「大規模ログ対応」の両立にある。  

---

### 13.2 前提条件

| 項目 | 設定値（推奨） |
|------|----------------|
| CPU | 4コア以上（x86_64 / ARM両対応） |
| メモリ | 8GB以上（16GB推奨） |
| ストレージ | SSD推奨（ランダムアクセス高速化のため） |
| Python | 3.10 以降 |
| I/O方式 | ストリーム読取（チャンク単位） |
| ファイル形式 | UTF-8 / BOMなし JSON(L) / NDJSON |

---

### 13.3 パフォーマンス要件

| 分類 | 目標値（MVP段階） |
|------|--------------------|
| **入力サイズ上限** | 2GB / 1ファイル |
| **メッセージ数上限** | 約 500,000 件 |
| **処理時間** | 1GBあたり 60秒以内（SSD環境） |
| **スレッド分割** | 約 10,000件／thread単位（可変） |
| **出力速度** | 50〜100MB/分（Markdown生成時） |
| **キャッシュI/O** | JSON書込100MB/s以上を目標 |

---

### 13.4 メモリ管理

- 全件を保持せず、**ストリーム＋ジェネレータ方式**で逐次処理。  
- メモリ常駐オブジェクトはスレッド単位の最小構成とする。  
- 処理パイプライン例：

  ```python
  for record in stream_jsonl(path):
      entry = normalize(record)
      if entry.is_valid():
          buffer.append(entry)
      if len(buffer) > FLUSH_THRESHOLD:
          flush_to_file(buffer)
          buffer.clear()
  ```

* `FLUSH_THRESHOLD` 既定値：1,000件（設定可能）

---

### 13.5 I/O分割と並列性

| 項目            | 内容                                        |
| ------------- | ----------------------------------------- |
| **分割単位**      | サイズ / 件数 / 日付による複合分割（5章参照）                |
| **並列処理**      | MVPでは**シングルスレッド固定**。将来 `--parallel` 追加予定。 |
| **キャッシュI/O**  | スレッド別にファイル分離。競合を防ぐため排他ロックを実施。             |
| **書込フラッシュ間隔** | 既定 5秒（または 1,000件）でバッファ書込。                 |

---

### 13.6 分割パフォーマンス設計

| モード       | 設計概要         | 想定用途      |
| --------- | ------------ | --------- |
| **none**  | 単一Markdown出力 | 小規模テスト    |
| **size**  | 20MBごとに分割    | 通常運用      |
| **count** | 8,000件ごとに分割  | 固定件数比較に有効 |
| **date**  | 日付/週/月単位     | 履歴アーカイブ用  |

* 出力分割時はメタ情報（`meta.json`）を同時生成して範囲を記録。
* チャンク名はASCII固定：`thread-{id}__2025-10-18_part01.md`

---

### 13.7 キャッシュ動作仕様

* `artifacts/cache/state.json` に最新スレッド情報を保存。
* 処理済み `(provider, conversation_id, message_id)` を高速ハッシュ化して保持。
* **更新検出方式**：`update_time`比較（MVPでは差分検出なし、REPLACE再生成）。
* キャッシュサイズ上限：既定 256MB（超過時はLRU削除予定）。

---

### 13.8 エラーハンドリング時の性能保証

* JSON破損行はスキップ処理し、統計のみ加算（再試行不要）。
* キャッシュ破損時は再構築モードへ自動フォールバック。
* ログ出力によりエラー発生率を可視化（処理速度には影響しない設計）。

---

### 13.9 パフォーマンス測定基準

CLIには `--benchmark` オプションを追加予定。
実行時に以下の情報を標準出力／JSONで返す。

```json
{
  "elapsed_time_sec": 78.4,
  "processed_messages": 42693,
  "avg_speed_msg_per_sec": 544.2,
  "input_size_mb": 820.1,
  "output_size_mb": 122.3,
  "memory_peak_mb": 480
}
```

将来、`artifacts/logs/perf.log` に累積記録される。

---

### 13.10 ストレージ最適化（MVP仕様）

* 出力・キャッシュ・ログを階層分離（I/O干渉防止）

  ```
  artifacts/
  ├── output/
  ├── cache/
  ├── logs/
  └── tmp/
  ```
* ファイル書込は逐次フラッシュ。大容量バッファは使用しない。
* 書込失敗時はリトライ1回→失敗ログを出力し処理継続。

---

### 13.11 パフォーマンス改善の将来検討

| 項目              | 概要                                            |
| --------------- | --------------------------------------------- |
| 並列化             | スレッド単位で非同期出力化。Python concurrent.futures対応を検討。 |
| 圧縮出力            | `--compress` によりMarkdownをgzip圧縮予定。            |
| JSONL→Parquet変換 | Apps SDK連携時の高速再利用を想定。                         |
| メモリマップI/O       | 超大規模ファイル対応用。                                  |
| キャッシュDB化        | SQLite化によりインデックス検索を高速化予定。                     |

---

### 13.12 設計方針まとめ

* **ストリーム処理＋フラッシュ書込**でメモリを圧迫しない構造。
* **I/Oと出力処理を分離**し、エラー耐性を確保。
* **パフォーマンス目標は1GBあたり60秒以内**（SSD環境）。
* 並列処理・圧縮・DBキャッシュは将来拡張項目として設計済み。
* MVPでは安定性を最優先とし、すべて単一スレッドで実装する。

---

## 14. テスト方針（仕様書版）

### 14.1 目的

本章は、`llm-logparser` のMVP開発および将来リリースにおける  
品質保証・自動検証・再現性確保のためのテスト戦略を定義する。  

CLI単体での安定動作を保証しつつ、  
GitHub Actionsを用いたCI/CD統合で再現可能な品質監査を行う。  

---

### 14.2 テスト分類

| 種別 | 目的 | 実施環境 |
|------|------|----------|
| **ユニットテスト** | 個々の関数・モジュールの正当性検証 | pytest（ローカル） |
| **統合テスト** | Parser⇔Exporter間の動作整合 | pytest＋サンプルJSON |
| **i18nテスト** | 多言語ロケールの整合確認 | pytest＋ロケール環境変数 |
| **設定テスト** | config.yaml のロード・マージ・保存 | pytest |
| **パフォーマンステスト** | ストリーム処理性能の目標確認 | pytest-benchmark |
| **セキュリティテスト** | 通信遮断・リーク防止確認 | pytest + disable-socket |
| **CIテスト** | GitHub Actionsでの自動化検証 | GitHub Actions |
| **リリース検証** | 依存監査・ビルド署名 | pip-audit / gpg / hash検証 |

---

### 14.3 テストデータ構成

```

tests/
├── unit/
│   ├── test_parser.py
│   ├── test_exporter.py
│   └── test_utils.py
├── integration/
│   ├── test_pipeline_openai.py
│   ├── test_config_merge.py
│   └── sample-data/
│       ├── sample-openai-message.json
│       ├── sample-openai-threads.json
│       └── sample-output.md
├── perf/
│   └── test_stream_speed.py
└── i18n/
└── test_locale_switch.py

```

すべてのサンプルデータは**人工生成（PIIなし）**を原則とする。

---

### 14.4 実行ルール（pytest）

| コマンド | 内容 |
|----------|------|
| `pytest -q` | 通常テスト実行 |
| `pytest -m slow` | 大規模ファイル性能テスト |
| `pytest --disable-socket` | ネットワーク遮断テスト |
| `pytest --maxfail=1 -q` | CI用軽量実行 |
| `pytest --cov=llm_logparser` | カバレッジ測定 |

エラー検出時は `artifacts/logs/test-failures.log` に詳細出力。  

---

### 14.5 成功基準（MVP段階）

| 項目 | 基準 |
|------|------|
| カバレッジ | 80%以上（Core, Provider） |
| パース成功率 | 99.9%以上（サンプル入力に対して） |
| ファイル整合 | 出力MDとmeta.jsonの整合100% |
| メモリリーク | 0件（pytest-memory-profiler確認） |
| ロケールテスト | 未訳キー発生率 0% |
| エラー発生時 | JSON契約形式で通知されること（10章準拠） |

---

### 14.6 GitHub CI統合（自動検証）

#### GitHub Actions 設定例

```yaml
name: CI
on: [push, pull_request]

jobs:
  build-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run unit tests
        run: pytest --disable-socket --maxfail=1 -q
      - name: Lint and format
        run: |
          yamllint .
          markdownlint-cli2 .
      - name: Security audit
        run: pip-audit
```

#### CIチェックポリシー

* Push / PRごとに全テストを実行。
* Lint／脆弱性／フォーマットを同時検証。
* 失敗時はコミットをブロック（PR gate方式）。
* 成功時に「✅ Checks passed」として自動可視化。

---

### 14.7 静的解析 / 構文検証

| ツール                 | 対象                | 用途          |
| ------------------- | ----------------- | ----------- |
| `ruff`              | Pythonコード         | 構文・スタイル・型推論 |
| `yamllint`          | config / manifest | 設定整合性検証     |
| `markdownlint-cli2` | docs/*.md         | ドキュメント体裁検証  |
| `pylint`            | 全体構造              | モジュール依存監査   |

---

### 14.8 CI/CD署名・監査（将来対応）

* リリース時に自動署名：

  * `SHA256` ハッシュ生成
  * `GPG` による署名
* 依存関係の監査：

  * `pip-audit` 自動実行
  * 結果を `artifacts/logs/audit.log` に保存
* 将来的に GitHub Releases へハッシュ付き成果物を添付予定。

---

### 14.9 バージョン整合テスト

* `schema_version`（設定・出力・エラーペイロード）を
  すべて統一管理し、互換性テストで自動比較。
* `pytest` 内で version mismatch を検出し、
  不一致時に警告を出力（非致命）。

---

### 14.10 テスト方針まとめ

* **pytest＋サンプルデータ**による再現性保証。
* **オフラインCIテスト＋GitHub Actions自動検証**で品質維持。
* **脆弱性・署名・lintチェック**を継続的に実施。
* **サンプルデータは人工生成限定**（個人情報を含まない）。
* **Apps SDK未対応時点でもCI構造は維持可能**。
* 今後のリリースフェーズで `--self-test` オプションを追加し、
  環境健全性をユーザーが自検証できるよう拡張予定。

---

## 15. バージョニング / 互換（仕様書版）

### 15.1 目的

本章は、`llm-logparser` のリリース管理および後方互換性の維持方針を定義する。  
CLI / Parser / Exporter / Config / Output の各コンポーネントが  
一貫したバージョン体系のもとで動作することを保証し、  
将来の拡張時に破壊的変更を最小化することを目的とする。

---

### 15.2 バージョン体系（SemVer準拠）

| 項目 | 意味 | 例 |
|------|------|----|
| **MAJOR** | 互換性を破壊する変更 | `1.0.0 → 2.0.0` |
| **MINOR** | 互換性を保った機能追加 | `1.1.0 → 1.2.0` |
| **PATCH** | バグ修正・微調整 | `1.2.1 → 1.2.2` |

#### 運用ポリシー
- `0.x` 系列は試験的（互換保証なし）。  
- `1.x` 以降で初めて**安定版（Stable Release）**と定義する。  
- `x.0.0` リリースではCHANGELOGで破壊的変更を明示。  
- `x.y.z-betaN` 形式はプレリリース／テスト版。  

---

### 15.3 コンポーネント別バージョン管理

| コンポーネント | バージョン依存 | 更新ポリシー |
|----------------|----------------|---------------|
| Core (parser/exporter) | 同期 | SemVer準拠 |
| Providers | 独立（例：`openai@1.1.0`） | Provider単位で小規模更新可能 |
| Config Schema | Coreと同期 | schema_version により識別 |
| Output Schema | Coreと同期 | schema_version により識別 |
| CLI | Coreと連動 | CLIヘルプに明記 |
| Viewer | 独立可能（UI系変更のみ） | API/Schemaの互換保証を前提 |

---

### 15.4 スキーマ互換ポリシー

#### 1. Config Schema

```yaml
schema_version: 1.0
profiles:
  ...
```

* **上位互換**を原則。旧バージョンは読み込み可。
* 不明キーは無視し、警告を発行（破壊的扱いしない）。
* 下位バージョン出力を求める場合は `--schema-downgrade` オプションを追加予定。

#### 2. Output Schema

```json
{
  "schema_version": "1.0",
  "thread_id": "...",
  "messages": [...]
}
```

* Markdown出力／meta.json／エラーペイロードに共通キー `schema_version` を付与。
* Viewerは `>=` 判定で互換性を確認し、差分がある場合は警告。
* schema_version の不一致はFATALではなくWARN扱い。

#### 3. Error Schema

* LPxxxxコード体系は固定化。
* 新規コード追加時のみMINORアップ。
* 既存コードの削除・再割当ては禁止。

---

### 15.5 破壊的変更の定義

破壊的変更（Breaking Change）とは以下のいずれかに該当するものを指す：

| 種別           | 内容                         |
| ------------ | -------------------------- |
| **Schema変更** | 必須キーの削除／型変更／構造変更           |
| **CLI引数変更**  | 既存オプションの削除・再定義             |
| **出力仕様変更**   | ファイル構造・命名規則・分割方式の変更        |
| **互換コード削除**  | 廃止済Providerや旧Config形式の完全削除 |

これらが発生する場合、必ず **MAJOR バージョンアップ** として扱う。

---

### 15.6 非破壊的変更の定義

非破壊的変更（Backward Compatible）とは：

* 新しいキー・フィールドの追加
* CLI引数の追加（削除・再定義を伴わない）
* 内部アルゴリズムの最適化（結果不変）
* ログ／統計出力の拡張
* i18n翻訳・ローカライズ追加

これらは **MINOR or PATCH** として扱う。

---

### 15.7 互換性テスト

CI上で以下の互換テストを自動実施する。

| テスト項目              | 検証内容                         |
| ------------------ | ---------------------------- |
| Config Schema Diff | 旧バージョンとYAMLスキーマの比較           |
| Output Schema Diff | 旧meta.jsonとの構造比較             |
| CLI互換              | ヘルプ出力のオプション一致検証              |
| Error Schema       | LPコードの重複・消失チェック              |
| Version Bump Check | setup.py / **init**.py の整合確認 |

例：

```bash
pytest tests/compat --baseline v1.0.0
```

---

### 15.8 バージョン宣言の位置

| 対象     | 定義箇所                        | 形式                         |
| ------ | --------------------------- | -------------------------- |
| Core   | `llm_logparser/__init__.py` | `__version__ = "1.0.0"`    |
| CLI    | `--version` 出力              | `"llm-logparser 1.0.0"`    |
| Config | `schema_version`            | `"1.0"`                    |
| Output | `meta.json`                 | `"schema_version": "1.0"`  |
| Docs   | `docs/requirements.md` 冒頭   | `"MVP schema_version 1.0"` |

---

### 15.9 CHANGELOG 運用

`CHANGELOG.md` に以下の形式で変更履歴を記録する：

```markdown
## [1.2.0] - 2025-11-02
### Added
- Viewerで検索機能を追加
- Provider設定にPerplexity対応を追加

### Fixed
- Exporterが分割閾値を誤判定するバグを修正
- Markdown出力で改行が欠落する問題を修正
```

`keep a changelog` 形式に準拠し、
リリースタグ（vX.Y.Z）と連動してGitHub Releasesに自動掲載。

---

### 15.10 廃止ポリシー（Deprecation Policy）

* 非推奨となる機能は **2リリース前に警告を発行**。
* `DeprecationWarning` をCLIで明示表示。
* 最低2つのMINORバージョン間は互換サポートを維持。
* Docs内で「Deprecated」セクションを設け、移行先を案内。

---

### 15.11 設計方針まとめ

* バージョニングは **SemVer準拠＋schema_version併用**。
* 破壊的変更はMAJOR更新として明示宣言。
* Config／Output／Error Schemaは個別に互換維持。
* CIで自動互換テストを実施し、差分を可視化。
* CHANGELOG運用で開発履歴を一元管理。
* Deprecated要素は段階的廃止ポリシーに従う。
* MVP以降の安定リリースは `1.x` 系を目標とする。

---

## 16. GitHub公開チェック（仕様書版）

### 16.1 目的

本章は、`llm-logparser` をGitHub上で安全かつ信頼性をもって公開・運用するための  
最低限のリポジトリ構成および監査項目を定義する。

公開リポジトリに対し、第三者（OSS利用者・レビュワー・監査担当）が  
「安心して参照・利用・Forkできる」状態を保証することを目的とする。

---

### 16.2 必須ファイル一覧

| ファイル名 | 目的 | ステータス |
|-------------|------|-------------|
| **LICENSE** | 利用・再配布条件の明示（MIT or Apache-2.0） | ✅ |
| **SECURITY.md** | 脆弱性報告窓口・対応ポリシー | ✅ |
| **CODE_OF_CONDUCT.md** | OSS行動規範 | ✅ |
| **CONTRIBUTING.md** | コントリビューション手順・開発ルール | ✅ |
| **CHANGELOG.md** | バージョン履歴・変更履歴 | ✅ |
| **README.md** | プロジェクト概要・使用例・スクリーンショット | ✅ |
| **THIRD_PARTY_NOTICES.md** | 依存ライセンスの明示 | ✅ |
| **.github/workflows/ci.yml** | 自動テスト（GitHub Actions） | ✅ |
| **docs/error-codes.md** | エラーコード一覧（10章連動） | ✅ |
| **docs/provider-guide.md** | Provider実装ガイドライン | ✅ |
| **docs/output-contract.md** | 出力仕様／スキーマ定義 | ✅ |

---

### 16.3 README.md 必須要素

READMEは**ユーザー・開発者・レビュワーの入口**。  
次の情報を必ず含める。

| 項目 | 内容 |
|------|------|
| プロジェクト名 | `LLM Log Parser` |
| 概要 | LLMエクスポートJSONをMarkdownへ変換するCLIツール |
| 特徴 | 完全ローカル動作・Apps SDK準備設計・軽量HTMLビューア付き |
| スクリーンショット | CLI出力例／Viewer画面例 |
| インストール方法 | `pip install .` または `make setup` |
| 使い方 | CLI例＋主要オプション（--provider, --input, --outdir等） |
| 出力例 | Markdownプレビューまたはサンプルリンク |
| 開発状況 | MVP / Stable / In Progress などの明示 |
| 依存関係 | Pythonバージョン・主要ライブラリ一覧 |
| ライセンス | MIT or Apache-2.0明記 |
| 貢献者 | ContributorsセクションまたはGitHub自動生成バッジ |
| 将来展望 | マルチプロバイダ対応・Apps SDK統合予定など |

---

### 16.4 セキュリティチェック項目

| チェック項目 | 内容 | 状態 |
|---------------|------|------|
| Secrets検出 | リポジトリにトークン／キー類を含まない | ✅ |
| SBOM出力 | `pip freeze > SBOM.txt` により依存追跡可能 | ✅ |
| pip-audit | CI上で依存ライブラリ脆弱性をスキャン | ✅ |
| Dependabot | 自動更新通知を有効化 | ✅ |
| Actions署名 | `actions/checkout@v4` 等のバージョン固定 | ✅ |
| Lint／Format | yamllint / markdownlint / ruff による自動整形 | ✅ |

---

### 16.5 CI / CD チェックフロー

GitHub Actions (`.github/workflows/ci.yml`) で以下を自動実行：

1. **lint**（フォーマット検証）  
2. **test**（pytest実行）  
3. **audit**（pip-auditによる依存監査）  
4. **build**（wheel生成・署名確認）  
5. **deploy**（タグ付きコミット時のみリリース）

#### 成功条件：
- すべてのジョブが成功 → PR自動マージ可  
- 失敗時は「required check」としてブロック  

---

### 16.6 ドキュメント構成ポリシー

`docs/` 以下は3系統に分割する。

| カテゴリ | 目的 | ファイル例 |
|-----------|------|------------|
| **仕様書** | 本要件定義書、出力仕様、エラー契約 | `requirements.md`, `output-contract.md` |
| **開発ガイド** | Provider追加手順、Config拡張例 | `provider-guide.md` |
| **運用ガイド** | CLI利用例、Viewer操作説明 | `usage-guide.md`, `troubleshooting.md` |

#### 表記ルール：
- 見出しは英語タイトル＋日本語補足（例：「Error Codes / エラーコード一覧」）  
- すべてMarkdown形式（HTML禁止）  
- 画像は `/docs/assets/` に格納（相対パスリンク）  

---

### 16.7 公開ポリシー（OSS運用指針）

1. **オープンソース公開**  
   - `main` ブランチは常に安定版。  
   - `dev/*` ブランチで開発、PR経由で統合。

2. **Issue / Pull Request運用**  
   - Issue Template・PR Templateを整備。  
   - 既知問題は `Known Issues` にラベル分類。  

3. **リリース管理**  
   - タグ：`vX.Y.Z`  
   - 自動CHANGELOG生成（`github_changelog_generator`対応予定）

4. **署名付きリリース**  
   - `dist/*.whl` に GPG署名＋SHA256ハッシュを添付。  

5. **外部コントリビューションの取り扱い**  
   - CLA（Contributor License Agreement）を不要とするが、  
     PR時に明示的に「自身の著作であること」を確認。  

---

### 16.8 GitHubバッジ（推奨）

READMEヘッダに以下を配置：

```

[![CI](https://github.com/Syun-tnb/llm-logparser/actions/workflows/ci.yml/badge.svg)](https://github.com/Syun-tnb/llm-logparser/actions)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)]()
[![Status](https://img.shields.io/badge/Status-MVP-green.svg)]()

```

これによりプロジェクトの信頼度を即座に可視化。

---

### 16.9 公開前セルフチェックリスト

| チェック項目 | 状態 |
|---------------|------|
| ✅ LICENSE の整合（MIT / Apache-2.0） |
| ✅ README に使用例・出力例を記載 |
| ✅ SECURITY.md に報告手順明記 |
| ✅ CHANGELOG 最新版更新済み |
| ✅ CIが全ジョブ成功 |
| ✅ 依存ライブラリ脆弱性ゼロ |
| ✅ 個人情報を含むデータなし |
| ✅ 署名・ハッシュ出力確認済み |

---

### 16.10 設計方針まとめ

- GitHub上での**安全性・信頼性・透明性**を最優先。  
- 公開前に **法務・セキュリティ・CI全チェックを通過**。  
- OSS文化（透明な履歴と行動規範）を尊重する構成を採用。  
- すべての自動テストとLintを「必須チェック」として設定。  
- MVP時点でもOSS水準のリポジトリ品質を維持する。

---

## 17. 非対象（MVP範囲外）

本章では、MVP（Minimum Viable Product）の範囲に含めない機能・仕様を定義する。  
目的は「段階的な開発計画の明確化」と「仕様の肥大化防止」。

---

### 17.1 高度マージ機能（除外理由：仕様複雑化）

- 複数のエクスポートファイルを時間軸で統合し、  
  会話スレッドを自動再構築する高度マージ処理。  
- JSON構造・日付整合・多言語要素などの整合性検証が必要なため、  
  **正確性とパフォーマンスの両立が困難**。  
- 将来対応予定：`--merge-strategy full|diff|auto` オプションとして拡張。

---

### 17.2 フル機能GUI（除外理由：別モジュール設計）

- ドラッグ＆ドロップ、複雑フィルタ、リアルタイムプレビュー等を持つ  
  **完全GUI版Viewer** は対象外。  
- MVPではCLI＋HTMLビューアを提供。  
- 将来的に **Electron / Tauri** ベースでGUI拡張予定。

---

### 17.3 異プロバイダ統合（除外理由：識別精度・語彙差異）

- OpenAI / Claude / Gemini / Perplexity 等、  
  異プロバイダのスレッドを同一人物／同一会話として統合する処理は非対応。  
- 語彙・構造・識別子差の吸収ロジックが未確立。  
- 将来拡張時には `"conversation_fingerprint"` 構造の導入を検討。

---

### 17.4 自動マスキング（除外理由：安全より誤検知リスク）

- 個人情報や機密文言を自動検出・置換する機能。  
- NLP依存部分が不安定かつ誤検知リスクが高い。  
- 将来オプトイン設定として `--mask-sensitive` を導入予定。

---

### 17.5 Apps SDK公開アプリ化（除外理由：段階的統合）

- Apps SDK との直接統合（＝ChatGPT内実行アプリ化）は  
  現段階では対象外。  
- 現仕様では「SDK対応しやすい構造」に留める。  
- 将来的には **Apps SDK JSON Schema 準拠化** を計画。

---

### 17.6 自動要約・タグ付け（除外理由：外部API依存）

- LLM推論を利用したスレッド要約やトピック抽出。  
- 外部APIコールを伴うため「完全オフライン設計」と相反。  
- 将来、`--enable-network` 設定時の追加モジュールとして検討。

---

### 17.7 メタ編集・コメント挿入機能（除外理由：責務分離）

- Markdown出力後にコメント追記・要約注釈などを行う機能。  
- Editor的責務に踏み込むため、Viewer以降の範疇。  
- 将来GUI版Viewerで「インライン編集」として再検討。

---

### 17.8 その他（将来検討枠）

| 項目 | 内容 | 備考 |
|------|------|------|
| **差分抽出ツール** | 前回出力との比較差分を可視化 | CLI拡張案あり |
| **AIメトリクス出力** | LLM応答長・反応傾向など統計出力 | プロバイダ別統計として拡張 |
| **プラグインシステム** | `providers/` に外部追加可能な構造 | v1.0以降に検討 |

---

### 17.9 方針まとめ

- MVPは「**ローカル完結・変換専用ツール**」に徹する。  
- ユーザー操作が増える領域（編集・統合・要約）は明示的に除外。  
- 外部連携を伴う処理は安全が確認されるまで**封印ポリシー**。  
- 今後は「Plugin + SDK対応」による拡張で段階的開放を行う。  
- 異プロバイダ統合や自動マスキングといった**“責務を越える処理”は実装対象外**とし、  
  Parserは常に「**触らない・変えない・壊さない**」原則を維持する。

---

## 18. 将来拡張メモ

本章は、MVP以降に検討される追加要素や改善案を整理したものである。  
優先度・実装難易度を踏まえて、段階的開発の参考とする。

---

### 18.1 短期（v1.x 想定）

| 項目 | 内容 | 備考 |
|------|------|------|
| **全文検索インデックス** | Markdown出力を横断検索可能にする簡易インデクサ。 | SQLite / Meilisearch 連携候補 |
| **出力フォーマット追加** | HTML/TXT/JSON差分などを追加。 | Markdown整形ルーチン共通化 |
| **メタ情報強化** | Thread単位で model, token数, duration 等を付加。 | meta.json拡張 |

---

### 18.2 中期（v2.x 想定）

| 項目 | 内容 | 備考 |
|------|------|------|
| **要約・タグ自動付与** | LLMまたは外部APIによる要約生成／カテゴリ推定。 | `--enable-network` 有効時のみ動作 |
| **再試行／ヘルスチェック** | `--self-test` によりパーサー自身の健全性検証。 | JSON構造／I/O権限確認など |
| **差分統合モード** | 旧出力との比較差分を可視化。 | CLIオプション化想定 |

---

### 18.3 長期（構想／夢フェーズ）

| 項目 | 内容 | 状況 |
|------|------|------|
| **Apps SDK対応** | OpenAI Apps上で動作するViewerアプリ化。 | 設計思想のみ残置中 |
| **完全GUI版（Electron/Tauri）** | CLIを内包したデスクトップツール。 | HTML Viewerの発展形 |
| **AI連携分析モード** | LLM応答傾向の統計化、推論パターン抽出。 | 研究用途想定 |
| **ローカルLLM連携** | Ollama / LM Studio等との統合。 | オフライン補完想定 |

---

### 18.4 方針まとめ

- 将来機能は **「独立モジュール／オプトイン」** として提供し、  
  核心ロジック（parser/exporter）は変更しない。  
- ネットワーク依存や外部LLM利用は明示的フラグで制御。  
- 「夢」レベルの構想も文書として残し、将来設計の連続性を担保する。

---

### 一文サマリ

> **「フルエクスポートでも、差分をきれいにMarkdownへ。MVPは完全ローカル＋簡易HTMLビュー、Apps SDK対応の芽を残す。」**
