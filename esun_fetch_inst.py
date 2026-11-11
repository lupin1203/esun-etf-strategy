# -*- coding: utf-8 -*-
"""
esun_fetch_inst.py — 三大法人買賣超（FinMind TaiwanStockInstitutionalInvestorsBuySell，免費）
  · 只抓選股池（150 檔排除航運，以 esun_strategy.pool_sids() 為準）＋台積電，每檔抓最近 20 天，和舊資料合併
  · 三大法人合計 = 外資、外資自營商、投信、自營商自行買賣、自營商避險 五類的（買 − 賣）加總，單位：股
  · 存到 esun_inst/{代號}.parquet（index=日期，欄位 net）
用法：python esun_fetch_inst.py（每日流程每天自動呼叫一次）
"""
import os, json, time, random
from datetime import datetime, timedelta
import pandas as pd, requests

URL = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
OUT_DIR = "./esun_inst"
def pool():
    """跟選股規則同一個來源，選股池改了這裡自動跟著改"""
    import esun_strategy as S
    return sorted(set(S.pool_sids()) | {"2330"})


def parse(records):
    d = pd.DataFrame(records)
    if d.empty:
        return None
    d["net"] = pd.to_numeric(d["buy"], errors="coerce") - pd.to_numeric(d["sell"], errors="coerce")
    s = d.groupby("date")["net"].sum()
    s.index = pd.to_datetime(s.index)
    return s.sort_index().to_frame("net")


def fetch_one(sid, start):
    p = {"dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": sid, "start_date": start}
    if TOKEN:
        p["token"] = TOKEN
    try:
        j = requests.get(URL, params=p, timeout=20).json()
    except Exception:
        return "NET_ERROR", None
    if j.get("data"):
        return "OK", parse(j["data"])
    msg = str(j.get("msg", ""))
    if "limit" in msg.lower():
        return "RATE_LIMIT", None
    if msg and msg.lower() != "success":                  # token 無效等錯誤：不要當成「沒資料」默默略過
        return "ERROR:" + msg[:120], None
    return "EMPTY", None


def main():
    """回傳 True 代表選股池全部更新完成"""
    os.makedirs(OUT_DIR, exist_ok=True)
    start = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    todo, ok = pool(), 0
    for sid in todo:
        st, df = fetch_one(sid, start)
        if st in ("RATE_LIMIT", "NET_ERROR"):
            print(f"  ⚠ 三大法人：{'限流' if st == 'RATE_LIMIT' else '斷網'}（{sid}），下次執行會再試"); return False
        if st.startswith("ERROR:"):
            print(f"  ⚠ 三大法人：FinMind 回應錯誤（{sid}）：{st[6:]}"
                  + ("（有設定 FINMIND_TOKEN，可能是 token 無效或過期）" if TOKEN else "")); return False
        if st == "OK":
            path = os.path.join(OUT_DIR, f"{sid}.parquet")
            if os.path.exists(path):
                old = pd.read_parquet(path)
                df = pd.concat([old[~old.index.isin(df.index)], df]).sort_index()
            df.to_parquet(path); ok += 1
        time.sleep(random.uniform(0.2, 0.4))
    print(f"三大法人：更新 {ok} / {len(todo)} 檔")
    return True


def load(sid):
    p = os.path.join(OUT_DIR, f"{sid}.parquet")
    return pd.read_parquet(p)["net"] if os.path.exists(p) else pd.Series(dtype=float)


if __name__ == "__main__":
    main()