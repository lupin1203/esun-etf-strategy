# -*- coding: utf-8 -*-
r"""
esun_backtest_indcap.py — 核心持股加「產業上限」的回測（其他全部同第 9 組）

  1  第 9 組（對照）：只有記憶體股核心最多 3 檔
  2  ＋同一產業（官方分類）核心最多 3 檔
  3  ＋同一產業核心最多 2 檔
  4  ＋傳產（塑膠、化學、玻璃、鋼鐵、水泥、電機、電纜、食品、紡織、汽車、油電、百貨等合為一類）核心最多 2 檔
  上限只管核心 8 檔；被擋下的股票順延，由排名下一檔補上，小部位不受限
需要：esun_strategy.py 已有 EXTRA_GROUP_CAPS（10/14 版）
輸出：_research\bt_out_indcap\，最後印出每組對第 9 組的配對差與 t 值，以及各組核心平均有幾檔傳產
用法（在 SMC- 資料夾）：python _research\esun_backtest_indcap.py
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import esun_backtest as B
import numpy as np, pandas as pd

S = B.S
if not hasattr(S, "EXTRA_GROUP_CAPS"):
    raise SystemExit("esun_strategy.py 還是舊版（沒有 EXTRA_GROUP_CAPS），請先換上 10/14 版")

TECH_FIN = {"半導體業", "電腦及週邊設備業", "光電業", "通信網路業", "電子零組件業", "電子通路業",
            "資訊服務業", "其他電子業", "數位雲端", "金融保險", "生技醫療業", "航運業"}
IND = {}
for sid, v in B.SECTOR.items():
    IND.setdefault(v["name"], set()).add(sid)
TRAD = {sid for sid, v in B.SECTOR.items() if v["name"] not in TECH_FIN}

GROUPS = {
    "NOSHIP": ("第9組（對照）", []),
    "CAP_IND3": ("＋同產業核心最多3檔", [(g, 3) for g in IND.values()]),
    "CAP_IND2": ("＋同產業核心最多2檔", [(g, 2) for g in IND.values()]),
    "CAP_TRAD2": ("＋傳產核心最多2檔", [(TRAD, 2)]),
}
B.POOLS = {k: v[0] for k, v in GROUPS.items()}
B.COMBOS = [(k, "CORE", "new_only") for k in GROUPS]
B.OUT_DIR = os.path.join(HERE, "bt_out_indcap")

_orig_pool_table = B.pool_table


def pool_table(raw, pool, d=None):
    t = _orig_pool_table(raw, "NOSHIP", d)
    if t is not None and len(t):
        t = t.copy()
        t["_cap"] = pool
    return t


B.pool_table = pool_table

_orig_build = S.build_targets
_trad_count = {k: [] for k in GROUPS}


def build_targets(d, prev=None, current_weights=None, mode="live"):
    t = S.score_pool(d, mode)
    key = t["_cap"].iat[0] if t is not None and len(t) and "_cap" in t.columns else "NOSHIP"
    S.EXTRA_GROUP_CAPS = GROUPS[key][1]
    try:
        tg, tab = _orig_build(d, prev, current_weights, mode)
    finally:
        S.EXTRA_GROUP_CAPS = []
    _trad_count[key].append(sum(1 for s in tg["core"] if s in TRAD))
    return tg, tab


S.build_targets = build_targets


def summary():
    p = os.path.join(B.OUT_DIR, "每月報酬.csv")
    if not os.path.exists(p):
        return
    m = pd.read_csv(p, index_col=0)
    base = B.label(B.COMBOS[0])
    print("\n" + "=" * 78 + "\n產業上限：跟第 9 組每月配對比較\n" + "=" * 78)
    print(f"  {'組別':<22}{'平均月報酬':>10}{'比第9組':>9}{'t值':>7}{'贏的月數':>9}{'核心平均傳產檔數':>14}")
    for c in B.COMBOS:
        col = B.label(c)
        dlt = m[col] - m[base]
        t = dlt.mean() / (dlt.std() / np.sqrt(len(dlt))) if dlt.std() > 0 else np.nan
        tc = np.mean(_trad_count[c[0]]) if _trad_count[c[0]] else np.nan
        tail = "" if c == B.COMBOS[0] else f"{dlt.mean():>+9.2%}{t:>7.2f}{f'{int((dlt > 0).sum())}/{len(dlt)}':>9}"
        print(f"  {GROUPS[c[0]][0]:<22}{m[col].mean():>10.2%}" + (tail if tail else f"{'':>25}") + f"{tc:>14.2f}")
    print("  → 比第 9 組為正且 t > 2 才採用")


if __name__ == "__main__":
    B.main()
    summary()
