# -*- coding: utf-8 -*-
r"""
esun_fix_kline_gaps.py — 找出 150 檔 K 線所有「缺日」並從 FinMind 補齊，再列出剩下的單日大跳動

  · 交易日曆：150 檔裡有一半以上有 K 線的日子（從 2024-03-01 起，回測用得到的範圍）
  · 每檔從自己第一根 K 線之後，日曆上有、它卻沒有的日子 = 缺日
  · 每檔只打一次 FinMind（缺日最早～最晚前後 10 天），補上有成交的缺日；
    FinMind 也沒有或成交量 0 的視為停牌，不補
  · local_data 是還原價：每個缺日用前後最近兩個交易日的「本地收盤 ÷ 原始收盤」當係數換算
  · 改檔前備份到 _research\kfix_backup\gaps_時間\
  · 補完後檢查單日漲跌超過 11% 的地方（回測會當成減資／分割換算股數），列出來給人看：
    真的減資、分割、新股前 5 天是正常；其他就是資料有問題
用法（在 SMC- 資料夾）：
  python _research\esun_fix_kline_gaps.py --check     只列缺日，不改檔
  python _research\esun_fix_kline_gaps.py             補齊
補完後回測要重算評分：刪掉 _research\bt_cache 資料夾再跑回測（第一次會比較久）
"""
import os, sys, time, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.exists(os.path.join(os.path.dirname(HERE), "esun_strategy.py")) else HERE
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import pandas as pd, requests
import esun_strategy as S
from dplan_validator import UNIVERSE

URL = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
FROM = pd.Timestamp("2024-03-01")
JUMP = 0.11
CHECK = "--check" in sys.argv
BACKUP = os.path.join(HERE, "kfix_backup", "gaps_" + time.strftime("%Y%m%d_%H%M"))


def path_of(sid):
    for p in (os.path.join(S.LOCAL_DATA, f"{sid}.TW.parquet"), os.path.join(S.OTC_DIR, f"{sid}.TWO.parquet")):
        if os.path.exists(p):
            return p
    return None


def load(p):
    df = pd.read_parquet(p)
    idx = pd.to_datetime(df.index)
    tz = idx.tz
    return df, (idx.tz_localize(None) if tz is not None else idx), tz


def fetch(sid, start, end):
    prm = {"dataset": "TaiwanStockPrice", "data_id": sid, "start_date": f"{start:%Y-%m-%d}", "end_date": f"{end:%Y-%m-%d}"}
    if TOKEN:
        prm["token"] = TOKEN
    for _ in range(3):
        try:
            j = requests.get(URL, params=prm, timeout=30).json()
        except Exception:
            time.sleep(3); continue
        if "limit" in str(j.get("msg", "")).lower():
            print("  ! FinMind 限流，等 60 秒"); time.sleep(60); continue
        d = pd.DataFrame(j.get("data") or [])
        if d.empty:
            return None
        d["date"] = pd.to_datetime(d["date"])
        return d.set_index("date").sort_index()
    return None


def main():
    files, keys = {}, {}
    for sid in sorted(UNIVERSE | {"0050"}):           # 0050：回測的對照組
        p = path_of(sid)
        if p is None:
            print(f"  ! {sid} 沒有 K 線檔"); continue
        df, key, tz = load(p)
        files[sid] = p
        keys[sid] = key[key >= FROM]
    cnt = pd.Series(1, index=pd.DatetimeIndex([d for k in keys.values() for d in k])).groupby(level=0).sum()
    cal = cnt.index[cnt >= 0.5 * len(keys)]
    print(f"交易日曆：{cal[0]:%Y-%m-%d} 到 {cal[-1]:%Y-%m-%d}，{len(cal)} 天（{len(keys)} 檔）")

    gaps = {}
    for sid, key in keys.items():
        if not len(key):
            continue
        need = cal[(cal > key.min()) & (cal <= cal[-1])]
        miss = need.difference(key)
        if len(miss):
            gaps[sid] = miss
    n = sum(len(v) for v in gaps.values())
    print(f"缺日：{len(gaps)} 檔、共 {n} 天")
    byday = pd.Series({d: [s for s, m in gaps.items() if d in m] for d in sorted({d for m in gaps.values() for d in m})})
    for d, ss in byday.items():
        print(f"  {d:%Y-%m-%d}  {len(ss):>3} 檔  " + "、".join(ss[:12]) + (" …" if len(ss) > 12 else ""))
    if CHECK or not gaps:
        return check_jumps(files)

    os.makedirs(BACKUP, exist_ok=True)
    added, halted, fail = 0, [], []
    for i, (sid, miss) in enumerate(sorted(gaps.items()), 1):
        p = files[sid]
        df, key, tz = load(p)
        raw = fetch(sid, miss.min() - pd.Timedelta(days=10), miss.max() + pd.Timedelta(days=10))
        if raw is None:
            fail.append(sid); continue
        cols = {c.lower(): c for c in df.columns}
        loc_close = pd.Series(df[cols["close"]].values, index=key)
        both = loc_close.index.intersection(raw.index)
        rows = []
        for d in miss:
            if d not in raw.index or float(raw.at[d, "Trading_Volume"]) == 0:
                halted.append(f"{sid} {d:%m/%d}"); continue
            near = sorted(both, key=lambda x: abs((x - d).days))[:2]
            if not near:
                fail.append(f"{sid} {d:%m/%d}"); continue
            fac = sum(loc_close[x] / float(raw.at[x, "close"]) for x in near) / len(near)
            r = raw.loc[d]
            v = {"open": r["open"] * fac, "high": r["max"] * fac, "low": r["min"] * fac,
                 "close": r["close"] * fac, "volume": float(r["Trading_Volume"])}
            rows.append(pd.DataFrame({cols[k]: [x] for k, x in v.items() if k in cols},
                                     index=pd.DatetimeIndex([d.tz_localize(tz) if tz is not None else d], name=df.index.name)))
        if rows:
            add = pd.concat(rows)
            for c in df.columns:
                if c not in add.columns:
                    add[c] = pd.NA
            out = pd.concat([df, add[df.columns].astype(df.dtypes.to_dict(), errors="ignore")])
            out.index = pd.to_datetime(out.index)
            out = out[~out.index.duplicated(keep="first")].sort_index()
            shutil.copy2(p, os.path.join(BACKUP, os.path.basename(p)))
            out.to_parquet(p)
            added += len(rows)
        print(f"  [{i}/{len(gaps)}] {sid} 缺 {len(miss)} 天，補上 {len(rows)} 天")
        time.sleep(0.3)

    print(f"\n補上 {added} 天；停牌（FinMind 也沒成交）{len(halted)} 天"
          + (("：" + "、".join(halted[:30]) + (" …" if len(halted) > 30 else "")) if halted else ""))
    if fail:
        print(f"抓不到：{'、'.join(map(str, fail))}")
    print(f"原檔備份在 {BACKUP}")
    check_jumps(files)
    print("\n回測評分快取要重算：刪掉 _research\\bt_cache 資料夾，再跑回測")


def check_jumps(files):
    print("\n" + "=" * 60 + f"\n單日漲跌超過 {JUMP:.0%}（回測會當成減資／分割）\n" + "=" * 60)
    k = 0
    for sid, p in sorted(files.items()):
        df, key, _ = load(p)
        c = pd.Series(df[{x.lower(): x for x in df.columns}["close"]].values, index=key).sort_index()
        c = c[~c.index.duplicated(keep="last")]
        r = c / c.shift(1) - 1
        seasoned = pd.Series(range(len(c)), index=c.index) >= 5
        hit = r[(r.abs() > JUMP) & seasoned & (c.index >= FROM)]
        for d, x in hit.items():
            prev = c.index[c.index.get_loc(d) - 1]
            gap = (d - prev).days
            print(f"  {sid}  {d:%Y-%m-%d}  {x:+.1%}  （前一根 {prev:%m/%d}，隔 {gap} 天）" + ("  ← 隔太久，可能還有缺日／停牌" if gap > 5 else ""))
            k += 1
    print(f"共 {k} 筆" if k else "  沒有")


if __name__ == "__main__":
    main()
