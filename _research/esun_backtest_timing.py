# -*- coding: utf-8 -*-
r"""
esun_backtest_timing.py — 降低換手的三個「什麼時候交易」參數（2023-01 起，跟正式策略比）

  只改交易時機，不改選股邏輯，也不違反已繳交的策略說明文件：
  BASE  正式（對照）：EPS 增幅股價用 20 日均價｜排除航運｜8×9.5%＋11×1%＋台積電 10%｜new_only｜記憶體、同產業核心各最多 3 檔
  K40   小部位保留：前一天是小部位、仍持有、排名仍在前 40 名 → 繼續當小部位（不被排名更前的換掉）
  T90   超過上限減碼（權重 > 9.9%）時，非台積電減到 9.0%（正式是減回 9.5%）
  C90   核心目標整體降為 9.0%（現金目標約 8%）
  R4    「預估現金比目標多 X 以上就不套用無交易帶、全部調回目標」的 X 從 2% 放寬到 4%（23.5% 那條不變）
  ALL   K40＋T90＋R4 一起
做法：沿用 esun_backtest（每月本金 10 億從現金開始建倉、隔天均價成交、手續費證交稅照比賽）
      與 esun_backtest_epsprice 的 P20 股價（跟目前正式程式一致）
客觀性：參數（40 名、9.0%、4%）在看結果之前定好；
        採用標準 = 全期平均月報酬不低於正式、兩段子期間 t > −1、月換手比正式低
輸出：_research\bt_out_timing\  每月報酬.csv、每日淨值.csv、摘要.csv、回測明細.xlsx
用法（在 SMC- 資料夾）：python _research\esun_backtest_timing.py
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if not any(a.startswith("--start") for a in sys.argv[1:]):
    sys.argv += ["--start", "2023-01-01"]

import esun_backtest_epsprice as EP          # 會一併設定好 esun_backtest（P20 股價、產業上限）
import numpy as np, pandas as pd

B, S = EP.B, EP.S
O = B.O
B.OUT_DIR = os.path.join(HERE, "bt_out_timing")
os.makedirs(B.OUT_DIR, exist_ok=True)

SMALL_KEEP_RANK, TRIM_TO, CASH_GAP_EXTRA = 40, 0.09, 0.02
GROUPS = {"BASE": "正式（對照）", "K40": "小部位前40名保留", "T90": "超限減到9.0%",
          "C90": "核心目標9.0%", "R4": "現金偏高門檻4%", "ALL": "K40＋T90＋R4"}
FLAGS = {"BASE": "", "K40": "K", "T90": "T", "C90": "", "R4": "R", "ALL": "KTR"}
B.POOLS = dict(GROUPS)
B.WTS["C90"] = ("9.0%+1%", TRIM_TO, 0.01)
B.COMBOS = [(k, "C90" if k == "C90" else "CORE", "new_only") for k in GROUPS]


def pool_table(raw, pool, d=None):
    t = EP.pool_table(raw, "P20", d)          # 每組都用 P20（目前正式程式的 EPS 股價）
    if t is not None and len(t):
        t = t.copy()
        t["_var"] = pool
    return t


B.pool_table = pool_table


def flags():
    t = B._current_table[0]
    return FLAGS.get(t["_var"].iat[0], "") if t is not None and len(t) else ""


_orig_build = S.build_targets


def build_targets(d, prev=None, current_weights=None, mode="live"):
    tg, t = _orig_build(d, prev, current_weights, mode)
    if "K" not in flags() or not len(t):
        return tg, t
    held = {k for k, w in (current_weights or {}).items() if w and w > 0}
    prev_small = set((prev or {}).get("small", []))
    keep = [s for s in t.index if s in prev_small and s in held and s not in tg["core"]
            and t.loc[s, "排名"] <= SMALL_KEEP_RANK][:S.N_SMALL]
    small = keep + [s for s in tg["small"] if s not in keep][:S.N_SMALL - len(keep)]
    if small != tg["small"]:
        w = {s: x for s, x in tg["weights"].items() if s not in tg["small"]} | {s: S.W_SMALL for s in small}
        roles = {s: r for s, r in tg["roles"].items() if r != "小部位"} | {s: "小部位" for s in small}
        tg = dict(tg, small=small, weights=w, roles=roles, cash_target=round(1 - sum(w.values()), 4))
    return tg, t


S.build_targets = build_targets

_orig_make_orders = O.make_orders


def make_orders(targets, state, price, band=None):
    f = flags()
    tg = targets
    if "T" in f:
        nav, w = state["nav"], dict(targets["weights"])
        for t, sh in state["holdings"].items():
            if t != O.TSMC and t in w and price.get(t) and sh * price[t] / nav > O.CAP_OTHER - O.CAP_TRIGGER_MARGIN:
                w[t] = min(w[t], TRIM_TO)
        tg = dict(tg, weights=w)
    if "R" in f and tg.get("cash_target") is not None and band is None:   # band=0 的重算不再加一次
        tg = dict(tg, cash_target=tg["cash_target"] + CASH_GAP_EXTRA)
    return _orig_make_orders(tg, state, price, band)


O.make_orders = make_orders


def summary():
    p = os.path.join(B.OUT_DIR, "每月報酬.csv")
    if not os.path.exists(p):
        return
    m = pd.read_csv(p, index_col=0, encoding="utf-8-sig")
    sp = os.path.join(B.OUT_DIR, "摘要.csv")
    sm = pd.read_csv(sp, encoding="utf-8-sig") if os.path.exists(sp) else None
    base = B.label(B.COMBOS[0])
    print("\n" + "=" * 100 + "\n降低換手的交易時機參數：跟正式策略每月配對比較\n" + "=" * 100)
    print(f"  {'組別':<20}{'期間':<10}{'平均月報酬':>10}{'比正式':>9}{'t值':>7}{'贏/輸月數':>11}{'最差月':>9}")
    for a, b, lab in [(None, None, "全部"), ("2023-01", "2024-12", "2023–24"), ("2025-01", None, "2025–26")]:
        mm = m.loc[a:b] if a else m
        for c in B.COMBOS:
            col = B.label(c)
            dlt = mm[col] - mm[base]
            t = dlt.mean() / (dlt.std() / np.sqrt(len(dlt))) if dlt.std() > 0 else np.nan
            tail = (f"{'':>27}" if c == B.COMBOS[0] else
                    f"{dlt.mean():>+9.2%}{t:>7.2f}{f'{int((dlt > 0).sum())}/{int((dlt < 0).sum())}':>11}")
            print(f"  {GROUPS[c[0]]:<20}{lab:<10}{mm[col].mean():>10.2%}" + tail + f"{mm[col].min():>9.2%}")
        print()
    if sm is not None and "月換手" in sm.columns:
        print("  月換手（買賣金額 ÷ 淨值）：")
        for c in B.COMBOS:
            r = sm[sm["組合"] == B.label(c)]
            if len(r):
                print(f"    {GROUPS[c[0]]:<20}{float(r['月換手'].iat[0]):>8.1%}")
    print("  → 採用標準：全期比正式 ≥ 0、兩段子期間 t > −1、月換手比正式低")


if __name__ == "__main__":
    B.main()
    summary()
