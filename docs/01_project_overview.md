# プロジェクト概要

## 1. 背景

社内の設計部門では、プラント設計に関するナレッジが複数の情報システムに分散しています。

| システム | 格納情報 | 課題 |
|----------|----------|------|
| 設計データベース | 過去の設計事例、計算書 | 検索UIが独自で習熟コストが高い |
| 文書管理システム（DMS） | 仕様書、報告書（.docx/.pdf） | 全文検索が弱く、ファイル名検索が中心 |
| 保全履歴管理 | 保守記録、不具合事例 | 設計情報との横串検索ができない |
| 図面管理システム | 設計図面、P&ID | テキスト検索に非対応 |
| SharePoint/ファイルサーバー | 各種技術資料 | フォルダ構造に依存、横断検索困難 |

設計者は1つの技術課題に対して、これらのシステムを**個別に検索**する必要があり、大きな非効率が生じています。

## 2. 目的

**Cursor IDE + MCP（Model Context Protocol）** を活用し、AIアシスタントから複数情報源を統合検索するインターフェースを実現します。

### 目指す姿
- 設計者がCursor Composerに自然言語で質問
- MCPサーバーが複数の情報源を**並列検索**
- 統合結果を**参照元（出典）付き**で回答
- 参照箇所を**ワンクリックでブラウザ確認**

## 3. スコープ

| フェーズ | 期間 | 内容 |
|----------|------|------|
| **Phase 0: PoC（本プロジェクト）** | 2-4週間 | 技術的実現性検証、無料APIでの動作確認 |
| Phase 1: パイロット | 1-2ヶ月 | Azure OpenAI接続、社内API 1-2本接続 |
| Phase 2: 本番MVP | 2-3ヶ月 | 全社内API接続、セキュリティ実装 |
| Phase 3: 拡張 | 継続 | ベクトルDB、カスタムプロンプト、全社展開 |

## 4. ステークホルダー

| 部門 | 役割 | 関心事項 |
|------|------|----------|
| 設計部門 | エンドユーザー | 検索効率向上、回答精度、使いやすさ |
| IT基盤部門 | インフラ構築・運用 | デプロイ方式、監視、スケーラビリティ |
| 情報セキュリティ部門 | ガバナンス | データ保護、モデル使用制限、監査 |
| 経営層 | 投資判断 | ROI、展開計画、リスク |

## 5. 用語定義

| 用語 | 定義 |
|------|------|
| **MCP** | Model Context Protocol — AIモデルに外部ツール・データを接続する標準プロトコル |
| **RAG** | Retrieval-Augmented Generation — 検索結果を基にAIが回答を生成する手法 |
| **LLM** | Large Language Model — 大規模言語モデル |
| **FastMCP** | Python向けMCPサーバーフレームワーク |
| **Azure OpenAI** | Microsoft AzureでホストされたOpenAI APIサービス |
| **Cursor Composer** | Cursor IDEのAIチャットインターフェース |
| **TF-IDF** | Term Frequency-Inverse Document Frequency — 文書検索の重み付け手法 |
| **Streamable HTTP** | MCPの通信トランスポート方式（HTTP上でJSON-RPCを伝送） |
| **参照ビューア** | MCPサーバーに内蔵されたWeb UI（参照箇所のハイライト表示） |

## 6. 関連ドキュメント

| ドキュメント | パス |
|-------------|------|
| PoC仕様書 | [docs/02_poc_spec.md](./02_poc_spec.md) |
| アーキテクチャ設計書 | [docs/03_architecture.md](./03_architecture.md) |
| 本番設計書 | [docs/04_production_design.md](./04_production_design.md) |
| セキュリティ設計書 | [docs/05_security_design.md](./05_security_design.md) |
| ロードマップ | [docs/06_roadmap.md](./06_roadmap.md) |
| MCPツール仕様書 | [docs/07_mcp_tool_spec.md](./07_mcp_tool_spec.md) |
| 運用・構築ガイド | [docs/08_operation_guide.md](./08_operation_guide.md) |
