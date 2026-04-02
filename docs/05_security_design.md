# セキュリティ設計書

## 1. モデル使用制限（インフラレベル4層防御）

本番環境では、Azure OpenAI API 以外のAIモデルへのアクセスをインフラレベルで制限する。

### レイヤー1: ネットワーク制御

Azure Firewall / NSG で以下のドメインを**ブロック**:

| ブロック対象 | ドメイン | 理由 |
|-------------|---------|------|
| OpenAI直接 | `api.openai.com` | 社内テナント経由を強制 |
| Google AI | `generativelanguage.googleapis.com` | Gemini直接利用防止 |
| Hugging Face | `api-inference.huggingface.co` | 外部推論API防止 |
| Anthropic | `api.anthropic.com` | Claude直接利用防止 |

**許可対象**: `<corp-resource>.openai.azure.com` のみ

### レイヤー2: プロキシ制御

| 項目 | 設定 |
|------|------|
| プロキシ | Forward Proxy (Zscaler / Squid) |
| HTTPS検査 | TLS Inspection 有効 |
| AIドメインリスト | 拒否リスト + 許可リストの二重管理 |
| ログ | 全AIドメインへのアクセス試行を記録 |

### レイヤー3: Cursor Enterprise 設定

| 設定 | 内容 |
|------|------|
| BYOK無効化 | パーソナルAPIキーの使用を禁止 |
| MCP Server Allowlist | 許可されたMCPサーバーURLのみ接続可 |
| MDMポリシー | AllowedTeamId、AllowedExtensions を強制 |
| Privacy Mode | 有効（AIプロバイダへのデータ保持ゼロ） |

### レイヤー4: Azure OpenAI 側

| 設定 | 内容 |
|------|------|
| Private Endpoint | VNet内からのみアクセス可能 |
| RBAC | `Cognitive Services OpenAI User` ロールのみ |
| Content Filtering | 有害コンテンツフィルタ有効 |
| Diagnostic Logs | 全APIリクエストの監査ログ |
| リージョン | Japan East (データ残留要件) |

## 2. データ保護

| 対策 | 内容 |
|------|------|
| Cursor Privacy Mode | AIプロバイダがコードやプロンプトを保持しない |
| 通信暗号化 | MCP Server ↔ 社内API 間は mTLS |
| キャッシュ暗号化 | Redis 参照ストアの暗号化 (TLS + at-rest) |
| PII検出 | オプション: Azure Purview でPII自動検出・マスク |
| データ分類 | 社内データ分類ラベルに基づくアクセス制御 |

## 3. 認証・認可

| コンポーネント | 認証方式 |
|---------------|----------|
| Cursor → Azure OpenAI | Azure AD + API Key (Key Vault管理) |
| Cursor → MCP Server | Azure AD Bearer Token (本番) / なし (PoC) |
| MCP Server → 社内API | Managed Identity / OAuth2.0 Client Credentials |
| ブラウザ → 参照ビューア | Azure AD SSO (本番) / なし (PoC) |

### ロール定義

| ロール | 権限 |
|--------|------|
| 設計者 | MCPツール実行、参照ビューア閲覧 |
| 管理者 | 設計者権限 + ツール設定変更 |
| 監査者 | ログ閲覧、アクセスレポート |

## 4. 監査・コンプライアンス

| 監査項目 | 実装 |
|----------|------|
| MCPツール呼出しログ | 全ツール呼出しをApplication Insightsに記録 |
| クエリ内容ログ | Azure Log Analytics にクエリ文字列を記録 |
| アクセスログ | 参照ビューアのアクセスログ |
| アクセスレビュー | 四半期ごとにロール割当を確認 |
| データ分類 | 機密度ラベル（公開/社内限定/機密/極秘） |

## 5. PoC時のセキュリティ対策

PoCでも最低限のセキュリティを実装:

| 対策 | 実装状況 |
|------|----------|
| パストラバーサル防止 | `DOCS_DIR` サブディレクトリに限定 |
| localhost バインド | `127.0.0.1` にバインド、外部アクセス遮断 |
| APIキー保護 | 環境変数で管理、コードに直書きしない |
| ファイルサイズ制限 | テキスト抽出 100KB 上限 |
| フォールバック | APIキー未設定時はモックデータで動作 |
