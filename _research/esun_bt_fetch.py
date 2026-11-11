# -*- coding: utf-8 -*-
"""
esun_bt_fetch.py — 回測用：補 150 檔三大法人、集保大戶、原始（未還原）收盤價的歷史（FinMind）
  · 存在 _research\\bt_data\\，不碰每日流程的 esun_inst、esun_tdcc
  · 已抓過的會略過；限流或斷網就停下，過一陣子重跑會接續
  · 集保（TaiwanStockHoldingSharesPer）如果你的 FinMind 等級沒有權限，會自動略過，回測就只用三項評分
  · 原始收盤價：K 線是還原價，用來算「EPS 增幅 ÷ 股價」會偷看到未來的配息，回測改用這份未還原的價格
  · 400 張以上 = 持股分級下限 >= 400,001 股的各級加總（跟 esun_fetch_tdcc 的 12–15 級相同）
用法（在 SMC- 資料夾）：python _research\\esun_bt_fetch.py（加 --tdcc 才抓集保）
"""
import os, sys, re, time, random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.exists(os.path.join(os.path.dirname(HERE), "esun_strategy.py")) else HERE
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import pandas as pd, requests
from dplan_validator import UNIVERSE
import esun_fetch_inst as INST

URL = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
START = "2022-06-01"                # 2026-10-20 改：回測要從 2023-01 起跑（原本 2024-03-01）
OUT_INST = os.path.join(HERE, "bt_data", "inst")
OUT_TDCC = os.path.join(HERE, "bt_data", "tdcc")
OUT_RAW = os.path.join(HERE, "bt_data", "rawpx")


def get(dataset, sid):
    p = {"dataset": dataset, "data_id": sid, "start_date": START}
    if TOKEN:
        p["token"] = TOKEN
    try:
        j = requests.get(URL, params=p, timeout=60).json()
    except Exception:
        return "NET_ERROR", None
    if j.get("data"):
        return "OK", j["data"]
    msg = str(j.get("msg", ""))
    return ("RATE_LIMIT" if "limit" in msg.lower() else "EMPTY"), msg


def done_file(out_dir):
    return os.path.join(out_dir, f"_fetched_from_{START}.txt")


def covered(path):
    """已有檔案，而且（最早的資料在 START 之後 30 天內，或這次 START 已經抓過，例如 2022 年後才上市）→ 不用重抓"""
    if not os.path.exists(path):
        return False
    sid = os.path.basename(path)[:-8]
    f = done_file(os.path.dirname(path))
    if os.path.exists(f) and sid in open(f, encoding="utf-8").read().split():
        return True
    try:
        return pd.read_parquet(path).index.min() <= pd.Timestamp(START) + pd.Timedelta(days=30)
    except Exception:
        return False


def lower_bound(level):
    m = re.search(r"\d[\d,]*", str(level))
    return int(m.group().replace(",", "")) if m else -1


def parse_tdcc(records):
    d = pd.DataFrame(records)
    col = "percent" if "percent" in d.columns else d.columns[-1]
    d[col] = pd.to_numeric(d[col], errors="coerce")
    lvl = d["HoldingSharesLevel"].astype(str)
    big = d[(lvl.map(lower_bound) >= 400001) & ~lvl.str.contains("total|差異", case=False)]
    s = big.groupby("date")[col].sum()
    s.index = pd.to_datetime(s.index)
    return s.sort_index().to_frame("big")


def parse_raw(records):
    d = pd.DataFrame(records)
    d = d[pd.to_numeric(d["close"], errors="coerce") > 0]
    s = pd.Series(pd.to_numeric(d["close"]).values, index=pd.to_datetime(d["date"]))
    return s[~s.index.duplicated(keep="last")].sort_index().to_frame("close")


def run(name, dataset, out_dir, parser, stop_if_no_access=False):
    os.makedirs(out_dir, exist_ok=True)
    todo = [s for s in sorted(UNIVERSE) if not covered(os.path.join(out_dir, f"{s}.parquet"))]
    print(f"\n{name}：150 檔｜已有 {len(UNIVERSE) - len(todo)}｜本次 {len(todo)}")
    ok, empty = 0, 0
    for i, sid in enumerate(todo, 1):
        st, data = get(dataset, sid)
        if st in ("RATE_LIMIT", "NET_ERROR"):
            print(f"  ⚠ {'限流' if st == 'RATE_LIMIT' else '斷網'}（{sid}），過一陣子再跑會接續")
            return False
        if st == "OK":
            df = parser(data)
            if df is not None and len(df):
                df.to_parquet(os.path.join(out_dir, f"{sid}.parquet"))
                open(done_file(out_dir), "a", encoding="utf-8").write(sid + "\n")
                ok += 1
                if i <= 2 or i % 25 == 0:
                    print(f"  [{i}/{len(todo)}] ✓ {sid} {df.index[0]:%Y-%m-%d} 到 {df.index[-1]:%Y-%m-%d}，{len(df)} 筆")
        else:
            empty += 1
            print(f"  [{i}/{len(todo)}] ✗ {sid} 沒有資料：{str(data)[:80]}")
            if stop_if_no_access and ok == 0 and empty >= 3:
                print(f"  → 前 3 檔都抓不到，應該是 FinMind 等級沒有這個資料集的權限，{name}略過")
                return False
        time.sleep(random.uniform(0.3, 0.6))
    print(f"  完成：成功 {ok}、沒有資料 {empty}")
    return True


def main():
    if not TOKEN:
        print("! 沒有設定 FINMIND_TOKEN，額度較低，可能要分幾次跑完")
    run("三大法人", "TaiwanStockInstitutionalInvestorsBuySell", OUT_INST, INST.parse)
    if "--tdcc" in sys.argv:                       # 正式評分已不用大戶（10/13 拿掉），預設不抓，省額度
        run("集保大戶", "TaiwanStockHoldingSharesPer", OUT_TDCC, parse_tdcc, stop_if_no_access=True)
    run("原始收盤價", "TaiwanStockPrice", OUT_RAW, parse_raw)
    p = os.path.join(OUT_TDCC, "2330.parquet")
    if os.path.exists(p):
        s = pd.read_parquet(p)["big"].tail(3)
        print("\n資料檢查：2330 台積電 400 張以上大戶比例最近 3 週："
              + "、".join(f"{i:%m/%d} {v:.2f}%" for i, v in s.items()))
        print("  可以跟 esun_fetch_tdcc.py 印出來的最新一週對一下，數字應該一樣")


if __name__ == "__main__":
    main()