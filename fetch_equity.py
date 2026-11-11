# -*- coding: utf-8 -*-
"""
fetch_equity.py — 補股東權益 + 股本歷史（價值因子 PB 用）【獨立腳本】

升級：同一張資產負債表，同時取 Equity(股東權益) + CapitalStock(股本)，
算每股淨值(BVPS)。之後 PB = 股價 / BVPS。
存到 ./revenue_cache/equity_history/{ticker}.parquet
  欄位：equity, capital_stock, shares, bvps, available_date
用法：放 SMC- 根目錄，python fetch_equity.py（斷點續傳，限流會提示，可分次跑）

★ 因為欄位變多，這版用新的 done 檔（equity_done_v2.txt），會重抓一輪。
  舊的 equity_history 檔會被逐檔覆蓋成新版（多了 capital_stock/bvps）。
"""
import os, glob, time, random
import pandas as pd
import requests

CACHE_DIR = "./revenue_cache"
EQUITY_DIR = os.path.join(CACHE_DIR, "equity_history")
LOCAL_DATA = "./local_data"
DONE_FILE = "equity_done_v2.txt"     # 新版用新的記錄檔，強制重抓（因為要多抓股本）
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
START_YEAR = 2011
PAR_VALUE = 10.0    # 台股面額 10 元


def _q_available_date(period_ts):
    """季報公告延遲：Q1→5/15、Q2→8/14、Q3→11/14、Q4→隔年3/31"""
    y, m = period_ts.year, period_ts.month
    if m == 3:   return pd.Timestamp(y, 5, 15)
    elif m == 6: return pd.Timestamp(y, 8, 14)
    elif m == 9: return pd.Timestamp(y, 11, 14)
    elif m == 12:return pd.Timestamp(y + 1, 3, 31)
    else:        return period_ts + pd.Timedelta(days=75)


def _finmind_get(dataset, data_id, start_date):
    params = {"dataset": dataset, "data_id": data_id, "start_date": start_date}
    if FINMIND_TOKEN:
        params["token"] = FINMIND_TOKEN
    for attempt in range(3):
        try:
            resp = requests.get(FINMIND_URL, params=params, timeout=10)
            data = resp.json()
            if data.get("data"):
                return pd.DataFrame(data["data"])
            msg = str(data.get("msg", ""))
            if "limit" in msg.lower():
                return "RATE_LIMIT"
            return pd.DataFrame()
        except requests.exceptions.ConnectionError:
            return "NET_ERROR"      # 網路斷（getaddrinfo failed 那種）
        except Exception:
            time.sleep(1)
    return pd.DataFrame()


def fetch_one(ticker):
    """抓單一檔 equity + capital_stock。回傳 (df或None或狀態字串, 訊息)。"""
    df = _finmind_get("TaiwanStockBalanceSheet", ticker, f"{START_YEAR}-01-01")
    if isinstance(df, str):
        return df, ("限流" if df == "RATE_LIMIT" else "網路斷")
    if df.empty:
        return None, "無資料"

    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"]  = pd.to_datetime(df["date"])

    def pick(typ):
        return df[df["type"] == typ].groupby("date")["value"].first()

    eq  = pick("Equity")          # 股東權益總計
    cap = pick("CapitalStock")    # 普通股股本
    if eq.empty or cap.empty:
        return None, "缺Equity或CapitalStock欄"

    m = pd.DataFrame({"equity": eq, "capital_stock": cap}).dropna()
    m = m[(m["equity"] > 0) & (m["capital_stock"] > 0)]
    if m.empty:
        return None, "權益/股本皆非正"

    m["shares"] = m["capital_stock"] / PAR_VALUE   # 股數 = 股本 / 面額
    m["bvps"]   = m["equity"] / m["shares"]        # 每股淨值
    m["available_date"] = m.index.map(_q_available_date)
    return m[["equity", "capital_stock", "shares", "bvps", "available_date"]], f"{len(m)}季"


def load_done():
    if os.path.exists(DONE_FILE):
        with open(DONE_FILE, encoding="utf-8") as f:
            return set(l.strip() for l in f if l.strip())
    return set()

def mark_done(t):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(t + "\n")


def main():
    os.makedirs(EQUITY_DIR, exist_ok=True)
    files = glob.glob(os.path.join(LOCAL_DATA, "*.parquet"))
    tickers = sorted(set(os.path.basename(f).replace(".TW.parquet","").replace(".parquet","") for f in files))
    done = load_done()
    todo = [t for t in tickers if t not in done]

    print(f"補 equity+股本：全 {len(tickers)} 檔｜已完成 {len(done)}｜本次 {len(todo)}")
    print("（限流/斷網會停下提示，過陣子再跑會接續）\n")

    ok = fail = 0
    for i, t in enumerate(todo, 1):
        result, status = fetch_one(t)
        if isinstance(result, str):   # RATE_LIMIT 或 NET_ERROR
            print(f"\n⚠ {status}（第 {i} 檔 {t}）。已完成 {len(done)+ok+fail} 檔。")
            print(f"  過一陣子再跑一次 python fetch_equity.py，會接續。")
            return
        if result is not None:
            result.to_parquet(os.path.join(EQUITY_DIR, f"{t}.parquet"))
            mark_done(t); ok += 1
            print(f"[{i}/{len(todo)}] ✓ {t}  {status}")
        else:
            mark_done(t); fail += 1
            print(f"[{i}/{len(todo)}] ✗ {t}  {status}")
        time.sleep(random.uniform(0.3, 0.6))

    print(f"\n完成：成功 {ok}｜無資料 {fail}｜累計 {len(done)+ok+fail}/{len(tickers)}")
    if len(done)+ok+fail >= len(tickers):
        print("全部補完！下一步：用 K線股價 / bvps 算 PB，建價值因子。")


if __name__ == "__main__":
    main()