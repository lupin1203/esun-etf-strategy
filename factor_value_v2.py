# -*- coding: utf-8 -*-
"""
factor_value_v2.py — 動能+價值：修正第 1~4 條，並印出每一條各自的影響

FIX_PB    第1條：PB 改用官方 PBR（修還原價造成的前視偏差；順便解決面額、權益欄位問題）
FIX_PICK  第2條：從最好那組「等距抽樣」20 檔、等權重（不再挑組內最極端的）
FIX_DIRTY 第3條：持有期間出現單日 >35% 跳動，只抵銷那一天，並記錄到 dirty_neutralized.txt
FIX_LIQ   第4條：20 日均量 >= 500 張、且 7 天內有交易，才可入選

執行時依序跑：原版 → +1 → +1+2 → +1+2+3 → 全部，印出每一步改變多少。
「原版」那列應接近上次結果（動能+價值 年化約 30.3%、夏普約 1.09），用來確認改寫沒弄壞。
最後對「全部修正」版印逐年報酬與前後半樣本外。
不修改任何舊檔。放 SMC- 根目錄：python factor_value_v2.py
"""
import os, glob
import numpy as np, pandas as pd

# ============ 設定 ============
LOCAL_DATA = "./local_data"
EQUITY_DIR = "./revenue_cache/equity_history"
PER_DIR    = "./revenue_cache/per_history"
LOOKBACK, SKIP, MIN_HISTORY = 252, 21, 300
N_GROUPS, TOP_HOLD = 5, 20
START, END, SPLIT = "2013-01-01", "2026-09-30", "2020-01-01"
DIRTY_TRIGGER, ANOM = 5.0, 0.35
MIN_VOL_LOTS = 500              # 20 日均量門檻（張）
STALE_DAYS = 7                  # 超過幾天沒交易視為停止更新
FEE_RATE, FEE_DISC, TAX_RATE, SLIPPAGE = 0.001425, 0.28, 0.003, 0.002
ONESIDE_COST = FEE_RATE*FEE_DISC + SLIPPAGE + TAX_RATE/2
NEUTRAL_LOG = "dirty_neutralized.txt"
# ==============================


def sid_of(t):
    return t.replace(".TW", "").split(".")[0]


def load_series():
    closes, opens, vols = {}, {}, {}
    for path in glob.glob(os.path.join(LOCAL_DATA, "*.parquet")):
        t = os.path.basename(path).replace(".parquet", "")
        try:
            df = pd.read_parquet(path)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df.columns = [c.capitalize() for c in df.columns]
            df = df[~df.index.duplicated(keep="last")].sort_index()
            if "Close" in df.columns: closes[t] = df["Close"]
            if "Open" in df.columns:  opens[t] = df["Open"]
            if "Volume" in df.columns: vols[t] = df["Volume"]
        except Exception:
            continue
    return closes, opens, vols


def month_end_dates(index, start, end):
    days = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    s = pd.Series(days, index=days)
    return pd.DatetimeIndex([g.iloc[-1] for _, g in s.groupby([s.index.year, s.index.month])])


def build_context():
    closes, opens, vols = load_series()
    master = pd.DatetimeIndex(sorted(set().union(*[c.index for c in closes.values()])))
    months = month_end_dates(master, START, END)

    MOM, DM, PBS, PBO, LIQ, FRESH, EP, CJE = {}, {}, {}, {}, {}, {}, {}, {}
    n_pbo = 0
    for t, c0 in closes.items():
        c = c0.dropna()
        if len(c) == 0:
            continue
        pos_n = np.arange(1, len(c) + 1)
        old = c.shift(LOOKBACK - 1)
        mom = c.shift(SKIP - 1) / old - 1
        mom[(pos_n < MIN_HISTORY) | ~(old > 0)] = np.nan
        r = c.pct_change()
        a = (r.abs() > ANOM)
        dm = a.astype(float).rolling(LOOKBACK - SKIP - 1).sum().shift(SKIP) > 0
        MOM[t] = mom.reindex(months, method="ffill")
        DM[t] = dm.reindex(months, method="ffill")

        # 流動性與新鮮度
        last_trade = pd.Series(c.index, index=c.index).reindex(months, method="ffill")
        FRESH[t] = (months.to_series() - last_trade).dt.days <= STALE_DAYS
        if t in vols:
            v = vols[t].reindex(c.index)
            LIQ[t] = v.rolling(20, min_periods=20).mean().reindex(months, method="ffill") >= MIN_VOL_LOTS * 1000

        price_at = c.reindex(months, method="ffill")
        sid = sid_of(t)

        # 自算 PB（原版，含還原價偏差）
        ep_path = os.path.join(EQUITY_DIR, f"{sid}.parquet")
        if os.path.exists(ep_path):
            try:
                e = pd.read_parquet(ep_path)
                if "bvps" in e.columns and "available_date" in e.columns:
                    b = pd.Series(e["bvps"].values, index=pd.to_datetime(e["available_date"])).sort_index()
                    b = b[~b.index.duplicated(keep="last")]
                    bv = b.reindex(months, method="ffill")
                    pb = price_at / bv
                    pb[~(bv > 0) | ~(price_at > 0)] = np.nan
                    PBS[t] = pb
            except Exception:
                pass

        # 官方 PBR
        pp = os.path.join(PER_DIR, f"{sid}.parquet")
        if os.path.exists(pp):
            try:
                p = pd.read_parquet(pp)["PBR"]
                p.index = pd.to_datetime(p.index)
                p = p[p > 0].sort_index()
                p = p[~p.index.duplicated(keep="last")]
                if len(p):
                    PBO[t] = p.reindex(months, method="ffill", tolerance=pd.Timedelta(days=10))
                    n_pbo += 1
            except Exception:
                pass

        # T+1 開盤價（選股日後第一個交易日）與髒資料抵銷用的累積跳動因子
        if t in opens:
            o = opens[t].dropna()
            if len(o):
                pos = o.index.searchsorted(months, side="right")
                ok = pos < len(o)
                ep = np.full(len(months), np.nan)
                ep[ok] = o.values[pos[ok]]
                EP[t] = pd.Series(ep, index=months)
                jf = pd.Series(np.where(a, 1 + r, 1.0), index=c.index).cumprod()
                cj = np.full(len(months), np.nan)
                if ok.any():
                    ed = pd.DatetimeIndex(o.index[pos[ok]])
                    cj[ok] = jf.reindex(ed, method="ffill").values
                CJE[t] = pd.Series(cj, index=months)

    df = lambda d: pd.DataFrame(d).reindex(index=months)
    ctx = dict(months=months, MOM=df(MOM), DM=df(DM), PBS=df(PBS), PBO=df(PBO),
               LIQ=df(LIQ), FRESH=df(FRESH), EP=df(EP), CJE=df(CJE),
               n_stocks=len(closes), n_pbo=n_pbo)
    return ctx


def pick(d, mode, sampling):
    if mode == "mom":
        d = d[d["mom"].notna()]
        key, best_high = d["mom"], True
    elif mode == "val":
        d = d[d["pb"] > 0]
        key, best_high = d["pb"], False
    else:
        d = d[(d["pb"] > 0) & d["mom"].notna()]
        key = (d["mom"].rank(ascending=False) + d["pb"].rank(ascending=True)) / 2
        best_high = False
    if len(d) < N_GROUPS:
        return {}
    grp = pd.qcut(key.rank(method="first"), N_GROUPS, labels=False)
    members = key[grp == (N_GROUPS - 1 if best_high else 0)]
    if len(members) == 0:
        return {}
    if sampling:   # 第2條：等距抽樣、等權重
        members = members.sort_values(ascending=not best_high)
        n = len(members)
        if n <= TOP_HOLD:
            chosen = members.index
        else:
            idx = np.unique(np.linspace(0, n - 1, TOP_HOLD).round().astype(int))
            chosen = members.index[idx]
        return {t: 1.0 / len(chosen) for t in chosen}
    raw = (members - members.min() + 0.01) if mode == "mom" else 1.0 / members
    w = (raw / raw.sum()).sort_values(ascending=False).iloc[:TOP_HOLD]
    return (w / w.sum()).to_dict()


def run(ctx, mode, cfg, log=None):
    months = ctx["months"]
    PB = ctx["PBO"] if cfg["pb"] else ctx["PBS"]
    eq, curve, rets, prev = 1.0, [], [], {}
    n_cand, n_neutral = [], 0
    for i in range(len(months) - 1):
        d = pd.DataFrame({
            "mom": ctx["MOM"].iloc[i], "dm": ctx["DM"].iloc[i],
            "pb": PB.iloc[i] if len(PB.columns) else np.nan,
            "liq": ctx["LIQ"].iloc[i] if len(ctx["LIQ"].columns) else False,
            "fresh": ctx["FRESH"].iloc[i],
        })
        d = d[d["mom"].notna()]
        d = d[~((d["mom"] > DIRTY_TRIGGER) & (d["dm"] == True))]
        if cfg["liq"]:
            d = d[(d["liq"] == True) & (d["fresh"] == True)]
        n_cand.append(len(d))
        weights = pick(d, mode, cfg["pick"])
        if not weights:
            continue

        port, realized = 0.0, {}
        for t, w in weights.items():
            if t not in ctx["EP"].columns:
                continue
            pi, po = ctx["EP"][t].iloc[i], ctx["EP"][t].iloc[i + 1]
            if not (pi > 0) or not (po > 0):
                continue
            ret = po / pi - 1
            if cfg["dirty"]:   # 第3條：抵銷持有期間的不可能跳動
                c0, c1 = ctx["CJE"][t].iloc[i], ctx["CJE"][t].iloc[i + 1]
                if c0 > 0 and c1 > 0:
                    f = c1 / c0
                    if abs(f - 1) > 1e-9:
                        ret = (1 + ret) / f - 1
                        n_neutral += 1
                        if log is not None:
                            log.append((months[i].date(), mode, t, round((f - 1) * 100, 1)))
            port += w * ret
            realized[t] = w
        tot = sum(realized.values())
        if tot <= 0:
            continue
        port /= tot
        realized = {k: v / tot for k, v in realized.items()}
        turnover = sum(abs(realized.get(k, 0) - prev.get(k, 0)) for k in set(realized) | set(prev))
        port -= turnover * ONESIDE_COST
        eq *= (1 + port)
        curve.append((months[i + 1], eq))
        rets.append(port)
        prev = realized
    eqs = pd.Series([e for _, e in curve], index=[x for x, _ in curve])
    rs = pd.Series(rets, index=eqs.index)
    return eqs, rs, (np.mean(n_cand) if n_cand else 0), n_neutral


def stats(rs):
    if len(rs) == 0:
        return None
    eq = (1 + rs).cumprod()
    n = len(rs)
    ann = (eq.iloc[-1] ** (12 / n) - 1) * 100
    dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    sh = rs.mean() / rs.std() * np.sqrt(12) if rs.std() > 0 else 0
    yr = eq.groupby(eq.index.year).last()
    yret = yr / yr.shift(1).fillna(1.0) - 1
    return dict(ann=ann, dd=dd, sh=sh, best=yret.max() * 100, worst=yret.min() * 100, yret=yret * 100)


CONFIGS = [
    ("原版(上次20檔)",   dict(pb=False, pick=False, dirty=False, liq=False)),
    ("+1 官方PBR",       dict(pb=True,  pick=False, dirty=False, liq=False)),
    ("+2 等距抽樣",      dict(pb=True,  pick=True,  dirty=False, liq=False)),
    ("+3 髒資料抵銷",    dict(pb=True,  pick=True,  dirty=True,  liq=False)),
    ("+4 流動性(全部)",  dict(pb=True,  pick=True,  dirty=True,  liq=True)),
]
MODES = [("mom", "純動能"), ("val", "純價值"), ("combo", "動能+價值")]


def main():
    print("讀取資料並預先計算（第一次會花一點時間）...")
    ctx = build_context()
    print(f"  K線 {ctx['n_stocks']} 檔｜官方PBR {ctx['n_pbo']} 檔｜{len(ctx['months'])} 個換股點")
    if ctx["n_pbo"] == 0:
        print("  [!] 找不到官方 PBR，請先跑 fetch_per.py。")
        return
    print("=" * 66)

    final = {}
    log = []
    for mode, name in MODES:
        print(f"\n【{name}】逐條加入修正的影響")
        print(f"  {'設定':<16}{'年化':>8}{'回撤':>9}{'夏普':>7}{'最好年':>9}{'最差年':>9}{'候選數':>7}")
        for label, cfg in CONFIGS:
            is_final = cfg == CONFIGS[-1][1]
            eqs, rs, ncand, nneu = run(ctx, mode, cfg, log if is_final else None)
            s = stats(rs)
            if s is None:
                print(f"  {label:<16}無結果")
                continue
            print(f"  {label:<16}{s['ann']:>7.1f}%{s['dd']:>8.1f}%{s['sh']:>7.2f}"
                  f"{s['best']:>8.1f}%{s['worst']:>8.1f}%{ncand:>7.0f}")
            if is_final:
                final[name] = (rs, s, nneu)

    print("\n" + "=" * 66)
    print("原版那列應接近上次結果：純動能 19.1%/0.68、純價值 29.8%/0.83、動能+價值 30.3%/1.09")
    print("純動能不用 PB，所以『原版』和『+1』兩列相同是正常的。")

    print("\n【全部修正後】逐年報酬")
    names = [n for _, n in MODES if n in final]
    print(f"  {'年':<6}" + "".join(f"{n:>11}" for n in names))
    years = sorted(set().union(*[final[n][1]["yret"].index for n in names]))
    for y in years:
        print(f"  {y:<6}" + "".join(f"{final[n][1]['yret'].get(y, np.nan):>10.1f}%" for n in names))

    print("\n【全部修正後】前後半樣本外")
    print(f"  {'策略':<12}{'前半年化':>9}{'前半夏普':>9}{'後半年化':>9}{'後半夏普':>9}")
    for n in names:
        rs = final[n][0]
        f = stats(rs[rs.index < SPLIT]); b = stats(rs[rs.index >= SPLIT])
        if f and b:
            print(f"  {n:<12}{f['ann']:>8.1f}%{f['sh']:>9.2f}{b['ann']:>8.1f}%{b['sh']:>9.2f}")

    total_neu = sum(final[n][2] for n in names)
    print("\n" + "=" * 66)
    if log:
        with open(NEUTRAL_LOG, "w", encoding="utf-8") as fh:
            fh.write("選股日,模式,代號,抵銷的跳動%\n")
            for row in log:
                fh.write(",".join(str(x) for x in row) + "\n")
        print(f"第3條：持有期間共抵銷 {total_neu} 次不可能跳動，明細在 {NEUTRAL_LOG}")
    else:
        print("第3條：持有期間沒有出現需要抵銷的跳動")
    print("⚠ 仍未修：倖存者偏差、沒跟大盤比、沒跟 SMC 比相關（第5~8條）。")


if __name__ == "__main__":
    main()