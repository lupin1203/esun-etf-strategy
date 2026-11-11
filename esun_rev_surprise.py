# -*- coding: utf-8 -*-
"""
esun_rev_surprise.py — 營收驚喜事件研究（定案規格，跑一次、不再調整）

規格（事先寫死）：
  · 驚喜 = 本月營收 YoY − 前 3 個月 YoY 平均（前 3 個月必須連續、都有值；|YoY|>500% 視為基期異常剔除）
  · 事件日 = 公布月的 10 日（FinMind 日期欄位即公布月）；之後第一個交易日開盤買進
  · 持有 5 / 10 / 20 個交易日，開盤賣出；持有期間單日 >35% 的不可能跳動會被抵銷
  · 每次公布依驚喜分 5 組（Q1=驚喜最大），各組等權重
  · 範圍 A：全市場（普通股，事件日前 20 日均量 >= 500 張）→ 主要結論
    範圍 B：比賽的 150 檔 → 輔助（有選樣偏差：是今年市值最大的公司）
  · 期間：近 5 年（事件日 2021-10-01 起）
  · 判斷：Q1→Q5 依序、Q1−Q5 的 t > 2、前後半都為正；另看 Q1 扣來回成本 0.585% 後是否仍贏過整體
結果附加到 trial_log.csv。
放 SMC- 根目錄：python esun_rev_surprise.py
"""
import os, glob, datetime
import numpy as np, pandas as pd
from dplan_validator import UNIVERSE

LOCAL_DATA = "./local_data"
REV_DIR = "./revenue_cache/history"
EVENT_START = "2021-10-01"
HORIZONS = [5, 10, 20]
NQ = 5
MIN_VOL_LOTS = 500
ANOM = 0.35
YOY_CAP = 500.0
ROUND_TRIP = 0.001425 * 2 + 0.003          # 0.585%
BENCH = "0050"
TRIAL_LOG = "trial_log.csv"


def load_prices():
    px = {}
    for path in glob.glob(os.path.join(LOCAL_DATA, "*.parquet")):
        sid = os.path.basename(path).replace(".TW.parquet", "").replace(".parquet", "")
        try:
            df = pd.read_parquet(path)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df.columns = [c.capitalize() for c in df.columns]
            df = df[~df.index.duplicated(keep="last")].sort_index()
            df = df[["Open", "Close", "Volume"]].dropna(subset=["Open", "Close"])
            df = df[(df["Open"] > 0) & (df["Close"] > 0)]
            if len(df):
                r = df["Close"].pct_change()
                df["cj"] = pd.Series(np.where(r.abs() > ANOM, 1 + r, 1.0), index=df.index).cumprod()
                df["vol20"] = df["Volume"].rolling(20, min_periods=20).mean()
                px[sid] = df
        except Exception:
            continue
    return px


def load_surprises():
    """回傳 DataFrame：sid, month, available_date, yoy, surprise"""
    rows = []
    for path in glob.glob(os.path.join(REV_DIR, "*.parquet")):
        sid = os.path.basename(path).replace(".parquet", "")
        try:
            h = pd.read_parquet(path)
            if "yoy" not in h.columns or "available_date" not in h.columns:
                continue
            h.index = pd.to_datetime(h.index)
            h = h[~h.index.duplicated(keep="last")].sort_index()
            h = h.asfreq("MS")                                   # 補齊月份，缺的月份會是 NaN
            y = pd.to_numeric(h["yoy"], errors="coerce")
            y[y.abs() > YOY_CAP] = np.nan
            prev3 = pd.concat([y.shift(1), y.shift(2), y.shift(3)], axis=1)
            ok = prev3.notna().all(axis=1) & y.notna()
            s = (y - prev3.mean(axis=1))[ok]
            # FinMind 的日期欄位是「公布月份」（例：2026-09-01 = 8 月營收，9/10 前公布）
            # 所以公告期限 = 該月 10 日（舊版用 available_date，晚了整整一個月）
            avail = pd.Series([pd.Timestamp(m.year, m.month, 10) for m in s.index], index=s.index)
            for m in s.index:
                if pd.notna(avail[m]):
                    rows.append((sid, m, avail[m], y[m], s[m]))
        except Exception:
            continue
    return pd.DataFrame(rows, columns=["sid", "month", "avail", "yoy", "surprise"])


def event_returns(df, event_date, h):
    """事件日後第一個交易日開盤進、第 h 個交易日後開盤出；抵銷不可能跳動。回傳報酬或 NaN。"""
    idx = df.index.searchsorted(event_date, side="right")
    if idx + h >= len(df):
        return np.nan, None
    d0, d1 = df.index[idx], df.index[idx + h]
    raw = df["Open"].iat[idx + h] / df["Open"].iat[idx]
    f = df["cj"].asof(d1 - pd.Timedelta(days=1)) / df["cj"].asof(d0 - pd.Timedelta(days=1)) \
        if idx > 0 else 1.0
    f = f if np.isfinite(f) and f > 0 else 1.0
    return raw / f - 1, d0


def liquid(df, event_date):
    s = df["vol20"].loc[:event_date].dropna()
    return len(s) > 0 and s.iat[-1] >= MIN_VOL_LOTS * 1000 and (event_date - s.index[-1]).days <= 7


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 1 and x.std() > 0 else np.nan


def study(px, sur, universe_name, keep):
    ev = sur[(sur["avail"] >= EVENT_START) & sur["sid"].isin(keep)]
    months = sorted(ev["avail"].unique())
    out = {h: {"q": {k: [] for k in range(NQ)}, "all": [], "bench": [], "date": [], "n": []} for h in HORIZONS}
    for d in months:
        e = ev[ev["avail"] == d]
        e = e[[sid in px and liquid(px[sid], d) for sid in e["sid"]]]
        if len(e) < NQ * 5:
            continue
        q = pd.qcut(e["surprise"].rank(method="first", ascending=False), NQ, labels=False)
        for h in HORIZONS:
            rets = pd.Series([event_returns(px[s], d, h)[0] for s in e["sid"]], index=e.index)
            ok = rets.notna()
            if ok.sum() < NQ * 5:
                continue
            for k in range(NQ):
                out[h]["q"][k].append(rets[ok & (q == k)].mean())
            out[h]["all"].append(rets[ok].mean())
            b = event_returns(px[BENCH], d, h)[0] if BENCH in px else np.nan
            out[h]["bench"].append(b)
            out[h]["date"].append(pd.Timestamp(d))
            out[h]["n"].append(int(ok.sum()))

    print("\n" + "=" * 74)
    print(f"【{universe_name}】營收驚喜五分位：持有期平均報酬（未扣成本；Q1=驚喜最大）")
    print("=" * 74)
    print(f"  {'持有':<6}" + "".join(f"{'Q'+str(k+1):>8}" for k in range(NQ))
          + f"{'Q1-Q5':>9}{'t值':>7}{'依序?':>6}{'月數':>6}{'平均檔數':>9}")
    rows = []
    for h in HORIZONS:
        o = out[h]
        if not o["date"]:
            print(f"  {h}天    無結果"); continue
        means = [np.nanmean(o["q"][k]) * 100 for k in range(NQ)]
        spread = pd.Series(np.array(o["q"][0]) - np.array(o["q"][NQ - 1]), index=o["date"])
        mono = all(means[k] >= means[k + 1] for k in range(NQ - 1))
        t = tstat(spread)
        print(f"  {str(h)+'天':<6}" + "".join(f"{m:>7.2f}%" for m in means)
              + f"{spread.mean()*100:>8.2f}%{t:>7.2f}{'是' if mono else '否':>6}{len(spread):>6}{np.mean(o['n']):>9.0f}")
        half = spread.index[len(spread) // 2]
        f, b = spread[spread.index < half], spread[spread.index >= half]
        q1 = pd.Series(o["q"][0], index=o["date"]); allm = pd.Series(o["all"], index=o["date"])
        bench = pd.Series(o["bench"], index=o["date"])
        nov = spread[[d.month == 11 for d in spread.index]]
        rows.append((h, spread.mean(), t, tstat(f), tstat(b), (spread > 0).mean(),
                     q1.mean(), q1.mean() - ROUND_TRIP, allm.mean(), bench.mean(), nov))
    print(f"\n  {'持有':<6}{'前半t':>7}{'後半t':>7}{'價差為正月份':>12}{'Q1扣成本':>10}{'整體平均':>9}{'0050':>8}")
    for h, sp, t, tf, tb, hit, q1, q1n, allm, bm, nov in rows:
        print(f"  {str(h)+'天':<6}{tf:>7.2f}{tb:>7.2f}{hit:>11.0%}{q1n*100:>9.2f}%{allm*100:>8.2f}%{bm*100:>7.2f}%")
    for h, *_, nov in rows:
        if len(nov):
            print(f"  11月（僅參考）{h}天：{len(nov)} 次，Q1−Q5 平均 {nov.mean()*100:.2f}%，"
                  f"為正 {int((nov > 0).sum())} 次")
    return rows


def main():
    print("讀取股價與營收歷史...")
    px = load_prices()
    sur = load_surprises()
    print(f"  股價 {len(px)} 檔｜營收事件 {len(sur)} 筆｜事件日 {EVENT_START} 起")
    if len(sur) == 0:
        print("  [!] 沒有營收資料，確認 revenue_cache/history 路徑"); return
    s = sur[sur["sid"] == "2330"].tail(1)
    if len(s):
        r = s.iloc[0]
        print(f"  資料檢查：2330 最新一筆 {(r['month'] - pd.DateOffset(months=1)):%Y-%m} 營收"
              f"（{r['month']:%Y-%m} 公布），公告期限 {r['avail']:%Y-%m-%d}，"
              f"YoY {r['yoy']:.1f}%，驚喜 {r['surprise']:+.1f}")

    common = {sid for sid in px if len(sid) == 4 and sid.isdigit() and not sid.startswith("00")}
    ra = study(px, sur, "A 全市場（主要結論）", common)
    rb = study(px, sur, "B 比賽 150 檔（輔助）", common & UNIVERSE)

    print("\n" + "=" * 74)
    print("判讀：")
    print("  · Q1→Q5 依序、t > 2、前後半都為正 → 營收驚喜有效")
    print("  · t 在 1~2 之間：方向對但樣本不夠證明（只有約 60 個月）")
    print("  · Q1 扣成本後 ≤ 整體平均 → 就算有效，成本也吃掉了；持有 5 天最容易發生")
    print("  · A 有效但 B 無效 → 效果主要在中小型股，比賽的 150 檔用不太到")
    print("  · 11 月只有約 5 次，只能當參考")

    new = not os.path.exists(TRIAL_LOG)
    with open(TRIAL_LOG, "a", encoding="utf-8") as fh:
        if new:
            fh.write("時間,腳本,規格,比較,年化差,IR,t,前半t,後半t\n")
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        spec = "營收驚喜=YoY−前3月平均YoY；事件日=公布月10日(修正時間錯位)；近5年"
        prev = "" if new else open(TRIAL_LOG, encoding="utf-8").read()
        if "esun_rev_surprise.py" in prev and "結果作廢" not in prev:
            fh.write(f"{now},esun_rev_surprise.py,註記,前一次事件日晚一個月（FinMind日期為公布月），結果作廢,,,,,\n")
        for name, rows in (("全市場", ra), ("150檔", rb)):
            for h, sp, t, tf, tb, *_ in rows:
                fh.write(f"{now},esun_rev_surprise.py,{spec},{name}{h}天 Q1−Q5,{sp*100:.2f}%/期,,"
                         f"{t:.2f},{tf:.2f},{tb:.2f}\n")
    print(f"\n已寫入試驗紀錄 {TRIAL_LOG}")


if __name__ == "__main__":
    main()