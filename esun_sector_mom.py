# -*- coding: utf-8 -*-
"""
esun_sector_mom.py — 產業動能：買過去 3 個月最強的產業，下個月能不能贏（定案規格，跑一次）

規格（事先寫死）：
  · 範圍：比賽 150 檔，排序日前 20 日均量 >= 500 張；產業別用官方代碼（esun_sector.json）
  · 每月最後一個交易日：產業動能 = 成員股過去 63 個交易日報酬的平均；成員 >= 3 檔的產業才列入
  · 買進最強產業的全部成員（等權重），隔日開盤買、持有 20 個交易日；不可能跳動會被抵銷
  · 對照：150 檔整體平均、固定買半導體業、最弱產業
  · 期間：近 5 年（2021-09 起）
  · 通過：最強產業減整體的 t > 2，且前後半都為正 → 主題用產業動能
    沒通過 → 主題用固定 AI 半導體，不再嘗試其他選法
結果附加到 trial_log.csv。放 SMC- 根目錄：python esun_sector_mom.py
"""
import os, json, datetime
import numpy as np, pandas as pd
from esun_factor150 import load_prices, hold_return, tstat, MIN_VOL_LOTS

SECTOR_FILE = "esun_sector.json"
START = "2021-09-01"
LOOKBACK = 63
MIN_MEMBERS = 3
SEMI = "24"
ROUND_TRIP = 0.001425 * 2 + 0.003
TRIAL_LOG = "trial_log.csv"


def main():
    sec = json.load(open(SECTOR_FILE, encoding="utf-8"))
    px = load_prices()
    print(f"股價 {len(px)} 檔｜有產業別 {sum(s in sec for s in px)} 檔")
    days = sorted(set().union(*[df.index for df in px.values()]))
    s = pd.Series(days, index=pd.DatetimeIndex(days))
    s = s[s.index >= START]
    month_ends = [g.iloc[-1] for _, g in s.groupby([s.index.year, s.index.month])]

    rec, prev_top, latest = [], None, None
    for d in month_ends:
        rows = {}
        for sid, df in px.items():
            if sid not in sec:
                continue
            v = df["vol20"].loc[:d].dropna()
            if len(v) == 0 or v.iat[-1] < MIN_VOL_LOTS * 1000 or (d - v.index[-1]).days > 7:
                continue
            c = df["Close"].loc[:d]
            if len(c) <= LOOKBACK:
                continue
            rows[sid] = {"mom": c.iat[-1] / c.iat[-1 - LOOKBACK] - 1, "ret": hold_return(df, d),
                         "ind": sec[sid]["code"]}
        t = pd.DataFrame(rows).T
        if t.empty:
            continue
        t["mom"] = t["mom"].astype(float)
        g = t.groupby("ind")["mom"].agg(["mean", "size"])
        g = g[g["size"] >= MIN_MEMBERS].sort_values("mean", ascending=False)
        latest = (d, g)
        t = t[t["ret"].notna()]
        if len(g) < 2 or t.empty:
            continue
        t["ret"] = t["ret"].astype(float)
        top, bot = g.index[0], g.index[-1]
        r_top = t.loc[t["ind"] == top, "ret"].mean()
        changed = 1.0 if top != prev_top else 0.0
        rec.append({"date": d, "top": top, "top_ret": r_top, "top_net": r_top - changed * ROUND_TRIP,
                    "uni": t["ret"].mean(), "semi": t.loc[t["ind"] == SEMI, "ret"].mean(),
                    "bot": t.loc[t["ind"] == bot, "ret"].mean(), "changed": changed})
        prev_top = top
    r = pd.DataFrame(rec).set_index("date")
    names = {v["code"]: v["name"] for v in sec.values()}

    x = r["top_ret"] - r["uni"]
    half = r.index[len(r) // 2]
    tf, tb = tstat(x[x.index < half]), tstat(x[x.index >= half])
    t_all = tstat(x)
    passed = t_all > 2 and tf > 0 and tb > 0
    print("\n" + "=" * 72)
    print(f"每月買過去 3 個月最強產業，持有 20 個交易日（{len(r)} 個月）")
    print("=" * 72)
    for lab, col in [("最強產業", "top_ret"), ("150 檔整體", "uni"), ("固定半導體", "semi"), ("最弱產業", "bot")]:
        print(f"  {lab:<10}平均 {r[col].mean()*100:>6.2f}%/期｜標準差 {r[col].std()*100:>5.2f}%｜"
              f"贏整體的月份 {(r[col] > r['uni']).mean():>4.0%}")
    print(f"\n  最強產業 − 整體：平均 {x.mean()*100:+.2f}%/期，t = {t_all:.2f}，前半 t = {tf:.2f}，後半 t = {tb:.2f}")
    print(f"  扣換產業成本後 − 整體：{(r['top_net'] - r['uni']).mean()*100:+.2f}%/期"
          f"（最強產業每月換掉的比例 {r['changed'].mean():.0%}）")
    print(f"  贏整體 5% 以上的月份：{(x > 0.05).mean():.0%}｜輸整體 5% 以上的月份：{(x < -0.05).mean():.0%}")
    xs = r["semi"] - r["uni"]
    print(f"  （對照）固定半導體 − 整體：平均 {xs.mean()*100:+.2f}%/期，t = {tstat(xs):.2f}")
    print("  最常成為最強的產業：" + "、".join(f"{names.get(k, k)} {v}次" for k, v in r["top"].value_counts().head(5).items()))
    print(f"\n  判定：{'通過 → 主題用產業動能' if passed else '未通過 → 主題用固定 AI 半導體'}")

    if latest:
        d, g = latest
        print(f"\n  目前（{d:%Y-%m-%d}）產業動能前 5：" +
              "、".join(f"{names.get(k, k)} {m*100:+.1f}%（{n}檔）" for k, (m, n) in g.head(5).iterrows()))

    new = not os.path.exists(TRIAL_LOG)
    with open(TRIAL_LOG, "a", encoding="utf-8") as fh:
        if new:
            fh.write("時間,腳本,規格,比較,年化差,IR,t,前半t,後半t\n")
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        fh.write(f"{now},esun_sector_mom.py,150檔產業動能3個月持有20日；門檻t>2,最強產業−整體（{'通過' if passed else '未通過'}）,"
                 f"{x.mean()*100:.2f}%/期,,{t_all:.2f},{tf:.2f},{tb:.2f}\n")
    print(f"\n已寫入試驗紀錄 {TRIAL_LOG}")


if __name__ == "__main__":
    main()