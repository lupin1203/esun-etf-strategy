# -*- coding: utf-8 -*-
r"""
esun_backtest_rotation.py — 產業輪動回測（2023-01 起，跟正式策略比）

  目的：AI 股資金回來（或流去別的產業）時，先抓到資金往哪個產業走，再在那些產業裡選股
  產業：主辦方分類（esun_sector.json），排除航運、成分股不到 3 檔的產業
  每天收盤後依「當天各產業的漲跌」評分：產業內各股當日漲跌幅的平均（等權），取前四名
  轉換：連續 3 個交易日都在前四名的產業 =「確認產業」，選股改成先在確認產業裡挑；
        3 天都在前四名的產業一個都沒有時，沿用上一批確認產業；一開始還沒有確認產業時照正式策略
  選股：照舊（營收驚喜、EPS 增幅÷股價、三率排名，過濾、記憶體與同產業核心各最多 3 檔都一樣），
        只是確認產業的股票排在前面；湊不滿 8 核心＋11 小部位時，用其他產業依原排名補（避免現金超過 25%）
  其他全部同正式設定：排除航運｜8×9.5%＋11×1%＋台積電 10%｜new_only
客觀性：參數（當日漲跌、前四名、3 天、至少 3 檔）在看結果之前定好，跑完不調；只測一組，不挑組別；
        訊號只用決策日收盤以前的資料、隔天成交；採用標準 = 全期 t > 2 且兩段子期間都不輸正式
需要：bt_data 已補到 2022-06 起（esun_bt_fetch.py）
輸出：_research\bt_out_rotation\  每月報酬.csv、每日淨值.csv、摘要.csv、回測明細.xlsx、確認產業.csv
用法（在 SMC- 資料夾）：python _research\esun_backtest_rotation.py
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
B.OUT_DIR = os.path.join(HERE, "bt_out_rotation")
os.makedirs(B.OUT_DIR, exist_ok=True)

TOP_K, HOLD_DAYS, MIN_MEMBERS = 4, 3, 3
AI_IND = {"半導體業", "電子零組件業", "電腦及週邊設備業", "通信網路業"}
AI_MONTHS = ["2026-02", "2026-04", "2026-08", "2026-09"]

IND = {}
for sid, v in B.SECTOR.items():
    IND.setdefault(v["name"], set()).add(sid)
CAPS = [(g, S.MAX_INDUSTRY_CORE) for g in IND.values()]

GROUPS = {"BASE": "正式（對照）", "D1": "輪動：當日漲跌前四"}
B.POOLS = dict(GROUPS)
B.COMBOS = [(k, "CORE", "new_only") for k in GROUPS]


# ---------- 產業排名與確認產業（只用當天收盤以前的資料） ----------
def industry_signals():
    excl = {"航運業"} | set(S.EXCLUDE_IND)          # esun_backtest 會清空 EXCLUDE_IND（航運改在選股池排除），這裡直接寫死
    members = {n: sorted(g) for n, g in IND.items() if n not in excl and not (set(g) & B.SHIP)}
    close, amt = {}, {}
    for n, g in members.items():
        for s in g:
            b = B._orig_read_px(s)
            if b is None or "Close" not in b:
                continue
            b = b.loc["2022-01-01":]
            close[s] = b["Close"]
            if "Volume" in b:
                amt[s] = b["Close"] * b["Volume"]
    close = pd.DataFrame(close).sort_index()
    amt = pd.DataFrame(amt).reindex(close.index)
    members = {n: [s for s in g if s in close] for n, g in members.items()}
    members = {n: g for n, g in members.items() if len(g) >= MIN_MEMBERS}
    stock = {"D1": close / close.shift(1) - 1}      # 當日漲跌幅
    out = {}
    for k, m in stock.items():
        ind = pd.DataFrame({n: m[g].mean(axis=1, skipna=True) for n, g in members.items()})
        cnt = pd.DataFrame({n: m[g].notna().sum(axis=1) for n, g in members.items()})
        ind = ind.where(cnt >= MIN_MEMBERS)
        top = [set(r.dropna().nlargest(TOP_K).index) for _, r in ind.iterrows()]
        active, cur = {}, None
        for i, d in enumerate(ind.index):
            if i >= HOLD_DAYS - 1:
                both = set.intersection(*top[i - HOLD_DAYS + 1:i + 1])
                if both:
                    cur = both
            active[d] = (cur, top[i])
        out[k] = active
    return out, members


SIG, MEMBERS = industry_signals()
print("產業輪動：" + "、".join(f"{n}（{len(g)}）" for n, g in sorted(MEMBERS.items(), key=lambda x: -len(x[1]))))


def active_on(key, d):
    a = SIG[key]
    idx = [x for x in a if x <= d]
    return a[idx[-1]] if idx else (None, set())


_orig_pool_table = B.pool_table
LOG = {}                                    # (日期, 組) → 確認產業、當天前四


def pool_table(raw, pool, d=None):
    t = _orig_pool_table(raw, "NOSHIP", d)
    if t is None or not len(t):
        return t
    t = t.copy()
    t["_var"] = pool
    if pool == "BASE":
        return t
    act, top = active_on(pool, d)
    LOG[(f"{d:%Y-%m-%d}", pool)] = ("、".join(sorted(act)) if act else "（尚未確認，照正式）", "、".join(sorted(top)))
    if not act:
        return t
    inside = t["產業"].isin(act)
    t = pd.concat([t[inside], t[~inside]])         # 確認產業先，組內順序＝原排名
    t["排名"] = range(1, len(t) + 1)
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
    lg = pd.DataFrame([(d, k, a, t) for (d, k), (a, t) in sorted(LOG.items())],
                      columns=["決策日", "組", "確認產業", "當天前四"])
    lg.to_csv(os.path.join(B.OUT_DIR, "確認產業.csv"), encoding="utf-8-sig", index=False)
    m = pd.read_csv(p, index_col=0, encoding="utf-8-sig")
    base = B.label(B.COMBOS[0])
    print("\n" + "=" * 96 + "\n產業輪動：跟正式策略每月配對比較\n" + "=" * 96)
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
    ai = [x for x in AI_MONTHS if x in m.index]
    print("  AI 強勢月份（" + "、".join(ai) + "）：")
    for c in B.COMBOS:
        col = B.label(c)
        print(f"    {GROUPS[c[0]]:<22}" + "".join(f"{m.loc[x, col]:>8.1%}" for x in ai) + f"   平均 {m.loc[ai, col].mean():+.2%}")
    print()
    for k in list(GROUPS)[1:]:
        x = lg[lg["組"] == k].copy()
        x["月"] = x["決策日"].str[:7]
        x["含AI"] = x["確認產業"].apply(lambda s: any(n in s for n in AI_IND))
        sw = int((x["確認產業"] != x["確認產業"].shift()).sum()) - 1
        print(f"  {GROUPS[k]}：確認產業換了 {sw} 次；確認產業裡有 AI 相關產業的天數 {x['含AI'].mean():.0%}")
        mo = x.groupby("月")["確認產業"].agg(lambda s: s.value_counts().index[0])
        print("    AI 強勢月份最常見的確認產業：" + "｜".join(f"{i} {mo.get(i, '—')}" for i in ai))
    for c in B.COMBOS[1:]:
        dlt = (m[B.label(c)] - m[base]).sort_values()
        print(f"  {GROUPS[c[0]]} 比正式差最多：" + "、".join(f"{i} {v:+.2%}" for i, v in dlt.head(3).items())
              + "｜好最多：" + "、".join(f"{i} {v:+.2%}" for i, v in dlt.tail(3)[::-1].items()))
    # 判斷標準在看結果之前就定好，跑完不能再改參數或挑組別
    thr = {1: 2.0, 2: 2.24, 3: 2.4}.get(len(B.COMBOS) - 1, 2.5)       # 多組同時測時用 Bonferroni 提高門檻
    print(f"\n  事先定好的採用標準（全期 t > {thr}，且兩段子期間都不輸正式）：")
    for c in B.COMBOS[1:]:
        col = B.label(c)
        d_all = m[col] - m[base]
        t_all = d_all.mean() / (d_all.std() / np.sqrt(len(d_all))) if d_all.std() > 0 else np.nan
        sub = [(m.loc[a:b, col] - m.loc[a:b, base]).mean() for a, b in (("2023-01", "2024-12"), ("2025-01", None))]
        ok = t_all > thr and min(sub) >= 0
        print(f"    {GROUPS[c[0]]:<22} 全期 t {t_all:+.2f}｜2023–24 {sub[0]:+.2%}｜2025–26 {sub[1]:+.2%} → {'通過' if ok else '不通過'}")
    print("  · AI 強勢月份是事後挑出來的，只能當參考，不能單獨拿來決定")
    print(f"  確認產業逐日明細：{os.path.join(B.OUT_DIR, '確認產業.csv')}")


if __name__ == "__main__":
    B.main()
    summary()
