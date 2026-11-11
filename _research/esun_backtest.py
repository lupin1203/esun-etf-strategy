# -*- coding: utf-8 -*-
"""
esun_backtest.py — 8 組回測：選股池 × 權重 × 過濾範圍

  選股池：三產業（24 半導體、25 電腦週邊、28 電子零組件）｜150 檔全部（台積電一樣另外處理）
  權重：  9.5%+1%（核心 8 檔各 9.5%、小部位 11 檔各 1%）｜均分 4.5%（19 檔各 4.5%）
  過濾：  new_only（只擋新買進）｜all（已持有的不通過也賣）
  台積電都是固定 10%（8%–12% 之間不動）

做法：
  · 直接呼叫 esun_strategy（評分、過濾、核心保留、記憶體上限）與 esun_orders（官方股數公式、
    無交易帶、超過上限減碼、最壞情況現金檢查），跟比賽每天跑的是同一套程式
  · 每個月各自獨立：月初本金 10 億、全部現金開始建倉（跟比賽第一天一樣），月底淨值 ÷ 10 億 − 1 = 當月報酬
    月與月之間不複利、不延續持股
  · 每個交易日收盤後選股，資料用 deadline 模式（營收公布月 10 日、財報法定期限後才能用），隔天成交
  · 成交價用當天 (高+低+收)/3 近似均價；手續費、證交稅照 esun_ledger
  · 出錯（例如交易後持股不在 20–30 檔）那天就不交易，跟比賽的「全部續抱」一樣，並計入出錯天數
  · 均分 4.5% 時，第一天現金不足會讓 19 檔等比例縮減（原本的做法是先把小部位砍到 1 張，
    均分時會砍掉一半資金、交易後現金超過 25% 被擋下）
  · 均分 4.5% 時，現金比目標多 2% 以上的那天不套用無交易帶（第一天只建到約 86%，
    第二天每檔只差約 0.7%，會被 1.5% 無交易帶擋住，現金一直閒著）
  · 單日漲跌超過 11% 且已上市超過 5 天（新股前 5 天沒有漲跌幅限制，是真的漲跌）視為減資或分割，
    持有時股數換算、當天不計報酬；有實際影響到持股的會列出來
限制：
  · K 線是還原價（含息），比賽期間配息不計入 → 回測報酬略高，各組偏差差不多
  · 「EPS 增幅 ÷ 股價」的股價改用未還原的收盤價（bt_data\rawpx，esun_bt_fetch.py 抓）：
    還原價會被「之後才配的息」往下調，高股息股分數偏高＝偷看未來；抓不到原始價的股票沿用還原價
  · 集保大戶要先跑 esun_bt_fetch.py；抓不到就只用三項評分
  · 每天的評分會存在 _research\\bt_cache\\，重跑很快；改了策略或資料要加 --fresh 重算
輸出：_research\\bt_out\\ 每月報酬.csv、每日淨值.csv、摘要.csv
用法（在 SMC- 資料夾）：
  python _research\\esun_bt_fetch.py         先補法人、集保歷史（一次就好）
  python _research\\esun_backtest.py         回測（預設 2025-01-01 到最新）
  python _research\\esun_backtest.py --start 2025-01-01 --fresh
"""
import os, sys, json, glob, time, shutil, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.exists(os.path.join(os.path.dirname(HERE), "esun_strategy.py")) else HERE
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import numpy as np, pandas as pd

# ---------- 讀檔快取：策略程式每次都重新讀 parquet，回測要跑幾百天，先記在記憶體 ----------
_orig_read_parquet = pd.read_parquet
_pq = {}


def _cached_read_parquet(path, *a, **k):
    key = os.path.abspath(str(path))
    if key not in _pq:
        _pq[key] = _orig_read_parquet(path, *a, **k)
    return _pq[key].copy(deep=False)


pd.read_parquet = _cached_read_parquet

import esun_strategy as S
import esun_orders as O
import esun_ledger as L
import esun_fetch_inst as INST
import esun_fetch_tdcc as TDCC
from dplan_validator import UNIVERSE

PX_FROM = "2024-03-01"            # K 線只留這之後的，算均線、爆量都夠用，速度快很多
INST_DIR = os.path.join(HERE, "bt_data", "inst")
TDCC_DIR = os.path.join(HERE, "bt_data", "tdcc")
RAW_DIR = os.path.join(HERE, "bt_data", "rawpx")
OUT_DIR = os.path.join(HERE, "bt_out")
INIT = float(L.INIT_CASH)
JUMP = 0.11

POOLS = {"AI": "三產業", "ALL": "150檔", "NOSHIP": "150檔不含航運",
         "N52": "排除航運＋52週高點評分", "N52F": "排除航運＋52週高點過濾",
         "EPSPOS": "排除航運＋EPS去年同季須為正", "EPSRATE": "排除航運＋EPS成長率截斷",
         "EPSPOS52F": "排除航運＋EPS須為正＋52週過濾", "EPSRATE52F": "排除航運＋EPS成長率＋52週過濾"}
WTS = {"CORE": ("9.5%+1%", 0.095, 0.01), "EQ": ("均分4.5%", 0.045, 0.045)}
SCOPES = {"new_only": "new_only", "all": "all"}
COMBOS = [(p, w, f) for p in ("AI", "ALL") for w in WTS for f in SCOPES]
COMBOS += [("NOSHIP", "CORE", "new_only"), ("NOSHIP", "EQ", "new_only")]   # 9、10：踢掉航運業的對照組
COMBOS += [("N52", "CORE", "new_only"), ("N52F", "CORE", "new_only")]       # 11、12：第 9 組＋52 週高點動能
COMBOS += [("EPSPOS", "CORE", "new_only"), ("EPSRATE", "CORE", "new_only")] # 13、14：第 9 組＋修正 EPS 增幅
COMBOS += [("EPSPOS52F", "CORE", "new_only"), ("EPSRATE52F", "CORE", "new_only")]  # 15、16：13、14 再加 52 週高點過濾
RATE_CAP = (-1.0, 2.0)   # 第 14 組：EPS 成長率截斷在 −100%～+200%（13、14 組：去年同季 EPS ≤ 0 的這一項都給最低分）
R52_MIN = 0.75     # 第 12 組：收盤價低於 52 週最高收盤價的 75%（距高點超過 25%）就不新買進
FACTORS = ("營收驚喜", "EPS增幅", "三率", "大戶")

SECTOR = json.load(open(S.SECTOR_FILE, encoding="utf-8"))
AI_POOL = {s for s, v in SECTOR.items() if v["code"] in {"24", "25", "28"}}
SHIP = {s for s, v in SECTOR.items() if v["name"] in S.EXCLUDE_IND}
S.EXCLUDE_IND = set()          # 原始分數表保留全部 150 檔，各選股池自己再排除
ALL_CODES = {v["code"] for v in SECTOR.values()}


def label(c):
    return f"{POOLS[c[0]]}｜{WTS[c[1]][0]}｜{SCOPES[c[2]]}"


# ---------- K 線快取 ----------
_orig_read_px = S._read_px
_px = {}


def _read_px(sid):
    if sid not in _px:
        df = _orig_read_px(sid)
        _px[sid] = None if df is None else df.loc[PX_FROM:]
    return _px[sid]


S._read_px = _read_px


# ---------- EPS 增幅 ÷ 股價：股價改用當時的原始收盤價（不偷看之後的配息）----------
_raw = {}
_raw_miss = set()


def raw_close(sid, d):
    if sid not in _raw:
        p = os.path.join(RAW_DIR, f"{sid}.parquet")
        _raw[sid] = _orig_read_parquet(p)["close"] if os.path.exists(p) else None
    s = _raw[sid]
    if s is None:
        return None
    s = s.loc[:d]
    return float(s.iat[-1]) if len(s) and (d - s.index[-1]).days <= 7 else None


_orig_eps_growth = S.eps_growth


def _eps_growth_raw(sid, d, mode):
    v, info = _orig_eps_growth(sid, d, mode)
    if info is None or not (v == v):
        return v, info
    adj, raw = info.get("股價"), raw_close(sid, d)
    if not raw or not adj:
        _raw_miss.add(sid)
        return v, info
    return v * adj / raw, dict(info, 股價=round(raw, 2), 還原價=adj)


S.eps_growth = _eps_growth_raw


# ---------- 集保歷史（資料日隔天才算公布）----------
def tdcc_hist():
    fs = glob.glob(os.path.join(TDCC_DIR, "*.parquet"))
    if not fs:
        return None
    h = pd.DataFrame({os.path.basename(f)[:-8]: _orig_read_parquet(f)["big"] for f in fs}).sort_index()
    h.index = (pd.to_datetime(h.index) + pd.Timedelta(days=1)).strftime("%Y%m%d")
    return h


# ---------- 評分：每天算一次 150 檔原始分數，兩個選股池各自重新排名 ----------
_orig_score_pool = S.score_pool
_current_table = [None]
S.score_pool = lambda d, mode="live": _current_table[0]


def raw_table(d, cache):
    p = os.path.join(cache, f"raw_{d:%Y-%m-%d}.pkl")
    recent = d >= pd.Timestamp.now().normalize() - pd.Timedelta(days=30)   # 最近一個月不用快取：資料會補正（K 線校正、法人每天更新）
    if os.path.exists(p) and not recent:
        return pd.read_pickle(p)
    S.AI_CODES = ALL_CODES
    t = _orig_score_pool(d, mode="deadline")
    t.to_pickle(p)
    return t


R52 = [None]        # 收盤價 ÷ 過去 252 個交易日最高收盤價（main 裡算好）


def r52_on(d, index):
    m = R52[0]
    if m is None:
        return pd.Series(np.nan, index=index)
    row = m.loc[:d]
    return (row.iloc[-1] if len(row) else pd.Series(dtype=float)).reindex(index)


def pool_table(raw, pool, d=None):
    """跟 esun_strategy.score_pool 最後一段一樣：在選股池內算百分位排名、取平均"""
    if raw is None or raw.empty:
        return raw
    if pool == "ALL":
        t = raw.copy()
    elif pool in ("NOSHIP", "N52", "N52F", "EPSPOS", "EPSRATE", "EPSPOS52F", "EPSRATE52F"):
        t = raw[~raw.index.isin(SHIP)].copy()
        if pool.startswith("EPS"):
            info = t["EPS資料"].apply(lambda x: x if isinstance(x, dict) else {})
            now = info.apply(lambda x: x.get("EPS", np.nan)).astype(float)
            base = info.apply(lambda x: x.get("去年同季", np.nan)).astype(float)
            # 去年同季虧損或打平 → 這一項給最低分（不能留空：留空會被略過，剩下三項平均反而更高）
            g = t["EPS增幅"].astype(float) if pool.startswith("EPSPOS") else ((now - base) / base.abs()).clip(*RATE_CAP)
            t["EPS增幅"] = g.where(base > 0, -np.inf).where(g.notna() | (base <= 0))
        if pool in ("N52", "N52F") or pool.endswith("52F"):
            r = r52_on(d, t.index)
            if pool == "N52":
                t["52週"] = r.astype(float)
            else:
                t["過濾"] = [list(f) + ([f"距52週高點{1 - x:.0%}"] if pd.notna(x) and x < R52_MIN else [])
                           for f, x in zip(t["過濾"], r)]
    else:
        t = raw[raw.index.isin(AI_POOL)].copy()
    facs = FACTORS + (("52週",) if "52週" in t.columns else ())
    ranks = pd.concat([t[c].astype(float).rank(pct=True) for c in facs], axis=1)
    t["綜合分數"] = ranks.mean(axis=1, skipna=True)
    t = t[t["綜合分數"].notna()].sort_values("綜合分數", ascending=False)
    t["排名"] = range(1, len(t) + 1)
    return t


def stats(nav, mret):
    r = nav.pct_change().dropna()
    total = nav.iat[-1] / nav.iat[0] - 1
    yrs = max(len(r), 1) / 252
    return {"總報酬": total, "年化報酬": (1 + total) ** (1 / yrs) - 1, "年化波動": r.std() * np.sqrt(252),
            "最大回撤": (nav / nav.cummax() - 1).min(),
            "報酬/波動": ((1 + total) ** (1 / yrs) - 1) / (r.std() * np.sqrt(252)) if r.std() > 0 else np.nan,
            "近3個月": (1 + mret.tail(3)).prod() - 1, "月勝率": (mret > 0).mean()}


def out_path(name):
    """檔案被 Excel 開著時無法覆蓋 → 改存成加上時間的新檔名，不中斷"""
    p = os.path.join(OUT_DIR, name)
    try:
        if os.path.exists(p):
            open(p, "a").close()
        return p
    except PermissionError:
        base, ext = os.path.splitext(name)
        p2 = os.path.join(OUT_DIR, f"{base}_{time.strftime('%H%M%S')}{ext}")
        print(f"  ! {name} 正在被其他程式開啟（Excel？），改存成 {os.path.basename(p2)}")
        return p2


def stock_names():
    """從最新行情快照取股票名稱（沒有就留空）"""
    fs = sorted(glob.glob(os.path.join("esun_data", "daily_*.json")))
    for f in reversed(fs[-5:]):
        try:
            q = json.load(open(f, encoding="utf-8"))["quotes"]
            names = {k: v.get("name", "") for k, v in q.items() if v.get("name")}
            if len(names) > 100:
                return names
        except Exception:
            continue
    return {}


def export_excel(states, mret, tcon, tsmc_m, summ, ranked, ids):
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    names = stock_names()
    sec = {k: v["name"] for k, v in SECTOR.items()}

    # 每月持股：月底持股＋整個月的損益貢獻（月中賣掉的也列出來）
    rows = []
    for k, c in enumerate(COMBOS, 1):
        st = states[c]
        months = sorted({m for m, _ in st["pnl"]})
        for m in months:
            me = st["mend"].get(m, {})
            w, sh, roles = me.get("w", {}), me.get("sh", {}), me.get("roles", {})
            tick = sorted({t for mm, t in st["pnl"] if mm == m}, key=lambda t: -w.get(t, 0))
            for t in tick:
                pl = st["pnl"].get((m, t), 0.0)
                rows.append({"月份": m, "代號組": k, "組合": label(c), "代號": t, "名稱": names.get(t, ""),
                             "產業": sec.get(t, ""), "月底角色": roles.get(t, "") if sh.get(t) else "月中出清",
                             "月底權重(%)": round(w.get(t, 0) * 100, 2), "月底張數": sh.get(t, 0) // 1000,
                             "持有天數": st["days"].get((m, t), 0), "當月損益(元)": round(pl),
                             "貢獻(百分點)": round(pl / INIT * 100, 3)})
            if me:
                rows.append({"月份": m, "代號組": k, "組合": label(c), "代號": "現金", "名稱": "", "產業": "",
                             "月底角色": "", "月底權重(%)": round(me["cash"] / me["nav"] * 100, 2), "月底張數": None,
                             "持有天數": None, "當月損益(元)": None, "貢獻(百分點)": None})
    hold = pd.DataFrame(rows)

    trades = pd.DataFrame([r | {"組合": label(COMBOS[r["代號組"] - 1]), "名稱": names.get(r["代號"], "")}
                           for c in COMBOS for r in states[c]["trades"]])
    if len(trades):
        trades = trades[["成交日", "決策日", "代號組", "組合", "代號", "名稱", "動作", "角色", "張數", "股數",
                         "成交價", "金額", "手續費", "證交稅", "交易前股數", "交易後股數"]]

    trow = []
    for k, c in enumerate(COMBOS, 1):
        col = label(c)
        for m in mret.index:
            tot, tc = mret.at[m, col], tcon.at[m, col]
            trow.append({"月份": m, "代號組": k, "組合": col, "當月報酬(%)": round(tot * 100, 2),
                         "台積電貢獻(百分點)": round(tc * 100, 3), "剔除台積電(%)": round((tot - tc) * 100, 2),
                         "台積電占比(%)": round(tc / tot * 100, 1) if abs(tot) > 0.001 else None,
                         "台積電當月漲幅(%)": round(float(tsmc_m.get(m, np.nan)) * 100, 2) if tsmc_m is not None else None})
    tsheet = pd.DataFrame(trow)

    path = out_path("回測明細.xlsx")
    legend = pd.DataFrame({"代號組": list(ids.values()), "組合": list(ids.keys())})
    summ_x = summ.copy()
    for col in summ_x.columns:
        if summ_x[col].dtype.kind == "f" and col not in ("平均持股", "月換手"):
            summ_x[col] = (summ_x[col] * 100).round(2)
    summ_x = summ_x.rename(columns={c_: f"{c_}(%)" for c_ in summ_x.columns
                                    if summ_x[c_].dtype.kind == "f" and c_ not in ("平均持股", "月換手")})
    notes = pd.DataFrame({"說明": [
        "每月本金 10 億、從現金開始建倉，月底淨值 ÷ 10 億 − 1 = 當月報酬，月與月之間不複利、不延續持股。",
        "每月持股：月底持股的權重與張數，加上當月每一檔的損益貢獻（月中賣掉的標「月中出清」）。各檔貢獻加總 = 當月報酬。",
        "貢獻(百分點)：該檔當月損益 ÷ 10 億，含買賣手續費與證交稅。",
        "交易明細：決策日收盤後選股，成交日以 (高+低+收)/3 近似均價成交。動作 BUY/ADD/TRIM/SELL_ALL 同 D-Plan v4.2。",
        "台積電占比：台積電貢獻 ÷ 當月報酬；當月報酬接近 0 時比例沒有意義，留空。",
        "K 線為還原價（含息），比賽期間配息不計入，回測報酬略高。",
    ]})
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        summ_x.to_excel(xw, sheet_name="摘要", index=False)
        (mret * 100).round(2).rename(columns=ids).to_excel(xw, sheet_name="每月報酬(%)")
        legend.to_excel(xw, sheet_name="每月報酬(%)", index=False, startrow=len(mret) + 3)
        tsheet.to_excel(xw, sheet_name="台積電每月", index=False)
        hold.to_excel(xw, sheet_name="每月持股", index=False)
        if len(trades):
            trades.to_excel(xw, sheet_name="交易明細", index=False)
        notes.to_excel(xw, sheet_name="說明", index=False)
        head = PatternFill("solid", start_color="D9E1F2")
        for ws in xw.book.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    cell.font = Font(name="Arial", bold=cell.row == 1)
            for cell in ws[1]:
                cell.fill = head
            ws.freeze_panes = "A2"
            for i, col in enumerate(ws.iter_cols(min_row=1, max_row=min(ws.max_row, 200)), 1):
                width = max((len(str(c.value)) if c.value is not None else 0) for c in col)
                ws.column_dimensions[get_column_letter(i)].width = min(max(8, width * 1.6 + 2), 60)
            if ws.max_row > 1 and ws.title in ("每月持股", "交易明細", "台積電每月"):
                ws.auto_filter.ref = ws.dimensions
    print(f"  Excel：每月持股 {len(hold):,} 列、交易明細 {len(trades):,} 筆")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--fresh", action="store_true", help="清掉評分快取重算")
    a = ap.parse_args()

    th = tdcc_hist()
    TDCC.history = lambda: th
    INST.OUT_DIR = INST_DIR
    live_dir = os.path.join(os.path.dirname(HERE), "esun_inst")       # 回測用的歷史只到抓的那天；之後的用每日流程每天更新的
    def _inst_load(sid):
        parts = []
        for dd in (INST_DIR, live_dir):
            p = os.path.join(dd, f"{sid}.parquet")
            if os.path.exists(p):
                x = _orig_read_parquet(p)["net"]
                x.index = pd.to_datetime(x.index)
                parts.append(x)
        if not parts:
            return pd.Series(dtype=float)
        s = pd.concat(parts)
        return s[~s.index.duplicated(keep="last")].sort_index()
    INST.load = _inst_load
    n_inst = len(glob.glob(os.path.join(INST_DIR, "*.parquet")))
    n_raw = len(glob.glob(os.path.join(RAW_DIR, "*.parquet")))
    cache = os.path.join(HERE, "bt_cache", "scores_" + ("tdcc" if th is not None else "notdcc") + ("_rawpx" if n_raw else ""))
    if a.fresh and os.path.isdir(cache):
        shutil.rmtree(cache)
    os.makedirs(cache, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"資料：三大法人 {n_inst} 檔｜集保大戶 {'無（只用三項評分）' if th is None else f'{th.shape[1]} 檔、{th.shape[0]} 週'}")
    print(f"      原始收盤價 {n_raw} 檔" + ("（EPS 增幅 ÷ 股價用原始價）" if n_raw else "（沒有 → EPS 增幅 ÷ 股價仍用還原價，先跑 esun_bt_fetch.py）"))
    if n_inst < 100:
        print("  ! 三大法人歷史不足，先跑 python _research\\esun_bt_fetch.py；不然法人過濾等於沒作用")

    bars = {s: S._read_px(s) for s in sorted(UNIVERSE)}
    miss = sorted(s for s, b in bars.items() if b is None)
    bars = {s: b for s, b in bars.items() if b is not None}
    if miss:
        print(f"  ! 沒有 K 線：{miss}")
    close_raw = pd.DataFrame({s: b["Close"] for s, b in bars.items()}).sort_index()
    cnt = close_raw.notna().sum(axis=1)
    close_raw = close_raw[cnt >= 0.9 * cnt.max()]
    close = close_raw.ffill()
    full = {s: _orig_read_px(s) for s in bars}                       # 52 週高點要用 PX_FROM 之前的資料
    fc = pd.DataFrame({s: b["Close"] for s, b in full.items() if b is not None}).sort_index().ffill()
    R52[0] = fc / fc.rolling(252, min_periods=200).max()
    print(f"52 週高點：{int(R52[0].loc[:a.start].iloc[-1].notna().sum())} 檔在回測起點有一年以上的 K 線")

    def vw(b):
        if {"High", "Low"} <= set(b.columns):
            return (b["High"] + b["Low"] + b["Close"]) / 3
        return b["Close"]

    vwap = pd.DataFrame({s: vw(b) for s, b in bars.items()}).reindex(close.index)
    seasoned = close_raw.notna().cumsum() > 5
    jump = ((close_raw / close.shift(1) - 1).abs() > JUMP) & seasoned
    cal = close.index
    days = cal[cal >= pd.Timestamp(a.start)]
    if len(days) == 0 or cal.get_loc(days[0]) == 0:
        print("日期範圍有問題，檢查 --start"); return
    d0 = cal[cal.get_loc(days[0]) - 1]
    print(f"回測期間：{days[0]:%Y-%m-%d} 到 {days[-1]:%Y-%m-%d}，{len(days)} 個交易日，{len(COMBOS)} 組，每月本金 10 億重新開始")
    hit = set()

    states = {c: {"led": None, "prev": None, "navs": [], "dret": [], "nh": [], "roles": {},
                  "pnl": {}, "days": {}, "mend": {}, "trades": [],
                  "err": 0, "err_msg": None, "turn": 0.0} for c in COMBOS}
    t0 = time.time()
    for i, day in enumerate(days):
        d = cal[cal.get_loc(day) - 1]
        raw = raw_table(d, cache)
        tables = {p: pool_table(raw, p, d) for p in POOLS}
        px_d = {s: float(v) for s, v in close.loc[d].items() if pd.notna(v)}
        px_t = {s: float(v) for s, v in close.loc[day].items() if pd.notna(v)}
        vw_t = vwap.loc[day]
        new_month = i == 0 or day.month != days[i - 1].month
        for c in COMBOS:
            st = states[c]
            if new_month:                                   # 每月重新開始：10 億現金、沒有持股、沒有前期核心
                st["led"], st["prev"] = L.new_state(f"{d:%Y-%m-%d}"), None
            led = st["led"]
            nav_before = led["nav"]
            cur_w = {s: sh * px_d[s] / led["nav"] for s, sh in led["holdings"].items() if s in px_d}
            S.W_CORE, S.W_SMALL = WTS[c[1]][1], WTS[c[1]][2]
            S.FILTER_SCOPE = c[2]
            _current_table[0] = tables[c[0]]
            orders = []
            try:
                tg, _ = S.build_targets(d, st["prev"], cur_w, mode="deadline")
                og = tg
                if c[1] == "EQ":      # 均分時 19 檔一視同仁：現金不足就全部等比例縮，不先砍小部位
                    og = dict(tg, roles={s: ("核心" if r == "小部位" else r) for s, r in tg["roles"].items()})
                fill = c[1] == "EQ" and led["cash"] / led["nav"] > tg["cash_target"] + 0.02
                band0 = O.BAND
                O.BAND = 0.0 if fill else band0     # 均分：現金多出 2% 以上時不套用無交易帶，補足建倉
                try:
                    _, orders, _, _ = O.make_orders(og, {"nav": led["nav"], "cash": led["cash"],
                                                         "holdings": led["holdings"]}, px_d)
                finally:
                    O.BAND = band0
                st["prev"], st["roles"] = tg, tg["roles"]
            except Exception as e:
                st["err"] += 1
                st["err_msg"] = st["err_msg"] or f"{d:%Y-%m-%d} {type(e).__name__}：{str(e)[:120]}"
            vd = {o["ticker"]: float(vw_t[o["ticker"]]) for o in orders
                  if o["ticker"] in vw_t.index and pd.notna(vw_t[o["ticker"]])}
            orders = [o for o in orders if o["ticker"] in vd]
            st["turn"] += sum(o["shares"] * vd[o["ticker"]] for o in orders) / led["nav"]
            h0 = dict(led["holdings"])
            flow = {}
            for o in orders:
                t, q, px = o["ticker"], o["shares"], vd[o["ticker"]]
                amt = q * px
                fee, tax = amt * L.FEE, (amt * L.TAX if o["side"] == "SELL" else 0.0)
                flow[t] = flow.get(t, 0.0) + ((-amt - fee) if o["side"] == "BUY" else (amt - fee - tax))
                before = h0.get(t, 0)
                after = before + (q if o["side"] == "BUY" else -q)
                act = ("ADD" if before > 0 else "BUY") if o["side"] == "BUY" else ("SELL_ALL" if after == 0 else "TRIM")
                st["trades"].append({"成交日": f"{day:%Y-%m-%d}", "決策日": f"{d:%Y-%m-%d}", "代號組": COMBOS.index(c) + 1,
                                     "代號": t, "動作": act, "角色": st["roles"].get(t, "出清" if after == 0 else ""),
                                     "股數": q, "張數": q // 1000, "成交價": round(px, 2), "金額": round(amt),
                                     "手續費": round(fee), "證交稅": round(tax), "交易前股數": before, "交易後股數": after})
            led = L.apply_trades(led, orders, vd, f"{day:%Y-%m-%d}")
            for s in list(led["holdings"]):
                if s in jump.columns and jump.at[day, s] and px_d.get(s) and px_t.get(s):
                    led["holdings"][s] = int(round(led["holdings"][s] * px_d[s] / px_t[s]))
                    hit.add((s, day))
            L.mark_to_market(led, px_t)
            st["led"] = led
            st["navs"].append((day, led["nav"]))
            st["dret"].append((day, led["nav"] / nav_before - 1))
            # 每檔當天損益（含買賣成本）：期末市值 − 期初市值 + 賣出收入 − 買進支出；加總 = 淨值變動
            mk = f"{day:%Y-%m}"
            for t in set(h0) | set(led["holdings"]) | set(flow):
                pl = led["holdings"].get(t, 0) * px_t.get(t, 0) - h0.get(t, 0) * px_d.get(t, 0) + flow.get(t, 0.0)
                st["pnl"][(mk, t)] = st["pnl"].get((mk, t), 0.0) + pl
                if led["holdings"].get(t, 0) > 0:
                    st["days"][(mk, t)] = st["days"].get((mk, t), 0) + 1
            st["mend"][mk] = {"date": f"{day:%Y-%m-%d}", "nav": led["nav"], "cash": led["cash"],
                              "w": {t: sh * px_t[t] / led["nav"] for t, sh in led["holdings"].items()},
                              "sh": dict(led["holdings"]), "roles": dict(st["roles"])}
            st["nh"].append(len(led["holdings"]))
        if (i + 1) % 20 == 0 or i + 1 == len(days):
            best = max(COMBOS, key=lambda c: states[c]["led"]["nav"])
            print(f"  {i + 1:>3}/{len(days)} {day:%Y-%m-%d}｜已用 {time.time() - t0:,.0f} 秒｜"
                  f"本月目前最高：{label(best)} {states[best]['led']['nav'] / INIT - 1:+.1%}")

    # ---------- 整理結果（每月獨立，本金 10 億）----------
    navs = pd.DataFrame({label(c): pd.Series(dict(states[c]["navs"])) for c in COMBOS})
    dret = pd.DataFrame({label(c): pd.Series(dict(states[c]["dret"])) for c in COMBOS})
    b = _orig_read_px("0050")
    if b is not None:
        bc = b["Close"].reindex(pd.DatetimeIndex([d0]).append(navs.index), method="ffill")
        dret["0050（對照）"] = bc.pct_change().iloc[1:]
    mkey = dret.index.strftime("%Y-%m")
    mret = (1 + dret).groupby(mkey).prod() - 1               # 月內逐日連乘 = 月底淨值 ÷ 10 億 − 1

    def month_dd(x):
        nav = (1 + x).cumprod()
        return (nav / nav.cummax().clip(lower=1) - 1).min()

    mdd = dret.groupby(mkey).agg(month_dd)
    rows = []
    for k, col in enumerate(dret.columns):
        m = mret[col]
        r = {"代號": str(k + 1) if k < len(COMBOS) else "—", "組合": col,
             "平均月報酬": m.mean(), "月報酬中位數": m.median(), "月報酬標準差": m.std(),
             "日波動(年化)": dret[col].std() * np.sqrt(252), "最差月": m.min(), "最好月": m.max(),
             "月內最大回撤": mdd[col].min(), "賺錢月數": f"{int((m > 0).sum())}/{len(m)}"}
        if "0050（對照）" in mret and k < len(COMBOS):
            r["贏0050月數"] = f"{int((m > mret['0050（對照）']).sum())}/{len(m)}"
        if k < len(COMBOS):
            st = states[COMBOS[k]]
            r |= {"平均持股": np.mean(st["nh"]), "月換手": st["turn"] / len(m), "出錯天數": st["err"]}
        rows.append(r)
    summ = pd.DataFrame(rows)
    ranked = summ[summ["代號"] != "—"].sort_values(["平均月報酬", "日波動(年化)"], ascending=[False, True])

    ids = {col: (str(k + 1) if k < len(COMBOS) else "0050") for k, col in enumerate(dret.columns)}
    print("\n" + "=" * 92)
    print("每月報酬（%，每月本金 10 億重新開始，不複利）")
    print("=" * 92)
    for col, i_ in ids.items():
        if i_ != "0050":
            print(f"  {i_}：{col}")
    print((mret * 100).rename(columns=ids).to_string(float_format=lambda x: f"{x:6.1f}"))

    print("\n" + "=" * 92)
    print("摘要（依平均月報酬排序，接近時看日波動）")
    print("=" * 92)
    print(f"  {'代號':<4}{'組合':<24}{'平均月報酬':>9}{'中位數':>8}{'月標準差':>8}{'日波動年化':>9}{'最差月':>8}"
          f"{'最好月':>8}{'月內回撤':>8}{'賺錢月':>7}{'贏0050':>7}{'月換手':>7}{'出錯':>5}")
    for _, r in pd.concat([ranked, summ[summ["代號"] == "—"]]).iterrows():
        mine = r["代號"] != "—"
        print(f"  {r['代號']:<4}{r['組合']:<24}{r['平均月報酬']:>9.2%}{r['月報酬中位數']:>8.2%}{r['月報酬標準差']:>8.2%}"
              f"{r['日波動(年化)']:>9.1%}{r['最差月']:>8.1%}{r['最好月']:>8.1%}{r['月內最大回撤']:>8.1%}{r['賺錢月數']:>7}"
              + (f"{r.get('贏0050月數', ''):>7}{r['月換手']:>6.1f}x{int(r['出錯天數']):>5}" if mine else ""))
    for c in COMBOS:
        if states[c]["err_msg"]:
            print(f"  ! {label(c)} 第一次出錯：{states[c]['err_msg']}")
    if _raw_miss:
        print(f"\n  ! 沒有原始收盤價、EPS 增幅仍用還原價的股票：{sorted(_raw_miss)}")
    if hit:
        print("\n  被當成減資／分割、股數換算的持股：" + "、".join(f"{s} {d:%Y-%m-%d}" for s, d in sorted(hit, key=lambda x: x[1])))

    top = ranked.head(3)
    print("\n平均月報酬前兩名：" + "、".join(f"{r['代號']} {r['組合']}" for _, r in top.head(2).iterrows()))
    if len(top) == 3 and top["平均月報酬"].iat[1] - top["平均月報酬"].iat[2] < 0.003:
        print(f"  ! 第 2、3 名平均月報酬只差 {(top['平均月報酬'].iat[1] - top['平均月報酬'].iat[2]):.2%}，"
              f"比日波動：{top['組合'].iat[1]} {top['日波動(年化)'].iat[1]:.1%} vs "
              f"{top['組合'].iat[2]} {top['日波動(年化)'].iat[2]:.1%}")

    # ---------- 台積電貢獻 vs 剔除台積電 ----------
    # 每月本金都是 10 億，所以當月報酬 = 當月每天損益加總 ÷ 10 億，可以直接拆成「台積電」＋「其他持股與現金」
    tcon = pd.DataFrame({label(c): pd.Series({m: v for (m, t), v in states[c]["pnl"].items() if t == S.TSMC})
                         for c in COMBOS}).reindex(mret.index).fillna(0.0) / INIT
    mine = [label(c) for c in COMBOS]
    ex = mret[mine] - tcon
    print("\n" + "=" * 92)
    print("剔除台積電（%，當月報酬扣掉台積電的貢獻，其他持股與現金的部分）")
    print("=" * 92)
    print((ex * 100).rename(columns=ids).to_string(float_format=lambda x: f"{x:6.1f}"))
    tsmc_m = None
    bt = _orig_read_px(S.TSMC)
    if bt is not None:
        tc = bt["Close"].reindex(pd.DatetimeIndex([d0]).append(dret.index), method="ffill")
        tsmc_m = (1 + tc.pct_change().iloc[1:]).groupby(dret.index.strftime("%Y-%m")).prod() - 1
    print(f"\n  {'代號':<4}{'組合':<24}{'含台積電':>9}{'台積電貢獻':>10}{'剔除台積電':>10}{'台積電占比':>10}"
          f"{'剔除後月標準差':>13}{'剔除後賺錢月':>12}")
    for col in ranked["組合"]:
        tot, tc_, x = mret[col].mean(), tcon[col].mean(), ex[col].mean()
        print(f"  {ids[col]:<4}{col:<24}{tot:>9.2%}{tc_:>10.2%}{x:>10.2%}"
              f"{(tc_ / tot if tot else float('nan')):>10.0%}{ex[col].std():>13.2%}"
              f"{f'{int((ex[col] > 0).sum())}/{len(ex)}':>12}")
    if tsmc_m is not None:
        print(f"  （參考）台積電本身平均月漲幅 {tsmc_m.mean():.2%}；0050 平均月報酬 {mret['0050（對照）'].mean():.2%}"
              if "0050（對照）" in mret else f"  （參考）台積電本身平均月漲幅 {tsmc_m.mean():.2%}")
    pd.concat({"含台積電": mret[mine], "台積電貢獻": tcon, "剔除台積電": ex}, axis=1).to_csv(
        out_path("台積電拆解.csv"), encoding="utf-8-sig", float_format="%.4f")

    print("\n" + "=" * 92)
    print("台積電每月貢獻（百分點；當月報酬裡有幾個百分點來自台積電）")
    print("=" * 92)
    tshow = (tcon * 100).rename(columns=ids)
    if tsmc_m is not None:
        tshow.insert(0, "台積電漲幅", (tsmc_m.reindex(tshow.index) * 100))
    print(tshow.to_string(float_format=lambda x: f"{x:6.2f}"))
    export_excel(states, mret, tcon, tsmc_m, summ, ranked, ids)

    if raw is not None and len(raw):
        cov = {f: int(raw[f].notna().sum()) for f in FACTORS}
        print(f"\n最後一天評分涵蓋（{len(raw)} 檔）：" + "、".join(f"{k} {v}" for k, v in cov.items()))

    mret.to_csv(out_path("每月報酬.csv"), encoding="utf-8-sig", float_format="%.4f")
    navs.to_csv(out_path("每日淨值.csv"), encoding="utf-8-sig", float_format="%.0f")  # 每月初重設為 10 億
    summ.to_csv(out_path("摘要.csv"), encoding="utf-8-sig", index=False, float_format="%.4f")
    print(f"\n已輸出 {OUT_DIR}\\回測明細.xlsx（每月持股、交易明細、台積電每月）、每月報酬.csv、每日淨值.csv、摘要.csv、台積電拆解.csv")


if __name__ == "__main__":
    main()