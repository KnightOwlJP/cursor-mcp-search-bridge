#!/bin/bash
# =============================================================================
# PlantDesignRAG MCP Server — セットアップスクリプト (Linux / macOS)
# =============================================================================
set -e

echo "============================================================"
echo "🚀 PlantDesignRAG MCP Server Setup"
echo "============================================================"
echo ""

# --- 依存パッケージインストール ---
echo "📦 依存パッケージをインストールしています..."
pip install fastmcp google-genai requests uvicorn python-docx openpyxl pypdf
echo "✅ インストール完了"
echo ""

# --- .env ファイル生成 ---
if [ ! -f .env ]; then
    cat > .env << 'ENVEOF'
# Hugging Face API Token
# 取得先: https://huggingface.co/settings/tokens
HF_API_TOKEN=YOUR_HF_TOKEN

# Google Gemini API Key
# 取得先: https://aistudio.google.com/apikey
GEMINI_API_KEY=YOUR_GEMINI_KEY

# ドキュメント検索ディレクトリ
DOCS_DIR=./sample_docs

# 参照ビューアのベースURL
VIEWER_BASE_URL=http://localhost:8000
ENVEOF
    echo "📝 .env ファイルを生成しました"
    echo "   ➡️  .env ファイルを開いて API キーを設定してください"
    echo ""
else
    echo "✅ .env ファイルは既に存在します"
fi

# --- .env の読み込み ---
set -a
# shellcheck disable=SC1091
source .env 2>/dev/null || true
set +a

# --- サーバー起動 ---
echo ""
echo "============================================================"
echo "🚀 MCP サーバーを起動します"
echo "============================================================"
echo "  MCP endpoint:     http://localhost:8000/mcp"
echo "  Reference viewer: http://localhost:8000/refs"
echo "  Reference API:    http://localhost:8000/api/refs"
echo "  Docs directory:   ${DOCS_DIR:-./sample_docs}"
echo "============================================================"
echo ""

uvicorn poc_mcp:app --host 127.0.0.1 --port 8000
