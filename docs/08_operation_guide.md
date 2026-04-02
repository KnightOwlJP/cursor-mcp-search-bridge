# 運用・構築ガイド

## 1. PoC環境構築手順

### 1.1 前提条件

- Python 3.10 以上
- pip パッケージマネージャ
- インターネット接続（APIキー設定時のみ必要、フォールバック動作あり）

### 1.2 インストール

```bash
# リポジトリをクローン
git clone <repository-url>
cd cursor-mcp-search-bridge

# 依存パッケージをインストール
pip install fastmcp google-genai requests uvicorn python-docx openpyxl pypdf
```

### 1.3 環境変数設定

`.env` ファイルを作成（または `setup.sh` で自動生成）:

```bash
# Hugging Face API Token（任意）
HF_API_TOKEN=YOUR_HF_TOKEN

# Google Gemini API Key（任意）
GEMINI_API_KEY=YOUR_GEMINI_KEY

# ドキュメント検索ディレクトリ
DOCS_DIR=./sample_docs

# 参照ビューアのベースURL
VIEWER_BASE_URL=http://localhost:8000
```

### 1.4 サーバー起動

```bash
# 方法1: セットアップスクリプト（推奨）
bash setup.sh

# 方法2: 直接起動
uvicorn poc_mcp:app --host 127.0.0.1 --port 8000

# 方法3: Python直接実行
python poc_mcp.py
```

### 1.5 Cursor MCP設定

プロジェクトルートの `.cursor/mcp.json` は同梱済み:

```json
{
  "mcpServers": {
    "plant-design-rag": {
      "type": "streamableHttp",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

設定後、Cursor を**完全に再起動**（終了→再起動）してください。

### 1.6 動作確認

1. サーバーが起動していることを確認: `http://localhost:8000/refs` をブラウザで開く
2. Cursor Composer でテストクエリを実行: 「search_local_ragで安全注入系統について検索して」
3. 参照元リンクをクリックしてビューアを確認

## 2. APIキー取得手順

### 2.1 Hugging Face API Token

1. https://huggingface.co にアクセスしアカウント作成
2. https://huggingface.co/settings/tokens にアクセス
3. 「New token」→ 名前を入力 → 「Inference」権限を選択 → 「Generate」
4. 生成されたトークンを `.env` の `HF_API_TOKEN` に設定

### 2.2 Google Gemini API Key

1. https://aistudio.google.com にGoogleアカウントでログイン
2. 左メニュー「Get API key」→「Create API key」
3. プロジェクトを選択（または新規作成）→ APIキーが生成される
4. 生成されたキーを `.env` の `GEMINI_API_KEY` に設定

> ℹ️ APIキーは任意です。未設定でもフォールバックモックデータで動作します。

## 3. ローカルファイル検索の設定

### 対応ファイル形式

| 形式 | 必要ライブラリ | 抽出内容 |
|------|--------------|----------|
| .txt / .csv | 不要（標準ライブラリ） | テキスト全文 |
| .docx | python-docx | 段落 + テーブル |
| .xlsx | openpyxl | 全シート全セル |
| .pdf | pypdf | 全ページテキスト |

### カスタムドキュメントの追加

1. `sample_docs/` フォルダに .docx/.xlsx/.pdf/.txt ファイルを配置
2. または `DOCS_DIR` 環境変数で別のフォルダを指定
3. サーバーの再起動は不要（検索時にリアルタイムスキャン）

## 4. 参照ビューアの使い方

### 一覧ページ (`/refs`)

- 直近の検索で生成された全参照をカード形式で表示
- フィルタボタン: 「すべて」「内部DB」「ファイル」「API」で絞り込み
- カードクリックで詳細ページへ

### 詳細ページ (`/refs/{ref_id}`)

- 参照元の全文テキストを表示
- **該当箇所が黄色ハイライト**で強調され、自動スクロール
- API出典には「⚠️ AI生成テキスト」の警告ラベル
- メタデータ（ファイルパス、ドキュメントID、モデル名等）を表示

## 5. トラブルシューティング

| 問題 | 原因 | 対策 |
|------|------|------|
| サーバーが起動しない | fastmcp 未インストール | `pip install fastmcp uvicorn` |
| ツールが表示されない | Cursor再起動していない | Cursorを完全終了→再起動 |
| MCP接続エラー | サーバー未起動 or ポート競合 | `lsof -i :8000` で確認 |
| HF/Gemini が動作しない | APIキー未設定 | `.env` にキーを設定（またはフォールバック利用） |
| ファイルが見つからない | DOCS_DIR が間違い | `.env` のDOCS_DIRを確認 |
| .docx/.pdf が読めない | ライブラリ未インストール | `pip install python-docx pypdf` |
| 参照ビューアが空 | まだ検索を実行していない | Composerで検索ツールを実行 |
| ハイライトされない | excerpt がテキスト内に見つからない | 部分一致で代替表示 |

## 6. 本番環境構築テンプレート

```bash
# 1. Dockerfile 作成
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY poc_mcp.py .
CMD ["uvicorn", "poc_mcp:app", "--host", "0.0.0.0", "--port", "8000"]

# 2. Azure Container Apps へデプロイ
az containerapp create \
  --name mcp-plant-rag \
  --resource-group rg-mcp \
  --image <acr>.azurecr.io/mcp-plant-rag:latest \
  --target-port 8000 \
  --ingress internal
```

## 7. FAQ

**Q: APIキーなしで動作しますか？**
A: はい。HF/Gemini APIキー未設定時はフォールバックモックデータが返されます。ローカル検索（search_local_rag, search_files）は常に動作します。

**Q: 同時に複数人で使えますか？**
A: PoCでは単一プロセスのため、同時アクセス数は限定的です。本番では複数ワーカー + Redis で対応します。

**Q: 自分のドキュメントを追加するには？**
A: `sample_docs/` フォルダにファイルを追加するだけです。サーバー再起動は不要です。

**Q: 日本語のPDFは検索できますか？**
A: pypdfのテキスト抽出が成功すれば検索可能です。スキャンPDF（画像PDF）は非対応です。

**Q: 参照ビューアに認証はありますか？**
A: PoCでは認証なしです。本番ではAzure AD SSO を推奨します。

**Q: ポート番号を変更できますか？**
A: `uvicorn poc_mcp:app --port 9000` のように指定し、`.cursor/mcp.json` のURLも変更してください。

**Q: WindowsでもMacでも動きますか？**
A: はい。Python 3.10+ であればWindows/Mac/Linux すべてで動作します。

**Q: MCPのプロトコルバージョンは？**
A: FastMCPが対応する最新のMCPプロトコル（Streamable HTTP transport）を使用しています。
