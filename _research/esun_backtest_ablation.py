# -*- coding: utf-8 -*-
r"""
esun_backtest_ablation.py — 拆解：新方法 C 的五個改動，到底哪個有用、哪個拉低

從第 9 組出發，每組只改「一件事」，其他全部跟第 9 組一樣：
  1  第 9 組（對照）
  2  拿掉大戶（其他三項不變）
  3  營收驚喜放寬（前 3 個月 YoY 有 2 個月就算）
  4  營收驚喜放寬 ＋ MoM ≥10% 加分
  5  EPS 改成 (本季 − 去年同季) ÷ |上一季|（C 版）
  6  三率不跌 → 營收／毛利／淨利成長 >10% 項數
  7  加換股門檻（新股票要比第 17 名好才換，已持有的不因排名下滑而賣）
  8  新方法 C 全部一起（對照上一次的結果）
排名一律用第 9 組的百分位算法（第 8 組照新方法原樣）
輸出：_research\bt_out_ablation\，最後印出每組對第 9 組的配對差、t 值
用法（在 SMC- 資料夾）：python _research\esun_backtest_ablation.py
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import esun_backtest_mtg_c as MC
import numpy as np, pandas as pd

M, B, S = MC.M, MC.B, MC.S

GROUPS = {          # 代碼: (說明, 改動)
    "NOSHIP":   ("第9組（對照）", {}),
    "AB_NOBIG": ("只拿掉大戶", {"big": False}),
    "AB_RELAX": ("只放寬營收驚喜", {"rev": "relax"}),
    "AB_MOM":   ("營收驚喜放寬＋MoM加分", {"rev": "mom"}),
    "AB_EPSC":  ("只改EPS（÷上一季）", {"eps": "C"}),
    "AB_GROW":  ("只改三率→成長項數", {"margin": "grow"}),
    "AB_BUF":   ("只加換股門檻", {"buffer": True}),
    "MTG_C":    ("新方法C全部", None),
}
B.POOLS = {k: v[0] for k, v in GROUPS.items()}
B.COMBOS = [(k, "CORE", "new_only") for k in GROUPS]
B.OUT_DIR = os.path.join(HERE, "bt_out_ablation")

_day = {}


def new_cols(raw, d):
    """新方法的各項分數，每天只算一次"""
    if d not in _day:
        _day.clear()
        t = M.mtg_table(raw, d, "deadline", absolute=True)
        t["EPS_C"] = MC.eps_rate_c(t, d, "deadline")
        _day[d] = t[["營收驚喜2", "I1_營收分數", "I3_成長項數", "EPS_C"]]
    return _day[d]


def ablation_table(raw, d, cfg):
    t = raw[~raw.index.isin(B.SHIP)].copy()
    n = new_cols(raw, d).reindex(t.index)
    cols = {"營收": t["營收驚喜"], "EPS": t["EPS增幅"], "三率": t["三率"]}
    if cfg.get("rev") == "relax":
        cols["營收"] = n["營收驚喜2"]
    elif cfg.get("rev") == "mom":
        cols["營收"] = n["I1_營收分數"]
    if cfg.get("eps") == "C":
        cols["EPS"] = n["EPS_C"]
    if cfg.get("margin") == "grow":
        cols["三率"] = n["I3_成長項數"]
    if cfg.get("big", True):
        cols["大戶"] = t["大戶"]
    ranks = pd.concat([c.astype(float).rank(pct=True) for c in cols.values()], axis=1)
    t["綜合分數"] = ranks.mean(axis=1, skipna=True)
    t = t[t["綜合分數"].notna()].sort_values("綜合分數", ascending=False)
    t["排名"] = range(1, len(t) + 1)
    if cfg.get("buffer"):
        t["I3_成長項數"] = 0.0          # 讓換股門檻版的目標組合接手（這一欄只當開關，不參與排名）
    return t


_prev_pool_table = B.pool_table


def pool_table(raw, pool, d=None):
    if pool in GROUPS and GROUPS[pool][1] is not None and pool != "NOSHIP" and raw is not None and not raw.empty:
        return ablation_table(raw, d, GROUPS[pool][1])
    return _prev_pool_table(raw, pool, d)


B.pool_table = pool_table


def summary():
    p = os.path.join(B.OUT_DIR, "每月報酬.csv")
    if not os.path.exists(p):
        return
    m = pd.read_csv(p, index_col=0)
    base = B.label(B.COMBOS[0])
    print("\n" + "=" * 78 + "\n拆解結果：每組只改一件事，跟第 9 組每月配對比較\n" + "=" * 78)
    print(f"  {'組別':<26}{'平均月報酬':>10}{'比第9組':>9}{'t值':>7}{'贏的月數':>9}")
    print(f"  {GROUPS['NOSHIP'][0]:<26}{m[base].mean():>10.2%}")
    for c in B.COMBOS[1:]:
        col = B.label(c)
        dlt = m[col] - m[base]
        t = dlt.mean() / (dlt.std() / np.sqrt(len(dlt))) if dlt.std() > 0 else np.nan
        print(f"  {GROUPS[c[0]][0]:<26}{m[col].mean():>10.2%}{dlt.mean():>+9.2%}{t:>7.2f}"
              f"{f'{int((dlt > 0).sum())}/{len(dlt)}':>9}")
    print("  → 比第 9 組為正且 t > 2：這個改動有用；為負且 t < −2：這個改動拉低")


if __name__ == "__main__":
    B.main()
    summary()
