#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JPX公式「東証上場銘柄一覧」から銘柄マスタを生成する（月1回程度の実行で十分）

  https://www.jpx.co.jp/markets/statistics-equities/misc/01.html で公開される
  data_j.xls（コード・銘柄名・市場区分・33業種）を取得し、
  プライム／スタンダード／グロースの内国株式のみを data/master.json に書き出す。

使い方: python3 scripts/fetch_master.py
"""
import json
import sys
import unicodedata
import urllib.request
from pathlib import Path

import xlrd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

TARGET_MARKETS = {
    "プライム（内国株式）": "プライム",
    "スタンダード（内国株式）": "スタンダード",
    "グロース（内国株式）": "グロース",
}


def main():
    print(f"downloading {URL} ...")
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=60).read()

    book = xlrd.open_workbook(file_contents=raw)
    sheet = book.sheet_by_index(0)
    header = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]

    def col(name):
        for i, h in enumerate(header):
            if name in h:
                return i
        raise KeyError(f"column not found: {name} in {header}")

    c_code = col("コード")
    c_name = col("銘柄名")
    c_market = col("市場・商品区分")
    c_sector = col("33業種区分")

    stocks = []
    for r in range(1, sheet.nrows):
        market_raw = str(sheet.cell_value(r, c_market)).strip()
        if market_raw not in TARGET_MARKETS:
            continue  # ETF/REIT/PRO Market/外国株は除外
        code_v = sheet.cell_value(r, c_code)
        code = str(int(code_v)) if isinstance(code_v, float) else str(code_v).strip()
        sector = str(sheet.cell_value(r, c_sector)).strip()
        stocks.append({
            "code": code,
            # JPXの銘柄名は全角英数のため NFKC で半角に正規化する
            "name": unicodedata.normalize("NFKC", str(sheet.cell_value(r, c_name)).strip()),
            "market": TARGET_MARKETS[market_raw],
            "sector": sector if sector and sector != "-" else "その他",
        })

    if len(stocks) < 3000:
        print(f"[ERROR] 取得件数が少なすぎる: {len(stocks)}件（フォーマット変更の可能性）", file=sys.stderr)
        sys.exit(1)

    DATA.mkdir(exist_ok=True)
    out = DATA / "master.json"
    out.write_text(json.dumps(stocks, ensure_ascii=False, indent=0), encoding="utf-8")

    by_market = {}
    for s in stocks:
        by_market[s["market"]] = by_market.get(s["market"], 0) + 1
    print(f"master saved: {len(stocks)}銘柄 {by_market} → {out}")


if __name__ == "__main__":
    main()
