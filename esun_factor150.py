# -*- coding: utf-8 -*-
"""
esun_factor150.py — 比賽 150 檔內的兩個訊號（定案規格，一起跑一次、不再調整）

規格（事先寫死）：
  · 範圍：比賽 150 檔，排序日前 20 日均量 >= 500 張
  · 期間：近 5 年，每月最後一個交易日排序（2021-09 起）
  · 持有：隔日開盤買進，持有 20 個交易日後開盤賣出；持有期間單日 >35% 的不可能跳動會被抵銷
  · 訊號（Q1 = 最好）：
      ROE     官方 PBR ÷ 官方本益比（本益比空白或 <=0 = 虧損，排除）
      EPS_G   最新已公告一季 EPS 相對去年同季的成長率（去年同季 |EPS| < 0.2 元視為基期太小，排除）
    篩選理由：52週高點≈動能（大型股已證實弱）、殖利率屬價值家族（PB 已失敗）、
              毛利率改善會反映在 EPS 上 → 事先淘汰，不測
  · 通過標準（三個都要）：
      1. Q1−Q5 的 t > 2.24（一次測兩個，門檻提高）
      2. 前後半 Q1−Q5 都為正
      3. Q1 扣實際換手成本後，每月比整體平均多 >= 0.5%
結果附加到 trial_log.csv。
放 SMC- 根目錄：python esun_factor150.py
"""
import os, glob, datetime
import numpy as np, pandas as pd
from dplan_validator import UNIVERSE

LOCAL_DATA = "./local_data"
OTC_DIR = "./local_data_otc"
PER_DIR = "./revenue_cache/per_history"
EPS_DIR = "./revenue_cache/eps_history"
START = "2021-09-01"
HOLD = 20
NQ = 5
MIN_VOL_LOTS = 500
ANOM = 0.35
ROUND_TRIP = 0.001425 * 2 + 0.003
T_PASS, EXCESS_PASS = 2.24, 0.005
TRIAL_LOG = "trial_log.csv"
SIGNALS = {"ROE": "ROE", "EPS_G": "EPS成長"}


def load_prices():
    px = {}
    for sid in UNIVERSE:
        p = os.path.join(LOCAL_DATA, f"{sid}.TW.parquet")
        if not os.path.exists(p):
            p = os.path.join(OTC_DIR, f"{sid}.TWO.parquet")
        if not os.path.exists(p):
            continue
        df = pd.read_parquet(p)
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.columns = [c.capitalize() for c in df.columns]
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df = df[["Open", "Close", "Volume"]].dropna(subset=["Open", "Close"])
        df = df[(df["Open"] > 0) & (df["Close"] > 0)]
        if len(df) < 300:
            continue
        r = df["Close"].pct_change()
        df["cj"] = pd.Series(np.where(r.abs() > ANOM, 1 + r, 1.0), index=df.index).cumprod()
        df["vol20"] = df["Volume"].rolling(20, min_periods=20).mean()
        px[sid] = df
    return px


def load_roe():
    out = {}
    for sid in UNIVERSE:
        p = os.path.join(PER_DIR, f"{sid}.parquet")
        if os.path.exists(p):
            d = pd.read_parquet(p)
            d.index = pd.to_datetime(d.index)
            d = d[~d.index.duplicated(keep="last")].sort_index()
            roe = d["PBR"] / d["PER"]
            roe[~(d["PER"] > 0) | ~(d["PBR"] > 0)] = np.nan
            out[sid] = roe
    return out


def load_eps_growth():
    """回傳 {sid: Series(index=公告期限, 值=EPS 相對去年同季成長率)}"""
    out = {}
    for sid in UNIVERSE:
        p = os.path.join(EPS_DIR, f"{sid}.parquet")
        if not os.path.exists(p):
            continue
        d = pd.read_parquet(p)
        d.index = pd.to_datetime(d.index)
        d = d[~d.index.duplicated(keep="last")].sort_index()
        eps = pd.to_numeric(d["eps"], errors="coerce")
        base = pd.Series(eps.reindex(d.index - pd.DateOffset(years=1)).values, index=d.index)
        g = (eps - base) / base.abs()
        g[base.abs() < 0.2] = np.nan
        g = pd.Series(g.values, index=pd.to_datetime(d["available_date"]).values).dropna()
        out[sid] = g[~g.index.duplicated(keep="last")].sort_index()
    return out


def value_asof(s, date, tol_days=None):
    if s is None or len(s) == 0:
        return np.nan
    s2 = s.loc[:date].dropna()
    if len(s2) == 0:
        return np.nan
    if tol_days is not None and (date - s2.index[-1]).days > tol_days:
        return np.nan
    return s2.iat[-1]


def hold_return(df, date):
    idx = df.index.searchsorted(date, side="right")
    if idx + HOLD >= len(df):
        return np.nan
    d0, d1 = df.index[idx], df.index[idx + HOLD]
    raw = df["Open"].iat[idx + HOLD] / df["Open"].iat[idx]
    f = df["cj"].asof(d1 - pd.Timedelta(days=1)) / df["cj"].asof(d0 - pd.Timedelta(days=1)) if idx > 0 else 1.0
    f = f if np.isfinite(f) and f > 0 else 1.0
    return raw / f - 1


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 1 and x.std() > 0 else np.nan


def main():
    print("讀取 150 檔股價、官方本益比／淨值比、季 EPS...")
    px, roe, epsg = load_prices(), load_roe(), load_eps_growth()
    print(f"  股價 {len(px)} 檔｜ROE {len(roe)} 檔｜EPS {len(epsg)} 檔")
    if len(epsg) == 0:
        print("  [!] 沒有 EPS 資料，請先跑 esun_fetch_eps.py"); return
    if "2330" in roe and "2330" in epsg:
        last = roe["2330"].dropna()
        print(f"  資料檢查：2330 最新 ROE {last.iat[-1]*100:.1f}%（{last.index[-1]:%Y-%m-%d}）；"
              f"最新 EPS 年增 {epsg['2330'].iat[-1]*100:+.1f}%（公告期限 {epsg['2330'].index[-1]:%Y-%m-%d}）")

    days = sorted(set().union(*[df.index for df in px.values()]))
    s = pd.Series(days, index=pd.DatetimeIndex(days))
    s = s[s.index >= START]
    month_ends = [g.iloc[-1] for _, g in s.groupby([s.index.year, s.index.month])]

    res = {k: {"q": {q: [] for q in range(NQ)}, "net_q1": [], "date": [], "n": []} for k in SIGNALS}
    uni, prev_q1 = [], {k: set() for k in SIGNALS}
    for d in month_ends:
        rows = {}
        for sid, df in px.items():
            v = df["vol20"].loc[:d].dropna()
            if len(v) == 0 or v.iat[-1] < MIN_VOL_LOTS * 1000 or (d - v.index[-1]).days > 7:
                continue
            r = hold_return(df, d)
            if not np.isfinite(r):
                continue
            rows[sid] = {"ret": r,
                         "ROE": value_asof(roe.get(sid), d, tol_days=10),
                         "EPS_G": value_asof(epsg.get(sid), d)}
        if len(rows) < NQ * 5:
            continue
        t = pd.DataFrame(rows).T.astype(float)
        uni.append((d, t["ret"].mean()))
        for k in SIGNALS:
            x = t.dropna(subset=[k])
            if len(x) < NQ * 5:
                continue
            q = pd.qcut(x[k].rank(method="first", ascending=False), NQ, labels=False)
            for g in range(NQ):
                res[k]["q"][g].append(x["ret"][q == g].mean())
            members = set(x.index[q == 0])
            changed = len(members - prev_q1[k]) / len(members) if prev_q1[k] else 1.0
            res[k]["net_q1"].append(x["ret"][q == 0].mean() - changed * ROUND_TRIP)
            prev_q1[k] = members
            res[k]["date"].append(d)
            res[k]["n"].append(len(x))
    uni = pd.Series(dict(uni))

    print("\n" + "=" * 76)
    print(f"比賽 150 檔：各訊號五分位，持有 {HOLD} 個交易日平均報酬（未扣成本；Q1=最好）")
    print("=" * 76)
    print(f"  {'訊號':<10}" + "".join(f"{'Q'+str(g+1):>8}" for g in range(NQ))
          + f"{'Q1-Q5':>9}{'t值':>7}{'依序?':>6}{'月數':>5}{'檔數':>6}")
    summary = []
    for k, name in SIGNALS.items():
        o = res[k]
        if not o["date"]:
            print(f"  {name:<10}無結果"); continue
        idx = pd.DatetimeIndex(o["date"])
        means = [np.nanmean(o["q"][g]) * 100 for g in range(NQ)]
        spread = pd.Series(np.array(o["q"][0]) - np.array(o["q"][NQ - 1]), index=idx)
        mono = all(means[g] >= means[g + 1] for g in range(NQ - 1))
        t = tstat(spread)
        print(f"  {name:<10}" + "".join(f"{m:>7.2f}%" for m in means)
              + f"{spread.mean()*100:>8.2f}%{t:>7.2f}{'是' if mono else '否':>6}{len(spread):>5}{np.mean(o['n']):>6.0f}")
        half = idx[len(idx) // 2]
        tf, tb = tstat(spread[spread.index < half]), tstat(spread[spread.index >= half])
        excess = (pd.Series(o["net_q1"], index=idx) - uni.reindex(idx)).mean()
        passed = (t > T_PASS) and (tf > 0) and (tb > 0) and (excess >= EXCESS_PASS)
        summary.append((k, name, spread.mean(), t, tf, tb, excess, passed))

    print(f"\n  {'訊號':<10}{'前半t':>7}{'後半t':>7}{'Q1扣成本後超額/月':>18}{'判定':>8}")
    for k, name, sp, t, tf, tb, ex, ok in summary:
        print(f"  {name:<10}{tf:>7.2f}{tb:>7.2f}{ex*100:>17.2f}%{'通過' if ok else '未通過':>8}")
    print(f"\n  整體平均（候選股等權重）每期 {uni.mean()*100:.2f}%")
    print(f"  通過標準：t > {T_PASS}、前後半皆為正、扣成本後每月超額 >= {EXCESS_PASS*100:.1f}%")

    new = not os.path.exists(TRIAL_LOG)
    with open(TRIAL_LOG, "a", encoding="utf-8") as fh:
        if new:
            fh.write("時間,腳本,規格,比較,年化差,IR,t,前半t,後半t\n")
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        for k, name, sp, t, tf, tb, ex, ok in summary:
            fh.write(f"{now},esun_factor150.py,150檔月排序持有20日；門檻t>2.24,{name} Q1−Q5（{'通過' if ok else '未通過'}）,"
                     f"{sp*100:.2f}%/期,,{t:.2f},{tf:.2f},{tb:.2f}\n")
    print(f"\n已寫入試驗紀錄 {TRIAL_LOG}")


if __name__ == "__main__":
    main()