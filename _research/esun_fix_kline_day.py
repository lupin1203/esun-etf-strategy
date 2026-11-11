# -*- coding: utf-8 -*-
r"""
esun_fix_kline_day.py — 補抓某一天缺的 K 線（預設 2026-10-01）

  · 檢查 150 檔的 local_data（上市 .TW）、local_data_otc（上櫃 .TWO），列出缺這一天的股票
  · 從 FinMind TaiwanStockPrice 抓「前一個交易日～後一個交易日」的原始價
  · local_data 是還原價：用「本地收盤 ÷ FinMind 原始收盤」在前後鄰近交易日算出還原係數，
    把補上的那天開高低收乘上係數，避免跟前後K線接不起來；成交量（股）照原樣
  · 改檔前備份到 _research\kfix_backup\日期\
  · 補完後刪掉回測評分快取裡 10 月以後的檔（bt_cache\*\raw_2026-10-*.pkl），讓回測重算這些天
用法（在 SMC- 資料夾）：python _research\esun_fix_kline_day.py
       其他日期：python _research\esun_fix_kline_day.py 2026-10-01
       只檢查不改檔：python _research\esun_fix_kline_day.py 2026-10-01 --check
"""
import os, sys, glob, shutil, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.exists(os.path.join(os.path.dirname(HERE), "esun_strategy.py")) else HERE
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import pandas as pd, requests
import esun_strategy as S
from dplan_validator import UNIVERSE

URL = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
args = [a for a in sys.argv[1:] if not a.startswith("--")]
DAY = pd.Timestamp(args[0] if args else "2026-10-01")
CHECK = "--check" in sys.argv
BACKUP = os.path.join(HERE, "kfix_backup", f"{DAY:%Y-%m-%d}")


def path_of(sid):
    for p in (os.path.join(S.LOCAL_DATA, f"{sid}.TW.parquet"), os.path.join(S.OTC_DIR, f"{sid}.TWO.parquet")):
        if os.path.exists(p):
            return p
    return None


def load(p):
    df = pd.read_parquet(p)
    idx = pd.to_datetime(df.index)
    tz = idx.tz
    key = idx.tz_localize(None) if tz is not None else idx
    return df, key, tz


def fetch(sid, start, end):
    prm = {"dataset": "TaiwanStockPrice", "data_id": sid, "start_date": f"{start:%Y-%m-%d}", "end_date": f"{end:%Y-%m-%d}"}
    if TOKEN:
        prm["token"] = TOKEN
    for k in range(3):
        try:
            j = requests.get(URL, params=prm, timeout=20).json()
        except Exception:
            time.sleep(2); continue
        if "limit" in str(j.get("msg", "")).lower():
            print("  ! FinMind 限流，等 60 秒"); time.sleep(60); continue
        d = pd.DataFrame(j.get("data") or [])
        if d.empty:
            return None
        d["date"] = pd.to_datetime(d["date"])
        return d.set_index("date").sort_index()
    return None


def main():
    missing, no_file, has = [], [], 0
    for sid in sorted(UNIVERSE | {"0050"}):           # 0050：回測的對照組
        p = path_of(sid)
        if p is None:
            no_file.append(sid); continue
        df, key, _ = load(p)
        if key.min() > DAY:            # 那天之後才上市
            continue
        if (key == DAY).any():
            has += 1
        else:
            missing.append(sid)
    print(f"{DAY:%Y-%m-%d}：{has} 檔已有、{len(missing)} 檔缺" + (f"、{len(no_file)} 檔沒有K線檔 {no_file}" if no_file else ""))
    if missing:
        print("  缺：" + "、".join(missing))
    if CHECK or not missing:
        return

    os.makedirs(BACKUP, exist_ok=True)
    ok, fail = [], []
    for i, sid in enumerate(missing, 1):
        p = path_of(sid)
        df, key, tz = load(p)
        raw = fetch(sid, DAY - pd.Timedelta(days=10), DAY + pd.Timedelta(days=10))
        if raw is None or DAY not in raw.index or float(raw.at[DAY, "Trading_Volume"]) == 0:
            fail.append(f"{sid}（FinMind 也沒有這天）"); continue
        cols = {c.lower(): c for c in df.columns}
        loc_close = pd.Series(df[cols["close"]].values, index=key)
        both = loc_close.index.intersection(raw.index)
        near = sorted(both, key=lambda d: abs((d - DAY).days))[:2]
        if not near:
            fail.append(f"{sid}（前後沒有可對照的交易日）"); continue
        fac = float(pd.Series([loc_close[d] / float(raw.at[d, "close"]) for d in near]).mean())
        r = raw.loc[DAY]
        new = {"open": r["open"] * fac, "high": r["max"] * fac, "low": r["min"] * fac,
               "close": r["close"] * fac, "volume": float(r["Trading_Volume"])}
        row = pd.DataFrame({cols[k]: [v] for k, v in new.items() if k in cols},
                           index=pd.DatetimeIndex([DAY.tz_localize(tz) if tz is not None else DAY], name=df.index.name))
        for c in df.columns:
            if c not in row.columns:
                row[c] = pd.NA
        out = pd.concat([df, row[df.columns].astype(df.dtypes.to_dict(), errors="ignore")])
        out.index = pd.to_datetime(out.index)
        out = out[~out.index.duplicated(keep="first")].sort_index()
        shutil.copy2(p, os.path.join(BACKUP, os.path.basename(p)))
        out.to_parquet(p)
        ok.append(sid)
        print(f"  [{i}/{len(missing)}] {sid} 收 {new['close']:.2f}（係數 {fac:.4f}）")
        time.sleep(0.3)

    print(f"\n補上 {len(ok)} 檔，失敗 {len(fail)} 檔" + (("：" + "、".join(fail)) if fail else ""))
    print(f"原檔備份在 {BACKUP}")
    if ok:
        n = 0
        for f in glob.glob(os.path.join(HERE, "bt_cache", "*", f"raw_{DAY:%Y-%m}-*.pkl")):
            if pd.Timestamp(os.path.basename(f)[4:14]) >= DAY:
                os.remove(f); n += 1
        print(f"已刪回測評分快取 {n} 個（{DAY:%Y-%m-%d} 以後），重跑：python _research\\esun_backtest_oct.py")


if __name__ == "__main__":
    main()
