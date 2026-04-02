#!/usr/bin/env python3
"""
PlantDesignRAG MCP Server — PoC
=================================
Cursor Composer から複数の RAG ソース（HF API / Gemini API / 内蔵DB / ローカルファイル）を
統合検索する MCP サーバー。参照データ付きレスポンスとワンクリック参照ビューアを提供。

起動:
    uvicorn poc_mcp:app --host 127.0.0.1 --port 8000

エンドポイント:
    /mcp          — MCP Streamable HTTP (Cursor 接続用)
    /refs         — 参照ビューア一覧 (ブラウザ)
    /refs/{id}    — 参照詳細+ハイライト (ブラウザ)
    /api/refs     — 参照データ JSON API
"""

import os
import json
import re
import math
import pathlib
import logging
import time
import html as html_mod
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastmcp import FastMCP
from starlette.routing import Route
from starlette.responses import HTMLResponse, JSONResponse

# ---------------------------------------------------------------------------
# 条件付きインポート（未インストール時はフォールバック動作）
# ---------------------------------------------------------------------------
try:
    import requests as _requests
except ImportError:
    _requests = None

try:
    from google import genai as _genai
except ImportError:
    _genai = None

try:
    from docx import Document as _DocxDocument
except ImportError:
    _DocxDocument = None

try:
    from openpyxl import load_workbook as _load_workbook
except ImportError:
    _load_workbook = None

try:
    from pypdf import PdfReader as _PdfReader
except ImportError:
    _PdfReader = None

# ---------------------------------------------------------------------------
# 定数・設定
# ---------------------------------------------------------------------------
logger = logging.getLogger("poc_mcp")
logging.basicConfig(level=logging.INFO)

HF_API_TOKEN = os.environ.get("HF_API_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DOCS_DIR = os.environ.get("DOCS_DIR", "./sample_docs")

MAX_EXCERPT_LEN = 200       # 参照 excerpt の最大文字数
MAX_TEXT_BYTES = 100_000    # ファイルテキスト抽出上限バイト
MAX_REFS_PER_SOURCE = 5     # ソースあたり最大参照数
MAX_REFS_TOTAL = 20         # search_all の最大統合参照数
MAX_STORED_REFS = 100       # 参照ストア最大保持数
VIEWER_BASE_URL = os.environ.get("VIEWER_BASE_URL", "http://localhost:8000")

HF_MODEL = "HuggingFaceH4/zephyr-7b-beta"
GEMINI_MODEL = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# FastMCP インスタンス
# ---------------------------------------------------------------------------
mcp = FastMCP("PlantDesignRAG")

# ---------------------------------------------------------------------------
# 参照ストア（インメモリ FIFO）
# ---------------------------------------------------------------------------
_refs_store: OrderedDict = OrderedDict()


def _store_reference(ref_data: dict, full_content: str = "") -> None:
    """参照データをストアに登録（FIFO で MAX_STORED_REFS 件まで保持）"""
    ref_id = ref_data["ref_id"]
    entry = {**ref_data, "full_content": full_content}
    _refs_store[ref_id] = entry
    # 古いエントリを削除
    while len(_refs_store) > MAX_STORED_REFS:
        _refs_store.popitem(last=False)


# ---------------------------------------------------------------------------
# 参照データヘルパー
# ---------------------------------------------------------------------------
def _make_reference(
    ref_id: str,
    source_type: str,
    source_name: str,
    title: str,
    location: dict,
    excerpt: str,
    score: float,
) -> dict:
    """統一構造の参照データオブジェクトを生成"""
    if len(excerpt) > MAX_EXCERPT_LEN:
        excerpt = excerpt[: MAX_EXCERPT_LEN] + "…"
    return {
        "ref_id": ref_id,
        "source_type": source_type,
        "source_name": source_name,
        "title": title,
        "location": location,
        "excerpt": excerpt,
        "relevance_score": round(score, 4),
        "viewer_url": f"{VIEWER_BASE_URL}/refs/{ref_id}",
    }


def _format_references_display(references: list[dict]) -> str:
    """参照データリストからリンク付きマークダウンテーブルを生成"""
    if not references:
        return ""
    lines = [
        "",
        "---",
        "📚 **参照元**",
        "",
        "| # | 出典 | タイトル | 該当箇所 | 関連度 | 詳細 |",
        "|---|------|---------|---------|--------|------|",
    ]
    for i, ref in enumerate(references, 1):
        src = ref["source_name"]
        stype = ref["source_type"]
        if stype == "file":
            fp = ref["location"].get("file_path", "")
            source_label = f"ファイル: {pathlib.Path(fp).name}" if fp else "ファイル"
        elif stype == "api":
            model = ref["location"].get("model", "")
            source_label = f"API: {src}" + (f" ({model})" if model else "")
        elif stype == "internal_db":
            doc_id = ref["location"].get("doc_id", "")
            source_label = f"内部DB: {doc_id}" if doc_id else f"内部DB: {src}"
        else:
            source_label = src

        excerpt_short = ref["excerpt"][:60] + "…" if len(ref["excerpt"]) > 60 else ref["excerpt"]
        score_pct = f"{ref['relevance_score'] * 100:.0f}%"
        viewer = ref.get("viewer_url", "")
        link = f"[📄開く]({viewer})" if viewer else ""

        lines.append(
            f"| [{i}] | {source_label} | {ref['title']} | {excerpt_short} | {score_pct} | {link} |"
        )

    lines.append("")
    lines.append("> ℹ️ 各参照の「📄開く」をクリックすると、該当箇所をハイライト表示で確認できます。")
    lines.append("> ⚠️ API出典（HF/Gemini）はAI生成テキストです。ファイル・内部DBの参照を優先的にご確認ください。")
    return "\n".join(lines)


def _make_response(
    source: str,
    query: str,
    results: list,
    references: list[dict],
    start_time: float,
    source_available: bool = True,
) -> dict:
    """全ツール共通レスポンス構造を組み立て"""
    elapsed_ms = int((time.time() - start_time) * 1000)
    return {
        "source": source,
        "query": query,
        "results": results,
        "references": references,
        "references_display": _format_references_display(references),
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "response_time_ms": elapsed_ms,
            "source_available": source_available,
        },
    }


# ============================================================================
# 参照ビューア HTML テンプレート
# ============================================================================

_CSS = """
:root { --bg: #ffffff; --fg: #1a1a2e; --card-bg: #f8f9fa; --border: #dee2e6;
        --accent: #0d6efd; --mark-bg: #fff3cd; --mark-border: #ffc107;
        --badge-api: #dc3545; --badge-file: #198754; --badge-db: #6f42c1; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1a1a2e; --fg: #e6e6e6; --card-bg: #16213e; --border: #3a3a5c;
          --accent: #4dabf7; --mark-bg: #5c4a00; --mark-border: #ffc107;
          --badge-api: #ff6b6b; --badge-file: #51cf66; --badge-db: #b197fc; }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: var(--bg); color: var(--fg); line-height: 1.6; padding: 2rem; max-width: 960px; margin: 0 auto; }
h1 { margin-bottom: 0.5rem; } h2 { margin: 1.5rem 0 0.5rem; }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
.badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.75rem;
         font-weight: 600; color: #fff; }
.badge-api { background: var(--badge-api); } .badge-file { background: var(--badge-file); }
.badge-db { background: var(--badge-db); }
.card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
        padding: 1rem; margin-bottom: 1rem; transition: box-shadow .15s; }
.card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
.card-title { font-weight: 600; margin-bottom: 0.25rem; }
.card-excerpt { font-size: 0.9rem; color: var(--fg); opacity: 0.8; margin: 0.5rem 0; }
.score-bar { height: 6px; border-radius: 3px; background: var(--border); }
.score-fill { height: 100%; border-radius: 3px; background: var(--accent); }
.meta { font-size: 0.8rem; opacity: 0.6; }
mark, .highlight { background: var(--mark-bg); border-bottom: 2px solid var(--mark-border);
                   padding: 0.1rem 0.2rem; border-radius: 2px; }
.content-block { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
                 padding: 1.5rem; white-space: pre-wrap; word-wrap: break-word;
                 font-size: 0.95rem; line-height: 1.8; max-height: 70vh; overflow-y: auto; }
.warning { background: #fff3cd; color: #856404; border: 1px solid #ffc107; border-radius: 6px;
           padding: 0.75rem; margin: 1rem 0; }
@media (prefers-color-scheme: dark) { .warning { background: #5c4a00; color: #ffc107; border-color: #806600; } }
.back-link { display: inline-block; margin-bottom: 1rem; }
.empty { text-align: center; padding: 3rem; opacity: 0.6; }
.filter-bar { margin: 1rem 0; display: flex; gap: 0.5rem; flex-wrap: wrap; }
.filter-btn { padding: 0.3rem 0.8rem; border: 1px solid var(--border); border-radius: 4px;
              background: var(--card-bg); color: var(--fg); cursor: pointer; font-size: 0.85rem; }
.filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
"""

_JS_SCROLL = """
document.addEventListener('DOMContentLoaded', function() {
    var mark = document.querySelector('mark');
    if (mark) {
        setTimeout(function() { mark.scrollIntoView({behavior:'smooth', block:'center'}); }, 300);
    }
});
"""

_JS_FILTER = """
function filterCards(type) {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
    document.querySelectorAll('.ref-card').forEach(card => {
        card.style.display = (type === 'all' || card.dataset.type === type) ? '' : 'none';
    });
}
"""


def _render_refs_list_html() -> str:
    """参照一覧ページの HTML を生成"""
    cards = ""
    if not _refs_store:
        cards = '<div class="empty"><p>📭 まだ参照データがありません。</p><p>Composer で検索ツールを実行すると、ここに参照が表示されます。</p></div>'
    else:
        for ref_id, ref in reversed(_refs_store.items()):
            stype = ref.get("source_type", "")
            badge_cls = {"api": "badge-api", "file": "badge-file", "internal_db": "badge-db"}.get(stype, "badge-db")
            badge_label = {"api": "API", "file": "ファイル", "internal_db": "内部DB"}.get(stype, stype)
            excerpt = html_mod.escape(ref.get("excerpt", "")[:120])
            title = html_mod.escape(ref.get("title", ""))
            score = ref.get("relevance_score", 0)
            score_pct = int(score * 100)
            cards += f'''
            <a href="/refs/{html_mod.escape(ref_id)}" style="text-decoration:none;color:inherit;">
            <div class="card ref-card" data-type="{stype}">
                <div class="card-title"><span class="badge {badge_cls}">{badge_label}</span> {title}</div>
                <div class="card-excerpt">{excerpt}…</div>
                <div class="score-bar"><div class="score-fill" style="width:{score_pct}%"></div></div>
                <div class="meta">{html_mod.escape(ref_id)} — 関連度 {score_pct}%</div>
            </div></a>'''

    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>📚 参照ビューア — PlantDesignRAG</title><style>{_CSS}</style></head><body>
<h1>📚 参照ビューア</h1>
<p>MCPツールの検索結果から生成された参照データの一覧です。</p>
<div class="filter-bar">
  <button class="filter-btn active" onclick="filterCards('all')">すべて</button>
  <button class="filter-btn" onclick="filterCards('internal_db')">🗄️ 内部DB</button>
  <button class="filter-btn" onclick="filterCards('file')">📁 ファイル</button>
  <button class="filter-btn" onclick="filterCards('api')">🌐 API</button>
</div>
{cards}
<script>{_JS_FILTER}</script></body></html>"""


def _highlight_excerpt(full_text: str, excerpt: str) -> str:
    """full_text 内の excerpt 部分を <mark> タグでハイライトする"""
    if not excerpt or not full_text:
        return html_mod.escape(full_text)
    # excerpt のクリーンアップ（末尾の … を除去）
    clean_excerpt = excerpt.rstrip("…").strip()
    if not clean_excerpt:
        return html_mod.escape(full_text)
    # full_text 内で excerpt を検索
    escaped_full = html_mod.escape(full_text)
    escaped_excerpt = html_mod.escape(clean_excerpt)
    if escaped_excerpt in escaped_full:
        return escaped_full.replace(
            escaped_excerpt,
            f'<mark id="highlight-target">{escaped_excerpt}</mark>',
            1,
        )
    # 完全一致しない場合は先頭40文字で部分マッチを試みる
    partial = escaped_excerpt[:40]
    if partial and partial in escaped_full:
        return escaped_full.replace(
            partial, f'<mark id="highlight-target">{partial}</mark>', 1
        )
    return escaped_full


def _render_ref_detail_html(ref_data: dict) -> str:
    """参照詳細ページの HTML を生成（ハイライト付き）"""
    ref_id = html_mod.escape(ref_data.get("ref_id", ""))
    title = html_mod.escape(ref_data.get("title", ""))
    stype = ref_data.get("source_type", "")
    source_name = html_mod.escape(ref_data.get("source_name", ""))
    excerpt = ref_data.get("excerpt", "")
    full_content = ref_data.get("full_content", "")
    score = ref_data.get("relevance_score", 0)
    location = ref_data.get("location", {})

    badge_cls = {"api": "badge-api", "file": "badge-file", "internal_db": "badge-db"}.get(stype, "badge-db")
    badge_label = {"api": "API", "file": "ファイル", "internal_db": "内部DB"}.get(stype, stype)

    # メタデータ表示
    meta_items = []
    if location.get("file_path"):
        meta_items.append(f"📁 ファイルパス: <code>{html_mod.escape(str(location['file_path']))}</code>")
    if location.get("url"):
        meta_items.append(f"🔗 URL: <a href=\"{html_mod.escape(location['url'])}\">{html_mod.escape(location['url'])}</a>")
    if location.get("doc_id"):
        meta_items.append(f"🗄️ ドキュメントID: <code>{html_mod.escape(location['doc_id'])}</code>")
    if location.get("page"):
        meta_items.append(f"📄 ページ: {location['page']}")
    if location.get("model"):
        meta_items.append(f"🤖 モデル: <code>{html_mod.escape(location['model'])}</code>")
    meta_html = "<br>".join(meta_items) if meta_items else ""

    # 警告（API出典）
    warning = ""
    if stype == "api":
        warning = '<div class="warning">⚠️ この参照はAI生成テキストです。情報の正確性を別途ご確認ください。</div>'

    # コンテンツ表示（ハイライト付き）
    display_text = full_content if full_content else excerpt
    highlighted = _highlight_excerpt(display_text, excerpt)

    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>📄 {title} — 参照ビューア</title><style>{_CSS}</style></head><body>
<a href="/refs" class="back-link">← 参照一覧に戻る</a>
<h1><span class="badge {badge_cls}">{badge_label}</span> {title}</h1>
<div class="meta" style="margin:0.5rem 0">{ref_id} — 関連度 {int(score*100)}% — ソース: {source_name}</div>
{warning}
<div style="margin:1rem 0">{meta_html}</div>
<h2>📝 コンテンツ</h2>
<div class="content-block">{highlighted}</div>
<script>{_JS_SCROLL}</script></body></html>"""


# ============================================================================
# 参照ビューア Starlette ルート
# ============================================================================

async def _refs_viewer_page(request):
    """参照一覧ページ"""
    return HTMLResponse(_render_refs_list_html())


async def _ref_detail_page(request):
    """参照詳細ページ"""
    ref_id = request.path_params.get("ref_id", "")
    ref_data = _refs_store.get(ref_id)
    if not ref_data:
        return HTMLResponse(
            f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><style>{_CSS}</style></head><body>
            <a href="/refs" class="back-link">← 参照一覧に戻る</a>
            <div class="empty"><h2>🔍 参照が見つかりません</h2><p>ID: {html_mod.escape(ref_id)}</p>
            <p>検索ツールを実行して参照データを生成してください。</p></div></body></html>""",
            status_code=404,
        )
    return HTMLResponse(_render_ref_detail_html(ref_data))


async def _refs_api(request):
    """参照データ JSON API（一覧）"""
    refs = []
    for ref_id, ref in _refs_store.items():
        r = {k: v for k, v in ref.items() if k != "full_content"}
        refs.append(r)
    return JSONResponse({"count": len(refs), "refs": refs})


async def _ref_api_detail(request):
    """参照データ JSON API（詳細）"""
    ref_id = request.path_params.get("ref_id", "")
    ref_data = _refs_store.get(ref_id)
    if not ref_data:
        return JSONResponse({"error": "not_found", "ref_id": ref_id}, status_code=404)
    return JSONResponse(ref_data)


# ============================================================================
# 内蔵プラント設計ドキュメント（ローカル RAG 用サンプルデータ 12 件）
# ============================================================================

PLANT_DOCUMENTS = [
    {
        "id": "DOC-001",
        "title": "安全注入系統（SIS）の設計基準",
        "content": (
            "安全注入系統（SIS: Safety Injection System）は、原子炉冷却材喪失事故（LOCA）発生時に"
            "炉心を緊急冷却するための安全系統である。設計基準はJEAC4603およびASME Section IIIに準拠し、"
            "単一故障基準を満たす冗長構成（2系列以上）が必要である。蓄圧タンク、高圧注入ポンプ、"
            "低圧注入ポンプの3段階で構成され、系統圧力に応じて自動的に作動する。"
            "設計圧力は17.2MPa、設計温度は350℃とし、耐震クラスSとする。"
            "定期検査では、弁の作動試験、ポンプの性能試験、蓄圧タンクの圧力確認を実施する。"
        ),
        "tags": ["安全", "注入", "SIS", "LOCA", "冷却", "耐震"],
        "file_path": "internal://plant-docs/sis-design.md",
    },
    {
        "id": "DOC-002",
        "title": "遠心ポンプの選定ガイドライン",
        "content": (
            "プラント設計における遠心ポンプの選定では、必要流量（Q）と全揚程（H）を基本パラメータとする。"
            "ポンプの比速度（Ns）により羽根車形式を決定し、Ns<300で渦巻型、300-600でディフューザ型、"
            "600以上で斜流・軸流型を選定する。NPSH（正味吸込揚程）の確認は必須であり、"
            "NPSHa > NPSHr + 0.5m以上のマージンを確保する。材質選定はプロセス流体の腐食性を考慮し、"
            "JIS B 8313に準拠した耐食材料を選定する。API 610規格適合ポンプを優先的に採用し、"
            "予備ポンプの設置（1+1構成）を標準とする。運転効率は BEP（最高効率点）の70-120%範囲で使用する。"
        ),
        "tags": ["ポンプ", "遠心", "選定", "流量", "揚程", "NPSH"],
        "file_path": "internal://plant-docs/pump-selection.md",
    },
    {
        "id": "DOC-003",
        "title": "プラント配管設計の基本原則",
        "content": (
            "配管設計はASME B31.1（動力配管）およびB31.3（プロセス配管）に準拠する。"
            "配管ルートは最短距離を基本としつつ、熱膨張の吸収、保守スペースの確保、"
            "サポート設置の容易性を考慮する。配管応力解析はCaesar IIまたはAutoPIPEで実施し、"
            "許容応力値はASME規格に従う。フランジ接続はASME B16.5に準拠し、"
            "ガスケット選定は流体特性と温度圧力条件により決定する。"
            "配管口径はプロセス流速（液体1-3m/s、気体15-25m/s）から算出し、"
            "圧力損失計算はDarcy-Weisbachの式を使用する。"
        ),
        "tags": ["配管", "設計", "ASME", "応力解析", "フランジ"],
        "file_path": "internal://plant-docs/piping-design.md",
    },
    {
        "id": "DOC-004",
        "title": "シェルアンドチューブ型熱交換器の設計",
        "content": (
            "シェルアンドチューブ型熱交換器はTEMA（Tubular Exchanger Manufacturers Association）規格に準拠して設計する。"
            "チューブ側にはクリーンな流体を、シェル側には汚れやすい流体を通すのが原則である。"
            "伝熱計算はLMTD法またはε-NTU法で実施し、汚れ係数（fouling factor）を考慮する。"
            "バッフル間隔はシェル内径の1/5以上とし、チューブ振動を防止する。"
            "材質選定はHASIG（Harmonised Alloy Steel Industry Guide）を参考にし、"
            "応力解析はASME Section VIII Division 1に準拠する。設計圧力は運転圧力の1.1倍以上とする。"
        ),
        "tags": ["熱交換器", "シェルチューブ", "TEMA", "伝熱", "設計"],
        "file_path": "internal://plant-docs/heat-exchanger.md",
    },
    {
        "id": "DOC-005",
        "title": "プラント用バルブの選定基準",
        "content": (
            "バルブ選定は流体特性、圧力等級、操作方式を基に行う。遮断弁にはゲートバルブ（全開/全閉用）"
            "またはボールバルブ（クイック操作用）を、流量調節にはグローブバルブまたはバタフライバルブを使用する。"
            "圧力等級はASME B16.34に準拠し、Class 150-2500の範囲で選定する。"
            "高温高圧条件ではステライト肉盛りシートを採用し、低温条件では脆性破壊を考慮した材質を選定する。"
            "緊急遮断弁（ESD弁）はSIL（Safety Integrity Level）に応じた信頼性要求を満たすこと。"
            "アクチュエータは空気式を標準とし、フェイルセーフ動作（FC/FO/FL）を流体安全要件に基づき決定する。"
        ),
        "tags": ["バルブ", "選定", "遮断", "制御", "安全", "SIL"],
        "file_path": "internal://plant-docs/valve-selection.md",
    },
    {
        "id": "DOC-006",
        "title": "圧力容器の設計と検査基準",
        "content": (
            "圧力容器はASME Section VIII Division 1（一般）またはDivision 2（代替規則）に準拠して設計する。"
            "最小板厚はPD/(2SE-1.2P)で計算し、腐れ代（通常1.5-3.0mm）を加算する。"
            "ノズル補強計算はASME UG-36からUG-43に従い、面積置換法で実施する。"
            "溶接継手の効率はX線検査の範囲により0.7-1.0とする。"
            "フランジ設計はASME Appendix 2に準拠し、ガスケット座面応力を確認する。"
            "水圧試験は設計圧力の1.3倍（Section VIII Div.1）で実施し、保持時間は板厚25mm当たり1時間以上とする。"
        ),
        "tags": ["圧力容器", "ASME", "設計", "検査", "溶接"],
        "file_path": "internal://plant-docs/pressure-vessel.md",
    },
    {
        "id": "DOC-007",
        "title": "プラント計装・制御システムの設計",
        "content": (
            "計装制御システムはIEC 61511（プロセス産業のための機能安全）に準拠して設計する。"
            "DCS（分散制御システム）とSIS（安全計装システム）は独立した系統として構成し、"
            "共通原因故障（CCF）を防止する。センサーは2oo3（3つのうち2つ）の多数決論理を採用し、"
            "フィールド機器はSIL認証品を使用する。通信プロトコルはHART（4-20mA + デジタル）を標準とし、"
            "将来的なFoundation FieldbusまたはPROFIBUS PA対応を考慮する。"
            "制御盤の設計はNEMA 4XまたはIP66以上の保護等級とし、危険区域分類はIEC 60079に従う。"
        ),
        "tags": ["計装", "制御", "DCS", "SIS", "機能安全", "IEC"],
        "file_path": "internal://plant-docs/instrumentation.md",
    },
    {
        "id": "DOC-008",
        "title": "プラント設備の防食対策",
        "content": (
            "防食対策は腐食環境の評価に基づき、材質選定、コーティング、カソード防食の3段階で実施する。"
            "炭素鋼配管の外面にはエポキシ系塗装（JIS K 5551）を適用し、膜厚300μm以上を確保する。"
            "ステンレス鋼の使用箇所では応力腐食割れ（SCC）を防止するため、塩化物濃度と温度の管理が必要である。"
            "SUS316LはMo含有により耐孔食性に優れるが、60℃以上の塩化物環境ではスーパーステンレスまたは"
            "ニッケル合金（Alloy 625等）の採用を検討する。地下埋設配管には電気防食（外部電源方式または犠牲陽極方式）"
            "を適用し、管対地電位を-850mV(CSE)以下に維持する。"
        ),
        "tags": ["防食", "コーティング", "カソード", "ステンレス", "腐食"],
        "file_path": "internal://plant-docs/corrosion-protection.md",
    },
    {
        "id": "DOC-009",
        "title": "プラント構造物の耐震設計",
        "content": (
            "耐震設計は建築基準法施行令および原子力発電所の耐震設計審査指針に準拠する。"
            "設計用地震動はSs（基準地震動）を設定し、動的解析（時刻歴応答解析または応答スペクトル解析）"
            "により構造応答を算定する。配管系の耐震設計ではASME B31Eに準拠し、"
            "OBE（運転時地震動）およびSSE（安全停止地震動）に対する応力評価を実施する。"
            "機器基礎はボルト固定を原則とし、アンカーボルトの引抜力とせん断力を検証する。"
            "免震・制震装置の採用は費用対効果を考慮し、重要度分類に応じて決定する。"
            "液状化対策は地盤調査結果に基づきN値15以上を確保するか、地盤改良を実施する。"
        ),
        "tags": ["耐震", "地震", "構造", "基礎", "免震", "液状化"],
        "file_path": "internal://plant-docs/seismic-design.md",
    },
    {
        "id": "DOC-010",
        "title": "緊急炉心冷却系統（ECCS）の概要",
        "content": (
            "緊急炉心冷却系統（ECCS）は原子炉冷却材喪失事故時に炉心の健全性を維持するための"
            "エンジニアドセーフティフィーチャーである。ECCSは高圧注入系、蓄圧注入系、低圧注入系で構成され、"
            "一次系圧力の低下に応じて段階的に作動する。高圧注入系は充填ポンプを利用し、"
            "小口径破断LOCAに対応する。蓄圧注入系は窒素ガス圧により自動的に注水し、"
            "大口径破断LOCAの初期段階に対応する。低圧注入系は残留熱除去ポンプを利用し、"
            "減圧後の長期冷却を担当する。各系統は単一故障基準を満たす設計とする。"
        ),
        "tags": ["ECCS", "冷却", "緊急", "LOCA", "安全"],
        "file_path": "internal://plant-docs/eccs-overview.md",
    },
    {
        "id": "DOC-011",
        "title": "排水処理系統の設計基準",
        "content": (
            "プラント排水処理系統は環境関連法規（水質汚濁防止法、下水道法等）に準拠して設計する。"
            "排水は性状別に分類し、一般排水、化学排水、放射性排水を個別の処理系統で処理する。"
            "化学排水処理では中和（pH調整）、凝集沈殿、活性炭吸着の工程を経て排出基準値以下とする。"
            "放射性排水は蒸発濃縮処理およびイオン交換処理により放射能濃度を告示濃度限度以下に低減する。"
            "処理水の放流前にはサンプリングタンクで水質を確認し、基準適合を確認後に放流する。"
            "汚泥はフィルタプレスで脱水後、産業廃棄物として適正に処分する。"
        ),
        "tags": ["排水", "処理", "環境", "水質", "放射性"],
        "file_path": "internal://plant-docs/wastewater.md",
    },
    {
        "id": "DOC-012",
        "title": "換気空調系統（HVAC）の設計",
        "content": (
            "プラントの換気空調系統は室内環境の維持、放射性物質の閉じ込め、作業員の被ばく低減を目的とする。"
            "管理区域の換気は負圧管理を基本とし、非管理区域→低汚染区域→高汚染区域の順に気流を制御する。"
            "HEPAフィルタ（JIS Z 4812準拠、効率99.97%以上）を排気系統に設置し、"
            "粒子状放射性物質の環境放出を防止する。空調設備の設計条件は、夏季26℃±2℃/60%RH以下、"
            "冬季20℃±2℃/40%RH以上を標準とする。制御室の空調は外気遮断モードを備え、"
            "有毒ガス検知時には活性炭フィルタを通した再循環運転に自動切替する。"
        ),
        "tags": ["換気", "空調", "HVAC", "HEPA", "フィルタ", "負圧"],
        "file_path": "internal://plant-docs/hvac-design.md",
    },
]


# ============================================================================
# TF-IDF 検索エンジン（標準ライブラリのみ）
# ============================================================================

_STOP_WORDS_JA = set("のはがをにでとももやかだけなどへよりからまでましてですいますありあるいるされこのそのあの".replace("", ""))
_STOP_WORDS_EN = {"a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
                  "have", "has", "had", "do", "does", "did", "will", "would", "shall",
                  "should", "may", "might", "can", "could", "of", "in", "to", "for",
                  "with", "on", "at", "from", "by", "and", "or", "not", "this", "that"}


def tokenize(text: str) -> list[str]:
    """テキストをトークン化する（日本語 N-gram + 英語空白分割）"""
    tokens = []
    # 英数字部分を空白分割
    ascii_tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    tokens.extend(t for t in ascii_tokens if t not in _STOP_WORDS_EN and len(t) > 1)
    # 日本語部分を 2-gram で分割
    ja_chars = re.findall(r"[\u3040-\u9fff\uf900-\ufaff]+", text)
    for segment in ja_chars:
        for n in (2, 3):
            for i in range(len(segment) - n + 1):
                gram = segment[i : i + n]
                tokens.append(gram)
    return tokens


def compute_relevance(
    query: str, documents: list[dict], content_key: str = "content"
) -> list[tuple[int, float]]:
    """TF-IDF + コサイン類似度でドキュメントをランキング"""
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    query_tf = Counter(query_tokens)

    doc_tokens_list = [tokenize(doc.get(content_key, "") + " " + doc.get("title", "")) for doc in documents]
    n_docs = len(documents)

    # DF 計算
    df: Counter = Counter()
    for dtokens in doc_tokens_list:
        for t in set(dtokens):
            df[t] += 1

    # IDF
    idf = {}
    for t in set(query_tokens):
        idf[t] = math.log((n_docs + 1) / (df.get(t, 0) + 1)) + 1

    # クエリベクトル
    q_vec = {t: tf * idf.get(t, 1) for t, tf in query_tf.items()}
    q_norm = math.sqrt(sum(v ** 2 for v in q_vec.values()))
    if q_norm == 0:
        return []

    results = []
    for idx, dtokens in enumerate(doc_tokens_list):
        d_tf = Counter(dtokens)
        d_vec = {t: tf * idf.get(t, 1) for t, tf in d_tf.items() if t in q_vec}
        dot = sum(q_vec.get(t, 0) * v for t, v in d_vec.items())
        d_norm = math.sqrt(sum(v ** 2 for v in d_vec.values()))
        if d_norm > 0 and dot > 0:
            score = dot / (q_norm * d_norm)
            results.append((idx, score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


# ============================================================================
# ファイルテキスト抽出エンジン
# ============================================================================

def extract_text_from_file(file_path: str) -> dict:
    """ファイルからテキストを抽出する"""
    p = pathlib.Path(file_path)
    result = {"file_path": str(p), "file_type": p.suffix.lower(), "text": "", "pages": None, "error": None}

    if not p.exists():
        result["error"] = f"ファイルが見つかりません: {file_path}"
        return result

    ext = p.suffix.lower()
    try:
        if ext in (".txt", ".csv", ".md"):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                result["text"] = f.read(MAX_TEXT_BYTES)

        elif ext == ".docx":
            if _DocxDocument is None:
                result["error"] = "python-docx が未インストールです (pip install python-docx)"
                return result
            doc = _DocxDocument(str(p))
            paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
            # テーブルも抽出
            for table in doc.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        paragraphs.append(" | ".join(cells))
            result["text"] = "\n".join(paragraphs)[:MAX_TEXT_BYTES]

        elif ext == ".xlsx":
            if _load_workbook is None:
                result["error"] = "openpyxl が未インストールです (pip install openpyxl)"
                return result
            wb = _load_workbook(str(p), read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets:
                lines.append(f"=== シート: {ws.title} ===")
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        lines.append(" | ".join(cells))
            wb.close()
            result["text"] = "\n".join(lines)[:MAX_TEXT_BYTES]

        elif ext == ".pdf":
            if _PdfReader is None:
                result["error"] = "pypdf が未インストールです (pip install pypdf)"
                return result
            reader = _PdfReader(str(p))
            result["pages"] = len(reader.pages)
            texts = []
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                texts.append(f"--- ページ {i+1} ---\n{page_text}")
            result["text"] = "\n".join(texts)[:MAX_TEXT_BYTES]

        else:
            result["error"] = f"未対応のファイル形式: {ext}"
    except Exception as e:
        result["error"] = f"ファイル読み取りエラー: {str(e)}"

    return result


def scan_directory(directory: str, file_types: list[str]) -> list[dict]:
    """ディレクトリを再帰スキャンし、対応ファイルの一覧を返す"""
    base = pathlib.Path(directory)
    if not base.exists():
        return []
    files = []
    extensions = set(f".{ft.strip('.')}" for ft in file_types)
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.suffix.lower() in extensions:
            # パストラバーサル防止
            try:
                p.resolve().relative_to(base.resolve())
            except ValueError:
                continue
            files.append({
                "path": str(p),
                "name": p.name,
                "size": p.stat().st_size,
                "type": p.suffix.lower(),
            })
    return files


# ============================================================================
# 内部ヘルパー関数（MCP ツールから呼ばれる、参照データ生成+ストア登録）
# ============================================================================

def _call_hf_api(query: str) -> dict:
    """Hugging Face Inference API を呼び出す"""
    t0 = time.time()
    model_url = f"https://huggingface.co/{HF_MODEL}"
    prompt = (
        f"<|system|>あなたはプラント設計の専門家です。技術的に正確で具体的な回答をしてください。</s>\n"
        f"<|user|>{query}</s>\n<|assistant|>"
    )

    answer_text = ""
    source_available = True

    if _requests and HF_API_TOKEN and HF_API_TOKEN != "YOUR_HF_TOKEN":
        try:
            resp = _requests.post(
                f"https://api-inference.huggingface.co/models/{HF_MODEL}",
                headers={"Authorization": f"Bearer {HF_API_TOKEN}"},
                json={"inputs": prompt, "parameters": {"max_new_tokens": 500, "temperature": 0.7}},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data:
                    answer_text = data[0].get("generated_text", "")
                    # プロンプト部分を除去
                    if "<|assistant|>" in answer_text:
                        answer_text = answer_text.split("<|assistant|>")[-1].strip()
                elif isinstance(data, dict):
                    answer_text = data.get("generated_text", str(data))
            else:
                logger.warning(f"HF API error: {resp.status_code}")
                source_available = False
        except Exception as e:
            logger.warning(f"HF API exception: {e}")
            source_available = False

    if not answer_text:
        source_available = False
        answer_text = (
            f"[モックデータ] プラント設計における「{query}」について: "
            "HF APIが利用できないため、フォールバック回答を提供しています。"
            "実際の運用では、Hugging Face Inference APIから技術的な回答が返されます。"
            "HF_API_TOKEN環境変数を設定してください。"
        )

    confidence = 0.75 if source_available else 0.30
    results = [{"answer": answer_text, "confidence": confidence}]

    ref = _make_reference(
        ref_id="REF-HF-001",
        source_type="api",
        source_name="HF",
        title=f"HF {HF_MODEL.split('/')[-1]} 応答" + ("" if source_available else " [mock]"),
        location={"url": model_url, "model": HF_MODEL},
        excerpt=answer_text[:MAX_EXCERPT_LEN],
        score=confidence,
    )
    _store_reference(ref, full_content=answer_text)
    references = [ref]

    return _make_response("HF", query, results, references, t0, source_available)


def _call_gemini_api(query: str) -> dict:
    """Google Gemini API を呼び出す"""
    t0 = time.time()
    prompt = f"あなたはプラント設計の専門家です。以下の質問に技術的に正確で具体的に回答してください。\n\n質問: {query}"

    answer_text = ""
    source_available = True

    if _genai and GEMINI_API_KEY and GEMINI_API_KEY != "YOUR_GEMINI_KEY":
        try:
            client = _genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            answer_text = response.text or ""
        except Exception as e:
            logger.warning(f"Gemini API exception: {e}")
            source_available = False

    if not answer_text:
        source_available = False
        answer_text = (
            f"[モックデータ] プラント設計における「{query}」について: "
            "Gemini APIが利用できないため、フォールバック回答を提供しています。"
            "実際の運用では、Google Gemini APIから技術的な回答が返されます。"
            "GEMINI_API_KEY環境変数を設定してください。"
        )

    confidence = 0.80 if source_available else 0.25
    results = [{"answer": answer_text, "model": GEMINI_MODEL, "confidence": confidence}]

    ref = _make_reference(
        ref_id="REF-GEMINI-001",
        source_type="api",
        source_name="Gemini",
        title=f"Gemini {GEMINI_MODEL} 応答" + ("" if source_available else " [mock]"),
        location={"url": "https://ai.google.dev/", "model": GEMINI_MODEL},
        excerpt=answer_text[:MAX_EXCERPT_LEN],
        score=confidence,
    )
    _store_reference(ref, full_content=answer_text)
    references = [ref]

    return _make_response("Gemini", query, results, references, t0, source_available)


def _call_local_rag(query: str, top_k: int = 3) -> dict:
    """内蔵プラント設計 DB に対して TF-IDF 検索"""
    t0 = time.time()
    ranked = compute_relevance(query, PLANT_DOCUMENTS)
    hits = ranked[: min(top_k, MAX_REFS_PER_SOURCE)]

    results = []
    references = []
    for i, (idx, score) in enumerate(hits):
        doc = PLANT_DOCUMENTS[idx]
        results.append({"title": doc["title"], "content": doc["content"], "score": round(score, 4), "doc_id": doc["id"]})

        ref = _make_reference(
            ref_id=f"REF-LOCAL-{i+1:03d}",
            source_type="internal_db",
            source_name="Local",
            title=doc["title"],
            location={"doc_id": doc["id"], "file_path": doc["file_path"]},
            excerpt=doc["content"][:MAX_EXCERPT_LEN],
            score=score,
        )
        _store_reference(ref, full_content=doc["content"])
        references.append(ref)

    return _make_response("Local", query, results, references, t0)


def _call_file_search(
    query: str, directory: str = "", file_types_str: str = "docx,xlsx,pdf,txt,csv", top_k: int = 3
) -> dict:
    """ローカルファイルを検索"""
    t0 = time.time()
    base_dir = directory if directory else DOCS_DIR
    file_types = [ft.strip() for ft in file_types_str.split(",")]

    files = scan_directory(base_dir, file_types)
    if not files:
        return _make_response("Files", query, [], [], t0, source_available=True)

    # 全ファイルからテキスト抽出
    file_docs = []
    file_metas = []
    for finfo in files:
        extracted = extract_text_from_file(finfo["path"])
        if extracted["text"]:
            file_docs.append({"content": extracted["text"], "title": finfo["name"]})
            file_metas.append({**finfo, **extracted})

    if not file_docs:
        return _make_response("Files", query, [], [], t0, source_available=True)

    # TF-IDF 検索
    ranked = compute_relevance(query, file_docs)
    hits = ranked[: min(top_k, MAX_REFS_PER_SOURCE)]

    results = []
    references = []
    for i, (idx, score) in enumerate(hits):
        meta = file_metas[idx]
        content = file_docs[idx]["content"]
        # クエリに近い部分をスニペットとして抽出
        snippet = content[:MAX_EXCERPT_LEN]
        # クエリトークンを含む部分を優先的に抽出
        query_tokens = tokenize(query)
        for token in query_tokens:
            pos = content.lower().find(token.lower()) if len(token) > 1 else -1
            if pos >= 0:
                start = max(0, pos - 50)
                snippet = content[start : start + MAX_EXCERPT_LEN]
                break

        results.append({
            "file_path": meta["path"],
            "file_name": meta["name"],
            "file_type": meta["type"],
            "snippet": snippet[:MAX_EXCERPT_LEN],
            "score": round(score, 4),
        })

        ref = _make_reference(
            ref_id=f"REF-FILES-{i+1:03d}",
            source_type="file",
            source_name="Files",
            title=meta["name"],
            location={
                "file_path": meta["path"],
                "page": meta.get("pages"),
            },
            excerpt=snippet[:MAX_EXCERPT_LEN],
            score=score,
        )
        _store_reference(ref, full_content=content)
        references.append(ref)

    return _make_response("Files", query, results, references, t0)


def _merge_results(results_dict: dict, query: str) -> dict:
    """複数ソースの結果を統合"""
    all_references = []
    source_results = {}

    for source_name, result in results_dict.items():
        source_results[source_name] = {
            "results": result.get("results", []),
            "references": result.get("references", []),
            "metadata": result.get("metadata", {}),
        }
        all_references.extend(result.get("references", []))

    # 関連度スコア降順でソート
    all_references.sort(key=lambda r: r.get("relevance_score", 0), reverse=True)
    all_references = all_references[:MAX_REFS_TOTAL]

    # 共通テーマ抽出
    all_text = " ".join(
        ref.get("excerpt", "") for ref in all_references
    )
    tokens = tokenize(all_text)
    common = Counter(tokens).most_common(10)
    common_themes = [t for t, _ in common if len(t) >= 2][:8]

    # 統合レポート生成
    report_lines = ["## 📊 統合検索レポート", "", f"**検索クエリ**: {query}", ""]

    for src, data in source_results.items():
        refs = data.get("references", [])
        report_lines.append(f"### 🔍 {src} ({len(refs)} 件)")
        if not refs:
            report_lines.append("  結果なし")
        for ref in refs:
            report_lines.append(
                f"- **{ref['title']}** (関連度: {ref['relevance_score']*100:.0f}%) [{ref['ref_id']}]"
            )
            report_lines.append(f"  > {ref['excerpt'][:100]}…")
        report_lines.append("")

    if common_themes:
        report_lines.append(f"### 🏷️ 共通テーマ: {', '.join(common_themes)}")
        report_lines.append("")

    # 参照テーブル
    report_lines.append(_format_references_display(all_references))

    integrated_report = "\n".join(report_lines)

    # サマリー
    source_names = list(results_dict.keys())
    total_refs = len(all_references)
    summary = (
        f"{len(source_names)}ソース（{', '.join(source_names)}）から合計{total_refs}件の参照を取得しました。"
    )

    return {
        "query": query,
        "sources_queried": source_names,
        "source_results": source_results,
        "all_references": all_references,
        "comparison": {
            "common_themes": common_themes,
            "summary": summary,
        },
        "integrated_report": integrated_report,
    }


# ============================================================================
# MCP ツール（7 つ）
# ============================================================================

@mcp.tool
def search_hf(query: str) -> str:
    """Hugging Face APIを使ってプラント設計情報を検索します。

    結果には「📚 参照元」セクションが含まれます。
    回答作成時には必ず参照元を明記し、[REF-HF-xxx] の形式で引用してください。
    「📄開く」リンクをクリックすると、参照箇所をブラウザで確認できます。

    Args:
        query: 検索クエリ（例: 安全注入系統の設計基準）
    """
    result = _call_hf_api(query)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
def search_gemini(query: str) -> str:
    """Google Gemini APIを使ってプラント設計情報を検索します。

    結果には「📚 参照元」セクションが含まれます。
    回答作成時には必ず参照元を明記し、[REF-GEMINI-xxx] の形式で引用してください。
    「📄開く」リンクをクリックすると、参照箇所をブラウザで確認できます。

    Args:
        query: 検索クエリ（例: ポンプ選定の基本原則）
    """
    result = _call_gemini_api(query)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
def search_local_rag(query: str, top_k: int = 3) -> str:
    """内蔵プラント設計データベースから関連事例を検索します。

    12件のプラント設計ドキュメント（安全系統、ポンプ、配管、熱交換器、バルブ、
    圧力容器、計装、防食、耐震、ECCS、排水、HVAC）から TF-IDF 検索します。

    結果には「📚 参照元」セクションが含まれます。
    回答作成時には必ず参照元を明記し、[REF-LOCAL-xxx] の形式で引用してください。
    「📄開く」リンクをクリックすると、参照箇所をブラウザで確認できます。

    Args:
        query: 検索クエリ
        top_k: 返却する最大件数（デフォルト: 3）
    """
    result = _call_local_rag(query, top_k)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
def search_files(
    query: str,
    directory: str = "",
    file_types: str = "docx,xlsx,pdf,txt,csv",
    top_k: int = 3,
) -> str:
    """ローカルファイル(.docx/.xlsx/.pdf/.txt/.csv)からプラント設計情報を検索します。

    指定ディレクトリ内のドキュメントファイルからテキストを抽出し、TF-IDF検索します。

    結果には「📚 参照元」セクションが含まれます。
    回答作成時には必ず参照元のファイルパスを明記し、[REF-FILES-xxx] の形式で引用してください。
    「📄開く」リンクをクリックすると、ファイル内容と該当箇所をブラウザで確認できます。

    Args:
        query: 検索クエリ
        directory: 検索対象ディレクトリ（空文字の場合は DOCS_DIR 環境変数または ./sample_docs）
        file_types: カンマ区切りの拡張子フィルタ（デフォルト: docx,xlsx,pdf,txt,csv）
        top_k: 返却する最大件数（デフォルト: 3）
    """
    result = _call_file_search(query, directory, file_types, top_k)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
def read_file(file_path: str) -> str:
    """指定ファイル(.docx/.xlsx/.pdf/.txt/.csv)の内容をテキストとして読み取ります。

    結果にはファイルの参照情報が含まれます。
    回答作成時にはファイルパスを出典として明記してください。
    「📄開く」リンクをクリックすると、ファイル全文をブラウザで確認できます。

    Args:
        file_path: 読み取るファイルのパス
    """
    t0 = time.time()
    extracted = extract_text_from_file(file_path)
    p = pathlib.Path(file_path)

    results = [{
        "file_path": extracted["file_path"],
        "file_type": extracted["file_type"],
        "text": extracted["text"][:5000],  # レスポンスサイズ制限
        "pages": extracted["pages"],
        "error": extracted["error"],
    }]

    references = []
    if extracted["text"]:
        ref = _make_reference(
            ref_id=f"REF-FILE-READ-001",
            source_type="file",
            source_name="Files",
            title=p.name,
            location={"file_path": str(p), "page": extracted.get("pages")},
            excerpt=extracted["text"][:MAX_EXCERPT_LEN],
            score=1.0,
        )
        _store_reference(ref, full_content=extracted["text"])
        references.append(ref)

    return json.dumps(
        _make_response("Files", f"read: {file_path}", results, references, t0),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool
def list_files(
    directory: str = "",
    file_types: str = "docx,xlsx,pdf,txt,csv",
) -> str:
    """指定ディレクトリ内のドキュメントファイル一覧を取得します。

    Args:
        directory: 対象ディレクトリ（空文字の場合は DOCS_DIR 環境変数または ./sample_docs）
        file_types: カンマ区切りの拡張子フィルタ（デフォルト: docx,xlsx,pdf,txt,csv）
    """
    base_dir = directory if directory else DOCS_DIR
    ft_list = [ft.strip() for ft in file_types.split(",")]
    files = scan_directory(base_dir, ft_list)
    return json.dumps(
        {"directory": base_dir, "file_types": ft_list, "count": len(files), "files": files},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool
def search_all(query: str, sources: str = "hf,gemini,local,files") -> str:
    """複数のRAGソース(HF/Gemini/内部DB/ローカルファイル)を並列検索し、結果を統合・比較します。

    検索結果には全ソースからの「📚 参照元」一覧が関連度順で含まれます。

    回答作成時には必ず以下を守ってください:
    - 各情報の出典を [REF-xxx] 形式で引用してください
    - レスポンス末尾の参照元テーブルをそのまま回答に含めてください
    - 「📄開く」リンクをクリックすると、各参照の詳細をブラウザで確認できます
    - ファイル出典とAPI出典を区別して記載してください

    Args:
        query: 検索クエリ
        sources: 検索ソース（カンマ区切り: hf,gemini,local,files）
    """
    source_list = [s.strip().lower() for s in sources.split(",")]

    # ソース別の呼び出し関数マップ
    call_map = {
        "hf": lambda: _call_hf_api(query),
        "gemini": lambda: _call_gemini_api(query),
        "local": lambda: _call_local_rag(query),
        "files": lambda: _call_file_search(query),
    }

    # 並列実行
    results_dict = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for src in source_list:
            if src in call_map:
                futures[executor.submit(call_map[src])] = src

        for future in as_completed(futures, timeout=60):
            src = futures[future]
            try:
                results_dict[src.upper() if src != "files" else "Files"] = future.result()
            except Exception as e:
                logger.error(f"Error calling {src}: {e}")
                results_dict[src.upper() if src != "files" else "Files"] = {
                    "results": [],
                    "references": [],
                    "metadata": {"error": str(e)},
                }

    merged = _merge_results(results_dict, query)
    return json.dumps(merged, ensure_ascii=False, indent=2)


# ============================================================================
# ASGI アプリ組立（MCP + 参照ビューア）
# ============================================================================

_starlette_app = mcp.http_app()

# 参照ビューアルートを追加（MCP ルートより前に挿入）
_starlette_app.routes.insert(0, Route("/api/refs/{ref_id}", _ref_api_detail))
_starlette_app.routes.insert(0, Route("/api/refs", _refs_api))
_starlette_app.routes.insert(0, Route("/refs/{ref_id}", _ref_detail_page))
_starlette_app.routes.insert(0, Route("/refs", _refs_viewer_page))

app = _starlette_app

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("🚀 PlantDesignRAG MCP Server")
    print("=" * 60)
    print(f"  MCP endpoint:     http://localhost:8000/mcp")
    print(f"  Reference viewer: http://localhost:8000/refs")
    print(f"  Reference API:    http://localhost:8000/api/refs")
    print(f"  Docs directory:   {DOCS_DIR}")
    print("=" * 60)
    uvicorn.run("poc_mcp:app", host="127.0.0.1", port=8000, reload=True)
