# PlantDesignRAG — Cursor MCP 統合検索サーバー (PoC)

Cursor Composer から複数の情報ソースを統合検索する MCP (Model Context Protocol) サーバーです。  
プラント設計のナレッジ検索を、AI アシスタント経由で**参照元付き**で実現します。

## ✨ 特徴

- **8つのMCPツール**: HF API / Gemini API / 内蔵DB / ローカルファイル の検索 + 統合比較 + ソース推薦
- **参照データ付きレスポンス**: 全検索結果に出典情報（ソース種別・タイトル・抜粋・関連度）を統一構造で付与
- **ワンクリック参照ビューア**: 回答中の「📄開く」リンクをクリック → ブラウザで該当箇所をハイライト表示
- **1ファイル完結**: `poc_mcp.py` のみでサーバー + ビューア + 全ツールが動作
- **2つの検索モード**: `mode="all"`（全ソース検索）と `mode="auto"`（LLM/クエリ分析によるソース自動選択）
- **フォールバック対応**: APIキーなしでもローカル検索は完全動作

## 📁 ファイル構成

```
.
├── poc_mcp.py                  # MCPサーバー本体（7ツール + 参照ビューア）
├── sample_docs/                # テスト用サンプルドキュメント
│   ├── pump_selection_guide.txt
│   ├── heat_exchanger_spec.txt
│   └── safety_injection_report.txt
├── .cursor/mcp.json            # Cursor MCP設定
├── setup.sh                    # Linux/Mac セットアップスクリプト
├── setup.bat                   # Windows セットアップスクリプト
├── test_queries.txt            # Composer テスト用クエリ集
├── README.md                   # 本ファイル
└── docs/                       # 構造化ドキュメント
    ├── 01_project_overview.md  # プロジェクト概要
    ├── 02_poc_spec.md          # PoC仕様書
    ├── 03_architecture.md      # アーキテクチャ設計書
    ├── 04_production_design.md # 本番設計書
    ├── 05_security_design.md   # セキュリティ設計書
    ├── 06_roadmap.md           # ロードマップ
    ├── 07_mcp_tool_spec.md     # MCPツール仕様書
    └── 08_operation_guide.md   # 運用・構築ガイド
```

## 🚀 クイックスタート

### 1. 依存インストール

```bash
pip install fastmcp google-genai requests uvicorn python-docx openpyxl pypdf
```

### 2. 環境変数設定（任意）

```bash
# .env ファイルを作成（APIキーは任意、未設定でもフォールバック動作）
cat > .env << 'EOF'
HF_API_TOKEN=YOUR_HF_TOKEN
GEMINI_API_KEY=YOUR_GEMINI_KEY
DOCS_DIR=./sample_docs
EOF
```

APIキーの取得方法:
- **Hugging Face**: https://huggingface.co/settings/tokens （「Inference」権限で新規作成）
- **Gemini**: https://aistudio.google.com/apikey （「Create API key」）

### 3. サーバー起動

```bash
# セットアップスクリプト（インストール+起動を一括）
bash setup.sh

# または直接起動
uvicorn poc_mcp:app --host 127.0.0.1 --port 8000
```

### 4. Cursor で使用

1. `.cursor/mcp.json` は同梱済み（サーバー起動後に Cursor を再起動）
2. Composer で質問: 「search_allで安全注入系統の設計事例を全ソースから検索して比較して」
3. 回答の「📄開く」リンクをクリック → ブラウザで参照箇所を確認

## 🔗 エンドポイント

| URL | 用途 |
|-----|------|
| `http://localhost:8000/mcp` | MCP Streamable HTTP（Cursor接続用） |
| `http://localhost:8000/refs` | 参照ビューア一覧（ブラウザ） |
| `http://localhost:8000/refs/{ref_id}` | 参照詳細+ハイライト（ブラウザ） |
| `http://localhost:8000/api/refs` | 参照データ JSON API |

## 🛠️ MCPツール一覧

| ツール | 説明 | 本番での置換先 |
|--------|------|---------------|
| `search_hf` | HF Inference API 検索 | 社内システムA |
| `search_gemini` | Gemini API 検索 | 社内システムB |
| `search_local_rag` | 内蔵DB TF-IDF 検索 (12件) | 社内ベクトルDB |
| `search_files` | ローカルファイル検索 (.docx/.xlsx/.pdf/.txt) | SharePoint / DMS |
| `read_file` | ファイル全文読取 | DMS API |
| `list_files` | ファイル一覧取得 | DMS API |
| `search_all` | **全ソース統合検索** (mode=all/auto) | 全社内API統合 |
| `recommend_sources` | クエリに最適なソースを推薦 | ソース推薦エンジン |

### 検索モード

| モード | 動作 | ユースケース |
|--------|------|-------------|
| `mode="all"` (デフォルト) | 指定された全ソースに対して並列検索 | 網羅的に情報を収集したい場合 |
| `mode="auto"` | クエリ内容を分析し、関連度の高いソースのみ自動選択 | 効率的に検索したい場合 |

**LLM側でソースを絞り込む場合**: `recommend_sources` → 結果を確認 → `search_all(sources="...", mode="all")`

## 📚 ドキュメント

詳細は [docs/](./docs/) を参照してください:

| ドキュメント | 内容 |
|-------------|------|
| [プロジェクト概要](docs/01_project_overview.md) | 背景・目的・スコープ |
| [PoC仕様書](docs/02_poc_spec.md) | ツール仕様・参照データ仕様・成功基準 |
| [アーキテクチャ設計](docs/03_architecture.md) | PoC構成図・本番構成図・移行ポイント |
| [本番設計書](docs/04_production_design.md) | Azure OpenAI統合・社内API統合・インフラ |
| [セキュリティ設計](docs/05_security_design.md) | モデル使用制限(4層防御)・データ保護 |
| [ロードマップ](docs/06_roadmap.md) | Phase 0-3 の計画・マイルストーン |
| [MCPツール仕様](docs/07_mcp_tool_spec.md) | 全7ツールの詳細仕様・参照スキーマ |
| [運用ガイド](docs/08_operation_guide.md) | 構築手順・トラブルシューティング・FAQ |

## 🏢 本番構成の方針

- **Cursor参照モデル**: Azure OpenAI API（社内テナント、GPT-4o）
- **モデル使用制限**: インフラレベル4層防御（FW/Proxy/Cursor Enterprise/Azure OpenAI）
- **統合MCP接続先**: 社内導入済み複数システムの検索API

詳細は [本番設計書](docs/04_production_design.md) および [セキュリティ設計書](docs/05_security_design.md) を参照。
