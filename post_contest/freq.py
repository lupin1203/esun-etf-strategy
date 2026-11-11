# -*- coding: utf-8 -*-
r"""
post_contest\freq.py — 8 檔組（G3）的調整頻率：每日｜每週第一個交易日｜每月 11 日起第一個交易日
  事前定好的採用標準（跟每日配對比）：全期月報酬差 ≥ 0、2023–24 與 2025–26 兩段 t > −1、年成本更低
  滑價 0.1%、0.3% 各跑一次；另外印出最後一個決策日的排名與過濾，核對期末持股為什麼不滿 8 檔
輸出：post_contest\out\頻率_摘要.csv、頻率_每日淨值.csv
用法（在 SMC- 資料夾）：python post_contest\freq.py
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import backtest as B

P, days, mem, _ = B.build_panels()
ind = B.sectors()
base = next(c for c in B.GROUPS if c.key == "G3")
bench = B.bars_of(B.MKT)["Close"]
NAMES = {"D": "每日", "W": "每週", "M": "每月"}
res = {}
for slip, tag in ((0.001, "0.1%"), (0.003, "0.3%")):
    for f in "DWM":
        c = B.Cfg(**{**base.__dict__, "slip": slip, "freq": f})
        r = B.simulate(c, P, days, mem, ind)
        r["name"] = f"{NAMES[f]}｜滑價{tag}"
        res[f + tag] = r
        print(r["name"], f"期末 {r['nav'].iat[-1]:.3f}", flush=True)

s = B.summarize(res, bench)
pd.set_option("display.width", 240, "display.max_columns", 30, "display.unicode.east_asian_width", True)
print(s.drop(columns=["組別", "防守天數"]).to_string(index=False, float_format=lambda x: f"{x:.4f}"))
navs = pd.DataFrame({k: v["nav"] for k, v in res.items()})
m = navs.resample("ME").last().pct_change().dropna()
for tag in ("0.1%", "0.3%"):
    for f in "WM":
        for lab, x in (("全期", m), ("2023-24", m.loc["2023":"2024"]), ("2025-26", m.loc["2025":])):
            d = x[f + tag] - x["D" + tag]
            print(f"{NAMES[f]}−每日｜滑價{tag}｜{lab}：{d.mean():+.2%}/月 t={d.mean() / (d.std() / np.sqrt(len(d))):.2f} 贏{int((d > 0).sum())}/{len(d)}")
s.to_csv(os.path.join(B.OUT, "頻率_摘要.csv"), index=False, encoding="utf-8-sig")
navs.to_csv(os.path.join(B.OUT, "頻率_每日淨值.csv"), encoding="utf-8-sig")

# 核對：最後一個決策日
held = res["D0.1%"]["held"]
print("\n檔數分布（每日｜滑價0.1%）", (held > 0).sum(axis=1).value_counts().sort_index().to_dict())
print("不滿 8 檔的天數比例", f"{((held > 0).sum(axis=1) < 8).mean():.1%}", "；期末現金", f"{1 - held.iloc[-1].sum():.1%}")
d = days[-2]
pool = {x for x in B.members_on(mem, d) if not any(y in ind(x) for y in base.excl)} - {B.TSMC}
t = B.score_table(P, d, pool)
print(f"決策日 {d:%Y-%m-%d}，選股池有分數 {len(t)} 檔，通過過濾 {int((~t['flt']).sum())} 檔")
last = held.iloc[-1]
for x in t.index[:20]:
    print(f"  第{int(t.at[x, 'rank']):>2}名 {x} {ind(x):<10} 過濾={'擋' if t.at[x, 'flt'] else '過'} 持有權重={last.get(x, 0):.1%}")
print("持有但不在前 20：", {x: (int(t.at[x, 'rank']) if x in t.index else None) for x, w in last.items() if w > 0 and (x not in t.index or t.at[x, 'rank'] > 20)})
