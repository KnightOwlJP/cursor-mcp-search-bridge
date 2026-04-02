@echo off
REM =============================================================================
REM PlantDesignRAG MCP Server — セットアップスクリプト (Windows)
REM =============================================================================

echo ============================================================
echo 🚀 PlantDesignRAG MCP Server Setup
echo ============================================================
echo.

REM --- 依存パッケージインストール ---
echo 📦 依存パッケージをインストールしています...
pip install fastmcp google-genai requests uvicorn python-docx openpyxl pypdf
echo ✅ インストール完了
echo.

REM --- .env ファイル生成 ---
if not exist .env (
    (
        echo # Hugging Face API Token
        echo # 取得先: https://huggingface.co/settings/tokens
        echo HF_API_TOKEN=YOUR_HF_TOKEN
        echo.
        echo # Google Gemini API Key
        echo # 取得先: https://aistudio.google.com/apikey
        echo GEMINI_API_KEY=YOUR_GEMINI_KEY
        echo.
        echo # ドキュメント検索ディレクトリ
        echo DOCS_DIR=./sample_docs
        echo.
        echo # 参照ビューアのベースURL
        echo VIEWER_BASE_URL=http://localhost:8000
    ) > .env
    echo 📝 .env ファイルを生成しました
    echo    .env ファイルを開いて API キーを設定してください
    echo.
) else (
    echo ✅ .env ファイルは既に存在します
)

REM --- .env の読み込み ---
for /f "usebackq tokens=1,2 delims==" %%a in (".env") do (
    if not "%%a"=="" if not "%%a"=="#" set "%%a=%%b"
)

REM --- サーバー起動 ---
echo.
echo ============================================================
echo 🚀 MCP サーバーを起動します
echo ============================================================
echo   MCP endpoint:     http://localhost:8000/mcp
echo   Reference viewer: http://localhost:8000/refs
echo   Reference API:    http://localhost:8000/api/refs
echo ============================================================
echo.

uvicorn poc_mcp:app --host 127.0.0.1 --port 8000
