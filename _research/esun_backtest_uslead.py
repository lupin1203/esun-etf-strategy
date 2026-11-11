# -*- coding: utf-8 -*-
r"""
esun_backtest_uslead.py — 「美股 AI 領先台股」回測（2023-01 起，跟正式策略比）

  想法：客戶（美國 AI／半導體）股價先動，供應商（台灣 AI 供應鏈）慢一步跟上
  訊號：SOXX（費城半導體 ETF）10 日報酬 − SPY（S&P 500）10 日報酬
        連續 3 個美股交易日 > 0 → 開啟；開啟後連續 3 個美股交易日 < 0 才關閉
  時間：台股決策日 d 只用「美股日期 < d」的收盤（d 晚上 19:30 前美股還沒開盤，保守做法）
  開啟時：台灣 AI 相關產業（半導體、電子零組件、電腦及週邊設備、通信網路）的股票排前面，
          組內順序＝原排名；湊不滿 8 核心＋11 小部位時，用其他產業依原排名補
  關閉時：完全照正式策略
  其他全部同正式設定：排除航運｜8×9.5%＋11×1%＋台積電 10%｜new_only｜記憶體、同產業核心各最多 3 檔
客觀性：規則與參數（SOXX、SPY、10 日、3 天、四個產業）在看結果之前定好，跑完不調；只測一組；
        採用標準 = 全期 t > 2 且 2023–24、2025–26 兩段都不輸正式
資料：美股從 FinMind USStockPrice 抓（token 讀 run_daily.bat），存在 _research\bt_data\us\，已經有就不重抓（--us-fresh 重抓）
輸出：_research\bt_out_uslead\  每月報酬.csv、每日淨值.csv、摘要.csv、回測明細.xlsx、美股訊號.csv
用法（在 SMC- 資料夾）：python _research\esun_backtest_uslead.py
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
US_FRESH = "--us-fresh" in sys.argv
sys.argv = [a for a in sys.argv if a != "--us-fresh"]
if not any(a.startswith("--start") for a in sys.argv[1:]):
    sys.argv += ["--start", "2023-01-01"]

import esun_backtest as B
import esun_compare_active as C                 # token()、get()
import numpy as np, pandas as pd

S = B.S
if not hasattr(S, "EXTRA_GROUP_CAPS"):
    raise SystemExit("esun_strategy.py 還是舊版（沒有 EXTRA_GROUP_CAPS）")
B.PX_FROM = "2022-03-01"
B.tdcc_hist = lambda: None
B.OUT_DIR = os.path.join(HERE, "bt_out_uslead")
os.makedirs(B.OUT_DIR, exist_ok=True)
US_DIR = os.path.join(HERE, "bt_data", "us")
os.makedirs(US_DIR, exist_ok=True)

LEAD, BENCH, LOOKBACK, HOLD_DAYS = "SOXX", "SPY", 10, 3
AI_IND = {"半導體業", "電子零組件業", "電腦及週邊設備業", "通信網路業"}
AI_MONTHS = ["2026-02", "2026-04", "2026-08", "2026-09"]

IND = {}
for sid, v in B.SECTOR.items():
    IND.setdefault(v["name"], set()).add(sid)
CAPS = [(g, S.MAX_INDUSTRY_CORE) for g in IND.values()]

GROUPS = {"BASE": "正式（對照）", "US": "美股領先：SOXX強過SPY"}
B.POOLS = dict(GROUPS)
B.COMBOS = [(k, "CORE", "new_only") for k in GROUPS]


# ---------- 美股資料 ----------
def us_close(sym):
    p = os.path.join(US_DIR, f"{sym}.csv")
    if US_FRESH or not os.path.exists(p):
        df = C.get("USStockPrice", sym, "2022-01-01", C.token())
        if df.empty:
            raise SystemExit(f"抓不到美股 {sym}（FinMind USStockPrice）；檢查 token 或網路")
        cols = {c.lower(): c for c in df.columns}
        col = cols.get("adj_close") or cols.get("close")
        s = pd.Series(df[col].astype(float).values, index=pd.to_datetime(df[cols["date"]]), name=sym).sort_index()
        s = s[s > 0]
        s.to_csv(p, header=True, encoding="utf-8-sig")
        print(f"美股 {sym}：{s.index[0]:%Y-%m-%d} 到 {s.index[-1]:%Y-%m-%d}，{len(s)} 天（{col}）")
    s = pd.read_csv(p, index_col=0, parse_dates=True, encoding="utf-8-sig").iloc[:, 0]
    return s.sort_index()


lead, bench = us_close(LEAD), us_close(BENCH)
px = pd.concat([lead, bench], axis=1, keys=[LEAD, BENCH]).dropna()
spread = (px[LEAD] / px[LEAD].shift(LOOKBACK) - 1) - (px[BENCH] / px[BENCH].shift(LOOKBACK) - 1)
state, on, run_pos, run_neg = {}, False, 0, 0
for d, v in spread.dropna().items():
    run_pos, run_neg = (run_pos + 1, 0) if v > 0 else (0, run_neg + 1) if v < 0 else (0, 0)
    if not on and run_pos >= HOLD_DAYS:
        on = True
    elif on and run_neg >= HOLD_DAYS:
        on = False
    state[d] = (on, float(v))
SIG = pd.DataFrame(state, index=["開啟", "SOXX減SPY"]).T
SIG.index.name = "美股日期"
SIG.to_csv(os.path.join(B.OUT_DIR, "美股訊號.csv"), encoding="utf-8-sig")


def signal_on(d):
    x = SIG[SIG.index < pd.Timestamp(d)]              # 只用台股決策日之前的美股收盤
    return (bool(x["開啟"].iat[-1]), x.index[-1]) if len(x) else (False, None)


_orig_pool_table = B.pool_table
LOG = {}


def pool_table(raw, pool, d=None):
    t = _orig_pool_table(raw, "NOSHIP", d)
    if t is None or not len(t):
        return t
    t = t.copy()
    t["_var"] = pool
    if pool == "BASE":
        return t
    on, usd = signal_on(d)
    LOG[f"{d:%Y-%m-%d}"] = (on, f"{usd:%Y-%m-%d}" if usd is not None else "")
    if not on:
        return t
    inside = t["產業"].isin(AI_IND)
    t = pd.concat([t[inside], t[~inside]])            # AI 產業先，組內順序＝原排名
    t["排名"] = range(1, len(t) + 1)
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
    lg = pd.DataFrame([(d, on, u) for d, (on, u) in sorted(LOG.items())], columns=["決策日", "開啟", "用到的美股日期"])
    lg.to_csv(os.path.join(B.OUT_DIR, "每日開關.csv"), encoding="utf-8-sig", index=False)
    m = pd.read_csv(p, index_col=0, encoding="utf-8-sig")
    base, us = B.label(B.COMBOS[0]), B.label(B.COMBOS[1])
    print("\n" + "=" * 96 + "\n美股領先：跟正式策略每月配對比較\n" + "=" * 96)
    print(f"  {'期間':<12}{'正式':>9}{'美股領先':>10}{'差':>9}{'t值':>7}{'贏/輸月數':>11}")
    res = {}
    for a, b, lab in [(None, None, "全部"), ("2023-01", "2024-12", "2023–24"), ("2025-01", None, "2025–26")]:
        mm = m.loc[a:b] if a else m
        dlt = mm[us] - mm[base]
        t = dlt.mean() / (dlt.std() / np.sqrt(len(dlt))) if dlt.std() > 0 else np.nan
        res[lab] = (dlt.mean(), t)
        print(f"  {lab:<12}{mm[base].mean():>9.2%}{mm[us].mean():>10.2%}{dlt.mean():>+9.2%}{t:>7.2f}"
              f"{f'{int((dlt > 0).sum())}/{int((dlt < 0).sum())}':>11}")
    lg["月"] = lg["決策日"].str[:7]
    on_m = lg.groupby("月")["開啟"].mean()
    print(f"\n  開啟的交易日比例 {lg['開啟'].mean():.0%}；開關切換 {int((lg['開啟'] != lg['開啟'].shift()).sum()) - 1} 次")
    print("  AI 強勢月份（事後挑的，只當參考）：")
    for x in [x for x in AI_MONTHS if x in m.index]:
        print(f"    {x}  開啟 {on_m.get(x, 0):>4.0%}  正式 {m.loc[x, base]:>+6.1%}  美股領先 {m.loc[x, us]:>+6.1%}")
    dlt = (m[us] - m[base]).sort_values()
    print("  比正式差最多：" + "、".join(f"{i} {v:+.2%}" for i, v in dlt.head(3).items())
          + "｜好最多：" + "、".join(f"{i} {v:+.2%}" for i, v in dlt.tail(3)[::-1].items()))
    ok = res["全部"][1] > 2 and min(res["2023–24"][0], res["2025–26"][0]) >= 0
    print(f"\n  事先定好的採用標準（全期 t > 2，且兩段都不輸正式）→ {'通過' if ok else '不通過'}")
    print(f"  明細：{os.path.join(B.OUT_DIR, '美股訊號.csv')}、每日開關.csv")


if __name__ == "__main__":
    B.main()
    summary()
