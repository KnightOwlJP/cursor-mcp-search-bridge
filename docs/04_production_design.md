# 本番設計書

## 1. Azure OpenAI API 統合

### 1.1 Cursor Enterprise 設定

本番環境では、Cursor の参照モデルを社内 Azure OpenAI テナントに設定する。

| 設定項目 | 値 |
|----------|-----|
| Base URL | `https://<corp-resource>.openai.azure.com/` |
| Deployment Name | `gpt-4o-corp` (Azure上のデプロイメント名と**完全一致**) |
| API Version | `2024-12-01-preview` 以降 |
| API Key | Azure Key Vault から取得 |

### 1.2 推奨モデル

| モデル | 用途 | 理由 |
|--------|------|------|
| GPT-4o | Composer メインモデル | 高精度、マルチモーダル対応 |
| GPT-4o-mini | 軽量タスク | コスト効率、高速 |

### 1.3 APIキー管理

```
Azure Key Vault
  └── Secret: "cursor-aoai-key"
       ├── Managed Identity でアクセス
       └── ローテーション: 90日ごと自動
```

## 2. 社内検索API統合

### 2.1 MCPツール設計テンプレート

```python
@mcp.tool
def search_design_db(query: str, project_id: str = "", filters: str = "") -> str:
    """社内設計データベースから設計事例を検索します。

    結果には「📚 参照元」セクションが含まれます。
    回答作成時には必ず参照元を明記してください。

    Args:
        query: 検索クエリ
        project_id: プロジェクトIDでフィルタ（任意）
        filters: 追加フィルタ条件（JSON文字列）
    """
    t0 = time.time()
    token = _get_corp_token()  # Azure AD トークン取得
    response = requests.post(
        "https://internal-api.corp.example.com/design-db/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "project_id": project_id, "filters": filters},
        timeout=30,
    )
    data = response.json()

    results = data.get("results", [])
    references = []
    for i, item in enumerate(results):
        ref = _make_reference(
            ref_id=f"REF-DESIGN-{i+1:03d}",
            source_type="internal_db",
            source_name="DesignDB",
            title=item["title"],
            location={"doc_id": item["id"], "url": item.get("url")},
            excerpt=item.get("summary", "")[:MAX_EXCERPT_LEN],
            score=item.get("score", 0.5),
        )
        _store_reference(ref, full_content=item.get("content", ""))
        references.append(ref)

    return json.dumps(_make_response("DesignDB", query, results, references, t0))
```

### 2.2 接続先候補

| 社内システム | API形式 | 認証 | PoCでの代替 |
|-------------|---------|------|------------|
| 設計データベース | REST API | OAuth2.0 | search_local_rag |
| 文書管理（DMS） | REST API | mTLS | search_files |
| 保全履歴管理 | REST API | API Key | search_gemini |
| 図面管理 | GraphQL | OAuth2.0 | (未対応) |
| SharePoint | Microsoft Graph API | Azure AD | search_files |

### 2.3 認証方式

| 方式 | 用途 | 実装 |
|------|------|------|
| OAuth 2.0 (Client Credentials) | 社内REST API | `msal` ライブラリで実装 |
| mTLS | 高セキュリティAPI | クライアント証明書設定 |
| API Key | レガシーシステム | Azure Key Vault からシークレット取得 |
| Managed Identity | Azure上のサービス間 | `azure-identity` ライブラリ |

## 3. インフラ構成

### 3.1 MCPサーバーデプロイ

| 項目 | 推奨構成 |
|------|----------|
| コンピュート | Azure Container Apps (ACA) |
| コンテナレジストリ | Azure Container Registry (ACR) |
| CPU/メモリ | 2 vCPU / 4 GB RAM |
| スケーリング | 最小1 → 最大5 レプリカ |
| ヘルスチェック | `/refs` エンドポイント (HTTP 200) |

### 3.2 ネットワーク

- **Azure VNet**: MCPサーバーと社内APIを閉域接続
- **Private Endpoint**: Azure OpenAI、Azure AI Search
- **NSG**: インバウンドはCursor接続元のみ許可
- **DNS**: Azure Private DNS Zone

### 3.3 監視

| ツール | 監視対象 |
|--------|----------|
| Azure Monitor | インフラメトリクス（CPU, メモリ, ネットワーク） |
| Application Insights | アプリケーションログ、リクエストトレース |
| Azure Log Analytics | MCPツール呼び出しログ、クエリ監査 |
| アラート | エラー率 >5%, レイテンシ >10s, サーバーダウン |

### 3.4 CI/CD

```
GitHub (ソースコード)
  → GitHub Actions
    → ビルド (Docker image)
    → テスト (pytest)
    → ACR にプッシュ
    → ACA にデプロイ (Blue/Green)
```

## 4. スケーラビリティ

| 項目 | PoC | 本番 |
|------|-----|------|
| ワーカー数 | 1 | 4-8 (uvicorn workers) |
| 参照ストア | インメモリ (OrderedDict) | Redis Cluster |
| セッション管理 | ステートフル | `stateless_http=True` |
| 同時接続数 | ~5 | ~100 |
| ファイルキャッシュ | なし | Redis (TTL: 1時間) |

## 5. 可用性

| 項目 | 設計 |
|------|------|
| SLA目標 | 99.5% (月間ダウンタイム 3.6時間以内) |
| ヘルスチェック | 30秒間隔、3回連続失敗で再起動 |
| 自動復旧 | ACA の自動再起動 |
| バックアップ | 参照ストアはRedis RDB (日次) |
| 障害時対応 | Composerは検索なしで動作可能（graceful degradation） |
