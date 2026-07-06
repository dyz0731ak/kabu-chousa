#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""株式調査兵団 データ取得スクリプト（LLM不使用・cron/GitHub Actionsで定期実行）

データソースと2層構造:
  1. 株価・30日終値 : yfinance の一括バッチ（毎回・全銘柄・数分）※無調整終値
  2. 指標・財務     : かぶたん（kabutan.jp）個別2ページ — 古い順に N 銘柄/回のローテーション
                      予想EPS・予想1株配・BPS・発行済株式数・ROE・自己資本比率・
                      通期業績（売上/営業益/純利益/EPS/配当、会社予想行つき）

  ※Yahoo(.info)の日本株ファンダはEPS・発行済株式数が古く不正確なため使わない（2026-07確認。
    例: 日本製鉄PERが166倍と表示される等）。PER/PBR/利回り/時価総額の比率は
    ビルド時に「1株あたり値 × 最新株価」で毎日再計算する（四季報・証券会社と同じ予想ベース）。

使い方:
  python3 scripts/fetch_data.py                 # 株価全銘柄 + かぶたん500銘柄
  python3 scripts/fetch_data.py --kabutan 800   # ローテーション件数を増やす
  python3 scripts/fetch_data.py --prices-only
"""
import argparse
import datetime
import json
import re
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

try:
    import requests
    from bs4 import BeautifulSoup
    import yfinance as yf
except ImportError as e:
    print(f"[ERROR] 依存パッケージ不足: {e}（pip install -r scripts/requirements.txt）", file=sys.stderr)
    sys.exit(1)

JST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(JST).isoformat(timespec="seconds")
TODAY = NOW[:10]

MARKET_PRIORITY = {"プライム": 0, "スタンダード": 1, "グロース": 2}
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
KABUTAN_WAIT = 0.25  # リクエスト間隔（礼儀）

INDICES = [
    ("日経平均", "^N225"),
    ("NYダウ", "^DJI"),
    ("S&P500", "^GSPC"),
    ("ドル/円", "JPY=X"),
]


def load_existing(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def load_master():
    master = load_existing(DATA / "master.json")
    if not master:
        print("[ERROR] data/master.json がありません。先に scripts/fetch_master.py を実行してください。",
              file=sys.stderr)
        sys.exit(1)
    return master


# ---------------------------------------------------------------- 1. 株価一括

def fetch_prices(codes, market):
    """yfinance で全銘柄の（無調整）終値と30日推移を一括取得"""
    CHUNK = 400
    ok = 0
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        tickers = [f"{c}.T" for c in chunk]
        try:
            df = yf.download(tickers, period="2mo", interval="1d",
                             group_by="ticker", threads=True, progress=False,
                             auto_adjust=False)  # 証券会社表示と同じ「実際の終値」を使う
        except Exception as e:
            print(f"[WARN] price chunk {i}: {e}", file=sys.stderr)
            continue
        for code, ticker in zip(chunk, tickers):
            try:
                closes = df[ticker]["Close"].dropna() if len(tickers) > 1 else df["Close"].dropna()
                if len(closes) < 2:
                    continue
                last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
                m = market.setdefault(code, {})
                m["price"] = round(last, 1)
                m["chg"] = round((last - prev) / prev * 100, 2)
                m["hist"] = [round(float(v), 1) for v in closes.tail(30)]
                m["date"] = TODAY
                ok += 1
            except Exception:
                continue
        print(f"  prices {min(i + CHUNK, len(codes))}/{len(codes)}")
    return ok


# ---------------------------------------------------------------- 2. かぶたん

def _num(text):
    """'2,746' '11.2' '－' → float or None"""
    t = str(text).replace(",", "").replace("－", "").replace("―", "").replace("‐", "").strip()
    t = t.replace("倍", "").replace("％", "").replace("%", "").replace("円", "").replace("株", "").strip()
    if not t or t in ("-", "—"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _cells(tbl):
    return [c.get_text(strip=True) for c in tbl.find_all(["th", "td"])]


# 決算期セルは「I2023.03」「連2002.03」「I予2027.03」のように種別マークと同居する
DATE_RE = re.compile(r"\d{4}\.\d{2}")


def _find_date_cell(tds):
    """決算期らしいセルの位置を返す（長文セルの誤マッチは除外）"""
    for k, t in enumerate(tds):
        if len(t) <= 12 and DATE_RE.search(t):
            return k
    return None


def parse_kabutan_main(soup):
    """個別ページ → 基準株価・PER/PBR/利回り・時価総額・発行済株式数"""
    out = {}
    kabuka = soup.select_one("span.kabuka")
    if kabuka:
        out["k_price"] = _num(kabuka.get_text(strip=True))
    for tbl in soup.find_all("table"):
        cells = _cells(tbl)
        if "信用倍率" in cells and "PER" in cells:
            # 値セルは「11.5倍」「0.87倍」「3.42％」のように単位込みで並ぶ
            # （単位が別セルに分かれるケースにも耐えるよう、数値だけを順に拾う）
            i = cells.index("信用倍率")
            end = cells.index("時価総額") if "時価総額" in cells[i:] else min(i + 9, len(cells))
            if "時価総額" in cells:
                end = cells.index("時価総額")
            nums = [v for v in (_num(c) for c in cells[i + 1:end]) if v is not None]
            if len(nums) >= 3:
                out["k_per"], out["k_pbr"], out["k_yield"] = nums[0], nums[1], nums[2]
        if "発行済株式数" in cells:
            j = cells.index("発行済株式数")
            out["shares"] = _num(cells[j + 1]) if j + 1 < len(cells) else None
        if "時価総額" in cells and "mcap_k" not in out:
            j = cells.index("時価総額")
            # 「42兆6,611億円」（1セル）にも「41|兆|2,746|億円」（分割）にも対応
            text = "".join(cells[j + 1: j + 5])
            m = re.search(r"(?:([\d,]+)兆)?\s*([\d,]+)?億", text)
            if m:
                mcap = 0.0
                if m.group(1):
                    mcap += float(m.group(1).replace(",", "")) * 10000
                if m.group(2):
                    mcap += float(m.group(2).replace(",", ""))
                if mcap:
                    out["mcap_k"] = round(mcap)
    return out


def parse_kabutan_finance(soup):
    """財務ページ → 通期業績（予想行つき）・ROE・自己資本比率・1株純資産"""
    out = {}
    fin_rows = []
    for tbl in soup.find_all("table"):
        cells = _cells(tbl)
        head = "".join(cells[:12])
        # 通期業績テーブル（決算期/売上高/…/発表日。修正履歴テーブルは除外）
        if not fin_rows and "決算期" in head and "売上高" in head and "発表日" in head and "修正日" not in head:
            for tr in tbl.find_all("tr"):
                tds = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                k = _find_date_cell(tds)
                if k is None:
                    continue
                vals = tds[k + 1: k + 7]
                if len(vals) < 6:
                    continue
                rev, op, ordi, net, eps, div = (_num(v) for v in vals)
                if rev is None and net is None and eps is None:
                    continue
                fin_rows.append({
                    "year": DATE_RE.search(tds[k]).group(0),
                    "fc": "予" in "".join(tds[:k + 1]),
                    "revenue": round(rev / 100) if rev is not None else None,  # 百万円→億円
                    "op": round(op / 100) if op is not None else None,
                    "net": round(net / 100) if net is not None else None,
                    "eps": eps, "div": div,
                })
        # ROEテーブル（決算期|売上高|営業益|利益率|ＲＯＥ|ＲＯＡ|…）
        if "ＲＯＥ" in cells:
            actual = []
            for tr in tbl.find_all("tr"):
                tds = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                k = _find_date_cell(tds)
                if k is None:
                    continue
                if "予" in "".join(tds[:k + 1]):
                    continue
                roe = _num(tds[k + 4]) if k + 4 < len(tds) else None
                if roe is not None:
                    actual.append(roe)
            if actual:
                out["roe"] = actual[-1]
        # 財務テーブル（決算期|１株純資産|自己資本比率|…）
        if "自己資本比率" in cells and "決算期" in cells:
            for tr in tbl.find_all("tr"):
                tds = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                k = _find_date_cell(tds)
                if k is None:
                    continue
                bps = _num(tds[k + 1]) if k + 1 < len(tds) else None
                eq = _num(tds[k + 2]) if k + 2 < len(tds) else None
                if bps is not None:
                    out["bps"] = bps
                if eq is not None:
                    out["equity"] = eq
    if fin_rows:
        out["fin_rows"] = fin_rows[-6:]  # 実績最大5期 + 会社予想1行
    return out


def fetch_kabutan(code):
    """かぶたん2ページ → (market用レコード, financials用レコード)"""
    ses = requests.Session()
    r1 = ses.get(f"https://kabutan.jp/stock/?code={code}", headers=UA, timeout=20)
    time.sleep(KABUTAN_WAIT)
    r2 = ses.get(f"https://kabutan.jp/stock/finance?code={code}", headers=UA, timeout=20)
    time.sleep(KABUTAN_WAIT)
    if r1.status_code != 200 and r2.status_code != 200:
        raise ValueError(f"http {r1.status_code}/{r2.status_code}")
    out = {}
    if r1.status_code == 200:
        out.update(parse_kabutan_main(BeautifulSoup(r1.text, "html.parser")))
    if r2.status_code == 200:
        out.update(parse_kabutan_finance(BeautifulSoup(r2.text, "html.parser")))

    # ビルド時に最新株価から比率を再計算できるよう「1株あたり値」に還元して保存
    price = out.get("k_price")
    fin = out.get("fin_rows") or []
    fc = next((r for r in reversed(fin) if r["fc"]), None)
    last_actual = next((r for r in reversed(fin) if not r["fc"]), None)
    rec = {}
    eps_src = fc if (fc and fc.get("eps") is not None) else last_actual
    if eps_src and eps_src.get("eps") is not None:
        rec["eps_f"] = eps_src["eps"]          # 予想EPS（無ければ直近実績）
        rec["eps_fc"] = bool(eps_src["fc"])
    elif price and out.get("k_per"):
        rec["eps_f"] = round(price / out["k_per"], 1)
        rec["eps_fc"] = True
    div_src = fc if (fc and fc.get("div") is not None) else last_actual
    if div_src and div_src.get("div") is not None:
        rec["dps"] = div_src["div"]            # 予想1株配（無ければ直近実績）
    elif price and out.get("k_yield") is not None:
        rec["dps"] = round(price * out["k_yield"] / 100, 1)
    # BPSはかぶたん表示のPBR（直近四半期ベース）から逆算する方を優先し、財務テーブル（年度末）はフォールバック
    if price and out.get("k_pbr"):
        rec["bps"] = round(price / out["k_pbr"], 1)
    elif out.get("bps") is not None:
        rec["bps"] = out["bps"]
    if out.get("shares"):
        rec["shares"] = out["shares"]
    elif price and out.get("mcap_k"):
        rec["shares"] = round(out["mcap_k"] * 1e8 / price)
    if out.get("roe") is not None:
        rec["roe"] = out["roe"]
    if out.get("equity") is not None:
        rec["equity"] = out["equity"]

    fin_out = None
    if fin:
        fin_out = {
            "years": [r["year"].replace(".", "/").replace("/0", "/") + ("(予)" if r["fc"] else "") for r in fin],
            "revenue": [r["revenue"] for r in fin],
            "op": [r["op"] for r in fin],
            "net": [r["net"] for r in fin],
            "eps": [r["eps"] for r in fin],
            "div": [r["div"] for r in fin],
        }
    if not rec and not fin_out:
        raise ValueError("parse failed")
    return rec, fin_out


def pick_rotation(master, store, date_key, limit):
    """更新が古い順に limit 件。同日付なら「前回失敗（データ無し）」→プライムの順"""
    def key(s):
        rec = store.get(s["code"], {})
        has_data = any(rec.get(k) is not None for k in ("eps_f", "bps", "shares", "roe"))
        return (rec.get(date_key, "0000-00-00"), 1 if has_data else 0,
                MARKET_PRIORITY.get(s["market"], 9), s["code"])
    return [s["code"] for s in sorted(master, key=key)[:limit]]


def run_kabutan_rotation(codes, market, financials, workers=3):
    ok = err = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_kabutan, c): c for c in codes}
        for fut in as_completed(futs):
            code = futs[fut]
            rec_m = market.setdefault(code, {})
            try:
                rec, fin_out = fut.result()
                rec_m.update(rec)
                rec_m["kabu_date"] = TODAY
                if fin_out:
                    financials[code] = fin_out
                    financials[code]["fin_date"] = TODAY
                ok += 1
            except Exception:
                rec_m["kabu_date"] = TODAY  # 失敗しても日付は進め、翌回は別銘柄に枠を回す
                err += 1
            if (ok + err) % 100 == 0:
                print(f"  kabutan {ok + err}/{len(codes)}")
    print(f"kabutan: {ok} OK / {err} NG")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kabutan", type=int, default=500, help="かぶたんで指標・財務を更新する銘柄数/回")
    ap.add_argument("--prices-only", action="store_true")
    ap.add_argument("--skip-prices", action="store_true")
    args = ap.parse_args()

    master = load_master()
    codes = [s["code"] for s in master]
    market = load_existing(DATA / "market.json")
    financials = load_existing(DATA / "financials.json")

    if not args.skip_prices:
        print(f"=== 株価一括取得（{len(codes)}銘柄） ===")
        n = fetch_prices(codes, market)
        print(f"prices: {n} OK")
        market["_updated"] = NOW
        save(DATA / "market.json", market)

    if not args.prices_only:
        print(f"=== かぶたん指標・財務ローテーション（{args.kabutan}銘柄） ===")
        rot = pick_rotation(master, market, "kabu_date", args.kabutan)
        run_kabutan_rotation(rot, market, financials)
        save(DATA / "market.json", market)
        save(DATA / "financials.json", financials)

    idx = load_existing(DATA / "indices.json")
    for name, sym in INDICES:
        try:
            h = yf.Ticker(sym).history(period="5d", interval="1d")["Close"].dropna()
            last, prev = float(h.iloc[-1]), float(h.iloc[-2])
            idx[name] = {"value": round(last, 2), "chg": round(last - prev, 2),
                         "pct": round((last - prev) / prev * 100, 2)}
        except Exception as e:
            print(f"[WARN] index {name}: {e}", file=sys.stderr)
    idx["_updated"] = NOW
    save(DATA / "indices.json", idx)
    print("done.")


if __name__ == "__main__":
    main()
