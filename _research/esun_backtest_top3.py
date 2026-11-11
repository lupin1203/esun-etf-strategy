# -*- coding: utf-8 -*-
r"""
esun_backtest_top3.py — 「排名前三名只要通過一項過濾就能買」回測（2023-01 起，跟正式策略比）

  過濾有三項：均線 5／10／20 全部下彎、爆量下跌賣壓未消化、三大法人連 3 日賣超
  BASE  正式（對照）：三項都要通過才能新買進
  T1    前三名放寬：綜合排名前 3 名，三項只要通過一項（最多觸發兩項）就能買；第 4 名以後照舊三項都要通過
  T2    前三名不過濾（參考）：綜合排名前 3 名完全不看過濾，用來看 T1 的效果是不是只是「放掉過濾」
  其他全部同正式設定：排除航運｜8×9.5%＋11×1%＋台積電 10%｜new_only｜記憶體、同產業核心各最多 3 檔
  注意：放寬的股票也算進「通過過濾檔數」，防守模式的判斷最多差 3 檔
需要：bt_data 已補到 2022-06 起（esun_bt_fetch.py）
輸出：_research\bt_out_top3\  每月報酬.csv、每日淨值.csv、摘要.csv、回測明細.xlsx、放寬紀錄.csv
用法（在 SMC- 資料夾）：python _research\esun_backtest_top3.py
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
B.OUT_DIR = os.path.join(HERE, "bt_out_top3")
os.makedirs(B.OUT_DIR, exist_ok=True)

IND = {}
for sid, v in B.SECTOR.items():
    IND.setdefault(v["name"], set()).add(sid)
CAPS = [(g, S.MAX_INDUSTRY_CORE) for g in IND.values()]
TOP_N = 3

GROUPS = {"BASE": "正式（對照）", "T1": "前三名通過一項就買", "T2": "前三名不過濾（參考）"}
B.POOLS = dict(GROUPS)
B.COMBOS = [(k, "CORE", "new_only") for k in GROUPS]

_orig_pool_table = B.pool_table
LOG = []                                   # (決策日, 組, 代號, 排名, 被放寬的原因)
_seen = set()


def pool_table(raw, pool, d=None):
    t = _orig_pool_table(raw, "NOSHIP", d)
    if t is None or not len(t):
        return t
    t = t.copy()
    t["_var"] = pool
    if pool in ("T1", "T2"):
        new = []
        for s, f, rk in zip(t.index, t["過濾"], t["排名"]):
            f = list(f or [])
            if f and rk <= TOP_N and (pool == "T2" or len(f) <= 2):
                key = (f"{d:%Y-%m-%d}", pool, s)
                if key not in _seen:
                    _seen.add(key)
                    LOG.append((key[0], pool, s, int(rk), "、".join(f)))
                f = []
            new.append(f)
        t["過濾"] = new
    return t


B.pool_table = pool_table

_orig_build = S.build_targets


def build_targets(d, prev=None, current_weights=None, mode="live"):
    S.EXTRA_GROUP_CAPS = CAPS                     # 正式設定：同產業核心最多 3 檔
    try:
        return _orig_build(d, prev, current_weights, mode)
    finally:
        S.EXTRA_GROUP_CAPS = []


S.build_targets = build_targets


def summary():
    p = os.path.join(B.OUT_DIR, "每月報酬.csv")
    if not os.path.exists(p):
        return
    lg = pd.DataFrame(LOG, columns=["決策日", "組", "代號", "排名", "被放寬的原因"])
    lg.to_csv(os.path.join(B.OUT_DIR, "放寬紀錄.csv"), encoding="utf-8-sig", index=False)
    m = pd.read_csv(p, index_col=0, encoding="utf-8-sig")
    base = B.label(B.COMBOS[0])
    print("\n" + "=" * 96 + "\n前三名放寬過濾：跟正式策略每月配對比較\n" + "=" * 96)
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
    for k in ("T1", "T2"):
        x = lg[lg["組"] == k]
        print(f"  {GROUPS[k]}：放寬 {len(x)} 次、{x['代號'].nunique()} 檔（明細在 放寬紀錄.csv）")
    for c in B.COMBOS[1:]:                         # 差最多的 5 個月，方便看是哪幾檔造成的
        dlt = (m[B.label(c)] - m[base]).sort_values()
        print(f"  {GROUPS[c[0]]} 比正式差最多：" + "、".join(f"{i} {v:+.2%}" for i, v in dlt.head(3).items())
              + "｜好最多：" + "、".join(f"{i} {v:+.2%}" for i, v in dlt.tail(3)[::-1].items()))
    print("  → 比正式為正且 t > 2 才考慮改；T1 跟 T2 差不多，代表效果來自放掉過濾，不是「通過一項」這個條件")


if __name__ == "__main__":
    B.main()
    summary()
