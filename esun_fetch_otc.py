# -*- coding: utf-8 -*-
"""
esun_fetch_otc.py — 補抓比賽 150 檔中缺少的上櫃股資料（不碰 SMC 的 local_data）
  1. K 線：yfinance「代號.TWO」，2011 起、還原價（跟 local_data 一致），存到 local_data_otc/
  2. 官方本益比／淨值比／殖利率：FinMind TaiwanStockPER，存到 revenue_cache/per_history/
  · 只抓「local_data 裡沒有」或「per_history 裡沒有」的 150 檔成員
  · 限流或斷網會停下，再跑會自動接續；錯誤訊息不印 token
用法：python esun_fetch_otc.py
"""
import os, time, random
import pandas as pd, requests, yfinance as yf
from dplan_validator import UNIVERSE
from fetch_per import parse as parse_per

LOCAL_DATA, OTC_DIR, PER_DIR = "./local_data", "./local_data_otc", "./revenue_cache/per_history"
START = "2011-01-01"
URL = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")


def need_kline(sid):
    return not os.path.exists(os.path.join(LOCAL_DATA, f"{sid}.TW.parquet")) and \
           not os.path.exists(os.path.join(OTC_DIR, f"{sid}.TWO.parquet"))


def fetch_kline(sid):
    df = yf.Ticker(f"{sid}.TWO").history(start=START, auto_adjust=True)
    if df is None or df.empty:
        return None
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df[["Open", "High", "Low", "Close", "Volume"]].sort_index()


def fetch_per_one(sid):
    params = {"dataset": "TaiwanStockPER", "data_id": sid, "start_date": START}
    if TOKEN:
        params["token"] = TOKEN
    try:
        data = requests.get(URL, params=params, timeout=20).json()
    except Exception:
        return "NET_ERROR", None
    if data.get("data"):
        out, _ = parse_per(data["data"])
        return ("OK", out) if out is not None else ("NO_PBR_COL", None)
    if "limit" in str(data.get("msg", "")).lower():
        return "RATE_LIMIT", None
    return "EMPTY", None


def main():
    os.makedirs(OTC_DIR, exist_ok=True)
    os.makedirs(PER_DIR, exist_ok=True)
    kl = sorted(s for s in UNIVERSE if need_kline(s))
    print(f"K 線：要補 {len(kl)} 檔（存到 {OTC_DIR}）")
    for i, sid in enumerate(kl, 1):
        try:
            df = fetch_kline(sid)
        except Exception as e:
            print(f"  [{i}/{len(kl)}] ✗ {sid} {type(e).__name__}"); continue
        if df is None:
            print(f"  [{i}/{len(kl)}] ✗ {sid} 抓不到"); continue
        df.to_parquet(os.path.join(OTC_DIR, f"{sid}.TWO.parquet"))
        print(f"  [{i}/{len(kl)}] ✓ {sid} {df.index[0]:%Y-%m-%d} 起 {len(df)} 筆")
        time.sleep(random.uniform(0.3, 0.8))

    pe = sorted(s for s in UNIVERSE if not os.path.exists(os.path.join(PER_DIR, f"{s}.parquet")))
    print(f"\n本益比／淨值比：要補 {len(pe)} 檔")
    for i, sid in enumerate(pe, 1):
        status, out = fetch_per_one(sid)
        if status in ("RATE_LIMIT", "NET_ERROR"):
            print(f"  ⚠ {'限流' if status == 'RATE_LIMIT' else '斷網'}（{sid}），過一陣子再跑會接續"); break
        if status == "OK":
            out.to_parquet(os.path.join(PER_DIR, f"{sid}.parquet"))
            print(f"  [{i}/{len(pe)}] ✓ {sid} {out.index[0]:%Y-%m-%d} 起")
        else:
            print(f"  [{i}/{len(pe)}] ✗ {sid} {status}")
        time.sleep(random.uniform(0.3, 0.6))

    miss_k = sorted(s for s in UNIVERSE if need_kline(s))
    miss_p = sorted(s for s in UNIVERSE if not os.path.exists(os.path.join(PER_DIR, f"{s}.parquet")))
    print(f"\n完成後仍缺：K 線 {len(miss_k)} 檔 {miss_k}｜本益比 {len(miss_p)} 檔 {miss_p}")


if __name__ == "__main__":
    main()