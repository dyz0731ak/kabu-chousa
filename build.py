# -*- coding: utf-8 -*-
"""株式調査兵団 静的サイトビルダー（全上場銘柄対応）

使い方:
  python3 scripts/fetch_master.py  # 銘柄マスタ更新（月1回程度）
  python3 scripts/fetch_data.py    # 実データ取得（yfinance・LLM不使用）
  python3 build.py                 # dist/ に全ページを生成

BASE_URL は環境変数で上書き可能（サブドメイン公開時など）:
  BASE_URL=https://kabu.stock-overflow24.com python3 build.py
"""
import datetime
import json
import math
import os
import random
import shutil
from pathlib import Path

from data import DESC_OVERRIDES, MARKET_SUMMARY

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
DATA = ROOT / "data"
BASE_URL = os.environ.get("BASE_URL", "https://kabu-chousa.com").rstrip("/")
SITE_NAME = "株式調査兵団"
SITE_TAGLINE = "日本株全上場銘柄の企業分析・銘柄ランキング"
SITE_DESC = ("東証プライム・スタンダード・グロース全上場銘柄の財務データ分析とランキングで"
             "銘柄発見を支援する株式情報サイト。PER・PBR・ROE・配当利回りを毎営業日自動更新。")

JST = datetime.timezone(datetime.timedelta(hours=9))
PAGE_SIZE = 200          # 銘柄一覧のページネーション単位
RANK_LIMIT = 100         # ランキング掲載数
RANK_MIN_MCAP = 100      # ランキング対象の最低時価総額（億円）


# ---------------------------------------------------------------- data

def load_json(name):
    p = DATA / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


MASTER = load_json("master.json")
MARKET_DATA = load_json("market.json")
FINANCIALS = load_json("financials.json")
INDICES = load_json("indices.json")
if not MASTER:
    raise SystemExit("data/master.json がありません。scripts/fetch_master.py を先に実行してください。")

UPDATED = MARKET_DATA.get("_updated", datetime.datetime.now(JST).isoformat(timespec="seconds"))
UPDATED_DATE = UPDATED[:10]
THIS_YEAR = datetime.date.today().year

MARKET_SLUGS = {"プライム": "prime", "スタンダード": "standard", "グロース": "growth"}

SECTOR_SLUGS = {
    "水産・農林業": "fishery-agriculture", "鉱業": "mining", "建設業": "construction",
    "食料品": "foods", "繊維製品": "textiles", "パルプ・紙": "pulp-paper", "化学": "chemicals",
    "医薬品": "pharma", "石油・石炭製品": "oil-coal", "ゴム製品": "rubber",
    "ガラス・土石製品": "glass-ceramics", "鉄鋼": "steel", "非鉄金属": "nonferrous-metals",
    "金属製品": "metal-products", "機械": "machinery", "電気機器": "electronics",
    "輸送用機器": "transport-equipment", "精密機器": "precision", "その他製品": "other-products",
    "電気・ガス業": "utilities", "陸運業": "land-transport", "海運業": "marine-transport",
    "空運業": "air-transport", "倉庫・運輸関連業": "warehousing", "情報・通信業": "ict",
    "卸売業": "wholesale", "小売業": "retail", "銀行業": "banks",
    "証券、商品先物取引業": "securities", "保険業": "insurance", "その他金融業": "other-financials",
    "不動産業": "real-estate", "サービス業": "services", "その他": "other",
}


def auto_desc(s):
    parts = [f"{s['name']}（証券コード{s['code']}）は、{s['sector']}に分類される東証{s['market']}上場企業。"]
    m = []
    if s.get("mcap"):
        m.append(f"時価総額は約{fmt_oku(s['mcap'])}")
    if s.get("per"):
        m.append(f"PERは{s['per']:.1f}倍")
    if s.get("yield") is not None:
        m.append(f"配当利回りは{s['yield']:.2f}%")
    if m:
        parts.append("、".join(m) + f"（{UPDATED_DATE}時点）。")
    return "".join(parts)


def build_stocks():
    stocks = []
    for row in MASTER:
        s = dict(row)
        live = MARKET_DATA.get(s["code"], {})
        for k in ("price", "chg", "per", "pbr", "roe", "yield", "mcap", "eps", "hist"):
            if live.get(k) is not None:
                s[k] = live[k]
        fin = FINANCIALS.get(s["code"], {})
        if fin.get("equity") is not None:
            s["equity"] = fin["equity"]
        if fin.get("revenue"):
            rows = []
            for i, yr in enumerate(fin["years"]):
                rev = fin["revenue"][i]
                if rev is None:
                    continue
                rows.append({"year": yr, "revenue": rev,
                             "op": (fin.get("op") or [None] * 9)[i],
                             "net": (fin.get("net") or [None] * 9)[i],
                             "eps": (fin.get("eps") or [None] * 9)[i]})
            if rows:
                s["fin_rows"] = rows
        dby = fin.get("div_by_year")
        if dby:
            done = [(y, v) for y, v in sorted(dby.items()) if int(y) < THIS_YEAR]
            if done:
                s["div_rows"] = done[-5:]
        stocks.append(s)
    return stocks


# ---------------------------------------------------------------- helpers

def fmt_oku(v):
    neg = v < 0
    a = abs(v)
    if a >= 10000:
        cho, oku = divmod(a, 10000)
        s = f"{cho:,.0f}兆{oku:,.0f}億円" if oku else f"{cho:,.0f}兆円"
    else:
        s = f"{a:,.0f}億円"
    return ("△" + s) if neg else s


def fmt_num(v):
    if v is None:
        return "—"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.1f}"
    return f"{v:,.0f}"


def fmt_oku_or_dash(v):
    return "—" if v is None else fmt_oku(v)


def chg_span(pct):
    if pct is None:
        return '<span class="chg">—</span>'
    cls = "up" if pct >= 0 else "down"
    sign = "+" if pct >= 0 else ""
    arrow = "▲" if pct >= 0 else "▼"
    return f'<span class="chg {cls}">{arrow} {sign}{pct:.2f}%</span>'


def per_disp(s):
    per = s.get("per")
    return f"{per:.1f}倍" if per else "—"


def sparkline(s, w=120, h=36):
    chg = s.get("chg") or 0
    vals = s.get("hist")
    if not vals or len(vals) < 5:
        rnd = random.Random(int(s["code"], 36) * 7919)
        vals = [1.0]
        for _ in range(29):
            vals.append(vals[-1] * (1 + rnd.uniform(-0.018, 0.019)))
    vmin, vmax = min(vals), max(vals)
    rng = (vmax - vmin) or 1
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = i * w / (n - 1)
        y = h - 3 - (v - vmin) / rng * (h - 6)
        pts.append(f"{x:.1f},{y:.1f}")
    color = "var(--up)" if chg >= 0 else "var(--down)"
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.8" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def bar_chart(pairs, unit="億円", color="var(--accent)"):
    pairs = [(y, v) for y, v in pairs if v is not None]
    if not pairs:
        return '<p class="table-note">データなし</p>'
    years = [p[0] for p in pairs]
    values = [p[1] for p in pairs]
    w, h, pad_b, pad_t = 560, 230, 30, 24
    vmax = max(max(values), 0)
    vmin = min(min(values), 0)
    rng = (vmax - vmin) or 1
    plot_h = h - pad_b - pad_t
    zero_y = pad_t + vmax / rng * plot_h
    n = len(values)
    slot = w / n
    bw = slot * 0.52
    bars = []
    for i, (yr, v) in enumerate(zip(years, values)):
        x = i * slot + (slot - bw) / 2
        bh = abs(v) / rng * plot_h
        y = zero_y - bh if v >= 0 else zero_y
        c = color if v >= 0 else "var(--down)"
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(bh, 1):.1f}" rx="3" fill="{c}"/>')
        # 負値はバー下端だと年度ラベルと重なるため、ゼロライン上に表示
        label_y = (y - 7) if v >= 0 else (zero_y - 7)
        bars.append(f'<text x="{x + bw / 2:.1f}" y="{label_y:.1f}" class="c-val">{fmt_num(v)}</text>')
        bars.append(f'<text x="{x + bw / 2:.1f}" y="{h - 10}" class="c-year">{yr}</text>')
    zero_line = f'<line x1="0" y1="{zero_y:.1f}" x2="{w}" y2="{zero_y:.1f}" stroke="var(--border)" stroke-width="1"/>'
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="単位: {unit}">'
            f'{zero_line}{"".join(bars)}</svg>')


# ---------------------------------------------------------------- 調査スコア

def clamp(v, lo=0.3, hi=5.0):
    return max(lo, min(hi, v))


def rev_cagr(s):
    rows = [r for r in s.get("fin_rows", []) if r["revenue"]]
    if len(rows) < 3:
        return None
    a, b = rows[0]["revenue"], rows[-1]["revenue"]
    if a <= 0 or b <= 0:
        return None
    return (b / a) ** (1 / (len(rows) - 1)) - 1


def has_fund(s):
    return any(s.get(k) is not None for k in ("per", "pbr", "roe", "yield", "equity"))


def scout_scores(s):
    per = s.get("per")
    pbr = s.get("pbr")
    val_parts = []
    if per:
        val_parts.append(clamp((40 - per) / 32 * 5))
    if pbr:
        val_parts.append(clamp((3 - pbr) / 2.5 * 5))
    valuation = sum(val_parts) / len(val_parts) if val_parts else 2.5
    roe = s.get("roe") or 0
    profitability = clamp(roe / 4)
    dividend = clamp(s.get("yield") or 0)
    equity = s.get("equity") or 40
    stability = clamp(equity / 16)
    g = rev_cagr(s)
    growth = clamp(2.5 if g is None else 2.5 + g * 25)
    return [("割安度", valuation), ("収益性", profitability), ("配当", dividend),
            ("財務", stability), ("成長", growth)]


def radar_chart(scores, size=280):
    cx = cy = size / 2
    r_max = size / 2 - 42
    n = len(scores)

    def pt(i, r):
        ang = -math.pi / 2 + i * 2 * math.pi / n
        return cx + r * math.cos(ang), cy + r * math.sin(ang)

    rings = []
    for lv in (1, 2, 3, 4, 5):
        p = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, r_max * lv / 5) for i in range(n)))
        rings.append(f'<polygon points="{p}" fill="none" stroke="var(--border)" stroke-width="{1.2 if lv == 5 else 0.6}"/>')
    axes = []
    labels = []
    for i, (name, v) in enumerate(scores):
        x, y = pt(i, r_max)
        axes.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="var(--border)" stroke-width="0.6"/>')
        lx, ly = pt(i, r_max + 24)
        labels.append(f'<text x="{lx:.1f}" y="{ly:.1f}" class="r-label">{name}</text>'
                      f'<text x="{lx:.1f}" y="{ly + 14:.1f}" class="r-score">{v:.1f}</text>')
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, r_max * v / 5) for i, (_, v) in enumerate(scores)))
    return (f'<svg class="radar" viewBox="0 0 {size} {size}" role="img" aria-label="5軸評価レーダーチャート">'
            f'{"".join(rings)}{"".join(axes)}'
            f'<polygon points="{poly}" fill="rgba(41,98,255,0.28)" stroke="var(--accent)" stroke-width="2"/>'
            f'{"".join(labels)}</svg>')


def scout_report(s):
    lines = []
    per, pbr = s.get("per"), s.get("pbr")
    if per and pbr:
        if per < 12 and pbr < 1.2:
            v = f"PER{per:.1f}倍・PBR{pbr:.2f}倍と、市場平均（PER約15倍・PBR約1.4倍）に対して割安な水準にある。バリュー株としての性格が強い。"
        elif per < 18:
            v = f"PER{per:.1f}倍・PBR{pbr:.2f}倍で、市場平均と比べて妥当な評価水準にある。"
        else:
            v = f"PER{per:.1f}倍・PBR{pbr:.2f}倍と市場平均より高く、将来の成長への期待が株価に織り込まれている。成長シナリオが崩れた場合の下振れには注意したい。"
        if pbr < 1.0:
            v += " PBRは1倍を割り込んでおり、解散価値を下回る評価となっている。"
        lines.append(("割安度", v))
    elif pbr:
        lines.append(("割安度", f"直近は赤字等でPERが算出できない。PBRは{pbr:.2f}倍。業績回復の見通しが評価の鍵になる。"))
    roe = s.get("roe")
    if roe is not None:
        if roe >= 15:
            p = f"ROEは{roe:.1f}%と非常に高く、資本を効率よく利益に変えている。目安とされる8%を大きく上回る。"
        elif roe >= 8:
            p = f"ROEは{roe:.1f}%で、目安とされる8%を上回る良好な水準。"
        else:
            p = f"ROEは{roe:.1f}%と、目安とされる8%を下回る。資本効率の改善が今後の課題といえる。"
        lines.append(("収益性", p))
    y = s.get("yield")
    if y is not None:
        if y >= 4:
            d = f"配当利回りは{y:.2f}%と高水準で、インカム狙いの投資家にとって魅力的な水準。ただし減配リスクや業績との整合性は確認したい。"
        elif y >= 2:
            d = f"配当利回りは{y:.2f}%で、東証プライム平均（約2%）を上回る。"
        else:
            d = f"配当利回りは{y:.2f}%と低めで、株主還元よりも成長投資を優先する銘柄と位置づけられる。"
        lines.append(("配当", d))
    eq = s.get("equity")
    if eq is not None:
        if s["sector"] in ("銀行業", "証券、商品先物取引業", "保険業", "その他金融業"):
            f = f"自己資本比率は{eq:.1f}%。金融業は預金・保険負債等が負債に計上される業種特性上この水準が通常であり、他業種との単純比較はできない。"
        elif eq >= 60:
            f = f"自己資本比率は{eq:.1f}%と厚く、財務基盤は堅牢。不況局面への耐性が高い。"
        elif eq >= 40:
            f = f"自己資本比率は{eq:.1f}%で、財務の健全性は標準的な水準にある。"
        else:
            f = f"自己資本比率は{eq:.1f}%とやや低め。有利子負債の動向や金利上昇の影響に注意したい。"
        lines.append(("財務", f))
    g = rev_cagr(s)
    if g is not None:
        if g >= 0.08:
            gr = f"売上高は年平均{g * 100:.1f}%のペースで拡大しており、成長トレンドが続いている。"
        elif g >= 0:
            gr = f"売上高の年平均成長率は{g * 100:.1f}%で、緩やかな拡大にとどまる。"
        else:
            gr = f"売上高は年平均{abs(g) * 100:.1f}%の減収トレンドにあり、事業環境の変化が業績に影響している。"
        lines.append(("成長", gr))
    return lines


# ---------------------------------------------------------------- layout

EMBLEM = """<svg class="emblem" viewBox="0 0 100 112" aria-hidden="true">
<path d="M50 3 L93 19 V57 C93 84 74 101 50 109 C26 101 7 84 7 57 V19 Z" fill="var(--panel)" stroke="var(--brass)" stroke-width="2.5"/>
<path d="M50 11 L86 24.5 V56 C86 79 70 94 50 101 C30 94 14 79 14 56 V24.5 Z" fill="none" stroke="var(--brass)" stroke-width="0.8" opacity="0.6"/>
<path d="M46 76 L21 62 L31 61 L19 47 L31 47.5 L25 33 L46 52 Z" fill="var(--up)" opacity="0.9"/>
<path d="M54 76 L79 62 L69 61 L81 47 L69 47.5 L75 33 L54 52 Z" fill="var(--brass)" opacity="0.9"/>
<line x1="50" y1="20" x2="50" y2="86" stroke="var(--text-strong)" stroke-width="2"/>
<rect x="46.5" y="36" width="7" height="30" rx="1.5" fill="var(--text-strong)"/>
</svg>"""

NAV = f"""
<div class="wall-band" aria-hidden="true"></div>
<header class="site-header">
  <div class="wrap header-inner">
    <a class="logo" href="/index.html">{EMBLEM}<span class="logo-text">株式調査兵団<span class="logo-sub">STOCK RECON CORPS</span></span></a>
    <nav class="gnav" aria-label="グローバルナビ">
      <a href="/stocks/index.html">銘柄一覧</a>
      <a href="/ranking/dividend.html">ランキング</a>
      <a href="/sector/index.html">業種別</a>
      <a href="/about.html">について</a>
    </nav>
  </div>
</header>
"""

FOOTER = f"""
<footer class="site-footer">
  <div class="wrap">
    <p class="f-logo">{EMBLEM} 株式調査兵団</p>
    <p class="f-motto">市場という壁の外を調査し、データという報告書を持ち帰る。</p>
    <nav class="f-nav">
      <a href="/index.html">ホーム</a>
      <a href="/stocks/index.html">銘柄一覧</a>
      <a href="/ranking/dividend.html">配当利回りランキング</a>
      <a href="/ranking/per.html">低PERランキング</a>
      <a href="/sector/index.html">業種別</a>
      <a href="/about.html">このサイトについて・免責事項</a>
    </nav>
    <p class="disclaimer">株価・財務データはYahoo!ファイナンス等の公開情報を基に自動取得しています（最終更新: {UPDATED_DATE}）。
    データには遅延・誤差が含まれる場合があり、正確性・完全性を保証しません。当サイトの情報は投資勧誘を目的としたものではなく、
    投資判断は必ずご自身の責任で行ってください。</p>
    <p class="copy">© 2026 株式調査兵団</p>
  </div>
</footer>
"""


def page(*, path, title, desc, body, jsonld=None, og_type="website"):
    url = BASE_URL + "/" + path.replace("index.html", "").rstrip("/")
    if not url.endswith("/") and "." not in url.rsplit("/", 1)[-1]:
        url += "/"
    ld = ""
    if jsonld:
        ld = "".join(f'<script type="application/ld+json">{json.dumps(j, ensure_ascii=False)}</script>' for j in jsonld)
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="{og_type}">
<meta property="og:url" content="{url}">
<meta name="twitter:card" content="summary">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=Noto+Sans+JP:wght@400;500;700&family=Shippori+Mincho+B1:wght@600;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/style.css">
{ld}</head>
<body>
{NAV}
<main>
{body}
</main>
{FOOTER}
</body>
</html>"""
    out = DIST / path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return path


def breadcrumb_ld(items):
    return {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": BASE_URL + href}
            for i, (name, href) in enumerate(items)
        ],
    }


def breadcrumb_html(items):
    parts = []
    for i, (name, href) in enumerate(items):
        if i == len(items) - 1:
            parts.append(f"<li aria-current='page'>{name}</li>")
        else:
            parts.append(f"<li><a href='{href}'>{name}</a></li>")
    return f"<ol class='breadcrumb wrap'>{''.join(parts)}</ol>"


def kicker(en, ja=""):
    return f'<p class="kicker">{en}{f"<span> / {ja}</span>" if ja else ""}</p>'


# ---------------------------------------------------------------- tables

def stock_table_rows(stocks):
    rows = []
    for s in stocks:
        rows.append(f"""<tr>
<td class="td-name"><a href="/stocks/{s['code']}.html"><span class="t-code">{s['code']}</span> {s['name']}</a>
<span class="t-sector">{s['sector']}・{s['market']}</span></td>
<td class="td-num">{fmt_num(s.get('price'))}円</td>
<td class="td-num">{chg_span(s.get('chg'))}</td>
<td class="td-num">{per_disp(s)}</td>
<td class="td-num">{f"{s['pbr']:.2f}倍" if s.get('pbr') else '—'}</td>
<td class="td-num">{f"{s['roe']:.1f}%" if s.get('roe') is not None else '—'}</td>
<td class="td-num">{f"{s['yield']:.2f}%" if s.get('yield') is not None else '—'}</td>
<td class="td-num">{fmt_oku_or_dash(s.get('mcap'))}</td>
</tr>""")
    return "".join(rows)


TABLE_HEAD = """<thead><tr><th>銘柄</th><th class="td-num">株価</th><th class="td-num">前日比</th>
<th class="td-num">PER</th><th class="td-num">PBR</th><th class="td-num">ROE</th>
<th class="td-num">利回り</th><th class="td-num">時価総額</th></tr></thead>"""


# ---------------------------------------------------------------- rankings

RANKINGS = {
    "dividend": {
        "title": "配当利回りランキング", "need": "yield", "filter_mcap": True,
        "desc": f"日本株の配当利回りが高い銘柄ランキングTOP{RANK_LIMIT}【毎営業日更新】。高配当株・インカム投資の銘柄発見に。",
        "key": lambda s: s.get("yield") or -1, "reverse": True,
        "fmt": lambda s: f"{s['yield']:.2f}%" if s.get("yield") is not None else "—",
        "note": "配当利回り＝年間配当 ÷ 株価。高配当でも減配リスクには注意。",
    },
    "per": {
        "title": "低PERランキング", "need": "per", "filter_mcap": True,
        "desc": f"PER（株価収益率）が低い割安株ランキングTOP{RANK_LIMIT}【毎営業日更新】。バリュー投資のスクリーニングに。",
        "key": lambda s: s.get("per") or 9999, "reverse": False,
        "fmt": per_disp,
        "note": "PER＝株価 ÷ 1株あたり利益（EPS）。低いほど利益に対して株価が割安。",
    },
    "pbr": {
        "title": "低PBRランキング", "need": "pbr", "filter_mcap": True,
        "desc": f"PBR（株価純資産倍率）が低い資産バリュー株ランキングTOP{RANK_LIMIT}【毎営業日更新】。PBR1倍割れ銘柄の発見に。",
        "key": lambda s: s.get("pbr") or 9999, "reverse": False,
        "fmt": lambda s: f"{s['pbr']:.2f}倍" if s.get("pbr") else "—",
        "note": "PBR＝株価 ÷ 1株あたり純資産。1倍未満は解散価値を下回る水準。",
    },
    "roe": {
        "title": "高ROEランキング", "need": "roe", "filter_mcap": True,
        "desc": f"ROE（自己資本利益率）が高い高収益企業ランキングTOP{RANK_LIMIT}【毎営業日更新】。クオリティ株投資の参考に。",
        "key": lambda s: s.get("roe") or -999, "reverse": True,
        "fmt": lambda s: f"{s['roe']:.1f}%" if s.get("roe") is not None else "—",
        "note": "ROE＝純利益 ÷ 自己資本。8%が一つの目安。",
    },
    "mcap": {
        "title": "時価総額ランキング", "need": "mcap", "filter_mcap": False,
        "desc": f"日本株の時価総額ランキングTOP{RANK_LIMIT}【毎営業日更新】。大型株・主力銘柄の規模比較に。",
        "key": lambda s: s.get("mcap") or 0, "reverse": True,
        "fmt": lambda s: fmt_oku(s["mcap"]),
        "note": "時価総額＝株価 × 発行済株式数。企業の市場評価の大きさを表す。",
    },
    "gainers": {
        "title": "値上がり率ランキング", "need": "chg", "filter_mcap": False,
        "desc": f"本日の値上がり率ランキングTOP{RANK_LIMIT}【毎営業日更新】。上昇トレンド銘柄・資金流入を掴む。",
        "key": lambda s: s.get("chg") if s.get("chg") is not None else -999, "reverse": True,
        "fmt": lambda s: f"{'+' if s['chg'] >= 0 else ''}{s['chg']:.2f}%" if s.get("chg") is not None else "—",
        "note": "前日終値比の騰落率。",
    },
}


def ranking_universe(slug, stocks):
    r = RANKINGS[slug]
    rows = [s for s in stocks if s.get(r["need"]) is not None]
    if r["filter_mcap"]:
        rows = [s for s in rows if (s.get("mcap") or 0) >= RANK_MIN_MCAP]
    return sorted(rows, key=r["key"], reverse=r["reverse"])


def ranking_rows(slug, stocks, limit=RANK_LIMIT):
    r = RANKINGS[slug]
    out = []
    for i, s in enumerate(ranking_universe(slug, stocks)[:limit], 1):
        medal = f'<span class="rank-badge r{i}">{i}</span>' if i <= 3 else f'<span class="rank-badge">{i}</span>'
        out.append(f"""<tr>
<td class="td-rank">{medal}</td>
<td class="td-name"><a href="/stocks/{s['code']}.html"><span class="t-code">{s['code']}</span> {s['name']}</a>
<span class="t-sector">{s['sector']}・{s['market']}</span></td>
<td class="td-num">{fmt_num(s.get('price'))}円</td>
<td class="td-num td-chg">{chg_span(s.get('chg'))}</td>
<td class="td-num td-val">{r['fmt'](s)}</td>
<td class="td-spark">{sparkline(s)}</td>
</tr>""")
    return "".join(out)


def ranking_tabs(active):
    tabs = []
    for slug, r in RANKINGS.items():
        cls = ' class="active"' if slug == active else ""
        tabs.append(f'<a href="/ranking/{slug}.html"{cls}>{r["title"].replace("ランキング", "")}</a>')
    return f'<nav class="rank-tabs" aria-label="ランキング切替">{"".join(tabs)}</nav>'


def build_ranking_pages(stocks):
    for slug, r in RANKINGS.items():
        crumbs = [("ホーム", "/"), ("ランキング", "/ranking/dividend.html"), (r["title"], f"/ranking/{slug}.html")]
        univ = ranking_universe(slug, stocks)[:RANK_LIMIT]
        items_ld = {
            "@context": "https://schema.org", "@type": "ItemList", "name": r["title"],
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "name": f"{s['name']}（{s['code']}）",
                 "url": f"{BASE_URL}/stocks/{s['code']}.html"}
                for i, s in enumerate(univ)
            ],
        }
        cond = f"時価総額{RANK_MIN_MCAP}億円以上・" if r["filter_mcap"] else ""
        body = f"""
{breadcrumb_html(crumbs)}
<div class="wrap">
{kicker("RECON REPORT", "索敵報告")}
<h1 class="page-title">{r['title']}<span class="title-sub">{cond}TOP{len(univ)}・{UPDATED_DATE}更新</span></h1>
<p class="page-lead">{r['desc']}</p>
{ranking_tabs(slug)}
<div class="panel table-scroll">
<table class="rank-table">
<thead><tr><th>順位</th><th>銘柄</th><th class="td-num">株価</th><th class="td-num">前日比</th><th class="td-num">{r['title'].replace('ランキング','')}</th><th>30日推移</th></tr></thead>
<tbody>{ranking_rows(slug, stocks)}</tbody>
</table>
</div>
<div class="note-box"><h2>指標の見方</h2><p>{r['note']}{f" 掲載は時価総額{RANK_MIN_MCAP}億円以上の銘柄に限定しています。" if r['filter_mcap'] else ""}</p></div>
</div>"""
        page(path=f"ranking/{slug}.html",
             title=f"{r['title']}TOP{RANK_LIMIT}【日本株・{UPDATED_DATE}更新】 | {SITE_NAME}",
             desc=r["desc"],
             body=body,
             jsonld=[breadcrumb_ld(crumbs), items_ld])


# ---------------------------------------------------------------- sectors

def build_sector_pages(stocks):
    grouped = {}
    for s in stocks:
        grouped.setdefault(s["sector"], []).append(s)

    crumbs = [("ホーム", "/"), ("業種別", "/sector/index.html")]
    cards = ""
    for sec, ss in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        slug = SECTOR_SLUGS.get(sec, "other")
        yields = [s["yield"] for s in ss if s.get("yield") is not None]
        avg = f"平均利回り {sum(yields) / len(yields):.2f}%" if yields else ""
        cards += f"""<a class="rel-card" href="/sector/{slug}.html">
<span class="rel-name">{sec}</span>
<span class="rel-price">{len(ss)}銘柄{f"・{avg}" if avg else ""}</span></a>"""
    body = f"""
{breadcrumb_html(crumbs)}
<div class="wrap">
{kicker("SECTOR MAP", "戦域図")}
<h1 class="page-title">業種別 銘柄マップ<span class="title-sub">東証33業種・全{len(stocks)}銘柄</span></h1>
<p class="page-lead">東証33業種分類にもとづく業種ごとの銘柄一覧。業種の平均利回りや構成銘柄の比較から、セクター単位での銘柄発見に。</p>
<div class="rel-grid">{cards}</div>
</div>"""
    page(path="sector/index.html",
         title=f"業種別 銘柄一覧｜東証33業種セクターマップ | {SITE_NAME}",
         desc=f"東証33業種分類の業種別銘柄一覧（全{len(stocks)}銘柄）。セクターごとの平均配当利回り・構成銘柄を比較。",
         body=body, jsonld=[breadcrumb_ld(crumbs)])

    for sec, ss in grouped.items():
        slug = SECTOR_SLUGS.get(sec, "other")
        crumbs = [("ホーム", "/"), ("業種別", "/sector/index.html"), (sec, f"/sector/{slug}.html")]
        ss_sorted = sorted(ss, key=lambda s: s.get("mcap") or 0, reverse=True)
        pers = [s["per"] for s in ss if s.get("per")]
        yields = [s["yield"] for s in ss if s.get("yield") is not None]
        stats = []
        if pers:
            stats.append(f"平均PERは{sum(pers) / len(pers):.1f}倍")
        if yields:
            stats.append(f"平均配当利回りは{sum(yields) / len(yields):.2f}%")
        lead = (f"{sec}セクター全{len(ss)}銘柄を時価総額順に、株価・PER・PBR・ROE・配当利回りで比較。"
                + ("・".join(stats) + "。" if stats else ""))
        body = f"""
{breadcrumb_html(crumbs)}
<div class="wrap">
{kicker("SECTOR RECON", "戦域調査")}
<h1 class="page-title">{sec}の銘柄一覧<span class="title-sub">{len(ss)}銘柄・{UPDATED_DATE}更新</span></h1>
<p class="page-lead">{lead}</p>
<div class="panel table-scroll">
<table class="rank-table list-table">{TABLE_HEAD}<tbody>{stock_table_rows(ss_sorted)}</tbody></table>
</div>
</div>"""
        page(path=f"sector/{slug}.html",
             title=f"{sec}の銘柄一覧（{len(ss)}社）｜株価・PER・配当利回り比較 | {SITE_NAME}",
             desc=f"{sec}セクター全{len(ss)}銘柄の株価・PER・PBR・ROE・配当利回りを時価総額順に一覧比較。{UPDATED_DATE}更新。",
             body=body, jsonld=[breadcrumb_ld(crumbs)])
    return grouped


# ---------------------------------------------------------------- stock list (paginated)

def build_stock_lists(stocks):
    by_market = {}
    for s in stocks:
        by_market.setdefault(s["market"], []).append(s)

    # ハブページ: 市場別リンク + 時価総額上位50
    crumbs = [("ホーム", "/"), ("銘柄一覧", "/stocks/index.html")]
    market_cards = ""
    for mkt, slug in MARKET_SLUGS.items():
        ss = by_market.get(mkt, [])
        pages = max(1, -(-len(ss) // PAGE_SIZE))
        market_cards += f"""<a class="rel-card" href="/stocks/{slug}-1.html">
<span class="rel-name">東証{mkt}</span>
<span class="rel-price">{len(ss)}銘柄・{pages}ページ</span></a>"""
    top50 = sorted(stocks, key=lambda s: s.get("mcap") or 0, reverse=True)[:50]
    body = f"""
{breadcrumb_html(crumbs)}
<div class="wrap">
{kicker("ROSTER", "戦力名簿")}
<h1 class="page-title">銘柄一覧<span class="title-sub">全{len(stocks)}銘柄・{UPDATED_DATE}更新</span></h1>
<p class="page-lead">東証プライム・スタンダード・グロースの全上場銘柄を市場別に掲載。まずは時価総額上位50銘柄から。</p>
<div class="rel-grid">{market_cards}</div>
<h2 class="sec-title" style="margin-top:36px">時価総額 上位50銘柄</h2>
<div class="panel table-scroll">
<table class="rank-table list-table">{TABLE_HEAD}<tbody>{stock_table_rows(top50)}</tbody></table>
</div>
</div>"""
    page(path="stocks/index.html",
         title=f"銘柄一覧｜全{len(stocks)}銘柄の株価・PER・配当利回り【{UPDATED_DATE}更新】 | {SITE_NAME}",
         desc=f"東証全上場{len(stocks)}銘柄の株価・PER・PBR・ROE・配当利回り一覧【{UPDATED_DATE}更新】。市場別・業種別に銘柄発見。",
         body=body, jsonld=[breadcrumb_ld(crumbs)])

    # 市場別ページネーション
    for mkt, slug in MARKET_SLUGS.items():
        ss = sorted(by_market.get(mkt, []), key=lambda s: s["code"])
        n_pages = max(1, -(-len(ss) // PAGE_SIZE))
        for p in range(1, n_pages + 1):
            chunk = ss[(p - 1) * PAGE_SIZE: p * PAGE_SIZE]
            crumbs = [("ホーム", "/"), ("銘柄一覧", "/stocks/index.html"),
                      (f"東証{mkt}（{p}）", f"/stocks/{slug}-{p}.html")]
            pager = '<nav class="pager">'
            for q in range(1, n_pages + 1):
                cls = ' class="active"' if q == p else ""
                pager += f'<a href="/stocks/{slug}-{q}.html"{cls}>{q}</a>'
            pager += "</nav>"
            body = f"""
{breadcrumb_html(crumbs)}
<div class="wrap">
{kicker("ROSTER", "戦力名簿")}
<h1 class="page-title">東証{mkt}の銘柄一覧<span class="title-sub">{len(ss)}銘柄中 {(p - 1) * PAGE_SIZE + 1}〜{min(p * PAGE_SIZE, len(ss))}件目</span></h1>
{pager}
<div class="panel table-scroll">
<table class="rank-table list-table">{TABLE_HEAD}<tbody>{stock_table_rows(chunk)}</tbody></table>
</div>
{pager}
</div>"""
            page(path=f"stocks/{slug}-{p}.html",
                 title=f"東証{mkt}の銘柄一覧（{p}/{n_pages}）｜株価・投資指標 | {SITE_NAME}",
                 desc=f"東証{mkt}上場{len(ss)}銘柄の一覧（{p}ページ目）。株価・PER・PBR・ROE・配当利回りを比較。",
                 body=body, jsonld=[breadcrumb_ld(crumbs)])
    return {slug: max(1, -(-len(by_market.get(mkt, [])) // PAGE_SIZE)) for mkt, slug in MARKET_SLUGS.items()}


# ---------------------------------------------------------------- stock pages

def metric_card(label, value, hint=""):
    hint_html = f'<span class="m-hint">{hint}</span>' if hint else ""
    return f'<div class="metric"><span class="m-label">{label}</span><span class="m-value">{value}</span>{hint_html}</div>'


def stock_faq(s):
    name, code = s["name"], s["code"]
    qa = []
    if s.get("yield") is not None:
        qa.append((f"{name}（{code}）の配当利回りは？",
                   f"{UPDATED_DATE}時点の配当利回りは{s['yield']:.2f}%です。東証プライム平均はおよそ2%です。"))
    if s.get("per"):
        level = "割安" if s["per"] < 12 else ("平均的" if s["per"] < 18 else "割高")
        qa.append((f"{name}のPERは割安ですか？",
                   f"PERは{s['per']:.1f}倍で、市場平均（約15倍）と比較すると{level}な水準です。PERのみで判断せず、成長性や業種特性も考慮してください。"))
    if s.get("mcap"):
        qa.append((f"{name}の時価総額はいくらですか？",
                   f"{UPDATED_DATE}時点の時価総額は約{fmt_oku(s['mcap'])}です。"))
    if not qa:
        return "", None
    faq_html = "".join(
        f'<details class="faq"><summary>{q}</summary><p>{a}</p></details>' for q, a in qa)
    faq_ld = {
        "@context": "https://schema.org", "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in qa
        ],
    }
    return faq_html, faq_ld


def build_stock_pages(stocks, grouped):
    for s in stocks:
        code, name = s["code"], s["name"]
        desc_text = DESC_OVERRIDES.get(code) or auto_desc(s)
        crumbs = [("ホーム", "/"), ("銘柄一覧", "/stocks/index.html"), (f"{name}（{code}）", f"/stocks/{code}.html")]

        # 同業種の時価総額上位
        related = [o for o in sorted(grouped.get(s["sector"], []),
                                     key=lambda x: x.get("mcap") or 0, reverse=True)
                   if o["code"] != code][:4]
        related_html = ""
        if related:
            cards = "".join(
                f"""<a class="rel-card" href="/stocks/{o['code']}.html">
<span class="t-code">{o['code']}</span><span class="rel-name">{o['name']}</span>
<span class="rel-price">{fmt_num(o.get('price'))}円 {chg_span(o.get('chg'))}</span></a>"""
                for o in related)
            slug = SECTOR_SLUGS.get(s["sector"], "other")
            related_html = (f'<section class="sec"><h2>同業種（{s["sector"]}）の銘柄</h2>'
                            f'<div class="rel-grid">{cards}</div>'
                            f'<a class="more-link" href="/sector/{slug}.html">{s["sector"]}の銘柄一覧 →</a></section>')

        # 分析レポート（指標データがあるときだけ）
        report_sec = ""
        if has_fund(s):
            report_html = "".join(
                f'<div class="report-line"><span class="report-tag">{tag}</span><p>{text}</p></div>'
                for tag, text in scout_report(s))
            if report_html:
                report_sec = f"""
<section class="sec">
  {kicker("SCOUT REPORT", "調査報告書")}
  <h2>{name}の分析レポート</h2>
  <div class="report-grid">
    <div class="panel radar-panel">
      <h3>5軸評価</h3>
      {radar_chart(scout_scores(s))}
      <p class="table-note">各指標を0〜5で機械的にスコア化（{UPDATED_DATE}時点）</p>
    </div>
    <div class="panel report-panel">{report_html}
      <p class="table-note">しきい値ベースの自動生成コメントです。投資判断はご自身で。</p>
    </div>
  </div>
</section>"""

        # 業績推移・財務テーブル（財務データがあるときだけ）
        fin_sec = ""
        if s.get("fin_rows"):
            fin_rows_html = "".join(f"""<tr><th scope="row">{r['year']}</th>
<td class="td-num">{fmt_oku_or_dash(r['revenue'])}</td>
<td class="td-num">{fmt_oku_or_dash(r['op'])}</td>
<td class="td-num">{fmt_oku_or_dash(r['net'])}</td>
<td class="td-num">{fmt_num(r['eps'])}{'円' if r['eps'] is not None else ''}</td></tr>"""
                                    for r in s["fin_rows"])
            div_chart = ""
            if s.get("div_rows"):
                div_chart = f'<div class="panel chart-panel"><h3>1株配当（年間）<span class="c-unit">（円）</span></h3>{bar_chart(s["div_rows"], "円", color="var(--gold)")}</div>'
            fin_sec = f"""
<section class="sec">
  {kicker("FIELD RECORDS", "戦績記録")}
  <h2>業績推移</h2>
  <div class="chart-grid">
    <div class="panel chart-panel"><h3>売上高<span class="c-unit">（億円）</span></h3>{bar_chart([(r['year'], r['revenue']) for r in s['fin_rows']])}</div>
    <div class="panel chart-panel"><h3>営業利益<span class="c-unit">（億円）</span></h3>{bar_chart([(r['year'], r['op']) for r in s['fin_rows']], color="var(--accent2)")}</div>
    <div class="panel chart-panel"><h3>純利益<span class="c-unit">（億円）</span></h3>{bar_chart([(r['year'], r['net']) for r in s['fin_rows']], color="var(--up)")}</div>
    {div_chart}
  </div>
</section>

<section class="sec">
  <h2>財務データ</h2>
  <div class="panel table-scroll">
  <table class="fin-table">
    <thead><tr><th>決算期</th><th class="td-num">売上高</th><th class="td-num">営業利益</th><th class="td-num">純利益</th><th class="td-num">EPS</th></tr></thead>
    <tbody>{fin_rows_html}</tbody>
  </table>
  </div>
  <p class="table-note">出典: Yahoo!ファイナンス等の公開情報より自動取得。「—」は取得不可の項目。</p>
</section>"""

        faq_html, faq_ld = stock_faq(s)
        faq_sec = f'<section class="sec"><h2>よくある質問</h2>{faq_html}</section>' if faq_html else ""

        jsonld = [
            breadcrumb_ld(crumbs),
            {"@context": "https://schema.org", "@type": "Corporation",
             "name": name, "tickerSymbol": f"TYO:{code}",
             "description": desc_text,
             "url": f"{BASE_URL}/stocks/{code}.html"},
        ]
        if faq_ld:
            jsonld.append(faq_ld)

        body = f"""
{breadcrumb_html(crumbs)}
<div class="wrap">
<div class="stock-head panel">
  <div class="sh-id">
    <span class="sh-code">{code} <span class="sh-market">東証{s['market']}</span> <span class="sh-sector"><a href="/sector/{SECTOR_SLUGS.get(s['sector'], 'other')}.html">{s['sector']}</a></span></span>
    <h1>{name}<span class="h1-sub">の株価・財務分析</span></h1>
  </div>
  <div class="sh-price">
    <span class="sh-now">{fmt_num(s.get('price'))}<span class="sh-yen">円</span></span>
    {chg_span(s.get('chg'))}
    {sparkline(s, w=160, h=44)}
  </div>
</div>
<p class="updated-note">最終更新: {UPDATED_DATE}（Yahoo!ファイナンス等の公開情報より自動取得）</p>

<section class="sec">
  {kicker("KEY METRICS", "主要計器")}
  <h2>投資指標</h2>
  <div class="metric-grid">
    {metric_card("PER（株価収益率）", per_disp(s), "15倍前後が市場平均の目安")}
    {metric_card("PBR（株価純資産倍率）", f"{s['pbr']:.2f}倍" if s.get('pbr') else "—", "1倍未満は解散価値割れ")}
    {metric_card("ROE（自己資本利益率）", f"{s['roe']:.1f}%" if s.get('roe') is not None else "—", "8%以上で資本効率良好")}
    {metric_card("配当利回り", f"{s['yield']:.2f}%" if s.get('yield') is not None else "—", "東証プライム平均は約2%")}
    {metric_card("時価総額", fmt_oku_or_dash(s.get('mcap')))}
    {metric_card("自己資本比率", f"{s['equity']:.1f}%" if s.get('equity') is not None else "—", "財務健全性の目安")}
  </div>
</section>
{report_sec}
<section class="sec">
  <h2>企業概要</h2>
  <p class="stock-desc">{desc_text}</p>
</section>
{fin_sec}
{faq_sec}
{related_html}
</div>"""
        yield_disp = f"{s['yield']:.2f}%" if s.get("yield") is not None else "—"
        pbr_disp = f"{s['pbr']:.2f}倍" if s.get("pbr") else "—"
        roe_disp = f"{s['roe']:.1f}%" if s.get("roe") is not None else "—"
        page(path=f"stocks/{code}.html",
             title=f"{name}（{code}）の株価・財務分析｜PER {per_disp(s)}・配当利回り {yield_disp} | {SITE_NAME}",
             desc=f"{name}（{code}）の株価・投資指標・業績推移を分析【{UPDATED_DATE}更新】。PER{per_disp(s)}、PBR{pbr_disp}、ROE{roe_disp}。東証{s['market']}・{s['sector']}。",
             body=body,
             og_type="article",
             jsonld=jsonld)


# ---------------------------------------------------------------- top page

HERO_MAP = """<svg class="hero-map" viewBox="0 0 1200 480" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
<defs>
<pattern id="grid" width="60" height="60" patternUnits="userSpaceOnUse">
<path d="M60 0H0V60" fill="none" stroke="rgba(140,150,180,0.10)" stroke-width="1"/>
</pattern>
<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M0 0L10 5L0 10Z" fill="rgba(201,162,39,0.55)"/>
</marker>
</defs>
<rect width="1200" height="480" fill="url(#grid)"/>
<path d="M-40 380 C160 330 240 400 430 356 C620 312 700 380 900 330 C1060 290 1140 340 1240 310"
 fill="none" stroke="rgba(140,150,180,0.16)" stroke-width="1.4"/>
<path d="M-40 320 C180 270 300 330 480 290 C660 250 760 320 960 270 C1100 236 1170 280 1240 255"
 fill="none" stroke="rgba(140,150,180,0.12)" stroke-width="1.2"/>
<path d="M-40 430 C140 390 260 450 470 415 C680 380 780 440 980 395 C1120 364 1180 400 1240 380"
 fill="none" stroke="rgba(140,150,180,0.10)" stroke-width="1.2"/>
<circle cx="915" cy="120" r="46" fill="none" stroke="rgba(201,162,39,0.30)" stroke-width="1"/>
<circle cx="915" cy="120" r="34" fill="none" stroke="rgba(201,162,39,0.18)" stroke-width="1"/>
<path d="M915 66 V174 M861 120 H969 M915 74 L921 114 L915 120 L909 114 Z" stroke="rgba(201,162,39,0.35)" stroke-width="1.2" fill="rgba(201,162,39,0.25)"/>
<path d="M120 400 C260 340 330 300 470 292 C610 284 700 220 830 196"
 fill="none" stroke="rgba(201,162,39,0.45)" stroke-width="1.6" stroke-dasharray="7 6" marker-end="url(#arrow)"/>
<circle cx="120" cy="400" r="4" fill="rgba(201,162,39,0.6)"/>
</svg>"""


def build_index(stocks):
    idx_items = []
    for m in MARKET_SUMMARY:
        live = INDICES.get(m["name"])
        if live:
            up = live["pct"] >= 0
            idx_items.append({"name": m["name"],
                              "value": f"{live['value']:,.2f}",
                              "chg": f"{'+' if live['chg'] >= 0 else ''}{live['chg']:,.2f}",
                              "pct": f"{'+' if up else ''}{live['pct']:.2f}%", "up": up})
    strip = ""
    if idx_items:
        market_items = "".join(
            f"""<div class="mkt-item"><span class="mkt-name">{m['name']}</span>
<span class="mkt-val">{m['value']}</span>
<span class="chg {'up' if m['up'] else 'down'}">{m['chg']}（{m['pct']}）</span></div>"""
            for m in idx_items)
        strip = f'<div class="mkt-strip" aria-label="マーケットサマリー"><div class="mkt-track">{market_items}{market_items}</div></div>'

    featured = sorted(stocks, key=lambda s: s.get("mcap") or 0, reverse=True)[:6]
    cards = "".join(f"""<a class="stock-card" href="/stocks/{s['code']}.html">
<div class="sc-head"><span class="t-code">{s['code']}</span><span class="sc-sector">{s['sector']}</span></div>
<h3 class="sc-name">{s['name']}</h3>
<div class="sc-price">{fmt_num(s.get('price'))}円 {chg_span(s.get('chg'))}</div>
{sparkline(s, w=200, h=44)}
<div class="sc-metrics"><span>PER {per_disp(s)}</span><span>ROE {f"{s['roe']:.1f}%" if s.get('roe') is not None else '—'}</span><span>利回り {f"{s['yield']:.2f}%" if s.get('yield') is not None else '—'}</span></div>
</a>""" for s in featured)

    previews = ""
    for slug in ("dividend", "per", "roe"):
        r = RANKINGS[slug]
        previews += f"""<div class="panel rank-preview">
<h3><a href="/ranking/{slug}.html">{r['title']}</a></h3>
<table class="rank-table mini"><tbody>{ranking_rows(slug, stocks, limit=5)}</tbody></table>
<a class="more-link" href="/ranking/{slug}.html">TOP{RANK_LIMIT}をすべて見る →</a>
</div>"""

    jsonld = [{
        "@context": "https://schema.org", "@type": "WebSite",
        "name": SITE_NAME, "url": BASE_URL + "/", "description": SITE_DESC,
    }]

    body = f"""
<div class="hero">
  {HERO_MAP}
  <div class="wrap hero-inner">
    {EMBLEM}
    {kicker("STOCK RECON CORPS")}
    <h1>壁の外の市場を、<span class="grad">調査せよ。</span></h1>
    <p class="hero-lead">株式調査兵団は、東証プライム・スタンダード・グロース全{len(stocks):,}銘柄の財務データ・投資指標・ランキングを毎営業日自動更新する銘柄調査アーカイブ。数字という武器で、市場の霧を晴らす。</p>
    <div class="hero-cta">
      <a class="btn btn-primary" href="/stocks/index.html">銘柄一覧を見る</a>
      <a class="btn btn-ghost" href="/ranking/dividend.html">ランキングで探す</a>
    </div>
  </div>
</div>

{strip}

<div class="wrap">
<section class="sec">
  {kicker("MAIN FORCE", "主力戦力")}
  <h2 class="sec-title">注目銘柄<span class="title-sub">時価総額上位・{UPDATED_DATE}更新</span></h2>
  <div class="card-grid">{cards}</div>
</section>

<section class="sec">
  {kicker("RECON REPORT", "索敵報告")}
  <h2 class="sec-title">ランキングで銘柄発見</h2>
  <div class="preview-grid">{previews}</div>
</section>

<section class="sec about-sec panel">
  <h2>株式調査兵団とは</h2>
  <p>株式調査兵団は、企業の財務データを見やすく可視化する<strong>企業分析</strong>と、
  配当利回り・PER・ROEなどの<strong>ランキング</strong>を組み合わせた日本株の情報サイトです。
  東証全上場{len(stocks):,}銘柄の株価・財務データを公開情報から毎営業日自動取得し、銘柄ごとの分析レポートを機械的に生成しています。
  「気になる銘柄を深く知る」「条件に合う銘柄を素早く見つける」——投資家の2つのニーズに、1つのサイトで応えます。</p>
</section>
</div>"""
    page(path="index.html",
         title=f"{SITE_NAME}｜{SITE_TAGLINE}【毎営業日更新】",
         desc=SITE_DESC,
         body=body, jsonld=jsonld)


# ---------------------------------------------------------------- about

def build_about(n_stocks):
    crumbs = [("ホーム", "/"), ("このサイトについて", "/about.html")]
    body = f"""
{breadcrumb_html(crumbs)}
<div class="wrap narrow">
{kicker("HEADQUARTERS", "本部")}
<h1 class="page-title">このサイトについて</h1>
<section class="sec panel pad">
<h2>株式調査兵団の目的</h2>
<p>株式調査兵団は、日本株の財務データ可視化とランキングによる銘柄発見を提供する株式情報サイトです。
東証プライム・スタンダード・グロースの全上場{n_stocks:,}銘柄について、個別銘柄ページでは業績推移・投資指標・
自動生成の分析レポートを、ランキングページでは配当利回り・PER・PBR・ROE・時価総額など複数の切り口での銘柄比較を提供します。</p>
</section>
<section class="sec panel pad">
<h2>データについて</h2>
<ul class="plain-list">
<li>銘柄マスタは日本取引所グループ（JPX）公表の東証上場銘柄一覧にもとづきます。</li>
<li>株価・投資指標・財務データはYahoo!ファイナンス等の公開情報を基に、毎営業日プログラムで自動取得しています。</li>
<li>データには遅延（通常20分以上）や誤差が含まれる場合があります。投資指標・財務諸表は銘柄ごとに数日周期で巡回更新しているため、更新タイミングに差があります。</li>
<li>分析レポートは指標のしきい値にもとづく機械的な自動生成であり、個別の投資助言ではありません。</li>
<li>最終更新: {UPDATED_DATE}</li>
</ul>
</section>
<section class="sec panel pad">
<h2>免責事項</h2>
<ul class="plain-list">
<li>当サイトの情報は投資勧誘を目的としたものではありません。</li>
<li>投資に関する最終決定はご自身の判断と責任において行ってください。</li>
<li>掲載情報の正確性・完全性・有用性についていかなる保証も行いません。</li>
<li>当サイトの情報に基づいて被ったいかなる損害についても、運営者は一切の責任を負いません。</li>
</ul>
</section>
</div>"""
    page(path="about.html",
         title=f"このサイトについて・免責事項 | {SITE_NAME}",
         desc="株式調査兵団の運営方針・データの取得方法・免責事項について。",
         body=body, jsonld=[breadcrumb_ld(crumbs)])


# ---------------------------------------------------------------- sitemap / robots

def build_sitemap(paths):
    urls = "".join(
        f"<url><loc>{BASE_URL}/{p.replace('index.html', '')}</loc><lastmod>{UPDATED_DATE}</lastmod></url>"
        for p in paths)
    (DIST / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n',
        encoding="utf-8")
    (DIST / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n", encoding="utf-8")


# ---------------------------------------------------------------- main

def main():
    all_stocks = build_stocks()
    stocks = [s for s in all_stocks if s.get("price")]  # 株価が取れた銘柄のみページ生成
    skipped = len(all_stocks) - len(stocks)

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    shutil.copy(ROOT / "static" / "style.css", DIST / "style.css")

    build_index(stocks)
    pages_per_market = build_stock_lists(stocks)
    grouped = build_sector_pages(stocks)
    build_stock_pages(stocks, grouped)
    build_ranking_pages(stocks)
    build_about(len(stocks))

    paths = ["index.html", "stocks/index.html", "sector/index.html", "about.html"]
    paths += [f"stocks/{s['code']}.html" for s in stocks]
    paths += [f"ranking/{slug}.html" for slug in RANKINGS]
    paths += [f"sector/{SECTOR_SLUGS.get(sec, 'other')}.html" for sec in grouped]
    for slug, n_pages in pages_per_market.items():
        paths += [f"stocks/{slug}-{p}.html" for p in range(1, n_pages + 1)]
    build_sitemap(paths)

    n = len(list(DIST.rglob("*.html")))
    fund = sum(1 for s in stocks if has_fund(s))
    fin = sum(1 for s in stocks if s.get("fin_rows"))
    print(f"Build complete: {n:,} pages → {DIST}")
    print(f"  銘柄 {len(stocks):,}（株価なしスキップ {skipped}） / 指標あり {fund:,} / 財務あり {fin:,} / 更新 {UPDATED_DATE}")
    print(f"  BASE_URL = {BASE_URL}")


if __name__ == "__main__":
    main()
