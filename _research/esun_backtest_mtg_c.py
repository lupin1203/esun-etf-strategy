# -*- coding: utf-8 -*-
r"""
esun_backtest_mtg_c.py — 新方法 C 版：EPS 改成 (本季 EPS − 去年同季 EPS) ÷ 上一季 EPS

  其他全部跟 esun_backtest_mtg.py 的新方法一樣（營收驚喜＋MoM 加分、成長 >10% 項數、
  拿掉大戶、新股票要比第 17 名好才換、過濾／防守／記憶體上限沿用第 9 組）
  · 上一季 EPS 用絕對值當分母（上一季虧損時，分數方向才不會顛倒），截斷在 −100%～+200%
  · 上一季 EPS = 0：本季比去年同季好給 +200%、差給 −100%
  · 上一季必須跟本季連續（相隔 ≤100 天），不然這一項空白
對照：第 9 組、新方法 B（上一次跑過最好的新方法）
輸出：_research\bt_out_mtg_c\
用法（在 SMC- 資料夾）：python _research\esun_backtest_mtg_c.py
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import esun_backtest_mtg as M
import numpy as np, pandas as pd

B, S = M.B, M.S
B.POOLS = {"NOSHIP": B.POOLS["NOSHIP"], "MTG_B": B.POOLS["MTG_B"],
           "MTG_C": "新方法C（EPS÷上一季EPS）"}
B.COMBOS = [("NOSHIP", "CORE", "new_only"), ("MTG_B", "CORE", "new_only"), ("MTG_C", "CORE", "new_only")]
B.OUT_DIR = os.path.join(HERE, "bt_out_mtg_c")

_prev_memo = {}


def prev_quarter_eps(sid, d, mode, q_label):
    key = (sid, d, mode)
    if key not in _prev_memo:
        e = M._eps(sid, d, mode)
        v = np.nan
        if e is not None and len(e) >= 2 and q_label and f"{e.index[-1]:%Y-%m}" == q_label \
                and (e.index[-1] - e.index[-2]).days <= 100:
            v = float(e["eps"].iat[-2])
        _prev_memo[key] = v
    return _prev_memo[key]


def eps_rate_c(t, d, mode):
    info = t["EPS資料"].apply(lambda x: x if isinstance(x, dict) else {})
    now = info.apply(lambda x: x.get("EPS", np.nan)).astype(float)
    base = info.apply(lambda x: x.get("去年同季", np.nan)).astype(float)
    prev = pd.Series({s: prev_quarter_eps(s, d, mode, info[s].get("季")) for s in t.index}, dtype=float)
    g = ((now - base) / prev.abs()).clip(*M.RATE_CAP)
    zero = np.where(now > base, M.RATE_CAP[1], np.where(now < base, M.RATE_CAP[0], 0.0))
    g = g.where(prev != 0, pd.Series(zero, index=t.index))
    return g.where(now.notna() & base.notna() & prev.notna())


def mtg_c_table(raw, d, mode="deadline"):
    t = M.mtg_table(raw, d, mode, absolute=True)           # 先用 B 版算好營收、成長項數
    t["I2_EPS成長率"] = eps_rate_c(t, d, mode)
    ranks = pd.concat([t[c].astype(float).rank(method="min", pct=True)
                       for c in ("I1_營收分數", "I2_EPS成長率", "I3_成長項數")], axis=1)
    t["綜合分數"] = ranks.mean(axis=1, skipna=True)
    t = t[t["綜合分數"].notna()].sort_values("綜合分數", ascending=False)
    t["排名"] = t["綜合分數"].rank(method="min", ascending=False).astype(int)
    return t


_mtg_pool_table = M.pool_table


def pool_table(raw, pool, d=None):
    if pool == "MTG_C" and raw is not None and not raw.empty:
        if len(_prev_memo) > 20000:
            _prev_memo.clear()
        return mtg_c_table(raw, d, "deadline")
    return _mtg_pool_table(raw, pool, d)


B.pool_table = pool_table


def latest_c():
    """最新一天 C 版排名前 25（對照 B 版、第 9 組）"""
    d = pd.Timestamp.today().normalize()
    S.AI_CODES = B.ALL_CODES
    S._read_px = B._orig_read_px
    B.INST.OUT_DIR = M._INST_DIR_LIVE
    B.TDCC.history = M._TDCC_LIVE
    raw = B._orig_score_pool(d, mode="live")
    names = B.stock_names()
    c = mtg_c_table(raw, d, "live")
    b = M.mtg_table(raw, d, "live", absolute=True)
    g9 = M._orig_pool_table(raw, "NOSHIP")
    rows = []
    for s in c.index:
        info = c.loc[s, "EPS資料"] if isinstance(c.loc[s, "EPS資料"], dict) else {}
        rows.append({"代號": s, "名稱": names.get(s, ""), "產業": c.loc[s, "產業"], "C版排名": int(c.loc[s, "排名"]),
                     "B版排名": int(b.loc[s, "排名"]) if s in b.index else None,
                     "第9組排名": int(g9.loc[s, "排名"]) if s in g9.index else None,
                     "本季EPS": info.get("EPS"), "去年同季": info.get("去年同季"),
                     "上一季": prev_quarter_eps(s, d, "live", info.get("季")),
                     "C版EPS分數(%)": None if pd.isna(c.loc[s, "I2_EPS成長率"]) else round(float(c.loc[s, "I2_EPS成長率"]) * 100, 1),
                     "過濾": "、".join(c.loc[s, "過濾"])})
    df = pd.DataFrame(rows)
    os.makedirs(B.OUT_DIR, exist_ok=True)
    path = B.out_path("C版排名_最新.xlsx")
    df.to_excel(path, index=False)
    print(f"\n最新一天 C 版排名（全部 {len(df)} 檔已存 {path}）：")
    print(df.head(25).to_string(index=False))


if __name__ == "__main__":
    B.main()
    M.paired()
    try:
        latest_c()
    except Exception as e:
        print(f"\n! 最新排名清單失敗：{type(e).__name__}：{e}")
