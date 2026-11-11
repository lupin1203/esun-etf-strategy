# -*- coding: utf-8 -*-
"""
fetch_per.py — 抓官方 PBR / PER / 殖利率（FinMind TaiwanStockPER）
存到 ./revenue_cache/per_history/{代號}.parquet（index=日期，欄位 PBR, PER, dividend_yield）

1. 先驗證 2330（上市）、6488（上櫃）都抓得到，才繼續抓全市場
2. 斷點續傳：限流或斷網會停下，過一陣子再跑會接續
3. 出錯時不會把 token 印出來
用法：放 SMC- 根目錄，python fetch_per.py
"""
import os, glob, time, random
import pandas as pd, requests

LOCAL_DATA = "./local_data"
OUT_DIR = "./revenue_cache/per_history"
DONE_FILE = "per_done.txt"
URL = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
START = "2011-01-01"


def parse(records):
    df = pd.DataFrame(records)
    cols = {c.lower(): c for c in df.columns}
    if "pbr" not in cols or "date" not in cols:
        return None, list(df.columns)
    out = pd.DataFrame(index=pd.to_datetime(df[cols["date"]]))
    for key, name in [("pbr", "PBR"), ("per", "PER"), ("dividend_yield", "dividend_yield")]:
        out[name] = pd.to_numeric(df[cols[key]], errors="coerce").values if key in cols else float("nan")
    out = out.sort_index()
    return out[~out.index.duplicated(keep="last")], None


def fetch(sid):
    params = {"dataset": "TaiwanStockPER", "data_id": sid, "start_date": START}
    if TOKEN:
        params["token"] = TOKEN
    try:
        data = requests.get(URL, params=params, timeout=20).json()
    except Exception:
        return "NET_ERROR", None
    if data.get("data"):
        out, bad_cols = parse(data["data"])
        return ("OK", out) if out is not None else ("NO_PBR_COL", bad_cols)
    if "limit" in str(data.get("msg", "")).lower():
        return "RATE_LIMIT", None
    return "EMPTY", None


def load_done():
    if os.path.exists(DONE_FILE):
        with open(DONE_FILE, encoding="utf-8") as f:
            return set(l.strip() for l in f if l.strip())
    return set()


def mark_done(sid):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(sid + "\n")


def verify(done):
    print("=== 先驗證上市、上櫃都有資料 ===")
    for sid, kind in [("2330", "上市"), ("6488", "上櫃")]:
        if sid in done:
            print(f"  {sid}（{kind}）已驗證過")
            continue
        status, out = fetch(sid)
        if status != "OK":
            print(f"  {sid}（{kind}）：{status} {out if out else ''}")
            if status in ("RATE_LIMIT", "NET_ERROR"):
                print("  → 限流或斷網，過一陣子再跑。")
            else:
                print("  → 這個資料集可能不涵蓋" + kind + "，請把這段貼給我，要改用備案。")
            return False
        out.to_parquet(os.path.join(OUT_DIR, f"{sid}.parquet"))
        mark_done(sid)
        print(f"  {sid}（{kind}）：{out.index.min().date()} ~ {out.index.max().date()}，"
              f"{len(out)} 筆，最新 PBR = {out['PBR'].iloc[-1]}")
        time.sleep(0.5)
    return True


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    done = load_done()
    if not verify(done):
        return
    done = load_done()
    files = glob.glob(os.path.join(LOCAL_DATA, "*.parquet"))
    sids = sorted(set(os.path.basename(f).replace(".TW.parquet", "").replace(".parquet", "") for f in files))
    todo = [s for s in sids if s not in done]
    print(f"\n全市場 {len(sids)} 檔｜已完成 {len(done)}｜本次 {len(todo)}\n")

    ok = empty = 0
    for i, sid in enumerate(todo, 1):
        status, out = fetch(sid)
        if status in ("RATE_LIMIT", "NET_ERROR"):
            print(f"\n⚠ {'限流' if status == 'RATE_LIMIT' else '斷網'}（第 {i} 檔 {sid}）。"
                  f"過一陣子再跑 python fetch_per.py 會接續。")
            break
        if status == "OK":
            out.to_parquet(os.path.join(OUT_DIR, f"{sid}.parquet"))
            ok += 1
            print(f"[{i}/{len(todo)}] ✓ {sid}  {out.index.min().date()}起 {len(out)}筆")
        else:
            empty += 1
            print(f"[{i}/{len(todo)}] ✗ {sid}  {status}")
        mark_done(sid)
        time.sleep(random.uniform(0.3, 0.6))
    print(f"\n本次：成功 {ok}｜無資料 {empty}")


if __name__ == "__main__":
    main()