# -*- coding: utf-8 -*-
"""
esun_strategy.py — 比賽選股：150 檔（排除航運）營收驚喜／EPS 增幅／三率三項綜合排名 → 目標權重

規則（比賽前定案，比賽期間不改）：
  · 選股池：主辦方 150 檔全部產業，排除航運業（EXCLUDE_IND）；20 日均量 >= 500 張
    （回測第 9 組：150 檔排除航運｜核心 9.5%＋小部位 1%｜new_only，2026-10-12 定案）
    排除航運的理由：EPS 增幅 ÷ 股價、營收驚喜會系統性偏向運價暴漲期的航運股（本益比低、盈餘隨運價大起大落），
    高分常出現在景氣高點附近；運價由外生衝擊驅動，不代表可持續的成長
  · 營收驚喜 = 最新 YoY − 前 3 個月 YoY 平均（YoY 超過 ±500% 截斷為 ±500%）
    前 3 個月只要有 2 個月 YoY 算得出來就用（2026-10-14 起）：金控去年有幾個月營收為負（匯損），
    那幾個月的 YoY 無法計算，原本整項變空白，金控只剩 EPS 一項排名；回測此改動對報酬無影響（+0.05%／月）
  · EPS 增幅 = (最新一季 EPS − 去年同季 EPS) ÷ 選股日收盤價
    （原本用成長率，會被虧損轉盈或低基期灌爆；除以股價是學術上的標準處理）
  · 三率 = 毛利率、營業利益率、稅後淨利率跟上一季比，「沒有下跌」的項數（0–3）
  · 大戶（400 張以上大戶比例上升週數）仍會計算、列在報表，但不列入排名（2026-10-13 起）：
    回測沒有集保歷史，第 9 組實際是用前三項驗證的，正式流程跟回測一致
  · 綜合分數 = 營收驚喜、EPS 增幅、三率在選股池內的百分位排名平均；缺的項目略過；都沒有則不列入
  · 過濾（不影響排名，擋新買進；FILTER_SCOPE="all" 時已持有的也賣出）：
      均線 5／10／20 日三條全部下彎；三大法人連 3 日賣超；
      爆量下跌後賣壓未消化：個股跌 ≥3%、量 ≥ 20 日均量 2 倍（只看個股自己的籌碼，不管大盤），
      之後沒有出現「上漲且量更大」的一天 → 代表買盤還沒真正進場，短線不追
    通過過濾的湊不滿核心 8 檔＋小部位 11 檔時（例如大跌日剛好要建倉），依排名用未通過過濾的補足，
    確保持股 20 檔、現金低於 25%，不會因此整天交不出報告
  · 防守模式：整個選股池通過過濾的少於 10 檔，代表全市場行情很差
    → 台積電 24%、小部位 11 檔各 1% 不變、現金約 22%，核心 8 檔平均分剩下的（各 5.375%）
    （看的是全選股池的通過數，不含已持有的豁免；進入後要超過 15 檔才回到一般權重，
      10–15 檔之間維持原本的模式，避免在門檻附近來回切換）
  · 台積電固定 10%（目前權重在 8%–12% 之間就維持不動）
  · 核心 8 檔各 9.5%：已是核心且排名仍在前 12 名者保留，其餘依排名補滿
    核心最多 3 檔記憶體股（清單固定：華邦電、南亞科、旺宏、威剛、群聯、宜鼎、創見），多的順延
    同一產業（證交所／櫃買官方分類）核心最多 3 檔，多的順延（2026-10-14 起）
  · 小部位 11 檔各 1%：核心之外的下一批
  · 現金約 3%
資料時間：mode="live" 比賽用（抓得到的資料即已公布）；mode="deadline" 回測用（依法定期限）
          季報（EPS、三率）在 live 也依法定期限（EPS_TIMING，2026-11-01 起），月營收維持抓得到就用
用法：python esun_strategy.py [YYYY-MM-DD]   （預設用最新資料，印出目標組合）
"""
import os, sys, json
import numpy as np, pandas as pd
from dplan_validator import UNIVERSE

SECTOR_FILE = "esun_sector.json"
REV_DIR = "./revenue_cache/esun_revenue"
EPS_DIR = "./revenue_cache/eps_history"
LOCAL_DATA, OTC_DIR = "./local_data", "./local_data_otc"
TARGET_DIR = "./esun_targets"
def _all_codes():
    """選股池＝產業對照表裡的全部產業（150 檔都在裡面）；名稱沿用 AI_CODES 讓回測／彩排可以覆寫"""
    for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), SECTOR_FILE), SECTOR_FILE):
        if os.path.exists(p):
            return {v["code"] for v in json.load(open(p, encoding="utf-8")).values()}
    raise FileNotFoundError(f"找不到 {SECTOR_FILE}，無法建立選股池")


AI_CODES = _all_codes()               # 第 9 組：全部產業（舊版三產業為 {"24", "25", "28"}）
EXCLUDE_IND = {"航運業"}               # 不論選股池，這些產業一律不選（運價、地緣政治驅動，基本面分數失真）


def pool_sids():
    """選股池內的股票代號（含台積電；三大法人等資料要抓的範圍）"""
    sec = json.load(open(SECTOR_FILE, encoding="utf-8"))
    return sorted(s for s, v in sec.items()
                  if s in UNIVERSE and v["code"] in AI_CODES and v["name"] not in EXCLUDE_IND)
TSMC = "2330"
W_TSMC, TSMC_BAND = 0.10, (0.08, 0.12)
N_CORE, W_CORE, CORE_KEEP_RANK = 8, 0.095, 12
N_SMALL, W_SMALL = 11, 0.01
DEF_MAX_PASS = 9                      # 防守模式：全選股池通過過濾的少於 10 檔（行情很差）→ 進入
DEF_EXIT_PASS = 15                    # 已在防守模式：通過過濾超過 15 檔才離開（10–15 檔維持原模式，避免來回切換）
W_TSMC_DEF, CASH_DEF = 0.24, 0.22     # 防守模式：台積電 24%、現金約 22%、小部位不變，核心平均分剩下的
W_CORE_DEF = round((1 - W_TSMC_DEF - CASH_DEF - N_SMALL * W_SMALL) / N_CORE, 5)   # = 5.375%
MEMORY = {"2344", "2408", "2337", "3260", "8299", "5289", "2451"}   # 比賽前固定，比賽期間不改
MAX_MEMORY_CORE = 3
MAX_INDUSTRY_CORE = 3   # 同一產業（官方分類）核心最多 3 檔（2026-10-14 起；回測 +0.25%／月、t 1.59，主要作用是降低集中風險）


def _industry_caps(cap):
    for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), SECTOR_FILE), SECTOR_FILE):
        if os.path.exists(p):
            groups = {}
            for sid, v in json.load(open(p, encoding="utf-8")).items():
                groups.setdefault(v["name"], set()).add(sid)
            return [(g, cap) for g in groups.values()]
    return []


EXTRA_GROUP_CAPS = _industry_caps(MAX_INDUSTRY_CORE)   # 核心檔數上限 [(股票代號集合, 上限)]，記憶體上限另外加
MIN_VOL_LOTS, YOY_CAP = 500, 500.0
FILTER_SCOPE = "new_only"        # "new_only"：只擋新買進；"all"：已持有的不通過也賣出（比賽前決定，之後不改）
CRASH_RET, CRASH_VOL, CRASH_LOOKBACK = -0.03, 2.0, 60
CRASH_EXCESS = None              # None：只看個股本身的賣壓（預設）；設成 -0.03：只算比 0050 多跌 3 個百分點的爆量下跌
MARKET = "0050"
SCORE_FACTORS = ("營收驚喜", "EPS增幅", "三率")   # 大戶只計算不排名（回測未驗證，2026-10-13 拿掉）


def _read_px(sid):
    for p in (os.path.join(LOCAL_DATA, f"{sid}.TW.parquet"), os.path.join(OTC_DIR, f"{sid}.TWO.parquet")):
        if os.path.exists(p):
            df = pd.read_parquet(p)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df.columns = [c.capitalize() for c in df.columns]
            if "Volume" in df.columns:                  # 休市日（颱風假等）資料源會補一根成交量 0 的假 K 線，會讓均線、均量算錯
                df = df[~(df["Volume"] == 0)]
            return df[~df.index.duplicated(keep="last")].sort_index()
    return None


def liquid(sid, d):
    df = _read_px(sid)
    if df is None or "Volume" not in df.columns:
        return False, None
    v = df["Volume"].loc[:d].dropna()
    if len(v) < 20 or (d - v.index[-1]).days > 7:
        return False, None
    avg = v.iloc[-20:].mean()
    return avg >= MIN_VOL_LOTS * 1000, avg / 1000


def revenue_surprise(sid, d, mode):
    p = os.path.join(REV_DIR, f"{sid}.parquet")
    if not os.path.exists(p):
        return np.nan, None
    h = pd.read_parquet(p).sort_index()
    avail = h["pub_month"] if mode == "live" else h["available_date"]
    h = h[pd.to_datetime(avail) <= d]
    if len(h) < 4:
        return np.nan, None
    y = h["yoy"].clip(-YOY_CAP, YOY_CAP)
    last = h.index[-1]
    prev = [last - pd.DateOffset(months=k) for k in (1, 2, 3)]
    pv = [y[m] for m in prev if m in y.index and pd.notna(y[m])]
    if pd.isna(y[last]) or len(pv) < 2:
        return np.nan, None
    return float(y[last] - np.mean(pv)), {"月份": f"{last:%Y-%m}", "YoY": round(float(y[last]), 1)}


EPS_TIMING = "deadline"   # 2026-11-01 起：比賽也跟回測一樣，季報（EPS、三率）等法定期限才換季，避免先公布的公司用新一季、其他用舊一季混著排名


def _eps_asof(e, d, mode):
    """季報資料截至 d 可以用的部分：回測或 EPS_TIMING=deadline 依法定期限；否則抓得到就用"""
    if mode == "deadline" or EPS_TIMING == "deadline":
        return e[pd.to_datetime(e["available_date"]) <= d]
    return e[e.index <= d]


EPS_PX_DAYS = 20   # 2026-11-02 起：EPS 增幅的分母用最近 20 個交易日的平均收盤價，避免一兩天的漲跌讓排名來回跳
                   # （回測 2023-01～2026-11：平均月報酬 +0.05%、兩段子期間都較好、月換手 289% → 281%）


def eps_growth(sid, d, mode):
    """(最新一季 EPS − 去年同季 EPS) ÷ 最近 20 個交易日平均收盤價"""
    p = os.path.join(EPS_DIR, f"{sid}.parquet")
    if not os.path.exists(p):
        return np.nan, None
    e = _eps_asof(pd.read_parquet(p).sort_index(), d, mode)
    if len(e) == 0:
        return np.nan, None
    q = e.index[-1]
    base_q = q - pd.DateOffset(years=1)
    if base_q not in e.index:
        return np.nan, None
    px = _read_px(sid)
    close = px["Close"].loc[:d].dropna() if px is not None else pd.Series(dtype=float)
    if len(close) == 0 or not close.iat[-1] > 0:
        return np.nan, None
    now, base = float(e.loc[q, "eps"]), float(e.loc[base_q, "eps"])
    px_avg = float(close.tail(EPS_PX_DAYS).mean())
    return (now - base) / px_avg, {"季": f"{q:%Y-%m}", "EPS": now, "去年同季": base,
                                   "股價": round(px_avg, 2), "股價說明": f"近{EPS_PX_DAYS}日均價"}


def margin_score(sid, d, mode):
    """三率跟上一季比，沒有下跌的項數（以有資料的項目換算成 0–3）"""
    p = os.path.join(EPS_DIR, f"{sid}.parquet")
    if not os.path.exists(p):
        return np.nan, None
    e = pd.read_parquet(p).sort_index()
    if not {"gm", "om", "nm"} <= set(e.columns):
        return np.nan, None
    e = _eps_asof(e, d, mode)
    if len(e) < 2:
        return np.nan, None
    q, prev = e.index[-1], e.index[-2]
    if (q - prev).days > 100:                        # 上一季必須連續
        return np.nan, None
    ok = [bool(e.loc[q, c] >= e.loc[prev, c]) for c in ("gm", "om", "nm")
          if pd.notna(e.loc[q, c]) and pd.notna(e.loc[prev, c])]
    if not ok:
        return np.nan, None
    return 3 * sum(ok) / len(ok), {"季": f"{q:%Y-%m}", "毛利率": round(float(e.loc[q, "gm"]), 1) if pd.notna(e.loc[q, "gm"]) else None,
                                   "不跌項數": int(sum(ok)), "比較項數": len(ok)}


def bigholder_score(sid, hist):
    """400 張以上大戶比例最近 4 週的上升週數，同分看變化幅度"""
    if hist is None or sid not in getattr(hist, "columns", []):
        return np.nan, None
    h = hist[sid].dropna().tail(5)
    diffs = h.diff().dropna()
    if diffs.empty:
        return np.nan, None
    up, chg = int((diffs > 0).sum()), float(h.iat[-1] - h.iat[0])
    return up + chg / 100, {"週數": len(diffs), "上升週": up, "變化": round(chg, 2)}


def filters(sid, d):
    """回傳未通過的原因清單（空的代表通過）"""
    reasons = []
    px = _read_px(sid)
    if px is not None and len(px.loc[:d]) >= 25:
        x = px.loc[:d]
        c, v = x["Close"], x["Volume"]
        ma = [c.rolling(n).mean() for n in (5, 10, 20)]
        if all(m.iat[-1] < m.iat[-2] for m in ma):
            reasons.append("均線5/10/20全部下彎")
        r, avgv = c.pct_change(), v.rolling(20).mean().shift(1)
        cond = (r <= CRASH_RET) & (v >= CRASH_VOL * avgv)
        if CRASH_EXCESS is not None:                                  # 選用：只算比大盤多跌的
            mk = _read_px(MARKET)
            mr = mk["Close"].pct_change().reindex(r.index) if mk is not None else pd.Series(0.0, index=r.index)
            cond &= (r - mr.fillna(0.0)) <= CRASH_EXCESS
        crash = cond.tail(CRASH_LOOKBACK)
        if crash.any():
            dday = crash[crash].index[-1]
            later = x.index > dday
            if not ((r[later] > 0) & (v[later] > v.loc[dday])).any():   # 之後沒有「上漲且量更大」的一天
                reasons.append(f"{dday:%m/%d}爆量下跌賣壓未消化")
    try:
        import esun_fetch_inst as INST
        n = INST.load(sid).loc[:d]
        if len(n) >= 3 and (n.tail(3) < 0).all():
            reasons.append("三大法人連3日賣超")
    except Exception:
        pass
    return reasons


def score_pool(d, mode="live"):
    sec = json.load(open(SECTOR_FILE, encoding="utf-8"))
    try:
        import esun_fetch_tdcc as TDCC
        hist = TDCC.history()
        hist = hist[hist.index <= f"{d:%Y%m%d}"]
    except Exception:
        hist = None
    rows = {}
    for sid, v in sec.items():
        if sid not in UNIVERSE or v["code"] not in AI_CODES or v["name"] in EXCLUDE_IND or sid == TSMC:
            continue
        ok, vol = liquid(sid, d)
        if not ok:
            continue
        s, s_info = revenue_surprise(sid, d, mode)
        g, g_info = eps_growth(sid, d, mode)
        m, m_info = margin_score(sid, d, mode)
        b, b_info = bigholder_score(sid, hist)
        rows[sid] = {"產業": v["name"], "均量張": round(vol) if vol else None, "營收驚喜": s, "EPS增幅": g,
                     "三率": m, "大戶": b, "營收資料": s_info, "EPS資料": g_info, "三率資料": m_info,
                     "大戶資料": b_info, "過濾": filters(sid, d)}
    t = pd.DataFrame(rows).T
    if t.empty:
        return t
    for c in ("營收驚喜", "EPS增幅", "三率", "大戶"):
        t[c] = t[c].astype(float)
    ranks = pd.concat([t[c].rank(pct=True) for c in SCORE_FACTORS], axis=1)
    t["綜合分數"] = ranks.mean(axis=1, skipna=True)
    t = t[t["綜合分數"].notna()].sort_values("綜合分數", ascending=False)
    t["排名"] = range(1, len(t) + 1)
    return t


def build_targets(d, prev=None, current_weights=None, mode="live"):
    """prev：上一期目標（含 core 名單）；current_weights：目前實際權重（用於台積電帶狀）。"""
    t = score_pool(d, mode)
    prev_core = set((prev or {}).get("core", []))
    held = {k for k, w in (current_weights or {}).items() if w and w > 0}
    core = []
    caps = [(MEMORY, MAX_MEMORY_CORE)] + list(EXTRA_GROUP_CAPS)

    def eligible(s):
        if not t.loc[s, "過濾"]:
            return True
        return FILTER_SCOPE == "new_only" and s in held      # 已持有的不因過濾賣出

    def add(s, force=False):
        if len(core) >= N_CORE or s in core or not (force or eligible(s)):
            return
        for grp, cap in caps:                           # 記憶體最多 3 檔（回測可另加產業上限）
            if s in grp and sum(1 for c in core if c in grp) >= cap:
                return
        core.append(s)

    for s in t.index:                                   # 先保留：前期核心且排名仍在前 12
        if s in prev_core and t.loc[s, "排名"] <= CORE_KEEP_RANK:
            add(s)
    for s in t.index:                                   # 再依排名補滿（記憶體最多 3 檔）
        add(s)
    small = [s for s in t.index if s not in core and eligible(s)][:N_SMALL]
    filled = []                                         # 通過過濾的湊不滿 19 檔（例如大跌日建倉）→ 依排名用被擋的補足
    if len(core) + len(small) < N_CORE + N_SMALL:
        for s in t.index:
            n0 = len(core)
            add(s, force=True)
            if len(core) > n0:
                filled.append(s)
        for s in t.index:
            if len(small) >= N_SMALL:
                break
            if s not in core and s not in small:
                small.append(s)
                if not eligible(s):
                    filled.append(s)
    blocked = [(s, int(t.loc[s, "排名"]), t.loc[s, "過濾"]) for s in t.index
               if not eligible(s) and s not in filled and t.loc[s, "排名"] <= N_CORE + N_SMALL + 5]
    # 防守模式看「整個選股池」有幾檔通過過濾（不算已持有的豁免），持有後隔天才不會馬上切回一般模式
    n_pass = int(sum(1 for s in t.index if not t.loc[s, "過濾"]))
    was_def = bool((prev or {}).get("defensive"))
    defensive = n_pass <= DEF_MAX_PASS or (was_def and n_pass <= DEF_EXIT_PASS)
    if not defensive:
        def_reason = ""
    elif n_pass <= DEF_MAX_PASS:
        def_reason = f"全選股池僅 {n_pass} 檔通過過濾（少於 {DEF_MAX_PASS + 1} 檔）" + ("，維持防守模式" if was_def else "，進入防守模式")
    else:
        def_reason = f"全選股池 {n_pass} 檔通過過濾，尚未超過 {DEF_EXIT_PASS} 檔，維持防守模式"
    w = {}
    cw = (current_weights or {}).get(TSMC)
    if defensive:
        w[TSMC] = W_TSMC_DEF
    else:
        w[TSMC] = cw if cw is not None and TSMC_BAND[0] <= cw <= TSMC_BAND[1] else W_TSMC
    for s in core:
        w[s] = W_CORE_DEF if defensive else W_CORE
    for s in small:
        w[s] = W_SMALL
    roles = {TSMC: "台積電"} | {s: "核心" for s in core} | {s: "小部位" for s in small}
    return {"date": f"{d:%Y-%m-%d}", "mode": mode, "weights": w, "core": core, "small": small,
            "roles": roles, "blocked": blocked, "filled": filled, "defensive": defensive, "n_pass": n_pass,
            "def_reason": def_reason,
            "filter_scope": FILTER_SCOPE,
            "cash_target": round(1 - sum(w.values()), 4)}, t


OVERRIDE_FILE = "esun_override.json"


def apply_override(tg, t, trade_date, current_weights=None):
    """esun_override.json 有這個交易日 → 改用操作者指定的臨時防守配置（人工啟用，報告會照實寫明）。
    其他持股維持小部位；還不夠檔數時，依排名補通過過濾的。tg["core"]、tg["defensive"] 不動，隔天才能照常接回。"""
    if not os.path.exists(OVERRIDE_FILE):
        return tg
    o = json.load(open(OVERRIDE_FILE, encoding="utf-8")).get(trade_date)
    if not o:
        return tg
    big = {str(k): float(v) for k, v in o["big"].items()}
    held = {k for k, w in (current_weights or {}).items() if w and w > 0}
    rest = [s for s in t.index if s not in big and s in held]
    rest += [s for s in held if s not in big and s not in rest and s != TSMC]          # 掉出排名的持股也留著
    rest += [s for s in t.index if s not in big and s not in rest and not t.loc[s, "過濾"]]
    small = rest[:int(o["n_holdings"]) - 1 - len(big)]
    w = {TSMC: float(o["tsmc"])} | big | {s: float(o["small_weight"]) for s in small}
    roles = {TSMC: "台積電"} | {s: "核心" for s in big} | {s: "小部位" for s in small}
    return tg | {"weights": w, "roles": roles, "small": small, "override": o, "filled": [], "blocked": [],
                 "cash_target": round(1 - sum(w.values()), 4)}


def save_targets(tg):
    os.makedirs(TARGET_DIR, exist_ok=True)
    path = os.path.join(TARGET_DIR, f"targets_{tg['date']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tg, f, ensure_ascii=False, indent=1)
    return path


def load_prev_targets(before):
    if not os.path.exists(TARGET_DIR):
        return None
    fs = sorted(f for f in os.listdir(TARGET_DIR) if f.startswith("targets_") and f[8:18] < before)
    return json.load(open(os.path.join(TARGET_DIR, fs[-1]), encoding="utf-8")) if fs else None


if __name__ == "__main__":
    d = pd.Timestamp(sys.argv[1]) if len(sys.argv) > 1 else pd.Timestamp.today().normalize()
    prev = load_prev_targets(f"{d:%Y-%m-%d}")
    tg, table = build_targets(d, prev)
    print(f"目標組合（{tg['date']}，{len(tg['weights'])} 檔，預計現金 {tg['cash_target']:.1%}）")
    print(f"  選股池 {len(table)} 檔有分數\n")
    print(f"  {'角色':<6}{'代號':<6}{'產業':<10}{'權重':>6}{'排名':>5}{'營收驚喜':>9}{'EPS增幅/股價':>12}{'三率':>6}{'大戶':>6}")
    for s, wt in tg["weights"].items():
        r = table.loc[s] if s in table.index else None
        sp = f"{r['營收驚喜']:+.1f}" if r is not None and pd.notna(r["營收驚喜"]) else "—"
        ep = f"{r['EPS增幅']*100:+.2f}%" if r is not None and pd.notna(r["EPS增幅"]) else "—"
        rk = int(r["排名"]) if r is not None else "—"
        ind = r["產業"] if r is not None else "半導體業"
        mg = f"{r['三率資料']['不跌項數']}/{r['三率資料']['比較項數']}" if r is not None and r["三率資料"] else "—"
        bh = f"{r['大戶資料']['上升週']}/{r['大戶資料']['週數']}" if r is not None and r["大戶資料"] else "—"
        print(f"  {tg['roles'][s]:<6}{s:<6}{ind:<10}{wt:>6.1%}{rk:>5}{sp:>9}{ep:>12}{mg:>6}{bh:>6}")
    if tg["blocked"]:
        print(f"\n  排名前列、但未通過過濾而不新買進（FILTER_SCOPE={tg['filter_scope']}）：")
        for sid, rk, why in tg["blocked"]:
            print(f"    第 {rk:>2} 名 {sid}：{'、'.join(why)}")
    if tg["defensive"]:
        print(f"\n  ‼ 防守模式：{tg['def_reason']}；"
              f"台積電 {W_TSMC_DEF:.0%}、核心各 {W_CORE_DEF:.3%}、現金約 {tg['cash_target']:.0%}")
    if tg["filled"]:
        print(f"\n  通過過濾的不足 {N_CORE + N_SMALL} 檔，依排名用未通過過濾的補足：{'、'.join(tg['filled'])}")
    n_eps = sum(1 for x in table["三率資料"] if x) if len(table) else 0
    n_big = sum(1 for x in table["大戶資料"] if x) if len(table) else 0
    print(f"\n  資料涵蓋：三率 {n_eps}/{len(table)} 檔、大戶 {n_big}/{len(table)} 檔"
          + ("（大戶需累積至少 2 週集保資料）" if n_big == 0 else ""))
    if tg.get("blocked"):
        print(f"\n  被過濾擋下、沒有新買進的（過濾範圍：{tg['filter_scope']}）：")
        for sid, rk, why in tg["blocked"]:
            r = table.loc[sid]
            print(f"    第{rk:>2}名 {sid} {r['產業']:<8}｜{'、'.join(why)}")
    print(f"\n已存 {save_targets(tg)}")