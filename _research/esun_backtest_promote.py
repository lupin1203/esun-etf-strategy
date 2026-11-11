# -*- coding: utf-8 -*-
r"""
esun_backtest_promote.py — 「小部位升級成核心，要不要通過進場過濾」回測（2023-01 起，跟正式策略比）

  問題：正式策略的 new_only 寫法是「有持股就不擋過濾」，持股多少不管 →
        1% 的小部位可以繞過均線／爆量／法人過濾，直接升級成 9.5% 核心（例如 2026-10-19 南亞科、華邦電）
  BASE  正式（對照）：照現在的程式
  P1    升級要過濾：上一期不是核心、目前權重 < 5% 的股票，要進核心必須通過過濾；
        沒通過的留在小部位（1%），核心由排名下一檔「通過過濾」的補上（一樣受記憶體、同產業各最多 3 檔限制）
  P2    升級和加碼都要過濾：P1 之外，核心股如果沒通過過濾、目前權重又低於目標，就維持目前權重、不加碼
  其他全部同正式設定：排除航運｜8×9.5%＋11×1%＋台積電 10%｜new_only｜同產業核心最多 3 檔
需要：bt_data 已補到 2022-06 起（esun_bt_fetch.py）
輸出：_research\bt_out_promote\  每月報酬.csv、每日淨值.csv、摘要.csv、回測明細.xlsx、升級紀錄.csv
用法（在 SMC- 資料夾）：python _research\esun_backtest_promote.py
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
B.OUT_DIR = os.path.join(HERE, "bt_out_promote")
os.makedirs(B.OUT_DIR, exist_ok=True)

IND = {}
for sid, v in B.SECTOR.items():
    IND.setdefault(v["name"], set()).add(sid)
CAPS = [(g, S.MAX_INDUSTRY_CORE) for g in IND.values()]
SMALL_MAX = 0.05                       # 目前權重低於這個就算「小部位」，要升級核心得先過濾

GROUPS = {"BASE": "正式（對照）", "P1": "升級要過濾", "P2": "升級和加碼都要過濾"}
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


def build_targets(d, prev=None, current_weights=None, mode="live"):
    t = S.score_pool(d, mode)
    key = t["_var"].iat[0] if t is not None and len(t) and "_var" in t.columns else "BASE"
    S.EXTRA_GROUP_CAPS = CAPS
    try:
        tg, tab = _orig_build(d, prev, current_weights, mode)
    finally:
        S.EXTRA_GROUP_CAPS = []
    if key == "BASE":
        return tg, tab
    cur = {k: float(w or 0) for k, w in (current_weights or {}).items()}
    prev_core = set((prev or {}).get("core", []))
    fail = lambda s: bool(t.loc[s, "過濾"]) if s in t.index else False
    caps = [(S.MEMORY, S.MAX_MEMORY_CORE)] + CAPS

    def cap_ok(s, chosen):
        return all(not (s in g and sum(1 for c in chosen if c in g) >= n) for g, n in caps)

    demoted = [s for s in tg["core"] if s not in prev_core and 0 < cur.get(s, 0) < SMALL_MAX and fail(s)]
    core = [s for s in tg["core"] if s not in demoted]
    for s in t.index:                                   # 依排名補滿，只收通過過濾的
        if len(core) >= S.N_CORE:
            break
        if s not in core and s not in demoted and not fail(s) and cap_ok(s, core):
            core.append(s)
    small = [s for s in demoted if s not in core]
    small += [s for s in tg["small"] if s not in core and s not in small]
    small += [s for s in t.index if s not in core and s not in small and (not fail(s) or cur.get(s, 0) > 0)]
    small = small[:S.N_SMALL]
    w_core = S.W_CORE_DEF if tg.get("defensive") else S.W_CORE
    w = {S.TSMC: tg["weights"][S.TSMC]}
    for s in core:
        w[s] = w_core
        if key == "P2" and fail(s) and 0 < cur.get(s, 0) < w_core:
            w[s] = cur[s]                               # 沒過濾就不加碼，維持目前權重
    for s in small:
        w[s] = S.W_SMALL
    if demoted:
        LOG.append((f"{d:%Y-%m-%d}", key, "、".join(demoted), "、".join(s for s in core if s not in tg["core"])))
    tg = dict(tg, core=core, small=small, weights=w,
              roles={S.TSMC: "台積電"} | {s: "核心" for s in core} | {s: "小部位" for s in small},
              cash_target=round(1 - sum(w.values()), 4))
    return tg, tab


S.build_targets = build_targets


def summary():
    p = os.path.join(B.OUT_DIR, "每月報酬.csv")
    if not os.path.exists(p):
        return
    pd.DataFrame(LOG, columns=["決策日", "組", "被擋下的升級", "改補進核心"]).to_csv(
        os.path.join(B.OUT_DIR, "升級紀錄.csv"), encoding="utf-8-sig", index=False)
    m = pd.read_csv(p, index_col=0, encoding="utf-8-sig")
    base = B.label(B.COMBOS[0])
    print("\n" + "=" * 96 + "\n升級要不要過濾：跟正式策略每月配對比較\n" + "=" * 96)
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
    n = pd.DataFrame(LOG, columns=["d", "k", "x", "y"])
    for k in ("P1", "P2"):
        print(f"  {GROUPS[k]}：擋下升級 {int((n['k'] == k).sum())} 次（明細在 升級紀錄.csv）")
    print("  → 比正式為正且 t > 2 才改；差不多的話，看哪一個跟策略說明文件的寫法比較一致")


if __name__ == "__main__":
    B.main()
    summary()
