# 玉山 ETF 選股策略 (esun-etf-strategy)

台股選股與每日自動化流程：從主辦方 150 檔選股池中，依**營收驚喜、EPS 增幅、三率（毛利率／營業利益率／淨利率）**綜合排名，產生目標權重、交易單與每日報告，並自動記帳、對帳。

## 策略重點（詳見 `esun_strategy.py` 開頭說明）

- **選股池**：150 檔，排除航運業；20 日均量 ≥ 500 張
- **營收驚喜** = 最新 YoY − 前 3 個月 YoY 平均
- **EPS 增幅** = (最新一季 EPS − 去年同季 EPS) ÷ 股價
- **三率** = 三項利潤率跟上一季比「沒有下跌」的項數
- **綜合分數** = 三項在選股池內的百分位排名平均
- **過濾**：均線全部下彎、三大法人連 3 日賣超、爆量下跌後賣壓未消化 → 不新買進
- **權重**：台積電固定約 10%、核心 8 檔各 9.5%、小部位 11 檔各 1%；行情差時進入防守模式

## 每日流程（`esun_daily.py`）

1. 抓行情快照 → 2. 記帳（`esun_ledger.py`）→ 3. 與官方資料包對帳 → 4. 補 K 線
5. 更新營收、EPS → 6. 產生下一個交易日的報告與 D-Plan（`esun_report.py`、`dplan_validator.py`）

```bash
pip install -r requirements.txt
set FINMIND_TOKEN=你的FinMind token
python esun_daily.py           # 正常執行
python esun_daily.py --force   # 強制重做下一日報告
```

Windows 排程可用 `esun_run.bat`。

## 目錄

| 路徑 | 內容 |
|---|---|
| `esun_*.py` | 主流程：策略、資料、記帳、報告、下單、ETF 持股分析 |
| `esun_fetch_*.py`, `fetch_*.py` | 營收、EPS、法人、集保、上櫃、產業等資料抓取（FinMind） |
| `factor_*.py`, `esun_factor150.py` | 因子研究（動能、強度、價值） |
| `dplan_validator.py`, `D-Plan_schema_v4_3.json` | D-Plan 格式驗證 |
| `_research/` | 回測、消融實驗、彩排與影子組合 |
| `research/vcp_sector/` | VCP + 族群連動研究模組（含測試） |
| `post_contest/` | 賽後點對點（point-in-time）回測 |

## 注意

- 資料檔（股價、營收快取、官方資料包、帳本、報告）**不在 repo 中**，需要自行用 fetch 腳本下載。
- 所有 API 金鑰都從環境變數讀取（`FINMIND_TOKEN`、`TELEGRAM_BOT_TOKEN` 等），請勿寫進程式碼。
- 本專案為個人研究，不構成投資建議。
