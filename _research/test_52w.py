# -*- coding: utf-8 -*-
r"""
test_52w.py — 52 週高點動能：資料檢查＋單因子測試＋跟綜合分數的關係（不動比賽程式）
  1. 資料檢查：選股池每一檔（上市／上櫃）K 線從哪天開始、2024-12-31 前夠不夠 200 個交易日
  2. 單因子：每月底依「收盤價 ÷ 過去 252 日最高收盤價」把選股池分五組，看下個月報酬
       · 最高組 − 最低組的月報酬差、t 值；Spearman IC（排名相關）平均、t 值
       · 期間 A：2025-01 起（跟回測同期）；期間 B：2013-01 起（長期，有倖存者偏誤：用的是現在的 150 檔）
  3. 跟我們的綜合分數：每月底兩者的排名相關（越低越互補）
     ＋雙重排序：綜合分數前 1/3 裡，52 週比例高一半 vs 低一半，下個月報酬差多少
用法（在 SMC- 資料夾）：python _research\test_52w.py
"""
import os, sys, glob, json
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.exists(os.path.join(os.path.dirname(HERE), "esun_strategy.py")) else HERE
os.chdir(ROOT); sys.path.insert(0, ROOT)
import esun_strategy as S
from dplan_validator import UNIVERSE

FACTORS = ("營收驚喜", "EPS增幅", "三率", "大戶")
sec = json.load(open(S.SECTOR_FILE, encoding="utf-8"))
pool = sorted(s for s in UNIVERSE if s in sec and sec[s]["name"] not in S.EXCLUDE_IND and s != S.TSMC)

# ---------- 1. 資料檢查 ----------
print("=" * 70); print("1. K 線資料檢查（選股池 %d 檔，不含航運、台積電）" % len(pool)); print("=" * 70)
px, rows = {}, []
for s in pool:
    tw, two = os.path.join(S.LOCAL_DATA, f"{s}.TW.parquet"), os.path.join(S.OTC_DIR, f"{s}.TWO.parquet")
    src = "上市" if os.path.exists(tw) else ("上櫃" if os.path.exists(two) else "無資料")
    df = S._read_px(s)
    if df is None or "Close" not in df:
        rows.append((s, sec[s]["name"], src, None, 0)); continue
    c = df["Close"].dropna(); px[s] = df
    rows.append((s, sec[s]["name"], src, c.index[0].date(), int((c.index <= "2024-12-31").sum())))
chk = pd.DataFrame(rows, columns=["代號", "產業", "市場", "最早日期", "2024底前天數"])
for m in ("上市", "上櫃", "無資料"):
    x = chk[chk["市場"] == m]
    if len(x):
        ok = (x["2024底前天數"] >= 200).sum()
        print(f"  {m}：{len(x)} 檔，2024-12-31 前已有 200 天以上 {ok} 檔")
bad = chk[chk["2024底前天數"] < 200]
if len(bad):
    print("  ! 2025 年初算不出 52 週高點的股票：")
    print(bad.to_string(index=False))
else:
    print("  全部都夠，2025 年初起每一檔都算得出 52 週高點")

# ---------- 2. 單因子測試 ----------
close = pd.DataFrame({s: d["Close"] for s, d in px.items()}).sort_index().ffill(limit=5)
r52 = close / close.rolling(252, min_periods=200).max()
me = close.groupby(close.index.to_period("M")).apply(lambda x: x.index[-1])      # 每月最後一個交易日
me = pd.DatetimeIndex(me.values)

def factor_test(start, label):
    ds = [d for d in me if d >= pd.Timestamp(start)][:-1]
    spread, ic, top, bot = [], [], [], []
    for d, nd in zip(ds, [me[me.get_loc(x) + 1] for x in ds]):
        f = r52.loc[d].dropna()
        ret = (close.loc[nd] / close.loc[d] - 1).reindex(f.index).dropna()
        f = f.reindex(ret.index)
        if len(f) < 30:
            continue
        q = pd.qcut(f.rank(method="first"), 5, labels=False)
        t, b = ret[q == 4].mean(), ret[q == 0].mean()
        top.append(t); bot.append(b); spread.append(t - b)
        ic.append(f.rank().corr(ret.rank()))
    sp, ic = pd.Series(spread), pd.Series(ic)
    tt = lambda s: s.mean() / s.std() * np.sqrt(len(s)) if len(s) > 2 and s.std() > 0 else np.nan
    print(f"\n  {label}（{len(sp)} 個月）")
    print(f"    最接近高點那組 平均月報酬 {np.mean(top):+.2%}｜離高點最遠那組 {np.mean(bot):+.2%}")
    print(f"    差距 {sp.mean():+.2%}／月，t ＝ {tt(sp):.2f}，最高組贏的月份 {(sp > 0).sum()}／{len(sp)}")
    print(f"    IC 平均 {ic.mean():+.3f}，t ＝ {tt(ic):.2f}")

print("\n" + "=" * 70); print("2. 52 週高點單因子（選股池內每月分五組，看下個月報酬）"); print("=" * 70)
factor_test("2024-12-01", "期間 A：2025-01 起，跟回測同期")
factor_test("2012-12-01", "期間 B：2013-01 起，長期（倖存者偏誤：用現在的 150 檔）")

# ---------- 3. 跟綜合分數的關係 ----------
print("\n" + "=" * 70); print("3. 跟我們綜合分數的關係（回測快取，第 9 組選股池）"); print("=" * 70)
caches = sorted(glob.glob(os.path.join(HERE, "bt_cache", "scores_*_rawpx"))) or sorted(glob.glob(os.path.join(HERE, "bt_cache", "scores_*")))
if not caches:
    print("  找不到 bt_cache 分數快取，先跑一次 esun_backtest.py"); sys.exit()
cache = caches[-1]; print("  快取：", cache)
corr, dsp, n = [], [], 0
for d in [x for x in me if x >= pd.Timestamp("2024-12-01")][:-1]:
    p = os.path.join(cache, f"raw_{d:%Y-%m-%d}.pkl")
    if not os.path.exists(p):
        continue
    raw = pd.read_pickle(p)
    t = raw[raw.index.isin(pool)].copy()
    t["綜合"] = pd.concat([t[c].astype(float).rank(pct=True) for c in FACTORS], axis=1).mean(axis=1)
    t = t[t["綜合"].notna()]
    nd = me[me.get_loc(d) + 1]
    t["52週"] = r52.loc[d].reindex(t.index)
    t["下月"] = (close.loc[nd] / close.loc[d] - 1).reindex(t.index)
    t = t.dropna(subset=["52週", "下月"])
    if len(t) < 30:
        continue
    corr.append(t["綜合"].rank().corr(t["52週"].rank()))
    topq = t[t["綜合"] >= t["綜合"].quantile(2 / 3)]
    hi = topq["52週"] >= topq["52週"].median()
    dsp.append(topq.loc[hi, "下月"].mean() - topq.loc[~hi, "下月"].mean()); n += 1
if n:
    c, s = pd.Series(corr), pd.Series(dsp)
    print(f"  {n} 個月底｜綜合分數與 52 週比例的排名相關 平均 {c.mean():+.2f}（範圍 {c.min():+.2f}～{c.max():+.2f}）")
    print("    → 接近 0 或負數：兩者衡量的東西不同，有互補的空間；接近 1：重疊")
    print(f"  綜合分數前 1/3 裡，52 週比例高一半 − 低一半：{s.mean():+.2%}／月，"
          f"t ＝ {s.mean() / s.std() * np.sqrt(len(s)):.2f}，高的贏 {(s > 0).sum()}／{len(s)} 個月")
    print("    → 這個為正而且 t > 2，才代表「基本面好 ＋ 接近高點」真的更好，值得改用雙重條件重測")
else:
    print("  快取裡沒有月底那幾天的分數（快取檔名是 raw_日期.pkl）")
