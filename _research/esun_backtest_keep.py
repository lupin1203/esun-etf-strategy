# -*- coding: utf-8 -*-
r"""
esun_backtest_keep.py — 「核心保留門檻」回測（2023-01 起，跟正式策略比）

  問題：正式策略只保留「排名仍在前 12 名」的前期核心。前面的股票被進場過濾擋掉時，
        核心會一路補到第 13、14、15 名；這些核心沒有保護，只要被下一名超過一點點就被換掉，
        隔天股價一動又可能換回來（例如 2026-11-02 台玻第 15 名、華邦電第 14 名，綜合分數只差 0.001，
        11/03 賣台玻 1,362 張、買華邦電 508 張）
  BASE  正式（對照）：前期核心排名 ≤ 12 才保留
  K3    邊界緩衝 3 名：先照排名（含過濾、記憶體與同產業上限）挑出「不考慮保留時」的 8 檔核心，
        最後一檔的名次 = 核心邊界；前期核心排名 ≤ max(12, 邊界 + 3) 就保留
  K5    邊界緩衝 5 名（敏感度，只用來看方向，不拿來挑）
  其他全部同正式設定：排除航運｜8×9.5%＋11×1%＋台積電 10%｜new_only｜記憶體、同產業核心各最多 3 檔
客觀性：緩衝 3 名在看結果之前定好；採用標準 = K3 全期平均月報酬不低於正式、兩段子期間都不明顯變差
        （t > −1），且月換手比正式低。這是降低無謂換手的修正，不要求 t > 2
需要：bt_data 已補到 2022-06 起（esun_bt_fetch.py）；評分用 bt_cache，重跑很快
輸出：_research\bt_out_keep\  每月報酬.csv、每日淨值.csv、摘要.csv、回測明細.xlsx、保留紀錄.csv
用法（在 SMC- 資料夾）：python _research\esun_backtest_keep.py
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if not any(a.startswith("--start") for a in sys.argv[1:]):
    sys.argv += ["--start", "2023-01-01"]

import esun_backtest as B
import numpy as np, pandas as pd

S = B.S
if not hasattr(S, "EXTRA_GROUP_CAPS"):
    raise SystemExit("esun_strategy.py 還是舊版（沒有 EXTRA_GROUP_CAPS）")
B.PX_FROM = "2022-03-01"
B.tdcc_hist = lambda: None
B.OUT_DIR = os.path.join(HERE, "bt_out_keep")
os.makedirs(B.OUT_DIR, exist_ok=True)

IND = {}
for sid, v in B.SECTOR.items():
    IND.setdefault(v["name"], set()).add(sid)
CAPS = [(g, S.MAX_INDUSTRY_CORE) for g in IND.values()]

GROUPS = {"BASE": "正式（對照）", "K3": "邊界緩衝 3 名", "K5": "邊界緩衝 5 名（敏感度）"}
BUF = {"K3": 3, "K5": 5}
B.POOLS = dict(GROUPS)
B.COMBOS = [(k, "CORE", "new_only") for k in GROUPS]

_orig_pool_table = B.pool_table


def pool_table(raw, pool, d=None):
    t = _orig_pool_table(raw, "NOSHIP", d)
    if t is not None and len(t):
        t = t.copy()
        t["_var"] = pool
    return t


B.pool_table = pool_table

_orig_build = S.build_targets
LOG = []


def boundary_rank(t, current_weights):
    """不考慮保留時，照排名挑出的第 8 檔核心的名次（跟 build_targets 的 eligible、上限一樣）"""
    held = {k for k, w in (current_weights or {}).items() if w and w > 0}
    caps = [(S.MEMORY, S.MAX_MEMORY_CORE)] + list(S.EXTRA_GROUP_CAPS)
    trial = []
    for s in t.index:
        if len(trial) >= S.N_CORE:
            break
        if t.loc[s, "過濾"] and not (S.FILTER_SCOPE == "new_only" and s in held):
            continue
        if any(s in g and sum(1 for c in trial if c in g) >= n for g, n in caps):
            continue
        trial.append(s)
    return int(t.loc[trial[-1], "排名"]) if trial else None


def build_targets(d, prev=None, current_weights=None, mode="live"):
    t = S.score_pool(d, mode)
    key = t["_var"].iat[0] if t is not None and len(t) and "_var" in t.columns else "BASE"
    S.EXTRA_GROUP_CAPS = CAPS
    keep0 = S.CORE_KEEP_RANK
    try:
        if key in BUF:
            b = boundary_rank(t, current_weights)
            if b is not None:
                S.CORE_KEEP_RANK = max(keep0, b + BUF[key])
        tg, tab = _orig_build(d, prev, current_weights, mode)
        if key in BUF and prev:
            base_keep = [s for s in prev.get("core", []) if s in tab.index and keep0 < tab.loc[s, "排名"] <= S.CORE_KEEP_RANK
                         and s in tg["core"]]
            if base_keep:
                LOG.append((f"{d:%Y-%m-%d}", key, S.CORE_KEEP_RANK, "、".join(base_keep)))
    finally:
        S.EXTRA_GROUP_CAPS = []
        S.CORE_KEEP_RANK = keep0
    return tg, tab


S.build_targets = build_targets


def summary():
    p = os.path.join(B.OUT_DIR, "每月報酬.csv")
    if not os.path.exists(p):
        return
    pd.DataFrame(LOG, columns=["決策日", "組", "當天保留門檻", "因緩衝而保留的核心"]).to_csv(
        os.path.join(B.OUT_DIR, "保留紀錄.csv"), encoding="utf-8-sig", index=False)
    m = pd.read_csv(p, index_col=0, encoding="utf-8-sig")
    sm = pd.read_csv(os.path.join(B.OUT_DIR, "摘要.csv"), encoding="utf-8-sig") if os.path.exists(
        os.path.join(B.OUT_DIR, "摘要.csv")) else None
    base = B.label(B.COMBOS[0])
    print("\n" + "=" * 96 + "\n核心保留門檻：跟正式策略每月配對比較\n" + "=" * 96)
    print(f"  {'組別':<22}{'期間':<12}{'平均月報酬':>10}{'比正式':>9}{'t值':>7}{'贏/輸月數':>11}{'最差月':>9}")
    for a, b, lab in [(None, None, "全部"), ("2023-01", "2024-12", "2023–24"), ("2025-01", None, "2025–26")]:
        mm = m.loc[a:b] if a else m
        for c in B.COMBOS:
            col = B.label(c)
            dlt = mm[col] - mm[base]
            t = dlt.mean() / (dlt.std() / np.sqrt(len(dlt))) if dlt.std() > 0 else np.nan
            tail = (f"{'':>27}" if c == B.COMBOS[0] else
                    f"{dlt.mean():>+9.2%}{t:>7.2f}{f'{int((dlt > 0).sum())}/{int((dlt < 0).sum())}':>11}")
            print(f"  {GROUPS[c[0]]:<22}{lab:<12}{mm[col].mean():>10.2%}" + tail + f"{mm[col].min():>9.2%}")
        print()
    if sm is not None and "月換手" in sm.columns:
        print("  月換手（買賣金額 ÷ 淨值）：")
        for c in B.COMBOS:
            r = sm[sm["組合"] == B.label(c)]
            if len(r):
                print(f"    {GROUPS[c[0]]:<22}{float(r['月換手'].iat[0]):>8.1%}")
    n = pd.DataFrame(LOG, columns=["d", "k", "r", "x"])
    for k in BUF:
        print(f"  {GROUPS[k]}：因緩衝多保留 {int((n['k'] == k).sum())} 天（明細在 保留紀錄.csv）")
    print("  → 採用標準：K3 全期比正式 ≥ 0、兩段子期間 t > −1、月換手比正式低")


if __name__ == "__main__":
    B.main()
    summary()
