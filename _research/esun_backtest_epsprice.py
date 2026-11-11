# -*- coding: utf-8 -*-
r"""
esun_backtest_epsprice.py — 「EPS 增幅 ÷ 股價」的股價要不要平滑（2023-01 起，跟正式策略比）

  問題：EPS 增幅 = (最新一季 EPS − 去年同季 EPS) ÷ 選股日收盤價。季報和營收沒變時，
        排名只因為當天股價漲跌就上下移動，核心邊界的股票（例如 2026-11-02 台玻第 15、華邦電第 14，
        綜合分數只差 0.001）會因為一兩天的漲跌被換來換去
  BASE  正式（對照）：除以選股日收盤價
  P20   除以最近 20 個交易日的平均收盤價（短期漲跌的影響降到約 1/20）
  PQ    除以「這一季財報可以用的那天」的收盤價，整季固定不變（敏感度：股價完全不影響這一項）
  其他全部同正式設定：排除航運｜8×9.5%＋11×1%＋台積電 10%｜new_only｜記憶體、同產業核心各最多 3 檔
  股價來源跟正式回測一樣（有原始收盤價用原始價，沒有就用還原價），三組一致
客觀性：P20 在看結果之前定好（20 日 ≈ 一個月）；PQ 只用來看方向，不拿來挑
        採用標準 = P20 全期平均月報酬不低於正式、兩段子期間 t > −1、月換手比正式低
需要：bt_data 已補到 2022-06 起（esun_bt_fetch.py）；評分用 bt_cache，重跑很快
輸出：_research\bt_out_epsprice\  每月報酬.csv、每日淨值.csv、摘要.csv、回測明細.xlsx
用法（在 SMC- 資料夾）：python _research\esun_backtest_epsprice.py
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
B.OUT_DIR = os.path.join(HERE, "bt_out_epsprice")
os.makedirs(B.OUT_DIR, exist_ok=True)

IND = {}
for sid, v in B.SECTOR.items():
    IND.setdefault(v["name"], set()).add(sid)
CAPS = [(g, S.MAX_INDUSTRY_CORE) for g in IND.values()]

GROUPS = {"BASE": "正式（對照）", "P20": "股價用 20 日均價", "PQ": "股價用財報可用日（敏感度）"}
B.POOLS = dict(GROUPS)
B.COMBOS = [(k, "CORE", "new_only") for k in GROUPS]

# ---------- 股價序列（跟正式回測同一個來源：有原始價用原始價，沒有用還原價）----------
_close = {}


def close_series(sid):
    if sid not in _close:
        s = None
        p = os.path.join(B.RAW_DIR, f"{sid}.parquet")
        if os.path.exists(p):
            s = B._orig_read_parquet(p)["close"]
            s.index = pd.to_datetime(s.index)
        else:
            b = B._orig_read_px(sid)
            s = None if b is None else b["Close"]
        _close[sid] = None if s is None else s.dropna().sort_index()
    return _close[sid]


_avail = {}


def avail_date(sid, q):
    """這一季（YYYY-MM）財報的 available_date"""
    if sid not in _avail:
        p = os.path.join(S.EPS_DIR, f"{sid}.parquet")
        e = B._orig_read_parquet(p) if os.path.exists(p) else None
        _avail[sid] = {} if e is None else {f"{pd.Timestamp(i):%Y-%m}": pd.Timestamp(a)
                                             for i, a in zip(e.index, e["available_date"])}
    return _avail[sid].get(q)


def alt_price(sid, d, key, info):
    s = close_series(sid)
    if s is None:
        return None
    s = s.loc[:d]
    if not len(s):
        return None
    if key == "P20":
        return float(s.tail(20).mean())
    a = avail_date(sid, info.get("季"))
    if a is None:
        return None
    s2 = s.loc[:a]
    return float(s2.iat[-1]) if len(s2) else float(s.iat[0])


_orig_pool_table = B.pool_table
_d = [None]


def pool_table(raw, pool, d=None):
    if raw is None or raw.empty or pool == "BASE":
        t = _orig_pool_table(raw, "NOSHIP", d)
    else:
        r = raw.copy()
        new = []
        for sid, info, g in zip(r.index, r["EPS資料"], r["EPS增幅"]):
            if not isinstance(info, dict) or not (g == g):
                new.append(g); continue
            p = alt_price(sid, d, pool, info)
            new.append((info["EPS"] - info["去年同季"]) / p if p and p > 0 else g)
        r["EPS增幅"] = new
        t = _orig_pool_table(r, "NOSHIP", d)
    if t is not None and len(t):
        t = t.copy()
        t["_var"] = pool
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
    m = pd.read_csv(p, index_col=0, encoding="utf-8-sig")
    sp = os.path.join(B.OUT_DIR, "摘要.csv")
    sm = pd.read_csv(sp, encoding="utf-8-sig") if os.path.exists(sp) else None
    base = B.label(B.COMBOS[0])
    print("\n" + "=" * 96 + "\nEPS 增幅的股價平滑：跟正式策略每月配對比較\n" + "=" * 96)
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
    print("  → 採用標準：P20 全期比正式 ≥ 0、兩段子期間 t > −1、月換手比正式低")


if __name__ == "__main__":
    B.main()
    summary()
