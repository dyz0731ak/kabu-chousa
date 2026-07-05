#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""株式調査兵団 データ取得スクリプト（LLM不使用・cron/GitHub Actionsで定期実行）

全上場銘柄（data/master.json ≈ 3,700銘柄）を対象に、規模に応じた3層で取得する:

  1. 株価・30日終値   : yf.download の一括バッチ（毎回・全銘柄・数分）
  2. 投資指標(.info)  : PER/PBR/ROE/利回り/時価総額 — 古い順に N 銘柄/回のローテーション
  3. 財務諸表         : 損益計算書/自己資本比率/年別配当 — 古い順に M 銘柄/回のローテーション

ローテーションにより数日〜1週間で全銘柄を一巡し、以降は鮮度順に更新し続ける。
失敗した銘柄・項目は既存JSONの値を温存する。

使い方:
  python3 scripts/fetch_data.py                # 株価全銘柄 + 指標600 + 財務250（日次想定）
  python3 scripts/fetch_data.py --fund 1500 --fin 400   # 初回など多めに
  python3 scripts/fetch_data.py --prices-only  # 株価だけ素早く
"""
import argparse
import datetime
import json
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

try:
    import yfinance as yf
except ImportError:
    print("[ERROR] yfinance が必要です: pip install yfinance", file=sys.stderr)
    sys.exit(1)

JST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(JST).isoformat(timespec="seconds")
TODAY = NOW[:10]

MARKET_PRIORITY = {"プライム": 0, "スタンダード": 1, "グロース": 2}

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
    """yf.download で全銘柄の株価と30日終値を一括取得"""
    CHUNK = 400
    ok = 0
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        tickers = [f"{c}.T" for c in chunk]
        try:
            df = yf.download(tickers, period="2mo", interval="1d",
                             group_by="ticker", threads=True, progress=False,
                             auto_adjust=True)
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


# ---------------------------------------------------------------- 2. 指標(.info)

def fetch_fund(code):
    info = yf.Ticker(f"{code}.T").info or {}
    roe = info.get("returnOnEquity")
    out = {
        "per": round(info["trailingPE"], 1) if info.get("trailingPE") else None,
        "pbr": round(info["priceToBook"], 2) if info.get("priceToBook") else None,
        "roe": round(roe * 100, 1) if roe is not None else None,
        "yield": round(info["dividendYield"], 2) if info.get("dividendYield") else None,
        "mcap": round(info["marketCap"] / 1e8) if info.get("marketCap") else None,
        "eps": round(info["trailingEps"], 1) if info.get("trailingEps") else None,
    }
    if not any(v is not None for v in out.values()):
        raise ValueError("no data")
    return out


# ---------------------------------------------------------------- 3. 財務諸表

def fetch_fin(code):
    t = yf.Ticker(f"{code}.T")
    out = {}
    try:
        inc = t.income_stmt
        cols = sorted(inc.columns)[-5:]
        if len(cols) == 0:
            raise ValueError("empty income_stmt")

        def row(name, alt=None):
            for key in filter(None, [name, alt]):
                if key in inc.index:
                    return [round(float(inc.at[key, c]) / 1e8) if inc.at[key, c] == inc.at[key, c] else None
                            for c in cols]
            return None

        def row_raw(name):
            if name in inc.index:
                return [round(float(inc.at[name, c]), 1) if inc.at[name, c] == inc.at[name, c] else None
                        for c in cols]
            return None

        fin = {
            "years": [f"{c.year}/{c.month}" for c in cols],
            "revenue": row("Total Revenue"),
            "op": row("Operating Income", "Pretax Income"),
            "net": row("Net Income"),
            "eps": row_raw("Basic EPS") or row_raw("Diluted EPS"),
        }
        if fin["revenue"] and fin["net"]:
            out.update(fin)
    except Exception:
        pass
    try:
        bs = t.balance_sheet
        col = sorted(bs.columns)[-1]
        eq = float(bs.at["Stockholders Equity", col])
        ta = float(bs.at["Total Assets", col])
        out["equity"] = round(eq / ta * 100, 1)
    except Exception:
        pass
    try:
        div = t.dividends
        if len(div):
            by_year = div.groupby(div.index.year).sum()
            out["div_by_year"] = {str(y): round(float(v), 1) for y, v in by_year.tail(6).items()}
    except Exception:
        pass
    if not out:
        raise ValueError("no data")
    return out


# ---------------------------------------------------------------- rotation

def pick_rotation(master, store, date_key, limit):
    """更新が古い順に limit 件選ぶ。同日付なら「前回失敗（データ無し）」→プライムの順で優先"""
    def key(s):
        rec = store.get(s["code"], {})
        has_data = any(v is not None for k, v in rec.items()
                       if k not in (date_key, "date", "price", "chg", "hist"))
        return (rec.get(date_key, "0000-00-00"), 1 if has_data else 0,
                MARKET_PRIORITY.get(s["market"], 9), s["code"])
    return [s["code"] for s in sorted(master, key=key)[:limit]]


def run_rotation(label, codes, fn, store, date_key, workers=4):
    ok = err = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, c): c for c in codes}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                data = fut.result()
                rec = store.setdefault(code, {})
                rec.update({k: v for k, v in data.items() if v is not None})
                rec[date_key] = TODAY
                ok += 1
            except Exception:
                # 取得失敗でも日付は進めて、翌回は別の銘柄に枠を回す
                store.setdefault(code, {})[date_key] = TODAY
                err += 1
            if (ok + err) % 200 == 0:
                print(f"  {label} {ok + err}/{len(codes)}")
    print(f"{label}: {ok} OK / {err} NG")
    return ok


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fund", type=int, default=600, help="指標(.info)を更新する銘柄数/回")
    ap.add_argument("--fin", type=int, default=250, help="財務諸表を更新する銘柄数/回")
    ap.add_argument("--prices-only", action="store_true")
    ap.add_argument("--skip-prices", action="store_true", help="株価一括をスキップして指標・財務だけ")
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
        print(f"=== 投資指標ローテーション（{args.fund}銘柄） ===")
        rot = pick_rotation(master, market, "fund_date", args.fund)
        run_rotation("fund", rot, fetch_fund, market, "fund_date")
        save(DATA / "market.json", market)

        print(f"=== 財務諸表ローテーション（{args.fin}銘柄） ===")
        rot = pick_rotation(master, financials, "fin_date", args.fin)
        # 財務諸表エンドポイントはスロットリングされやすいため並列を絞る
        run_rotation("fin", rot, fetch_fin, financials, "fin_date", workers=2)
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
