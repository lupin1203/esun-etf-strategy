# -*- coding: utf-8 -*-
r"""
esun_backtest_mtg.py — 10/13 開會新方法回測（跟第 9 組同樣 22 個月、同一套下單／帳本程式）

新方法（三項排名平均，拿掉大戶）：
  I1 營收：原本的營收驚喜（最新 YoY − 前 3 個月 YoY 平均）在選股池內的百分位
           ＋ 月增率（MoM）≥ 10% 的加分 MOM_BONUS（百分位 +0.20，約等於往前跳 30 名）
           前 3 個月 YoY 只要有 2 個月算得出來就用（金控常有某個月 YoY 算不出來，原本整項變空白）
  I2 EPS：(本季 EPS − 去年同季 EPS) ÷ 去年同季 EPS，截斷在 −100%～+200%
           A 版：去年同季 ≤ 0（虧轉盈）這項給最後一名（＝第 14 組的做法）
           B 版：去年同季 ≤ 0 用絕對值當分母（虧轉盈算大成長，多半被截斷在 +200%）
  I3 成長：營收、毛利、稅後淨利「跟上一期比」成長 > 10% 的項數（0–3）
           · 營收：最新月營收 vs 上個月（月資料）
           · 毛利、稅後淨利：只有季報，所以是最新一季 vs 上一季
             （季營收 = 該季 3 個月營收加總；毛利 = 毛利率 × 季營收；淨利 = 淨利率 × 季營收）
           · 金融股沒有毛利：用有資料的項目換算成 0–3（2 項都 >10% = 3 分）；淨利率沒有就用 EPS 季增代替
           · 上一期 ≤ 0：本期 > 0 才算「成長 >10%」
           · 同分就同名次（1、1、3…），三項排名用 min 名次
  綜合 = I1、I2、I3 百分位平均（缺的項目略過），取前 19 檔＋台積電 = 20 檔，第 20 名不買
  權重、台積電、過濾（均線／爆量／法人，只擋新買進）、防守模式、記憶體核心最多 3 檔：全部沿用第 9 組
  換股門檻：已持有的不因排名下滑而賣；新股票排名要比第 17 名好（第 1–16 名）才能進來，
            進來時換掉目前持股裡排名最差的那檔
  核心 8 檔：持股中排名最好的 8 檔（原核心排名仍在前 12 保留，同第 9 組）；其餘 11 檔小部位
對照組：第 9 組（同一次執行一起跑，月報酬可直接配對比較）
輸出：_research\bt_out_mtg\（不會蓋掉原本 bt_out 的 16 組結果）
      _research\bt_out_mtg\選股池排名_最新.xlsx：150 檔日均量、是否 ≥500 張、新方法排名（第 10 題）
用法（在 SMC- 資料夾）：python _research\esun_backtest_mtg.py
"""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import esun_backtest as B          # 會切到 SMC- 資料夾、裝好快取與原始價 EPS
import numpy as np, pandas as pd

S = B.S
MOM_BONUS, MOM_HOT = 0.20, 0.10
GROW_HOT = 0.10
ENTRY_RANK = 17                    # 新股票要比第 17 名好（名次 < 17）才能進來
RATE_CAP = (-1.0, 2.0)

B.POOLS = {"NOSHIP": B.POOLS["NOSHIP"], "MTG_A": "新方法A（虧轉盈EPS給最低）", "MTG_B": "新方法B（虧轉盈EPS用絕對值）"}
_TDCC_LIVE = B.TDCC.history
_INST_DIR_LIVE = B.INST.OUT_DIR          # 回測時會改成 bt_data\inst，第 10 題的最新清單要換回來
B.COMBOS = [("NOSHIP", "CORE", "new_only"), ("MTG_A", "CORE", "new_only"), ("MTG_B", "CORE", "new_only")]
B.OUT_DIR = os.path.join(HERE, "bt_out_mtg")


# ---------------- 新因子 ----------------
def _rev(sid, d, mode):
    p = os.path.join(S.REV_DIR, f"{sid}.parquet")
    if not os.path.exists(p):
        return None
    h = pd.read_parquet(p).sort_index()
    avail = h["pub_month"] if mode == "live" else h["available_date"]
    return h[pd.to_datetime(avail) <= d]


def _eps(sid, d, mode):
    p = os.path.join(S.EPS_DIR, f"{sid}.parquet")
    if not os.path.exists(p):
        return None
    e = pd.read_parquet(p).sort_index()
    return e[pd.to_datetime(e["available_date"]) <= d] if mode == "deadline" else e[e.index <= d]


def _hot(now, prev):
    if pd.isna(now) or pd.isna(prev):
        return None
    if prev > 0:
        return bool(now / prev - 1 > GROW_HOT)
    return bool(now > 0)


def rev_factors(sid, d, mode):
    """回傳 (營收驚喜(放寬), MoM)"""
    h = _rev(sid, d, mode)
    if h is None or len(h) < 2:
        return np.nan, np.nan
    last = h.index[-1]
    r = h["revenue"].astype(float)
    pm = last - pd.DateOffset(months=1)
    mom = r[last] / r[pm] - 1 if pm in r.index and r[pm] > 0 and r[last] > 0 else np.nan
    y = h["yoy"].astype(float).clip(-S.YOY_CAP, S.YOY_CAP)
    prev = [last - pd.DateOffset(months=k) for k in (1, 2, 3)]
    pv = [y[m] for m in prev if m in y.index and pd.notna(y[m])]
    sur = float(y[last] - np.mean(pv)) if pd.notna(y.get(last)) and len(pv) >= 2 else np.nan
    return sur, mom


def growth_count(sid, d, mode, mom):
    """營收（月）、毛利、稅後淨利（季）成長 > 10% 的項數，換算成 0–3"""
    items = {"營收": _hot(1 + mom, 1.0) if pd.notna(mom) else None}
    e = _eps(sid, d, mode)
    h = _rev(sid, pd.Timestamp("2100-01-01"), "live")       # 季營收只用來換算已公布季報，月營收早就公布了
    if e is not None and len(e) >= 2:
        q, p = e.index[-1], e.index[-2]
        if (q - p).days <= 100:
            def qrev(qe):
                if h is None:
                    return np.nan
                ms = [pd.Timestamp(qe.year, qe.month, 1) - pd.DateOffset(months=k) for k in (0, 1, 2)]
                v = [h["revenue"].get(m, np.nan) for m in ms]
                return float(np.sum(v)) if all(pd.notna(x) for x in v) else np.nan
            rq, rp = qrev(q), qrev(p)
            if pd.notna(e.loc[q, "gm"]) and pd.notna(e.loc[p, "gm"]):
                items["毛利"] = _hot(e.loc[q, "gm"] * rq, e.loc[p, "gm"] * rp)
            if pd.notna(e.loc[q, "nm"]) and pd.notna(e.loc[p, "nm"]):
                items["淨利"] = _hot(e.loc[q, "nm"] * rq, e.loc[p, "nm"] * rp)
            else:
                items["淨利"] = _hot(e.loc[q, "eps"], e.loc[p, "eps"])   # 金融股：EPS 季增代替
    ok = [v for v in items.values() if v is not None]
    if not ok:
        return np.nan, items
    return 3 * sum(ok) / len(ok), items


def eps_rate(t, absolute):
    info = t["EPS資料"].apply(lambda x: x if isinstance(x, dict) else {})
    now = info.apply(lambda x: x.get("EPS", np.nan)).astype(float)
    base = info.apply(lambda x: x.get("去年同季", np.nan)).astype(float)
    if absolute:
        g = ((now - base) / base.abs()).clip(*RATE_CAP)
        g = g.where(base != 0, np.where(now > 0, RATE_CAP[1], np.where(now < 0, RATE_CAP[0], 0.0)))
        return g.where(now.notna() & base.notna())
    g = ((now - base) / base.abs()).clip(*RATE_CAP)
    return g.where(base > 0, -np.inf).where(now.notna() & base.notna())


_memo = {}


def mtg_table(raw, d, mode="deadline", absolute=False):
    t = raw[~raw.index.isin(B.SHIP)].copy()
    key = (d, mode)
    if key not in _memo:                    # A、B 兩版同一天共用，只算一次
        _memo.clear()
        f = {}
        for s in t.index:
            sur, mom = rev_factors(s, d, mode)
            i3, det = growth_count(s, d, mode, mom)
            f[s] = (sur, mom, i3, det)
        _memo[key] = f
    f = _memo[key]
    t["營收驚喜2"] = [f[s][0] for s in t.index]
    t["MoM"] = [f[s][1] for s in t.index]
    t["I3_成長項數"] = [f[s][2] for s in t.index]
    t["成長明細"] = [f[s][3] for s in t.index]
    t["I2_EPS成長率"] = eps_rate(t, absolute)
    p1 = t["營收驚喜2"].rank(pct=True) + MOM_BONUS * (t["MoM"] >= MOM_HOT)
    t["I1_營收分數"] = p1.where(t["營收驚喜2"].notna())
    ranks = pd.concat([t[c].astype(float).rank(method="min", pct=True)
                       for c in ("I1_營收分數", "I2_EPS成長率", "I3_成長項數")], axis=1)
    t["綜合分數"] = ranks.mean(axis=1, skipna=True)
    t = t[t["綜合分數"].notna()].sort_values("綜合分數", ascending=False)
    t["排名"] = t["綜合分數"].rank(method="min", ascending=False).astype(int)
    return t


_orig_pool_table = B.pool_table


def pool_table(raw, pool, d=None):
    if pool in ("MTG_A", "MTG_B") and raw is not None and not raw.empty:
        return mtg_table(raw, d, "deadline", absolute=pool == "MTG_B")
    return _orig_pool_table(raw, pool, d)


B.pool_table = pool_table


# ---------------- 換股門檻版的目標組合 ----------------
_orig_build = S.build_targets


def build_targets(d, prev=None, current_weights=None, mode="live"):
    t = S.score_pool(d, mode)
    if t is None or "I3_成長項數" not in t.columns:
        return _orig_build(d, prev, current_weights, mode)
    rank = t["排名"]
    passed = lambda s: not t.loc[s, "過濾"]
    held = [s for s in (prev or {}).get("core", []) + (prev or {}).get("small", []) if s in t.index]
    book = list(dict.fromkeys(held))
    n_tot = S.N_CORE + S.N_SMALL
    # 1) 排名比第 17 名好的新股票，換掉持股裡排名最差的
    for s in t.index:
        if rank[s] >= ENTRY_RANK:
            break
        if s in book or not passed(s):
            continue
        if len(book) < n_tot:
            book.append(s)
            continue
        worst = max(book, key=lambda x: rank[x])
        if rank[worst] > rank[s]:
            book[book.index(worst)] = s
    # 2) 不足 19 檔（月初建倉、持股掉出選股池）→ 依排名補，先用通過過濾的，不夠再用被擋的
    filled = []
    for need_pass in (True, False):
        for s in t.index:
            if len(book) >= n_tot:
                break
            if s not in book and (passed(s) or not need_pass):
                book.append(s)
                if not passed(s):
                    filled.append(s)
    book.sort(key=lambda x: rank[x])
    # 3) 核心：原核心排名仍在前 12 保留，再依排名補滿 8 檔（記憶體最多 3 檔）
    prev_core = set((prev or {}).get("core", []))
    core, n_mem = [], 0

    def add(s):
        nonlocal n_mem
        if len(core) >= S.N_CORE or s in core:
            return
        if s in S.MEMORY:
            if n_mem >= S.MAX_MEMORY_CORE:
                return
            n_mem += 1
        core.append(s)

    for s in book:
        if s in prev_core and rank[s] <= S.CORE_KEEP_RANK:
            add(s)
    for s in book:
        add(s)
    small = [s for s in book if s not in core]
    # 4) 防守模式、台積電：同第 9 組
    n_pass = int(sum(1 for s in t.index if not t.loc[s, "過濾"]))
    was_def = bool((prev or {}).get("defensive"))
    defensive = n_pass <= S.DEF_MAX_PASS or (was_def and n_pass <= S.DEF_EXIT_PASS)
    def_reason = (f"全選股池 {n_pass} 檔通過過濾" if defensive else "")
    w = {}
    cw = (current_weights or {}).get(S.TSMC)
    w[S.TSMC] = S.W_TSMC_DEF if defensive else (cw if cw is not None and S.TSMC_BAND[0] <= cw <= S.TSMC_BAND[1] else S.W_TSMC)
    for s in core:
        w[s] = S.W_CORE_DEF if defensive else S.W_CORE
    for s in small:
        w[s] = S.W_SMALL
    roles = {S.TSMC: "台積電"} | {s: "核心" for s in core} | {s: "小部位" for s in small}
    blocked = [(s, int(rank[s]), t.loc[s, "過濾"]) for s in t.index
               if not passed(s) and s not in book and rank[s] <= ENTRY_RANK]
    return {"date": f"{d:%Y-%m-%d}", "mode": mode, "weights": w, "core": core, "small": small,
            "roles": roles, "blocked": blocked, "filled": filled, "defensive": defensive, "n_pass": n_pass,
            "def_reason": def_reason, "filter_scope": S.FILTER_SCOPE,
            "cash_target": round(1 - sum(w.values()), 4)}, t


S.build_targets = build_targets


# ---------------- 第 10 題：選股池日均量與新方法排名（最新一天）----------------
def latest_list():
    from openpyxl.styles import Font
    d = pd.Timestamp.today().normalize()
    S.AI_CODES = B.ALL_CODES
    S._read_px = B._orig_read_px
    B.INST.OUT_DIR = _INST_DIR_LIVE
    B.TDCC.history = _TDCC_LIVE
    raw = B._orig_score_pool(d, mode="live")
    names = B.stock_names()
    out = {}
    for absolute, tag in ((False, "A"), (True, "B")):
        t = mtg_table(raw, d, "live", absolute)
        out[tag] = t
    t = out["A"]
    vol = {}
    for sid, v in B.SECTOR.items():
        if sid not in B.UNIVERSE or sid == S.TSMC:
            continue
        ok, avg = S.liquid(sid, d)
        vol[sid] = (v["name"], avg)
    rows = []
    for sid, (ind, avg) in vol.items():
        r = t.loc[sid] if sid in t.index else None
        rows.append({"代號": sid, "名稱": names.get(sid, ""), "產業": ind,
                     "20日均量(張)": round(avg) if avg else None,
                     "≥500張": "是" if avg and avg >= S.MIN_VOL_LOTS else "否",
                     "排除原因": ("航運業" if ind in S.EXCLUDE_IND or sid in B.SHIP else
                              ("均量不足500張" if not (avg and avg >= S.MIN_VOL_LOTS) else
                               ("無分數" if r is None else ""))),
                     "新方法A排名": int(r["排名"]) if r is not None else None,
                     "新方法B排名": int(out["B"].loc[sid, "排名"]) if sid in out["B"].index else None,
                     "第9組排名": None,
                     "營收驚喜": None if r is None or pd.isna(r["營收驚喜2"]) else round(float(r["營收驚喜2"]), 1),
                     "MoM(%)": None if r is None or pd.isna(r["MoM"]) else round(float(r["MoM"]) * 100, 1),
                     "EPS成長率(%)": None if r is None or not np.isfinite(r["I2_EPS成長率"]) else round(float(r["I2_EPS成長率"]) * 100, 1),
                     "成長>10%項數(0-3)": None if r is None or pd.isna(r["I3_成長項數"]) else round(float(r["I3_成長項數"]), 2),
                     "成長明細": "" if r is None else "、".join(f"{k}{'✓' if v else '✗'}" for k, v in (r["成長明細"] or {}).items() if v is not None),
                     "過濾": "" if r is None else "、".join(r["過濾"])})
    g9 = _orig_pool_table(raw, "NOSHIP")
    for x in rows:
        x["第9組排名"] = int(g9.loc[x["代號"], "排名"]) if x["代號"] in g9.index else None
    df = pd.DataFrame(rows).sort_values(["新方法A排名", "20日均量(張)"], ascending=[True, False], na_position="last")
    os.makedirs(B.OUT_DIR, exist_ok=True)
    path = B.out_path("選股池排名_最新.xlsx")
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name=f"{d:%m%d}", index=False)
        ws = xw.book.worksheets[0]
        for cell in ws[1]:
            cell.font = Font(bold=True)
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = ws.dimensions
    n_ok = int((df["≥500張"] == "是").sum())
    print(f"\n第 10 題：150 檔（不含台積電）中 20 日均量 ≥500 張 {n_ok} 檔；已存 {path}")
    print(df[df["新方法A排名"].notna()].head(25)[["代號", "名稱", "產業", "20日均量(張)", "新方法A排名",
                                                 "新方法B排名", "第9組排名", "過濾"]].to_string(index=False))


def paired():
    p = os.path.join(B.OUT_DIR, "每月報酬.csv")
    if not os.path.exists(p):
        return
    m = pd.read_csv(p, index_col=0)
    base = B.label(B.COMBOS[0])
    print("\n" + "=" * 70 + "\n新方法 vs 第 9 組（每月配對）\n" + "=" * 70)
    for c in B.COMBOS[1:]:
        col = B.label(c)
        dlt = m[col] - m[base]
        t = dlt.mean() / (dlt.std() / np.sqrt(len(dlt))) if dlt.std() > 0 else np.nan
        print(f"  {col}：平均月報酬 {m[col].mean():.2%}（第 9 組 {m[base].mean():.2%}），"
              f"差 {dlt.mean():+.2%}／月，t ＝ {t:.2f}，贏 {int((dlt > 0).sum())}／{len(dlt)} 個月")
    print("  → t > 2 才算真的比第 9 組好")


if __name__ == "__main__":
    B.main()
    paired()
    try:
        latest_list()
    except Exception as e:
        print(f"\n! 第 10 題清單失敗：{type(e).__name__}：{e}")
