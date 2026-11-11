# -*- coding: utf-8 -*-
r"""
esun_backtest_eps.py — EPS 因子改法回測（其他全部同正式設定：排除航運｜8×9.5%＋11×1%＋台積電 10%｜new_only｜同產業核心最多 3 檔）

  E0  正式：EPS 增幅 =（本季 EPS − 去年同季）÷ 股價
  E1  成長率：（本季 − 去年同季）÷ |去年同季|，截在 −100%～+200%；去年同季 ≤ 0 給最低分
      （之前第 14 組測過、當時 −0.25%，這次加上產業上限重測）
  E2  絕對增加金額：本季 − 去年同季（元），不除任何東西 → 偏向高價股，看能不能抓到 AI 龍頭
  E3  拿掉 EPS：只用營收驚喜＋三率兩項
  · 另外統計：AI 強勢 4 個月（2026-02、04、08、09）的表現，以及核心裡平均有幾檔 AI 龍頭
    （聯發科、廣達、緯穎、台光電、智邦、緯創、鴻海、奇鋐、技嘉、英業達、川湖、健策，只算在 150 檔內的）
輸出：_research\bt_out_eps\
用法（在 SMC- 資料夾）：python _research\esun_backtest_eps.py
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
AI_LEADERS = {"2454", "2382", "6669", "2383", "2345", "3231", "2317", "3017", "2376", "2356", "2059", "3653"}
AI_MONTHS = ["2026-02", "2026-04", "2026-08", "2026-09"]

GROUPS = {"E0": "正式：EPS增幅÷股價", "E1": "EPS成長率（÷去年同季）",
          "E2": "EPS增加金額（不除）", "E3": "拿掉EPS（營收驚喜＋三率）"}
B.POOLS = dict(GROUPS)
B.COMBOS = [(k, "CORE", "new_only") for k in GROUPS]
B.OUT_DIR = os.path.join(HERE, "bt_out_eps")
B.tdcc_hist = lambda: None                            # 正式評分不含大戶

_orig_pool_table = B.pool_table


def pool_table(raw, pool, d=None):
    if raw is None or raw.empty:
        return raw
    r = raw.copy()
    if pool != "E0":
        info = r["EPS資料"].apply(lambda x: x if isinstance(x, dict) else {})
        now = info.apply(lambda x: x.get("EPS", np.nan)).astype(float)
        base = info.apply(lambda x: x.get("去年同季", np.nan)).astype(float)
        if pool == "E1":
            g = ((now - base) / base.abs()).clip(*B.RATE_CAP)
            r["EPS增幅"] = g.where(base > 0, -np.inf).where(g.notna() | (base <= 0))
        elif pool == "E2":
            r["EPS增幅"] = now - base
        elif pool == "E3":
            r["EPS增幅"] = np.nan
    t = _orig_pool_table(r, "NOSHIP", d)
    if t is not None and len(t):
        t = t.copy()
        t["_var"] = pool
    return t


B.pool_table = pool_table

_orig_build = S.build_targets
_ai_count = {k: [] for k in GROUPS}


def build_targets(d, prev=None, current_weights=None, mode="live"):
    t = S.score_pool(d, mode)
    key = t["_var"].iat[0] if t is not None and len(t) and "_var" in t.columns else "E0"
    S.EXTRA_GROUP_CAPS = CAPS
    try:
        tg, tab = _orig_build(d, prev, current_weights, mode)
    finally:
        S.EXTRA_GROUP_CAPS = []
    _ai_count[key].append(sum(1 for s in tg.get("core", []) if s in AI_LEADERS))
    return tg, tab


S.build_targets = build_targets


def summary():
    p = os.path.join(B.OUT_DIR, "每月報酬.csv")
    if not os.path.exists(p):
        return
    m = pd.read_csv(p, index_col=0, encoding="utf-8-sig")
    base = B.label(B.COMBOS[0])
    ai = [x for x in AI_MONTHS if x in m.index]
    other = [x for x in m.index if x not in ai]
    print("\n" + "=" * 104 + "\nEPS 因子：跟正式（E0）每月配對比較\n" + "=" * 104)
    print(f"  {'組別':<26}{'平均月報酬':>10}{'比正式':>9}{'t值':>7}{'贏的月數':>9}"
          f"{'AI強勢4個月':>12}{'其他月份':>10}{'最差月':>9}{'核心AI龍頭':>11}")
    for c in B.COMBOS:
        col = B.label(c)
        dlt = m[col] - m[base]
        t = dlt.mean() / (dlt.std() / np.sqrt(len(dlt))) if dlt.std() > 0 else np.nan
        n_ai = np.mean(_ai_count[c[0]]) if _ai_count[c[0]] else np.nan
        mid = (f"{'':>25}" if c == B.COMBOS[0] else
               f"{dlt.mean():>+9.2%}{t:>7.2f}{f'{int((dlt > 0).sum())}/{len(dlt)}':>9}")
        print(f"  {GROUPS[c[0]]:<26}{m[col].mean():>10.2%}" + mid +
              f"{m.loc[ai, col].mean():>12.2%}{m.loc[other, col].mean():>10.2%}{m[col].min():>9.2%}{n_ai:>9.2f} 檔")
    print(f"\n  AI 強勢月份：{'、'.join(ai)}；0050 平均 {m.loc[ai].iloc[:, -1].mean():+.2%}")
    print("  → 比正式為正且 t > 2 才考慮採用；只在 AI 月份變好、其他月份變差很多的，等於押注行情")


if __name__ == "__main__":
    B.main()
    summary()
