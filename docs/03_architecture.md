# アーキテクチャ設計書

## 1. PoC構成

```
┌─────────────────────────────┐
│     開発PC（ローカル環境）     │
│                              │
│  ┌────────────────────────┐  │
│  │ Cursor Composer (無料版) │  │
│  │   ↓ Streamable HTTP    │  │
│  └────────┬───────────────┘  │
│           │                  │
│  ┌────────▼───────────────┐  │
│  │ FastMCP Server         │  │
│  │ (127.0.0.1:8000)      │  │
│  │                        │  │
│  │  /mcp   → MCPプロトコル │  │
│  │  /refs  → 参照ビューア  │  │
│  │  /api/  → 参照JSON API │  │
│  │                        │  │
│  │  ┌─────────────────┐   │  │
│  │  │ MCP ツール (7個) │   │  │
│  │  ├─────────────────┤   │  │
│  │  │ TF-IDF エンジン  │   │  │
│  │  ├─────────────────┤   │  │
│  │  │ ファイル抽出     │   │  │
│  │  ├─────────────────┤   │  │
│  │  │ 参照ストア       │   │  │
│  │  └─────────────────┘   │  │
│  └────────┬───────────────┘  │
│           │                  │
│  ┌────────▼───────────────┐  │
│  │ sample_docs/           │  │
│  │ (.txt/.docx/.xlsx/.pdf)│  │
│  └────────────────────────┘  │
└──────────┬──────┬────────────┘
           │      │ (インターネット)
    ┌──────▼──┐ ┌─▼──────────┐
    │HF API   │ │Gemini API  │
    │(無料枠) │ │(無料枠)    │
    └─────────┘ └────────────┘
```

## 2. 本番構成

```
┌─────────────────────────────────────────────────────────┐
│                    社内ネットワーク                        │
│                                                          │
│  ┌──────────────────┐      ┌──────────────────────┐     │
│  │ 社内PC            │      │ Azure OpenAI          │     │
│  │ Cursor Enterprise │─────→│ (社内テナント)         │     │
│  │  ↓ MCP           │      │ GPT-4o デプロイ        │     │
│  └────────┬─────────┘      │ Private Endpoint      │     │
│           │                 └──────────────────────┘     │
│           │ Streamable HTTP                              │
│  ┌────────▼──────────────────────────────────────┐      │
│  │ MCP Gateway Server (AKS / Azure Container Apps)│      │
│  │                                                │      │
│  │  ┌─────────────┐  ┌──────────────┐            │      │
│  │  │ search_     │  │ search_      │            │      │
│  │  │ design_db() │  │ dms()        │            │      │
│  │  └──────┬──────┘  └──────┬───────┘            │      │
│  │         │                │                     │      │
│  │  ┌──────▼──────┐  ┌─────▼────────┐            │      │
│  │  │ search_     │  │ search_      │            │      │
│  │  │ maintenance│  │ sharepoint() │            │      │
│  │  └──────┬──────┘  └──────┬───────┘            │      │
│  │         │                │                     │      │
│  │  ┌──────▼────────────────▼──────┐              │      │
│  │  │ search_all() → 統合・比較     │              │      │
│  │  │ 参照ストア (Redis)            │              │      │
│  │  │ 参照ビューア                   │              │      │
│  │  └──────────────────────────────┘              │      │
│  └────────┬──────────────┬───────────────────────┘      │
│           │              │                               │
│  ┌────────▼──────┐ ┌────▼─────────────┐                 │
│  │ 設計DB API    │ │ 文書管理 API      │                 │
│  │ 保全履歴 API  │ │ SharePoint API    │                 │
│  │ 図面管理 API  │ │ Azure AI Search   │                 │
│  └───────────────┘ └──────────────────┘                 │
│                                                          │
│  ── Azure VNet + Private Endpoints ──                    │
└─────────────────────────────────────────────────────────┘
```

## 3. コンポーネント間インターフェース

| 接続元 | 接続先 | プロトコル | 認証方式 |
|--------|--------|-----------|----------|
| Cursor | MCP Server | Streamable HTTP (JSON-RPC 2.0) | なし(PoC) / Azure AD(本番) |
| Cursor | Azure OpenAI | HTTPS | Azure API Key |
| MCP Server | HF API | HTTPS | Bearer Token |
| MCP Server | Gemini API | HTTPS | API Key |
| MCP Server | ローカルファイル | ファイルシステム | OS権限 |
| MCP Server | 社内API（本番） | HTTPS | OAuth2.0 / mTLS |
| ブラウザ | 参照ビューア | HTTP | なし(PoC) / SSO(本番) |

## 4. データフロー

```
ユーザー → Composer → MCP tool call (JSON-RPC)
    → FastMCP Server
    → ThreadPoolExecutor で並列実行
        ├── _call_hf_api()      → HF Inference API → 応答
        ├── _call_gemini_api()  → Gemini API → 応答
        ├── _call_local_rag()   → TF-IDF 検索 → 結果
        └── _call_file_search() → ファイル抽出+TF-IDF → 結果
    → 各ヘルパーが _make_reference() で参照データ生成
    → _store_reference() でストアに登録
    → _merge_results() で統合
    → _format_references_display() でリンク付きテーブル生成
    → JSON 文字列として Composer に返却
    → Composer がユーザーに参照元付き回答を表示
    → ユーザーが「📄開く」クリック → ブラウザで /refs/{ref_id}
```

## 5. PoC→本番の移行ポイント

| PoC コンポーネント | 本番コンポーネント | 移行作業 |
|-------------------|-------------------|----------|
| HF Inference API | 社内システムA検索API | API呼出し先・認証変更 |
| Gemini API | 社内システムB検索API | API呼出し先・認証変更 |
| 内蔵TF-IDF (12件) | Azure AI Search | ベクトルDB接続実装 |
| sample_docs/ ローカル | SharePoint / DMS API | API経由のファイル取得に変更 |
| localhost:8000 | AKS / ACA | コンテナ化、Helm Chart作成 |
| インメモリ参照ストア | Redis | Redis接続実装 |
| Cursor無料版 | Cursor Business/Enterprise | ライセンス・SSO設定 |
| APIキー直接設定 | Azure Key Vault | シークレット管理の一元化 |
