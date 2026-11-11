# -*- coding: utf-8 -*-
r"""
esun_backtest_aiswitch.py — 「輝達財報前切換到 AI 股」回測，跟現在的正式策略比較（2023-01 起）

  正式策略（對照）：排除航運｜核心 8×9.5%＋小部位 11×1%＋台積電 10%｜new_only｜同產業核心最多 3 檔
  切換規則（全部用事前資料）：
    · 進場：距下一次輝達財報台灣反應日（美國盤後隔天）≤ N 天（日曆天），N = 28／21／14／7／0
    · 出場：過了財報反應日之後，「AI 候選股」20 日相對強弱 < 0 就換回；最多抱 60 天
    · AI 候選股（每天重算，不看未來）：電子類（半導體、電子零組件、電腦週邊、通信網路、光電、其他電子），
      不含台積電，20 日均量 ≥ 正式門檻，依「最近 3 個月營收年增率平均」排序（只用當天已公布的營收，前 3 月至少 2 個月有值）
      相對強弱 = 前 20 名候選股的 20 日平均漲幅 − 全選股池 20 日平均漲幅
  部位方式：
    · 小部位：小部位 11 檔換成 AI 候選股前 11 名（各 1%），核心不動
    · 漸增：小部位全換；核心從進場日到財報日依時間比例逐步換成 AI 股（財報日時 8 檔全換）
    · 換核心：核心 8 檔直接換成 AI 候選股前 8 名（各 9.5%），小部位不動
  過濾：F0 不套用；F1 AI 股也要通過現在的進場過濾（均線、爆量、法人，已持有的不擋）、同產業最多 3 檔、記憶體最多 3 檔
  防守模式那天不切換（照正式策略）
需要：先跑 python _research\esun_bt_fetch.py（法人、原始股價補到 2022-06 起）
輸出：_research\bt_out_aiswitch\ 每月報酬.csv、每日淨值.csv、摘要.csv、回測明細.xlsx、AI狀態.csv、財報事件.csv
用法（在 SMC- 資料夾）：python _research\esun_backtest_aiswitch.py          （31 組，約要跑一段時間）
       只跑部分：python _research\esun_backtest_aiswitch.py --only N14,N7     （N 天數）
"""
import os, sys, math, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
only = None
for i, a in enumerate(list(sys.argv)):
    if a.startswith("--only"):
        only = (a.split("=", 1)[1] if "=" in a else sys.argv[i + 1]).split(",")
        sys.argv = [x for j, x in enumerate(sys.argv) if j not in (i, i + 1 if "=" not in a else i)]
        break
if not any(a.startswith("--start") for a in sys.argv[1:]):
    sys.argv += ["--start", "2023-01-01"]

import esun_backtest as B
import numpy as np, pandas as pd

S = B.S
if not hasattr(S, "EXTRA_GROUP_CAPS"):
    raise SystemExit("esun_strategy.py 還是舊版（沒有 EXTRA_GROUP_CAPS）")
B.PX_FROM = "2022-03-01"                      # 2023-01 起跑，均線、爆量要往前看
B.tdcc_hist = lambda: None                    # 正式評分不含大戶
B.OUT_DIR = os.path.join(HERE, "bt_out_aiswitch")
os.makedirs(B.OUT_DIR, exist_ok=True)

NV = pd.to_datetime(["2022-11-17", "2023-02-23", "2023-05-25", "2023-08-24", "2023-11-22", "2024-02-22", "2024-05-23",
                     "2024-08-29", "2024-11-21", "2025-02-27", "2025-05-29", "2025-08-28", "2025-11-20", "2026-02-26",
                     "2026-05-21", "2026-08-27", "2026-11-19"])   # 台灣反應日；2026-05、2026-11 為推估
ELEC = {"半導體業", "電子零組件業", "電腦及週邊設備業", "通信網路業", "光電業", "其他電子業"}
HIND = set("3163 3363 4979 3081 4991 3105 3491 8046 3037 3189 4958 2383 6274 8358 1815 1303 2368 3044 6187 7751 7734 "
           "2360 3131 3374 6789 2404 6147 3711 6139 6515 6223 6510 1785 6182 3443 3661 2454 3529 3017 3324 3653 2059 "
           "6584 2308 1519 3665 6290 3533".split())          # 事後的 AI 贏家名單，只用來算「命中率」，不參與選股
MAX_HOLD, N_CAND = 60, 20

IND = {}
for sid, v in B.SECTOR.items():
    IND.setdefault(v["name"], set()).add(sid)
CAPS = [(g, S.MAX_INDUSTRY_CORE) for g in IND.values()]

GROUPS = {"BASE": ("正式（對照）", None, None, None)}
for n in (28, 21, 14, 7, 0):
    for mode, mname in (("SMALL", "小部位"), ("RAMP", "漸增"), ("CORE", "換核心")):
        for f in (0, 1):
            k = f"N{n}_{mode}_F{f}"
            if only and f"N{n}" not in only:
                continue
            GROUPS[k] = (f"財報前{n}天｜{mname}｜{'套過濾' if f else '不套過濾'}", n, mode, f)
B.POOLS = {k: v[0] for k, v in GROUPS.items()}
B.COMBOS = [(k, "CORE", "new_only") for k in GROUPS]

_orig_pool_table = B.pool_table


def pool_table(raw, pool, d=None):
    t = _orig_pool_table(raw, "NOSHIP", d)
    if t is not None and len(t):
        t = t.copy()
        t["_var"] = pool
    return t


B.pool_table = pool_table

# ---------- 事前資料：股價、量、營收 ----------
_px = {}


def px_tables():
    if not _px:
        c, v = {}, {}
        for s in B.UNIVERSE:
            df = S._read_px(s)
            if df is not None and len(df):
                c[s], v[s] = df["Close"], df["Volume"]
        _px["c"], _px["v"] = pd.DataFrame(c).sort_index(), pd.DataFrame(v).sort_index()
    return _px["c"], _px["v"]


_rev = {}


def rev_yoy3(sid, d):
    if sid not in _rev:
        p = os.path.join(S.REV_DIR, f"{sid}.parquet")
        _rev[sid] = pd.read_parquet(p) if os.path.exists(p) else None
    h = _rev[sid]
    if h is None:
        return np.nan
    h = h[pd.to_datetime(h["available_date"]) <= d].sort_index()
    y = pd.to_numeric(h["yoy"], errors="coerce").tail(3)
    return y.mean() if y.notna().sum() >= 2 else np.nan


_cand = {}


def candidates(d):
    """AI 候選股（依 3 月營收年增率排序）與 20 日相對強弱，同一天只算一次"""
    key = f"{d:%Y-%m-%d}"
    if key in _cand:
        return _cand[key]
    C, V = px_tables()
    c, v = C.loc[:d], V.loc[:d]
    if len(c) < 25:
        _cand[key] = ([], np.nan)
        return _cand[key]
    lots = (v.tail(20).mean() / 1000)
    ret20 = c.iloc[-1] / c.iloc[-21] - 1
    rows = []
    for s in c.columns:
        if s == S.TSMC or B.SECTOR.get(s, {}).get("name") not in ELEC or not (lots.get(s, 0) >= S.MIN_VOL_LOTS):
            continue
        y = rev_yoy3(s, d)
        if y == y:
            rows.append((s, y))
    rows.sort(key=lambda x: -x[1])
    cand = [s for s, _ in rows]
    liquid = [s for s in c.columns if lots.get(s, 0) >= S.MIN_VOL_LOTS]
    rel = ret20[cand[:N_CAND]].mean() - ret20[liquid].mean() if cand else np.nan
    _cand[key] = (cand, rel)
    return _cand[key]


# ---------- 切換狀態 ----------
STATE = {k: {"on": False, "since": None, "R": None} for k in GROUPS}
LOG = []


def window(key, d, rel):
    n = GROUPS[key][1]
    st = STATE[key]
    nxt = NV[NV >= d]
    R = nxt[0] if len(nxt) else None
    if not st["on"]:
        if R is not None and (R - d).days <= n:
            st.update(on=True, since=d, R=R)
    else:
        past = d > st["R"]
        if (past and rel == rel and rel < 0) or (d - st["since"]).days > MAX_HOLD:
            st.update(on=False, since=None, R=None)
    return st


_orig_build = S.build_targets


def build_targets(d, prev=None, current_weights=None, mode="live"):
    t = S.score_pool(d, mode)
    key = t["_var"].iat[0] if t is not None and len(t) and "_var" in t.columns else "BASE"
    S.EXTRA_GROUP_CAPS = CAPS
    try:
        tg, tab = _orig_build(d, prev, current_weights, mode)
    finally:
        S.EXTRA_GROUP_CAPS = []
    if key == "BASE":
        return tg, tab
    _, n, sw, f = GROUPS[key]
    cand, rel = candidates(d)
    st = window(key, d, rel)
    if not st["on"] or tg.get("defensive"):
        LOG.append((f"{d:%Y-%m-%d}", key, 0, rel, 0, ""))
        return tg, tab
    held = {k for k, w in (current_weights or {}).items() if w and w > 0}

    def ok(s, chosen):
        if not f:
            return True
        if s not in held and S.filters(s, d):
            return False
        for grp, cap in [(S.MEMORY, S.MAX_MEMORY_CORE)] + CAPS:
            if s in grp and sum(1 for c in chosen if c in grp) >= cap:
                return False
        return True

    if sw == "CORE":
        k_core = S.N_CORE
    elif sw == "RAMP":
        days_left = (st["R"] - d).days
        prog = 1.0 if n == 0 or days_left <= 0 else min(1.0, max(0.0, (n - days_left) / n))
        k_core = math.ceil(S.N_CORE * prog)
    else:
        k_core = 0
    ai_core = []
    for s in cand:
        if len(ai_core) >= k_core:
            break
        if ok(s, ai_core):
            ai_core.append(s)
    keep = [s for s in tg["core"] if s not in ai_core][:S.N_CORE - len(ai_core)]
    core = keep + ai_core
    if sw in ("SMALL", "RAMP"):
        small = [s for s in cand if s not in core and (not f or s in held or not S.filters(s, d))][:S.N_SMALL]
    else:
        small = [s for s in tg["small"] if s not in core]
    if len(small) < S.N_SMALL:                       # AI 股不夠就用原本的小部位補
        small += [s for s in list(tg["small"]) + list(t.index) if s not in core and s not in small][:S.N_SMALL - len(small)]
    w = {S.TSMC: tg["weights"][S.TSMC]}
    w.update({s: S.W_CORE for s in core})
    w.update({s: S.W_SMALL for s in small})
    tg = dict(tg, core=core, small=small, weights=w,
              roles={S.TSMC: "台積電"} | {s: "核心" for s in core} | {s: "小部位" for s in small},
              cash_target=round(1 - sum(w.values()), 4))
    LOG.append((f"{d:%Y-%m-%d}", key, 1, rel, len(ai_core), "、".join(ai_core)))
    return tg, tab


S.build_targets = build_targets


def daily_ret(nav):
    """回測每月重設 10 億：月初第一天用 10 億當分母"""
    r = nav / nav.shift(1) - 1
    newm = pd.Series(nav.index.to_period("M"), index=nav.index)
    isfirst = newm != newm.shift(1)
    r[isfirst] = nav[isfirst] / B.INIT - 1
    return r


def summary():
    p = os.path.join(B.OUT_DIR, "每月報酬.csv")
    if not os.path.exists(p):
        return
    m = pd.read_csv(p, index_col=0, encoding="utf-8-sig")
    base = B.label(B.COMBOS[0])
    print("\n" + "=" * 110 + "\nAI 切換：跟正式策略每月配對比較（2023-01 起）\n" + "=" * 110)
    print(f"  {'組別':<34}{'平均月報酬':>10}{'比正式':>9}{'t值':>7}{'贏的月數':>9}{'最差月':>9}{'切換天數':>9}{'命中率':>8}")
    lg = pd.DataFrame(LOG, columns=["日期", "組", "切換中", "AI相對強弱", "AI核心檔數", "AI核心"])
    lg.to_csv(os.path.join(B.OUT_DIR, "AI狀態.csv"), encoding="utf-8-sig", index=False)
    for c in B.COMBOS:
        col = B.label(c)
        dlt = m[col] - m[base]
        t = dlt.mean() / (dlt.std() / np.sqrt(len(dlt))) if dlt.std() > 0 else np.nan
        x = lg[(lg["組"] == c[0]) & (lg["切換中"] == 1)]
        names = [s for v in x["AI核心"] for s in str(v).split("、") if s]
        hit = f"{np.mean([s in HIND for s in names]):.0%}" if names else "—"
        mid = (f"{'':>25}" if c == B.COMBOS[0] else f"{dlt.mean():>+9.2%}{t:>7.2f}{f'{int((dlt > 0).sum())}/{len(dlt)}':>9}")
        print(f"  {B.POOLS[c[0]]:<34}{m[col].mean():>10.2%}" + mid + f"{m[col].min():>9.2%}{len(x):>9}{hit:>8}")
    nav = pd.read_csv(os.path.join(B.OUT_DIR, "每日淨值.csv"), index_col=0, encoding="utf-8-sig")
    nav.index = pd.to_datetime(nav.index)
    R = nav.apply(daily_ret)
    ev = []
    for e in NV:
        a, b = e - pd.Timedelta(days=28), e + pd.Timedelta(days=28)
        seg = R.loc[a:b]
        if len(seg) < 15:
            continue
        cum = (1 + seg).prod() - 1
        ev.append(pd.Series(cum - cum[base], name=f"{e:%Y-%m-%d}").drop(base))
    if ev:
        E = pd.DataFrame(ev)
        E.columns = [B.POOLS[c[0]] for c in B.COMBOS[1:]]
        E.to_csv(os.path.join(B.OUT_DIR, "財報事件.csv"), encoding="utf-8-sig", float_format="%.4f")
        print("\n每次財報前後 28 天（共 8 週）比正式多賺多少，取前 6 組：")
        best = E.mean().sort_values(ascending=False).index[:6]
        print(E[best].map(lambda v: f"{v:+.1%}").to_string())
        print("  事件平均：" + "、".join(f"{c} {E[c].mean():+.2%}（贏 {int((E[c] > 0).sum())}/{len(E)} 次）" for c in best))
    print("\n→ 比正式為正且 t > 2 才考慮採用；命中率 = 切換期間 AI 核心股裡，屬於事後 AI 贏家名單的比例")


if __name__ == "__main__":
    B.main()
    summary()
