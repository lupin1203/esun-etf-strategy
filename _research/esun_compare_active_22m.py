# -*- coding: utf-8 -*-
r"""
esun_compare_active_22m.py — 22 個月回測（正式設定：第 9 組＋同產業核心最多 3 檔）逐月跟主動式 ETF 排名

  · 我們：_research\bt_out_indcap\每月報酬.csv 的「＋同產業核心最多3檔」欄（每月月初 10 億重新建倉）
  · 對手：esun_etf_top10.csv 裡的主動式 ETF＋0050，月報酬 = 上月最後交易日收盤 → 本月最後交易日收盤（含息）
    最後一個月只算到回測的最後一天
  · 每個月只跟「那個月整月都已上市」的 ETF 比（主動式 ETF 大多 2025 年中以後才上市，所以前幾個月可比的很少）
  · 已知誤差：我們每月月初用均價建倉，ETF 從上月收盤起算，第一天的漲跌兩邊算法不同；ETF 用市價不是淨值
輸出：_research\bt_out_indcap\主動ETF逐月排名.csv、主動ETF兩兩比較.csv
用法（在 SMC- 資料夾）：python _research\esun_compare_active_22m.py
"""
import os, sys, time
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import esun_compare_active as C            # 共用：token()、total_return_index()、ETF 清單路徑

OUT_DIR = os.path.join(HERE, "bt_out_indcap")
MONTHLY = os.path.join(OUT_DIR, "每月報酬.csv")
DAILY = os.path.join(OUT_DIR, "每日淨值.csv")
COL_KEY = "同產業核心最多3檔"
BASE = "2024-12-31"


def month_returns(idx, last_day):
    """含息指數 → 每月報酬（Series，index 'YYYY-MM'）；只保留整月都有資料的月份"""
    idx = idx[idx.index <= last_day]
    ends = idx.groupby(idx.index.to_period("M")).tail(1)
    r = ends.pct_change().dropna()
    r.index = r.index.to_period("M").astype(str)
    return r


def main():
    m = pd.read_csv(MONTHLY, index_col=0, encoding="utf-8-sig")
    col = next(c for c in m.columns if COL_KEY in c)
    ours = m[col].astype(float)
    last_day = pd.to_datetime(pd.read_csv(DAILY, index_col=0, encoding="utf-8-sig").index[-1])
    print(f"我們：{col}｜{ours.index[0]}～{ours.index[-1]}（最後一天 {last_day:%Y-%m-%d}）")

    etf = pd.read_csv(C.ETF_CSV, dtype=str, encoding="utf-8-sig").drop_duplicates("etf_code")
    names = dict(zip(etf["etf_code"], etf["etf_name"]))
    names["0050"] = "元大台灣50（對照）"
    tk = C.token()
    rets = {}
    for k, code in enumerate(names, 1):
        px = C.get("TaiwanStockPrice", code, BASE, tk)
        if px.empty:
            print(f"[{k}/{len(names)}] {code} 抓不到"); continue
        first = pd.to_datetime(px["date"]).min().strftime("%Y-%m-%d")
        s = C.total_return_index(code, first, tk)          # 從上市第一天起算
        if s is None:
            print(f"[{k}/{len(names)}] {code} 抓不到"); continue
        r = month_returns(s, last_day)
        rets[code] = r
        print(f"[{k}/{len(names)}] {code} {names[code]}：{len(r)} 個整月（{first} 起）")
        time.sleep(0.6)

    R = pd.DataFrame(rets).reindex(ours.index)
    active = [c for c in R.columns if c != "0050"]

    rows = []
    for mo in ours.index:
        v = R.loc[mo, active].dropna()
        me = ours[mo]
        rank = 1 + int((v > me).sum())
        rows.append({"月份": mo, "我們": me, "可比檔數": len(v), "我們排名": f"{rank}/{len(v) + 1}" if len(v) else "—",
                     "百分位": (v < me).mean() if len(v) else None,
                     "主動中位數": v.median() if len(v) else None, "主動最好": v.max() if len(v) else None,
                     "最好的是": f"{v.idxmax()} {names[v.idxmax()]}" if len(v) else "", "0050": R.loc[mo, "0050"]})
    T = pd.DataFrame(rows).set_index("月份")
    T.to_csv(os.path.join(OUT_DIR, "主動ETF逐月排名.csv"), encoding="utf-8-sig", float_format="%.4f")

    pair = []
    for c in active:
        both = R[c].dropna().index
        if len(both) == 0:
            continue
        a, b = (1 + ours[both]).prod() - 1, (1 + R.loc[both, c]).prod() - 1
        pair.append({"ETF": f"{c} {names[c]}", "共同月數": len(both), "我們累計": a, "ETF累計": b,
                     "差距": a - b, "我們贏的月數": int((ours[both] > R.loc[both, c]).sum())})
    P = pd.DataFrame(pair).sort_values("差距")
    P.to_csv(os.path.join(OUT_DIR, "主動ETF兩兩比較.csv"), encoding="utf-8-sig", index=False, float_format="%.4f")

    pd.set_option("display.width", 200)
    print(f"\n{'=' * 70}\n逐月排名（只跟整月都已上市的主動式 ETF 比）\n{'=' * 70}")
    for mo, r in T.iterrows():
        if r["可比檔數"] == 0:
            print(f"  {mo}  我們 {r['我們']:+7.2%}  （還沒有可比的主動式 ETF）  0050 {r['0050']:+7.2%}")
        else:
            print(f"  {mo}  我們 {r['我們']:+7.2%}  排名 {r['我們排名']:>6}  中位數 {r['主動中位數']:+7.2%}"
                  f"  最好 {r['主動最好']:+7.2%}（{r['最好的是']}）")
    ok = T[T["可比檔數"] > 0]
    if len(ok):
        print(f"\n有可比對象的月份：{len(ok)} 個月（{ok.index[0]}～{ok.index[-1]}）")
        print(f"  平均百分位 {ok['百分位'].mean():.0%}（100% = 每個月都第一）")
        print(f"  贏過主動中位數：{int((ok['我們'] > ok['主動中位數']).sum())}/{len(ok)} 個月")
        print(f"  拿第一（贏過全部）：{int((ok['我們'] > ok['主動最好']).sum())}/{len(ok)} 個月")
        print(f"  掉到後半段：{int((ok['百分位'] < 0.5).sum())}/{len(ok)} 個月")
    print(f"\n{'=' * 70}\n兩兩比較（同一段期間複利累計，差距由小到大）\n{'=' * 70}")
    for _, r in P.iterrows():
        print(f"  {r['ETF']:<22}{r['共同月數']:>3} 個月  我們 {r['我們累計']:+8.1%}  ETF {r['ETF累計']:+8.1%}"
              f"  差 {r['差距']:+8.1%}  逐月贏 {r['我們贏的月數']}/{r['共同月數']}")
    print(f"\n存檔：{OUT_DIR}\\主動ETF逐月排名.csv、主動ETF兩兩比較.csv")


if __name__ == "__main__":
    main()
