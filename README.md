# 株式調査兵団（kabu-chousa.com）

東証プライム・スタンダード・グロース全上場銘柄（約3,700）の企業分析 × 銘柄ランキング静的サイト。
Buffett Code的な企業分析 ＋ 株ドラゴン的なランキング ＋ TradingView風ダークUI（調査兵団テイスト）。

## 構成

```
data.py                手書きの企業概要（DESC_OVERRIDES）。無い銘柄は自動テンプレ文
scripts/fetch_master.py JPX上場銘柄一覧 → data/master.json（月1回）【LLM不使用】
scripts/fetch_data.py   株価=yfinance一括 / 指標・財務=かぶたん → data/*.json（毎営業日）【LLM不使用】
build.py               静的サイト生成 → dist/（約3,800ページ）
static/style.css       テーマCSS
.github/workflows/deploy.yml  平日16:30 JST 自動更新→FTPデプロイ
```

## データ取得の2層構造

| 層 | ソース | 内容 | 頻度 |
|---|---|---|---|
| 株価・30日終値 | yfinance（無調整終値） | 一括バッチ・全銘柄・数分 | 毎回 |
| 指標・財務 | かぶたん個別2ページ | 予想EPS/1株配・BPS・株数・ROE・自己資本比率・通期業績（予想行つき） | 古い順500銘柄/回（約1週間で一巡） |

- PER/PBR/利回り/時価総額は**ビルド時に「1株あたり値 × 最新株価」で毎日再計算**（四季報・証券会社と同じ予想ベース）
- Yahoo(.info)の日本株ファンダはEPS・株数が古く不正確なため使わない（2026-07に確認）
- 失敗した銘柄は既存値を温存し、翌回のローテーションで優先的に再取得する

## ビルド

```bash
python3 scripts/fetch_master.py            # 初回・月1回
python3 scripts/fetch_data.py              # 日次（--kabutan N で件数調整）
python3 build.py                           # dist/ に生成
BASE_URL=https://kabu.stock-overflow24.com python3 build.py   # サブドメイン公開時
```

## デプロイ設定（GitHub側）

- Secrets: `FTP_SERVER` / `FTP_USERNAME` / `FTP_PASSWORD`（ConoHa WING。既存の stock-dashboard と同じ要領）
- Variables（任意）: `BASE_URL`（独自ドメイン以外で公開する場合）、`FTP_SERVER_DIR`（例: `public_html/kabu-chousa.com/`）

## 公開前チェックリスト

- [ ] ConoHa WINGでドメイン設定（kabu-chousa.com）＋無料SSL
- [ ] GitHub Secrets/Variables 設定
- [ ] Search Console 登録・sitemap.xml 送信
- [ ] GA4 / AdSense（新ドメインは新規審査が必要）
- [ ] 主要銘柄の企業概要を data.py の DESC_OVERRIDES に追記していく（SEOの独自性向上）
