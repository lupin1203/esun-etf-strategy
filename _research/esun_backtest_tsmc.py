# -*- coding: utf-8 -*-
r"""
esun_backtest_tsmc.py — 台積電比重回測：10%（正式）、15%、20%、24.5%，其他全部同正式設定

  · 正式設定：150 檔排除航運｜核心 8 檔＋小部位 11×1%｜new_only｜同產業核心最多 3 檔
  · 台積電多出來的權重從核心 8 檔平均扣（小部位 1% 和現金約 3% 不動）：
        台積電 10%   → 核心各 9.500%（正式）
        台積電 15%   → 核心各 8.875%
        台積電 20%   → 核心各 8.250%
        台積電 24.5% → 核心各 7.688%（上限 25% 留 0.5% 緩衝）
    台積電容許區間 = 目標 ±2%（正式是 8%–12%）；防守模式不變（台積電 24%）
  · 另外把 2026-02、04、08、09（主動式 ETF 大幅領先我們、AI 股強勢的月份）單獨拿出來看
輸出：_research\bt_out_tsmc\
用法（在 SMC- 資料夾）：python _research\esun_backtest_tsmc.py
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import esun_backtest as B
import numpy as np, pandas as pd

S = B.S
if not hasattr(S, "EXTRA_GROUP_CAPS"):
    raise SystemExit("esun_strategy.py 還是舊版（沒有 EXTRA_GROUP_CAPS），請先換上 10/14 版")

IND = {}
for sid, v in B.SECTOR.items():
    IND.setdefault(v["name"], set()).add(sid)
CAPS = [(g, S.MAX_INDUSTRY_CORE) for g in IND.values()]
REST = 1 - 0.03 - S.N_SMALL * S.W_SMALL             # 台積電＋核心合計 = 86%

VARIANTS = {"T10": 0.10, "T15": 0.15, "T20": 0.20, "T245": 0.245}
GROUPS = {k: (f"台積電{w:.1%}｜核心各{(REST - w) / S.N_CORE:.3%}", w) for k, w in VARIANTS.items()}
AI_MONTHS = ["2026-02", "2026-04", "2026-08", "2026-09"]

B.POOLS = {k: v[0] for k, v in GROUPS.items()}
B.COMBOS = [(k, "CORE", "new_only") for k in GROUPS]
B.OUT_DIR = os.path.join(HERE, "bt_out_tsmc")
B.tdcc_hist = lambda: None                            # 正式評分不含大戶

_orig_pool_table = B.pool_table


def pool_table(raw, pool, d=None):
    t = _orig_pool_table(raw, "NOSHIP", d)            # 選股池全部同正式設定（排除航運）
    if t is not None and len(t):
        t = t.copy()
        t["_var"] = pool
    return t


B.pool_table = pool_table

_orig_build = S.build_targets
_saved = (S.W_TSMC, S.TSMC_BAND, S.W_CORE)


def build_targets(d, prev=None, current_weights=None, mode="live"):
    t = S.score_pool(d, mode)
    key = t["_var"].iat[0] if t is not None and len(t) and "_var" in t.columns else "T10"
    w = GROUPS[key][1]
    S.W_TSMC, S.TSMC_BAND, S.W_CORE = w, (w - 0.02, min(w + 0.02, 0.249)), (REST - w) / S.N_CORE
    S.EXTRA_GROUP_CAPS = CAPS
    try:
        return _orig_build(d, prev, current_weights, mode)
    finally:
        S.W_TSMC, S.TSMC_BAND, S.W_CORE = _saved
        S.EXTRA_GROUP_CAPS = []


S.build_targets = build_targets


def summary():
    p = os.path.join(B.OUT_DIR, "每月報酬.csv")
    if not os.path.exists(p):
        return
    m = pd.read_csv(p, index_col=0, encoding="utf-8-sig")
    base = B.label(B.COMBOS[0])
    ai = [x for x in AI_MONTHS if x in m.index]
    other = [x for x in m.index if x not in ai]
    print("\n" + "=" * 92 + "\n台積電比重：跟正式（10%）每月配對比較\n" + "=" * 92)
    print(f"  {'組別':<28}{'平均月報酬':>10}{'比正式':>9}{'t值':>7}{'贏的月數':>9}"
          f"{'AI強勢4個月':>12}{'其他月份':>10}{'最差月':>9}")
    for c in B.COMBOS:
        col = B.label(c)
        dlt = m[col] - m[base]
        t = dlt.mean() / (dlt.std() / np.sqrt(len(dlt))) if dlt.std() > 0 else np.nan
        head = f"  {GROUPS[c[0]][0]:<28}{m[col].mean():>10.2%}"
        mid = (f"{'':>25}" if c == B.COMBOS[0] else
               f"{dlt.mean():>+9.2%}{t:>7.2f}{f'{int((dlt > 0).sum())}/{len(dlt)}':>9}")
        print(head + mid + f"{m.loc[ai, col].mean():>12.2%}{m.loc[other, col].mean():>10.2%}{m[col].min():>9.2%}")
    print(f"\n  AI 強勢月份：{'、'.join(ai)}；0050 平均 {m.loc[ai].iloc[:, -1].mean():+.2%}")
    print("  → 看兩件事：AI 強勢月份追回多少、其他月份少賺多少；平均月報酬比正式差太多就不值得換")


if __name__ == "__main__":
    B.main()
    summary()
