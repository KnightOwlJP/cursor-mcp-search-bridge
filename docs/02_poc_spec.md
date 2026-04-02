# PoC仕様書

## 1. PoC目標

複数の情報ソース（外部API + ローカルデータ + ローカルファイル）を統合検索する MCP サーバーの**技術的実現性**を検証する。

## 2. システム構成図

```
┌─────────────────────────────────────────────────────────────┐
│  Cursor Composer (無料版)                                     │
│    └── MCP ツール呼び出し                                      │
│         ↓ Streamable HTTP (JSON-RPC 2.0)                     │
├─────────────────────────────────────────────────────────────┤
│  FastMCP Server (localhost:8000)                              │
│  ┌──────────────┬──────────────┬──────────────┐              │
│  │ /mcp         │ /refs        │ /api/refs    │              │
│  │ MCPエンドポイント│ 参照ビューア  │ 参照JSON API │              │
│  └──────┬───────┴──────┬───────┴──────┬───────┘              │
│         │              │              │                       │
│  ┌──────▼──────────────▼──────────────▼───────┐              │
│  │         7 MCP ツール + 参照ストア            │              │
│  │  search_hf / search_gemini / search_local_rag│              │
│  │  search_files / read_file / list_files       │              │
│  │  search_all (統合検索)                        │              │
│  └──────────────────────────────────────────────┘              │
├──────────────────┬──────────────────┬─────────────────────────┤
│  HF Inference API│  Gemini API      │  ローカルリソース          │
│  (インターネット)  │  (インターネット)  │  ・内蔵DB (12件)         │
│                  │                  │  ・sample_docs/ (.txt等)  │
└──────────────────┴──────────────────┴─────────────────────────┘
```

## 3. MCPツール一覧

| ツール名 | 説明 | 主要パラメータ |
|----------|------|----------------|
| `search_hf` | HF Inference API検索 | `query: str` |
| `search_gemini` | Gemini API検索 | `query: str` |
| `search_local_rag` | 内蔵DB TF-IDF検索 | `query: str`, `top_k: int=3` |
| `search_files` | ローカルファイル検索 | `query: str`, `directory: str`, `file_types: str`, `top_k: int=3` |
| `read_file` | ファイル全文読取 | `file_path: str` |
| `list_files` | ファイル一覧取得 | `directory: str`, `file_types: str` |
| `search_all` | 全ソース統合検索 | `query: str`, `sources: str="hf,gemini,local,files"` |

## 4. 統一参照データ仕様

全ツールが以下の統一構造で参照データを返却する:

```json
{
  "ref_id": "REF-LOCAL-001",
  "source_type": "internal_db",
  "source_name": "Local",
  "title": "安全注入系統の設計基準",
  "location": {
    "file_path": null,
    "url": null,
    "doc_id": "DOC-001",
    "page": null
  },
  "excerpt": "安全注入系統（SIS）は原子炉冷却材喪失事故時に...",
  "relevance_score": 0.92,
  "viewer_url": "http://localhost:8000/refs/REF-LOCAL-001"
}
```

### source_type 一覧

| source_type | 説明 | location主キー |
|-------------|------|---------------|
| `api` | 外部API応答 | `url`, `model` |
| `internal_db` | 内蔵ドキュメント | `doc_id` |
| `file` | ローカルファイル | `file_path`, `page` |

## 5. 参照ビューア仕様

| エンドポイント | 機能 |
|---------------|------|
| `GET /refs` | 参照一覧（カードUI、フィルタ機能） |
| `GET /refs/{ref_id}` | 参照詳細（全文表示+excerptハイライト+自動スクロール） |
| `GET /api/refs` | JSON形式の参照データ一覧 |
| `GET /api/refs/{ref_id}` | JSON形式の参照データ詳細 |

- ハイライト: `<mark>` タグで該当箇所を黄色強調
- ダークモード: `prefers-color-scheme` で自動切替
- インメモリストア: 最大100件保持（FIFO）

## 6. 動作フロー

1. ユーザーがComposerに質問を入力
2. ComposerがMCPツール（例: `search_all`）を呼び出し
3. MCPサーバーが指定ソースを**並列検索**（ThreadPoolExecutor）
4. 各ソースが参照データ付き結果を返却
5. `_merge_results()` で統合・比較分析
6. 参照データを`_refs_store`に登録
7. `references_display`（リンク付きマークダウンテーブル）を生成
8. JSON文字列としてComposerに返却
9. Composerが参照元付き回答をユーザーに表示
10. ユーザーが「📄開く」リンクをクリック → ブラウザで参照詳細を確認

## 7. 使用技術スタック

| 技術 | バージョン | 用途 |
|------|----------|------|
| Python | 3.10+ | ランタイム |
| FastMCP | 3.x | MCPサーバーフレームワーク |
| Starlette | (FastMCP依存) | HTTPルーティング |
| uvicorn | latest | ASGIサーバー |
| requests | latest | HF API呼び出し |
| google-genai | latest | Gemini API呼び出し |
| python-docx | latest | .docxテキスト抽出 |
| openpyxl | latest | .xlsxテキスト抽出 |
| pypdf | latest | .pdfテキスト抽出 |

## 8. 前提条件・制約

- Python 3.10以上が必要
- APIキーは任意（未設定時はフォールバックモックデータで動作）
- ローカルファイル検索は `DOCS_DIR` 配下に限定（パストラバーサル防止）
- ファイルテキスト抽出は最大100KB
- 参照excerpt最大200文字

## 9. 成功基準

- [ ] `uvicorn poc_mcp:app` でサーバーが正常起動
- [ ] 7つのMCPツールがツール一覧に表示
- [ ] `search_local_rag` が関連ドキュメントを返却
- [ ] `search_files` がサンプルファイルから検索結果を返却
- [ ] `search_all` が複数ソースの統合結果を返却
- [ ] 全検索ツールのレスポンスに `references` + `references_display` が含まれる
- [ ] `references_display` にワンクリックビューアリンクが含まれる
- [ ] `/refs/{ref_id}` でハイライト付き参照詳細が表示

## 10. PoC範囲外

以下は本PoCの範囲外とし、Phase 1以降で対応する:
- ユーザー認証・認可
- スケーラビリティ（マルチワーカー）
- SLA・可用性保証
- 本番デプロイメント（コンテナ化等）
- 社内実APIとの接続
- Azure OpenAI統合
