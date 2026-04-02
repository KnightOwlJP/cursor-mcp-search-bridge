# MCPツール仕様書

## 1. 統一参照データ構造（Reference Schema）

全ツールが返す `references` 配列の各要素は以下の構造に従う:

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `ref_id` | string | ✅ | 一意の参照ID（例: `REF-LOCAL-001`） |
| `source_type` | string | ✅ | `"api"` / `"internal_db"` / `"file"` |
| `source_name` | string | ✅ | `"HF"` / `"Gemini"` / `"Local"` / `"Files"` |
| `title` | string | ✅ | 参照タイトル |
| `location` | object | ✅ | ソース固有の位置情報 |
| `location.file_path` | string? | - | ファイルパス（file出典時） |
| `location.url` | string? | - | URL（API出典時） |
| `location.doc_id` | string? | - | ドキュメントID（DB出典時） |
| `location.page` | int? | - | ページ番号（PDF時） |
| `location.model` | string? | - | モデル名（API出典時） |
| `excerpt` | string | ✅ | 該当箇所の抜粋（最大200文字） |
| `relevance_score` | float | ✅ | 関連度スコア（0.0〜1.0） |
| `viewer_url` | string | ✅ | 参照ビューアURL |

## 2. references_display 形式

マークダウンテーブルで参照一覧を表示。各行にワンクリックリンク付き:

```markdown
📚 **参照元**

| # | 出典 | タイトル | 該当箇所 | 関連度 | 詳細 |
|---|------|---------|---------|--------|------|
| [1] | 内部DB: DOC-001 | 安全注入系統の設計基準 | SISは原子炉冷却材... | 92% | [📄開く](http://localhost:8000/refs/REF-LOCAL-001) |
```

## 3. Tool: search_hf

| 項目 | 内容 |
|------|------|
| **名前** | `search_hf` |
| **説明** | Hugging Face APIでプラント設計情報を検索 |
| **パラメータ** | `query: str` (必須) |
| **モデル** | `HuggingFaceH4/zephyr-7b-beta` |
| **フォールバック** | APIキー未設定時はモックデータ返却 |

**レスポンス例:**
```json
{
  "source": "HF",
  "query": "安全注入系統",
  "results": [{"answer": "...", "confidence": 0.75}],
  "references": [{"ref_id": "REF-HF-001", "source_type": "api", ...}],
  "references_display": "📚 **参照元**\n...",
  "metadata": {"timestamp": "...", "response_time_ms": 1234}
}
```

## 4. Tool: search_gemini

| 項目 | 内容 |
|------|------|
| **名前** | `search_gemini` |
| **説明** | Google Gemini APIでプラント設計情報を検索 |
| **パラメータ** | `query: str` (必須) |
| **モデル** | `gemini-2.5-flash` |
| **フォールバック** | APIキー未設定時はモックデータ返却 |

## 5. Tool: search_local_rag

| 項目 | 内容 |
|------|------|
| **名前** | `search_local_rag` |
| **説明** | 内蔵プラント設計DB (12件) から TF-IDF 検索 |
| **パラメータ** | `query: str` (必須), `top_k: int = 3` |
| **内蔵データ** | 安全注入, ポンプ, 配管, 熱交換器, バルブ, 圧力容器, 計装, 防食, 耐震, ECCS, 排水, HVAC |

## 6. Tool: search_files

| 項目 | 内容 |
|------|------|
| **名前** | `search_files` |
| **説明** | ローカルファイルからテキスト抽出+TF-IDF検索 |
| **パラメータ** | `query: str` (必須), `directory: str = ""`, `file_types: str = "docx,xlsx,pdf,txt,csv"`, `top_k: int = 3` |
| **対応形式** | .docx (python-docx), .xlsx (openpyxl), .pdf (pypdf), .txt, .csv |

## 7. Tool: read_file

| 項目 | 内容 |
|------|------|
| **名前** | `read_file` |
| **説明** | 指定ファイルの全文テキスト抽出 |
| **パラメータ** | `file_path: str` (必須) |
| **制限** | テキスト抽出100KB上限、レスポンスは先頭5000文字 |

## 8. Tool: list_files

| 項目 | 内容 |
|------|------|
| **名前** | `list_files` |
| **説明** | ディレクトリ内の対応ファイル一覧取得 |
| **パラメータ** | `directory: str = ""`, `file_types: str = "docx,xlsx,pdf,txt,csv"` |
| **参照データ** | なし（メタデータのみ返却） |

## 9. Tool: search_all

| 項目 | 内容 |
|------|------|
| **名前** | `search_all` |
| **説明** | 複数ソースを並列検索し統合比較 |
| **パラメータ** | `query: str` (必須), `sources: str = "hf,gemini,local,files"`, `mode: str = "all"` |
| **並列実行** | `ThreadPoolExecutor(max_workers=4)`, timeout=60秒 |

### 検索モード (`mode` パラメータ)

| モード | 動作 | レスポンスに含まれる追加フィールド |
|--------|------|--------------------------------|
| `"all"` (デフォルト) | `sources` で指定された全ソースを並列検索 | `"mode": "all"` |
| `"auto"` | クエリ内容を分析し、関連度の高いソースのみ自動選択 | `"mode": "auto"`, `"source_selection": {...}` |

#### `mode="auto"` の source_selection 構造:
```json
{
  "selected": ["hf", "local"],
  "scores": {"hf": 0.253, "gemini": 0.05, "local": 0.235, "files": 0.05},
  "reasons": {"hf": "キーワード一致: 設計基準", "local": "キーワード一致: 安全注入", ...},
  "threshold": 0.15
}
```

## 10. Tool: recommend_sources

| 項目 | 内容 |
|------|------|
| **名前** | `recommend_sources` |
| **説明** | 検索クエリに最適な情報ソースを推薦 |
| **パラメータ** | `query: str` (必須) |
| **用途** | LLMが search_all 実行前にソース選択を判断するために使用 |

**レスポンス例:**
```json
{
  "query": "安全注入系統の設計基準",
  "recommendation": "クエリに対して、以下のソースを推薦します: HF, Local。...",
  "sources": [
    {"source_key": "hf", "name": "HF (Hugging Face)", "relevance_score": 0.253, "selected": true, ...},
    {"source_key": "local", "name": "内蔵DB", "relevance_score": 0.235, "selected": true, ...},
    ...
  ],
  "suggested_sources_param": "hf,local"
}
```

### 使い方パターン

**パターン1: 全ソース一括検索**
```
search_all(query="...", mode="all")
```

**パターン2: サーバー側自動選択**
```
search_all(query="...", mode="auto")
```

**パターン3: LLM側で判断して絞り込み**
```
recommend_sources(query="...")  → suggested_sources_param を取得
search_all(query="...", sources=suggested_sources_param, mode="all")
```

## 11. PoC→本番 ツール移行対応表

| PoC ツール | 本番ツール | 主な変更点 |
|-----------|-----------|-----------|
| `search_hf` | `search_design_db` | HF API → 設計DB REST API、認証追加 |
| `search_gemini` | `search_dms` | Gemini → 文書管理API、認証追加 |
| `search_local_rag` | `search_vector_db` | TF-IDF → Azure AI Search |
| `search_files` | `search_sharepoint` | ローカル → Microsoft Graph API |
| `read_file` | `read_document` | ローカル → DMS API経由 |
| `list_files` | `list_documents` | ローカル → DMS API経由 |
| `search_all` | `search_all` | sources パラメータ値を変更、mode は維持 |
| `recommend_sources` | `recommend_sources` | ソースプロファイルを社内API定義に置換 |

## 11. 社内API接続用テンプレート

新しい社内APIを追加する手順:

1. `_call_<system>_api()` 内部ヘルパーを実装
2. `_make_reference()` で参照データを生成
3. `_store_reference()` でストアに登録
4. `@mcp.tool` デコレータでMCPツールとして公開
5. `search_all` の `call_map` にエントリ追加

## 12. エラーハンドリング

| エラー | 処理 |
|--------|------|
| APIタイムアウト | 30秒でタイムアウト、フォールバックモック返却 |
| APIキー未設定 | フォールバックモック返却（`source_available: false`） |
| ファイル未検出 | エラーメッセージ付きレスポンス |
| ライブラリ未インストール | 条件付きインポート、エラーメッセージ返却 |
| パストラバーサル | `DOCS_DIR` 外のファイルアクセスを拒否 |
