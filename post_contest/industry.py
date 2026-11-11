# -*- coding: utf-8 -*-
r"""
post_contest\industry.py — 8 檔組（G3）持有哪些產業、每個產業賺賠多少，跟等權市場比
輸出：post_contest\out\產業權重_年度.csv、產業貢獻_年度.csv、回檔期間持股.csv
用法（在 SMC- 資料夾）：python post_contest\industry.py
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import backtest as B

P, days, mem, _ = B.build_panels()
ind = B.sectors()
cfg = next(c for c in B.GROUPS if c.key == "G3")
r = B.simulate(cfg, P, days, mem, ind)
w = r["held"]
ret = P["close"].ffill().pct_change().reindex(w.index)
ret = ret.where(ret.abs() <= 0.11, 0.0)           # 分割、減資不計報酬，跟模擬一致
contrib = (w.shift(1).fillna(0.0) * ret.reindex(columns=w.columns)).fillna(0.0)


def norm(s):
    x = ind(s)
    for a, b in (("金融", "金融"), ("半導體", "半導體"), ("電子零組件", "電子零組件"), ("電腦", "電腦週邊"), ("通信", "通信網路"),
                 ("光電", "光電"), ("電子通路", "電子通路"), ("其他電子", "其他電子"), ("資訊服務", "資訊服務")):
        if a in x:
            return b
    return x


ig = pd.Series({s: norm(s) for s in w.columns})
wi = w.T.groupby(ig).sum().T
ci = contrib.T.groupby(ig).sum().T
year = wi.index.year
pd.set_option("display.width", 240, "display.max_columns", 30, "display.max_rows", 60, "display.unicode.east_asian_width", True)

wy = (wi.groupby(year).mean() * 100).T
wy["全期"] = wi.mean() * 100
wy = wy.sort_values("全期", ascending=False)
print("平均權重（%）\n", wy.head(14).round(1).to_string())
cy = (ci.groupby(year).sum() * 100).T
cy["全期"] = ci.sum() * 100
cy = cy.reindex(wy.index)
print("\n報酬貢獻（百分點，單利加總）\n", cy.head(14).round(1).to_string())
wy.to_csv(os.path.join(B.OUT, "產業權重_年度.csv"), encoding="utf-8-sig")
cy.to_csv(os.path.join(B.OUT, "產業貢獻_年度.csv"), encoding="utf-8-sig")

a, z = "2024-04-09", "2025-04-09"
print(f"\n回檔期間 {a}～{z}")
win = w.loc[a:z]
print("平均權重", (win.T.groupby(ig).sum().T.mean().sort_values(ascending=False).head(8) * 100).round(1).to_dict())
print("貢獻", (contrib.loc[a:z].T.groupby(ig).sum().T.sum().sort_values().round(3) * 100).round(1).to_dict())
tops = contrib.loc[a:z].sum().sort_values()
print("拖累最大", (tops.head(10) * 100).round(1).to_dict())
print("貢獻最大", (tops.tail(5) * 100).round(1).to_dict())
tops.to_csv(os.path.join(B.OUT, "回檔期間持股.csv"), encoding="utf-8-sig")

close = P["close"].ffill()
print("\n同期漲跌：台積電", f"{close.at[pd.Timestamp(z), '2330'] / close.at[pd.Timestamp(a), '2330'] - 1:.1%}",
      "聯發科", f"{close.at[pd.Timestamp(z), '2454'] / close.at[pd.Timestamp(a), '2454'] - 1:.1%}",
      "鴻海", f"{close.at[pd.Timestamp(z), '2317'] / close.at[pd.Timestamp(a), '2317'] - 1:.1%}")
mem_a = B.members_on(mem, pd.Timestamp(a))
print("成員等權同期", f"{(close.loc[pd.Timestamp(z), list(mem_a & set(close.columns))] / close.loc[pd.Timestamp(a), list(mem_a & set(close.columns))] - 1).mean():.1%}")
print("單一產業權重 >30% 的天數比例", f"{(wi.max(axis=1) > 0.30).mean():.1%}", "；前兩大產業平均合計", f"{wi.apply(lambda x: x.nlargest(2).sum(), axis=1).mean():.1%}")
print("換股：平均每檔持有交易日", f"{(w > 0).sum().sum() / max(((w > 0) & ~(w.shift(1) > 0)).sum().sum(), 1):.0f}")
print("期末持股", {s: (ig[s], round(x * 100, 1)) for s, x in w.iloc[-1].items() if x > 0})
