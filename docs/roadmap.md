# Roadmap

- [x] CLI MVP – Markdown export, deduplication, thread splitting
- [ ] Minimal HTML Viewer – index + search bar
- [ ] Multi-provider adapters (Claude, Gemini, etc.)
- [ ] Apps SDK integration (experimental branch)
- [ ] Full GUI (desktop, later stage)

---

## MVP Roadmap — llm-logparser

### 🎯 Phase 1: Core Stability
| Priority | Item | Status | Notes |
|-----------|-------|--------|-------|
| ⭐⭐⭐ | Parser堅牢化 | 🔧 In progress | ストリーム処理 / fail-fast 実装中 |
| ⭐⭐ | Exporter Markdown生成 | 🕓 Pending | フロントマター・formatting導線整備 |
| ⭐⭐ | CLIチェーン実行確立 | ✅ Done | parse→export 一括実行動作OK |
| ⭐ | 分割ポリシー実装 | 🕓 Pending | size/count両対応、プレビュー予定 |

### ⚙️ Phase 2: Operation & Resilience
| Priority | Item | Status | Notes |
|-----------|-------|--------|-------|
| ⭐⭐⭐ | update_time差分キャッシュ | 🕓 Pending | Parser→Cache連携構築予定 |
| ⭐⭐ | エラーハンドリング統合 | 🔧 In progress | ログ粒度＆exit_code整理中 |
| ⭐ | ロケール/TZサニタイズ | 🕓 Pending | ZoneInfo + ファイル名安全化 |

### 🌐 Phase 3: Output & Viewer
| Priority | Item | Status | Notes |
|-----------|-------|--------|-------|
| ⭐⭐ | Viewer雛形HTML | 🕓 Planned | index+list+detail最小構成 |
| ⭐ | i18n辞書整備 | ✅ Done | locale辞書構造のみ定義済み |
| ⭐ | Quickstart / README更新 | 🕓 Planned | CLI使用例＆出力例追加予定 |

---

