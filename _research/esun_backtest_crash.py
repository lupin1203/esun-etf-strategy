# -*- coding: utf-8 -*-
r"""
esun_backtest_crash.py — 「爆量下跌：股價漲回下跌前價位也算消化」回測（2023-01 起，跟正式策略比）

  正式（BASE）：爆量下跌（跌 ≥3%、量 ≥ 20 日均量 2 倍，往回看 60 個交易日的最後一次）之後，
               要出現「上漲且量比爆量那天更大」的一天，才算賣壓消化
  新版（C1）  ：上面的條件，或「爆量下跌之後任何一天收盤價 ≥ 爆量下跌前一天的收盤價」，滿足一個就算消化
  其他全部同正式設定：排除航運｜8×9.5%＋11×1%＋台積電 10%｜new_only｜記憶體、同產業核心各最多 3 檔
客觀性：規則在看結果之前定好，跑完不調；只測一組；採用標準 = 全期 t > 2 且 2023–24、2025–26 兩段都不輸正式
需要：bt_data 已補到 2022-06 起（esun_bt_fetch.py）
輸出：_research\bt_out_crash\  每月報酬.csv、每日淨值.csv、摘要.csv、回測明細.xlsx、放行紀錄.csv
用法（在 SMC- 資料夾）：python _research\esun_backtest_crash.py
"""
import os, re, sys

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
B.OUT_DIR = os.path.join(HERE, "bt_out_crash")
os.makedirs(B.OUT_DIR, exist_ok=True)

IND = {}
for sid, v in B.SECTOR.items():
    IND.setdefault(v["name"], set()).add(sid)
CAPS = [(g, S.MAX_INDUSTRY_CORE) for g in IND.values()]

GROUPS = {"BASE": "正式（對照）", "C1": "漲回爆量前價位也算消化"}
B.POOLS = dict(GROUPS)
B.COMBOS = [(k, "CORE", "new_only") for k in GROUPS]
PAT = re.compile(r"^(\d{2})/(\d{2})爆量下跌賣壓未消化$")
LOG = []
_seen = set()


def recovered(sid, d, mmdd):
    """爆量下跌日（mm/dd，在 d 之前 60 個交易日內）之後，有沒有任何一天收盤 ≥ 爆量前一天收盤"""
    px = S._read_px(sid)
    if px is None:
        return False
    c = px["Close"].loc[:d].dropna()
    m, dd = int(mmdd[0]), int(mmdd[1])
    y = d.year if (m, dd) <= (d.month, d.day) else d.year - 1
    day = pd.Timestamp(y, m, dd)
    if day not in c.index:
        return False
    k = c.index.get_loc(day)
    if k == 0:
        return False
    pre = c.iloc[k - 1]
    after = c.iloc[k + 1:]
    return bool(len(after) and (after >= pre).any())


_orig_pool_table = B.pool_table


def pool_table(raw, pool, d=None):
    t = _orig_pool_table(raw, "NOSHIP", d)
    if t is None or not len(t):
        return t
    t = t.copy()
    t["_var"] = pool
    if pool == "C1":
        new = []
        for s, f, rk in zip(t.index, t["過濾"], t["排名"]):
            f = list(f or [])
            keep = []
            for x in f:
                mt = PAT.match(x)
                if mt and recovered(s, d, mt.groups()):
                    key = (f"{d:%Y-%m-%d}", s)
                    if key not in _seen:
                        _seen.add(key)
                        LOG.append((key[0], s, int(rk), x, "、".join(y for y in f if y != x)))
                    continue
                keep.append(x)
            new.append(keep)
        t["過濾"] = new
    return t


B.pool_table = pool_table
_orig_build = S.build_targets


def build_targets(d, prev=None, current_weights=None, mode="live"):
    S.EXTRA_GROUP_CAPS = CAPS
    try:
        return _orig_build(d, prev, current_weights, mode)
    finally:
        S.EXTRA_GROUP_CAPS = []


S.build_targets = build_targets


def summary():
    p = os.path.join(B.OUT_DIR, "每月報酬.csv")
    if not os.path.exists(p):
        return
    lg = pd.DataFrame(LOG, columns=["決策日", "代號", "排名", "放行的過濾", "仍未通過的其他過濾"])
    lg.to_csv(os.path.join(B.OUT_DIR, "放行紀錄.csv"), encoding="utf-8-sig", index=False)
    m = pd.read_csv(p, index_col=0, encoding="utf-8-sig")
    base, c1 = B.label(B.COMBOS[0]), B.label(B.COMBOS[1])
    print("\n" + "=" * 96 + "\n漲回爆量前價位也算消化：跟正式策略每月配對比較\n" + "=" * 96)
    print(f"  {'期間':<12}{'正式':>9}{'新版':>9}{'差':>9}{'t值':>7}{'贏/輸月數':>11}")
    res = {}
    for a, b, lab in [(None, None, "全部"), ("2023-01", "2024-12", "2023–24"), ("2025-01", None, "2025–26")]:
        mm = m.loc[a:b] if a else m
        dlt = mm[c1] - mm[base]
        t = dlt.mean() / (dlt.std() / np.sqrt(len(dlt))) if dlt.std() > 0 else np.nan
        res[lab] = (dlt.mean(), t)
        print(f"  {lab:<12}{mm[base].mean():>9.2%}{mm[c1].mean():>9.2%}{dlt.mean():>+9.2%}{t:>7.2f}"
              f"{f'{int((dlt > 0).sum())}/{int((dlt < 0).sum())}':>11}")
    only = lg[lg["仍未通過的其他過濾"] == ""]
    print(f"\n  放行 {len(lg)} 次（{lg['代號'].nunique()} 檔）；其中其他過濾也都通過、真的變成可以買的 {len(only)} 次")
    dlt = (m[c1] - m[base]).sort_values()
    print("  比正式差最多：" + "、".join(f"{i} {v:+.2%}" for i, v in dlt.head(3).items())
          + "｜好最多：" + "、".join(f"{i} {v:+.2%}" for i, v in dlt.tail(3)[::-1].items()))
    ok = res["全部"][1] > 2 and min(res["2023–24"][0], res["2025–26"][0]) >= 0
    print(f"\n  事先定好的採用標準（全期 t > 2，且兩段都不輸正式）→ {'通過' if ok else '不通過'}")
    print(f"  明細：{os.path.join(B.OUT_DIR, '放行紀錄.csv')}")


if __name__ == "__main__":
    B.main()
    summary()
