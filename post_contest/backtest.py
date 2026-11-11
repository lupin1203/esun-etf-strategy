# -*- coding: utf-8 -*-
r"""
post_contest\backtest.py — 賽後研究回測（獨立於比賽程式，只讀比賽資料，只寫 post_contest\out）

  修正比賽回測的三個問題：
    1. 倖存者偏差：選股池改用當時的市值排名（fetch_pit.py 的 members.json），含後來下市的公司
    2. 連續持有、逐日複利（比賽回測每月從現金重建）
    3. 交易成本含滑價；鎖漲停買不到、鎖跌停賣不掉；下市時以最後收盤價出場
  事前定好的組別（看結果前定案，之後不依結果挑參數）：
    G0  現行 20 檔｜固定 2026 名單（用來量偏差）
    G1  現行 20 檔｜當時名單
    G2  現行 20 檔｜當時名單，再排除生技
    G3  8 檔等權｜排除航運、生技｜不持有台積電
    G4  8 檔等權｜排除航運、生技｜台積電一起排名
    G5  台積電固定 10%＋7 檔等權｜排除航運、生技
    （8 檔組：沒有 20 檔下限、沒有單檔上限、不用被擋的股票補足、沒有防守模式；記憶體、同產業最多 3 檔照舊）
  限制：還原價含息（未計股利稅）；可買零股（不受整張限制）；用 (高+低+收)/3 當均價
用法（在 SMC- 資料夾）：python post_contest\backtest.py [--check]
"""
import os, sys, json, glob
from dataclasses import dataclass
import numpy as np, pandas as pd, requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from dplan_validator import UNIVERSE

DATA, OUT = os.path.join(HERE, "data"), os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)
FEE, TAX = 0.001425, 0.003
BT_START, PX_FROM = "2023-01-03", "2021-09-01"
MEMORY = {"2344", "2408", "2337", "3260", "8299", "5289", "2451"}
TSMC, MKT = "2330", "0050"


@dataclass
class Cfg:
    key: str
    name: str
    universe: str = "pit"          # pit｜fixed
    excl: tuple = ("航運",)
    n_core: int = 8
    w_core: float = 0.095
    n_small: int = 11
    w_small: float = 0.01
    tsmc: str = "fixed"            # fixed（固定 10%，8–12% 不動）｜ranked｜none
    w_tsmc: float = 0.10
    contest: bool = True           # 比賽限制：補足 20 檔、9.9% 減碼、防守模式、最壞情況現金
    slip: float = 0.001
    reset: bool = False            # 每月重建（只用來跟比賽回測對帳）
    freq: str = "D"                # D 每日｜W 每週第一個交易日｜M 每月 11 日起第一個交易日（當月營收多已公布）


E8 = dict(excl=("航運", "生技"), n_small=0, w_small=0.0, contest=False)
GROUPS = [
    Cfg("G0", "現行20檔｜固定名單", universe="fixed"),
    Cfg("G1", "現行20檔｜當時名單"),
    Cfg("G2", "現行20檔｜當時名單＋排除生技", excl=("航運", "生技")),
    Cfg("G3", "8檔等權｜不持有台積電", w_core=0.97 / 8, tsmc="none", **E8),
    Cfg("G4", "8檔等權｜台積電一起排名", w_core=0.97 / 8, tsmc="ranked", **E8),
    Cfg("G5", "台積電10%＋7檔等權", n_core=7, w_core=0.87 / 7, tsmc="fixed", **E8),
]


# ---------- 讀資料 ----------
def bars_of(sid):
    for p in (f"local_data/{sid}.TW.parquet", f"local_data_otc/{sid}.TWO.parquet", os.path.join(DATA, "adj", f"{sid}.parquet")):
        if os.path.exists(p):
            df = pd.read_parquet(p)
            df.index = pd.to_datetime(df.index)
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df.columns = [c.capitalize() for c in df.columns]
            if not len(df):
                continue
            df = df[~(df["Volume"] == 0)]
            df = df[~df.index.duplicated(keep="last")].sort_index()
            rp = os.path.join(DATA, "raw", f"{sid}.parquet")      # 非比賽 150 檔的本機 K 線停更：後面接 FinMind 原始價，避免被當成下市
            if os.path.exists(rp):
                x = pd.read_parquet(rp)
                x.index = pd.to_datetime(x.index)
                x = x[(x.index > df.index.max()) & (x["Volume"] > 0)]
                if len(x):
                    df = pd.concat([df[["Open", "High", "Low", "Close", "Volume"]], x[["Open", "High", "Low", "Close", "Volume"]]]).sort_index()
            return df.loc[PX_FROM:]
    return None


def raw_close(sid, adj):
    for p, c in ((f"_research/bt_data/rawpx/{sid}.parquet", "close"), (os.path.join(DATA, "raw", f"{sid}.parquet"), "Close")):
        if os.path.exists(p):
            s = pd.read_parquet(p)[c]
            s.index = pd.to_datetime(s.index)
            if len(s):
                return pd.concat([s, adj[adj.index > s.index.max()]]).sort_index()   # 之後的是官方未還原收盤
    return adj


def eps_of(sid):
    for p in (f"revenue_cache/eps_history/{sid}.parquet", os.path.join(DATA, "eps", f"{sid}.parquet")):
        if os.path.exists(p):
            e = pd.read_parquet(p)
            if len(e):
                e.index = pd.to_datetime(e.index)
                return e.sort_index()
    return None


def rev_of(sid):
    p = f"revenue_cache/esun_revenue/{sid}.parquet"
    if os.path.exists(p):
        r = pd.read_parquet(p)
        r.index = pd.to_datetime(r.index)
        return r[["yoy", "available_date"]].sort_index()
    p = os.path.join(DATA, "rev", f"{sid}.parquet")
    if os.path.exists(p):
        r = pd.read_parquet(p)
        if len(r):
            r.index = pd.to_datetime(r.index)
            return r[["yoy", "available_date"]].sort_index()
    p = f"revenue_cache/history/{sid}.parquet"              # 這份的日期是公布月，往前移一個月才是營收月份
    if os.path.exists(p):
        r = pd.read_parquet(p)
        r.index = pd.to_datetime(r.index) - pd.DateOffset(months=1)
        r["available_date"] = r.index + pd.DateOffset(months=1) + pd.Timedelta(days=9)
        return r[["yoy", "available_date"]].sort_index()
    return None


def inst_of(sid):
    parts = []
    for p in (f"_research/bt_data/inst/{sid}.parquet", f"esun_inst/{sid}.parquet", os.path.join(DATA, "inst", f"{sid}.parquet")):
        if os.path.exists(p):
            x = pd.read_parquet(p)["net"]
            x.index = pd.to_datetime(x.index)
            parts.append(x)
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts)
    return s[~s.index.duplicated(keep="last")].sort_index()


def sectors():
    sec = {k: v["name"] for k, v in json.load(open("esun_sector.json", encoding="utf-8")).items()}
    p = os.path.join(DATA, "info.json")
    if not os.path.exists(p):
        j = requests.get("https://api.finmindtrade.com/api/v4/data", timeout=60,
                         params={"dataset": "TaiwanStockInfo", "token": os.environ.get("FINMIND_TOKEN", "")}).json()
        json.dump({r["stock_id"]: r["industry_category"] for r in j.get("data", [])}, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    info = json.load(open(p, encoding="utf-8"))
    sl = pd.read_csv("stock_list.csv", dtype=str)
    lst = {t.split(".")[0]: c for t, c in zip(sl["ticker"], sl["category"])}
    return lambda s: sec.get(s) or info.get(s) or lst.get(s) or "其他"


# ---------- 訊號面板（每個決策日一列）----------
def factor_on(days, avail, values):
    """avail：可用日；values：當時最新一期的值（NaN 也算最新，不往回找舊的）"""
    s = pd.Series(values, index=pd.to_datetime(avail))
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.reindex(days, method="ffill")


def rev_surprise(r, days):
    if r is None or len(r) < 4:
        return pd.Series(np.nan, index=days)
    y = r["yoy"].astype(float).clip(-500, 500)
    out = []
    for m in r.index:
        pv = [y.get(m - pd.DateOffset(months=k), np.nan) for k in (1, 2, 3)]
        pv = [x for x in pv if pd.notna(x)]
        out.append(y[m] - np.mean(pv) if pd.notna(y[m]) and len(pv) >= 2 else np.nan)
    return factor_on(days, r["available_date"], out)


def eps_delta(e, days):
    if e is None:
        return pd.Series(np.nan, index=days)
    base = [e["eps"].get(q - pd.DateOffset(years=1), np.nan) for q in e.index]
    return factor_on(days, e["available_date"], e["eps"].astype(float).values - np.array(base, dtype=float))


def margins(e, days):
    if e is None or not {"gm", "om", "nm"} <= set(e.columns) or len(e) < 2:
        return pd.Series(np.nan, index=days)
    out = [np.nan]
    for k in range(1, len(e)):
        q, p = e.iloc[k], e.iloc[k - 1]
        ok = [q[c] >= p[c] for c in ("gm", "om", "nm") if pd.notna(q[c]) and pd.notna(p[c])]
        out.append(3 * sum(ok) / len(ok) if ok and (e.index[k] - e.index[k - 1]).days <= 100 else np.nan)
    return factor_on(days, e["available_date"], out)


def tech_filter(b, inst, days):
    c, v = b["Close"], b["Volume"]
    down = pd.concat([(m < m.shift(1)) for m in (c.rolling(n).mean() for n in (5, 10, 20))], axis=1).all(axis=1)
    r, avgv = c.pct_change(), v.rolling(20).mean().shift(1)
    crash = ((r <= -0.03) & (v >= 2 * avgv)).values
    ra, va = r.values, v.values
    blocked = np.zeros(len(c), bool)
    last, released = -1, True
    for i in range(len(c)):
        if crash[i]:
            last, released = i, False
        elif last >= 0 and ra[i] > 0 and va[i] > va[last]:
            released = True
        blocked[i] = last >= 0 and i - last < 60 and not released
    f = down | pd.Series(blocked, index=c.index)
    f = f.where(np.arange(len(c)) >= 24, False)
    out = f.reindex(days, method="ffill").fillna(False).astype(bool)
    if len(inst) >= 3:
        s = (inst < 0).astype(int).rolling(3).sum() == 3
        out = out | s.reindex(days, method="ffill").fillna(False).astype(bool)
    return out


def build_panels():
    mem = json.load(open(os.path.join(DATA, "members.json"), encoding="utf-8"))
    ever = {s for m in mem.values() for s in m["listed"] + m["otc"]}
    sids = sorted(ever | UNIVERSE | {TSMC})
    bars = {s: bars_of(s) for s in sids}
    missing = sorted(s for s, b in bars.items() if b is None or not len(b))
    bars = {s: b for s, b in bars.items() if s not in missing}
    days = bars_of(TSMC).index
    P = {"close": pd.DataFrame({s: b["Close"] for s, b in bars.items()}).reindex(days),
         "high": pd.DataFrame({s: b["High"] for s, b in bars.items()}).reindex(days),
         "low": pd.DataFrame({s: b["Low"] for s, b in bars.items()}).reindex(days)}
    P["vwap"] = (P["high"] + P["low"] + P["close"]) / 3
    rev, epsd, mar, p20, liq, flt = {}, {}, {}, {}, {}, {}
    for s, b in bars.items():
        e = eps_of(s)
        rev[s] = rev_surprise(rev_of(s), days)
        epsd[s] = eps_delta(e, days)
        mar[s] = margins(e, days)
        p20[s] = raw_close(s, b["Close"]).rolling(20, min_periods=1).mean().reindex(days, method="ffill")
        v = b["Volume"].rolling(20).mean()
        lq = (v >= 500_000).reindex(days, method="ffill", tolerance=pd.Timedelta(days=7))
        liq[s] = lq.where(lq.notna(), False).astype(bool)
        flt[s] = tech_filter(b, inst_of(s), days)
    P.update(rev=pd.DataFrame(rev), eps=pd.DataFrame(epsd) / pd.DataFrame(p20), mar=pd.DataFrame(mar),
             liq=pd.DataFrame(liq), flt=pd.DataFrame(flt))
    return P, days, mem, missing


def members_on(mem, d):
    """季底名單從下個月起生效"""
    use = None
    for q in sorted(mem):
        if (pd.Timestamp(q) + pd.offsets.MonthBegin(1)) <= d:
            use = q
    use = use or sorted(mem)[0]
    return set(mem[use]["listed"]) | set(mem[use]["otc"])


# ---------- 選股（照 esun_strategy.build_targets）----------
def score_table(P, d, pool):
    idx = sorted(s for s in pool if s in P["liq"].columns and P["liq"].at[d, s])   # 固定順序：同分時結果才不會每次跑都不同
    if not idx:
        return pd.DataFrame()
    t = pd.DataFrame({"rev": P["rev"].loc[d, idx], "eps": P["eps"].loc[d, idx], "mar": P["mar"].loc[d, idx]}).astype(float)
    t["score"] = t.rank(pct=True).mean(axis=1, skipna=True)
    t["flt"] = P["flt"].loc[d, idx].astype(bool)
    t = t[t["score"].notna()].sort_values("score", ascending=False, kind="mergesort")
    t["rank"] = range(1, len(t) + 1)
    return t


def targets(cfg, t, ind, prev, cur_w):
    held = {k for k, w in cur_w.items() if w > 0}
    prev_core = set(prev.get("core", []))
    groups = {}
    for s in t.index:
        groups.setdefault(ind(s), set()).add(s)
    caps = [(MEMORY, 3)] + [(g, 3) for g in groups.values()]
    core = []

    def eligible(s):
        return (not t.at[s, "flt"]) or s in held

    def add(s, force=False):
        if len(core) >= cfg.n_core or s in core or not (force or eligible(s)):
            return
        for grp, cap in caps:
            if s in grp and sum(1 for c in core if c in grp) >= cap:
                return
        core.append(s)

    for s in t.index:
        if s in prev_core and t.at[s, "rank"] <= 12:
            add(s)
    for s in t.index:
        add(s)
    small = [s for s in t.index if s not in core and eligible(s)][:cfg.n_small]
    if cfg.contest and len(core) + len(small) < cfg.n_core + cfg.n_small:
        for s in t.index:
            add(s, force=True)
        for s in t.index:
            if len(small) >= cfg.n_small:
                break
            if s not in core and s not in small:
                small.append(s)
    n_pass = int((~t["flt"]).sum())
    was_def = bool(prev.get("defensive"))
    dfn = cfg.contest and (n_pass <= 9 or (was_def and n_pass <= 15))
    w = {}
    if cfg.tsmc == "fixed":
        cw = cur_w.get(TSMC)
        w[TSMC] = 0.24 if dfn else (cw if cw is not None and 0.08 <= cw <= 0.12 else cfg.w_tsmc)
    wc = 0.05375 if dfn else cfg.w_core
    w.update({s: wc for s in core})
    w.update({s: cfg.w_small for s in small})
    return {"w": w, "core": core, "defensive": dfn, "cash": 1 - sum(w.values())}


def plan_trades(cfg, tg, cur_w, band):
    plan = {}
    for s in sorted(set(tg["w"]) | set(cur_w)):
        w, c = tg["w"].get(s, 0.0), cur_w.get(s, 0.0)
        over = cfg.contest and c > (0.249 if s == TSMC else 0.099)
        if c > 0 and w > 0 and not over and abs(c - w) < min(band, 0.5 * w):
            continue
        plan[s] = w
    return plan


# ---------- 模擬 ----------
def simulate(cfg, P, days, mem, ind):
    close = P["close"]
    cl = close.ffill()
    last_day = close.apply(lambda c: c.last_valid_index())
    run = days[days >= BT_START]
    cash, sh, prev = 1.0, {}, {}
    navs, bought, sold, costs, nh, cashw, defs, tsmc_top, scored = [], 0.0, 0.0, 0.0, [], [], 0, [], []
    fixed = sorted(UNIVERSE)
    held_log = []
    last_reb = None
    for i, day in enumerate(run):
        d = days[days.get_loc(day) - 1]
        if cfg.reset and (i == 0 or day.month != run[i - 1].month):
            cash, sh, prev = cash + sum(q * cl.at[d, s] for s, q in sh.items()), {}, {}
        # 下市：以最後收盤價出場
        for s in list(sh):
            if last_day[s] is not None and d >= last_day[s]:
                cash += sh.pop(s) * cl.at[last_day[s], s] * (1 - FEE - TAX)
        nav = cash + sum(q * cl.at[d, s] for s, q in sh.items())
        cur_w = {s: q * cl.at[d, s] / nav for s, q in sh.items()}
        if cfg.freq == "W":
            reb = last_reb is None or d.isocalendar()[:2] != last_reb.isocalendar()[:2]
        elif cfg.freq == "M":
            reb = last_reb is None or (d.day >= 11 and (d.year, d.month) != (last_reb.year, last_reb.month))
        else:
            reb = True
        t = pd.DataFrame()
        if reb:
            last_reb = d
            pool = set(fixed if cfg.universe == "fixed" else members_on(mem, d))
            pool = {s for s in pool if not any(x in ind(s) for x in cfg.excl)}
            if cfg.tsmc == "ranked":
                pool.add(TSMC)
            else:
                pool.discard(TSMC)
            t = score_table(P, d, pool)
            scored.append(len(t))
            if cfg.tsmc == "ranked" and TSMC in t.index:
                tsmc_top.append(int(t.at[TSMC, "rank"]))
        trades = {}
        if len(t):
            tg = targets(cfg, t, ind, prev, cur_w)
            prev = tg
            defs += bool(tg["defensive"])
            plan = plan_trades(cfg, tg, cur_w, 0.015)
            est = cash - sum((plan[s] - cur_w.get(s, 0.0)) * nav for s in plan)
            if est / nav > tg["cash"] + 0.02 or (cfg.contest and est / nav >= 0.235):
                plan = plan_trades(cfg, tg, cur_w, 0.0)
            trades = {s: plan[s] * nav / cl.at[d, s] - sh.get(s, 0.0) for s in plan if pd.notna(cl.at[d, s])}
        # 隔天均價成交，先賣後買；鎖漲停買不到、鎖跌停賣不掉、沒有成交就不動
        hi, lo, c0, c1 = P["high"].loc[day], P["low"].loc[day], cl.loc[d], close.loc[day]
        vw = P["vwap"].loc[day]

        def locked(s, up):
            if pd.isna(hi[s]) or hi[s] != lo[s] or pd.isna(c1[s]):
                return False
            return c1[s] >= c0[s] * 1.095 if up else c1[s] <= c0[s] * 0.905

        sells = {s: -q for s, q in trades.items() if q < 0 and pd.notna(vw[s]) and not locked(s, False)}
        buys = {s: q for s, q in trades.items() if q > 0 and pd.notna(vw[s]) and not locked(s, True)}
        if cfg.contest and buys:                        # 比賽：最壞情況（買漲停、賣跌停）現金要 ≥ 0.5%
            b = sum(q * c0[s] for s, q in buys.items())
            se = sum(q * c0[s] for s, q in sells.items())
            room = cash - 0.005 * nav + se * 0.9 * (1 - FEE - TAX)
            if b * 1.1 * (1 + FEE) > room:
                f = max(room, 0) / (b * 1.1 * (1 + FEE))
                buys = {s: q * f for s, q in buys.items()}
        for s, q in sells.items():
            px = vw[s] * (1 - cfg.slip)
            cash += q * px * (1 - FEE - TAX)
            sold += q * px / nav
            costs += (q * px * (FEE + TAX) + q * vw[s] * cfg.slip) / nav
            sh[s] = sh.get(s, 0.0) - q
            if sh[s] <= 1e-9:
                del sh[s]
        need = sum(q * vw[s] * (1 + cfg.slip) * (1 + FEE) for s, q in buys.items())
        f = min(1.0, cash / need) if need > 0 else 1.0
        for s, q in buys.items():
            q *= f
            px = vw[s] * (1 + cfg.slip)
            cash -= q * px * (1 + FEE)
            bought += q * px / nav
            costs += (q * px * FEE + q * vw[s] * cfg.slip) / nav
            sh[s] = sh.get(s, 0.0) + q
        # 減資／分割：單日變動超過 11% 視為股數換算，不計報酬
        for s in list(sh):
            if pd.notna(c1[s]) and pd.notna(c0[s]) and abs(c1[s] / c0[s] - 1) > 0.11 and close[s].loc[:day].notna().sum() > 5:
                sh[s] *= c0[s] / c1[s]
        nv = cash + sum(q * (c1[s] if pd.notna(c1[s]) else c0[s]) for s, q in sh.items())
        navs.append(nv)
        held_log.append({s: q * (c1[s] if pd.notna(c1[s]) else c0[s]) / nv for s, q in sh.items()})
        nh.append(len(sh))
        cashw.append(cash / nv)
    nav = pd.Series(navs, index=run)
    return {"nav": nav, "turn": (bought + sold), "cost": costs, "nh": np.mean(nh), "cash": np.mean(cashw), "defs": defs,
            "tsmc_rank": tsmc_top, "scored": np.mean(scored), "held": pd.DataFrame(held_log, index=run).fillna(0.0)}


def summarize(res, bench):
    rows = []
    b = bench.reindex(next(iter(res.values()))["nav"].index).ffill()
    bm = b.resample("ME").last().pct_change().dropna()
    for k, x in res.items():
        nav = x["nav"]
        yrs = len(nav) / 252
        m = nav.resample("ME").last().pct_change().dropna().reindex(bm.index)
        ex = (m - bm).dropna()
        rec = {"組別": k, "名稱": x["name"], "年化": (nav.iat[-1] / nav.iat[0]) ** (1 / yrs) - 1,
               "最大回撤": (nav / nav.cummax() - 1).min(), "年化波動": nav.pct_change().std() * np.sqrt(252),
               "月超額": ex.mean(), "t": ex.mean() / (ex.std() / np.sqrt(len(ex))), "贏0050": f"{int((ex > 0).sum())}/{len(ex)}",
               "超額2023-24": ex.loc["2023":"2024"].mean(), "超額2025-26": ex.loc["2025":].mean(),
               "月換手": x["turn"] / (len(nav) / 21), "年成本": x["cost"] / yrs,
               "平均檔數": x["nh"], "平均現金": x["cash"], "防守天數": x["defs"]}
        rows.append(rec)
    return pd.DataFrame(rows)


def main():
    P, days, mem, missing = build_panels()
    ind = sectors()
    print(f"股票 {P['close'].shape[1]} 檔，沒有 K 線的 {missing}", flush=True)
    bench = bars_of(MKT)["Close"]
    if "--check" in sys.argv:                  # 跟比賽回測對帳：每月重建、不計滑價
        c = Cfg("CHK", "對帳", universe="fixed", slip=0.0, reset=True)
        r = simulate(c, P, days, mem, ind)
        nav = r["nav"]
        print("每月重建 平均月報酬", f"{nav.resample('ME').last().pct_change().mean():.2%}（比賽回測 4.19%）", "換手", f"{r['turn'] / 47:.1%}")
        return
    res = {}
    for slip, tag in ((0.001, ""), (0.003, "s")):
        for c in GROUPS:
            if tag and c.key in ("G0", "G2"):
                continue
            c2 = Cfg(**{**c.__dict__, "slip": slip})
            r = simulate(c2, P, days, mem, ind)
            r["name"] = c.name + (f"｜滑價{slip:.1%}" if tag else "")
            res[c.key + tag] = r
            print(f"{c.key}{tag} 完成，期末 {r['nav'].iat[-1]:.3f}", flush=True)
    s = summarize(res, bench)
    bn = bench.reindex(next(iter(res.values()))["nav"].index).ffill()
    print(f"0050 年化 {(bn.iat[-1] / bn.iat[0]) ** (252 / len(bn)) - 1:.2%}，最大回撤 {(bn / bn.cummax() - 1).min():.2%}，波動 {bn.pct_change().std() * np.sqrt(252):.1%}")
    pd.set_option("display.width", 240, "display.max_columns", 30, "display.unicode.east_asian_width", True)
    print(s.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    tr = pd.Series(res["G4"]["tsmc_rank"])
    print(f"台積電一起排名時：有分數 {len(tr)} 天，第 1 名佔 {(tr == 1).mean():.1%}，前 8 名佔 {(tr <= 8).mean():.1%}，中位數第 {tr.median():.0f} 名")
    s.to_csv(os.path.join(OUT, "摘要.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame({k: v["nav"] for k, v in res.items()} | {"0050": bn / bn.iat[0]}).to_csv(os.path.join(OUT, "每日淨值.csv"), encoding="utf-8-sig")


if __name__ == "__main__":
    main()
