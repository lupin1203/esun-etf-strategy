# -*- coding: utf-8 -*-
r"""
why_big.py — 大型權值股（含大立光）最近幾個決策日排第幾、被哪個因子拖累（讀回測的分數快取，不重算）
用法（在 SMC- 資料夾）：python _research\why_big.py
"""
import os, glob, sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = sorted(glob.glob(os.path.join(HERE, "bt_cache", "scores_*_rawpx")) or glob.glob(os.path.join(HERE, "bt_cache", "scores_*")))[-1]
FACTORS = ("營收驚喜", "EPS增幅", "三率", "大戶")
DAYS = sorted(os.path.basename(p)[4:14] for p in glob.glob(os.path.join(CACHE, "raw_*.pkl")))[-4:]   # 最近 4 個決策日
WATCH = ["3008", "2317", "2454", "2308", "2382", "3711", "2891", "2412"]   # 大立光 鴻海 聯發科 台達電 廣達 日月光 中信金 中華電
sys.path.insert(0, os.path.dirname(HERE)); os.chdir(os.path.dirname(HERE))
import json, esun_strategy as S
sec = json.load(open(S.SECTOR_FILE, encoding="utf-8"))
ship = {s for s, v in sec.items() if v["name"] in ("航運業",)}

print("快取：", CACHE)
for d in DAYS:
    p = os.path.join(CACHE, f"raw_{d}.pkl")
    if not os.path.exists(p):
        print(f"\n{d}：沒有快取"); continue
    raw = pd.read_pickle(p)
    t = raw[~raw.index.isin(ship)].copy()                       # 第 9 組選股池
    pct = pd.concat({c: t[c].astype(float).rank(pct=True) for c in FACTORS}, axis=1)
    t["綜合"] = pct.mean(axis=1, skipna=True)
    t["項數"] = pct.notna().sum(axis=1)
    t = t[t["綜合"].notna()].sort_values("綜合", ascending=False)
    t["排名"] = range(1, len(t) + 1)
    passed = t[~t["過濾"].astype(bool)]
    passed_rank = {s: i for i, s in enumerate(passed.index, 1)}
    print(f"\n==== {d}（選股池 {len(t)} 檔，通過過濾 {len(passed)} 檔）====")
    for s in WATCH:
        if s not in t.index:
            print(f"  {s}：不在選股池（流動性或資料不足）"); continue
        r = t.loc[s]
        fs = "  ".join(f"{c} {r[c]}→{pct.loc[s, c]:.0%}" if pd.notna(pct.loc[s, c]) else f"{c} —" for c in FACTORS)
        print(f"  {s} {r['產業']}｜總排名 {r['排名']}｜通過過濾中第 {passed_rank.get(s, '—')}｜用了 {int(r['項數'])} 項｜綜合 {r['綜合']:.3f}")
        print(f"      {fs}")
        print(f"      EPS 細節：{r['EPS資料']}｜過濾：{r['過濾'] or '通過'}")
    print("  通過過濾的前 12 名：" + "、".join(f"{s}({t.loc[s, '排名']})" for s in passed.index[:12]))
