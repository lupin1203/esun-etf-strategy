# -*- coding: utf-8 -*-
r"""
esun_compare_active.py — 把 10 月重跑的淨值，拿去跟「主動式 ETF」比，而不是只跟 0050 比

  · 我們：_research\bt_out_oct\每日淨值.csv（先跑 python _research\esun_backtest_oct.py 更新）
  · 對手：esun_etf_top10.csv 裡的主動式 ETF（台股型＋全球型都比），加上 0050 當對照
  · ETF 報酬 = 從起點前一天收盤（預設 9/30）到每一天收盤，除息日把配息加回（含息報酬）
  · 價格來源：FinMind TaiwanStockPrice、TaiwanStockDividendResult；token 讀環境變數 FINMIND_TOKEN，
    沒有就從 run_daily.bat 讀（不會印出來）
  · 注意：ETF 用的是市價，不是淨值，折溢價會有一點誤差；我們是用 10 億從 10/1 建倉的重跑結果
輸出：_research\bt_out_oct\主動ETF比較.csv（每天累計報酬），畫面印出排名
用法（在 SMC- 資料夾）：python _research\esun_compare_active.py
       換起點：python _research\esun_compare_active.py --base 2026-09-30
"""
import os, re, sys, time
import pandas as pd, requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NAV_CSV = os.path.join(HERE, "bt_out_oct", "每日淨值.csv")
ETF_CSV = os.path.join(ROOT, "esun_etf_top10.csv")
OUT_CSV = os.path.join(HERE, "bt_out_oct", "主動ETF比較.csv")
URL = "https://api.finmindtrade.com/api/v4/data"
INIT = 1_000_000_000


def token():
    t = os.environ.get("FINMIND_TOKEN", "")
    if t:
        return t
    bat = os.path.join(ROOT, "run_daily.bat")
    if os.path.exists(bat):
        for line in open(bat, encoding="utf-8", errors="ignore"):
            m = re.match(r"\s*set\s+FINMIND_TOKEN=(\S+)", line, re.I)
            if m:
                return m.group(1)
    return ""


def get(dataset, sid, start, tk):
    p = {"dataset": dataset, "data_id": sid, "start_date": start}
    if tk:
        p["token"] = tk
    for k in range(3):
        try:
            j = requests.get(URL, params=p, timeout=30).json()
            return pd.DataFrame(j.get("data") or [])
        except Exception:
            time.sleep(2 + 2 * k)
    return pd.DataFrame()


def total_return_index(sid, base, tk):
    """回傳以 base 當天收盤 = 1 的含息指數（index=日期）"""
    px = get("TaiwanStockPrice", sid, base, tk)
    if px.empty:
        return None
    px["date"] = pd.to_datetime(px["date"])
    close = px.set_index("date")["close"].astype(float).sort_index()
    close = close[close > 0]
    if close.empty or close.index[0] != pd.Timestamp(base):
        return None                                        # 起點那天沒有價格（例如 ETF 還沒上市）
    div = get("TaiwanStockDividendResult", sid, base, tk)
    cash = {}
    if not div.empty and "stock_and_cache_dividend" in div:
        for _, r in div.iterrows():
            try:
                cash[pd.Timestamp(r["date"])] = float(r["stock_and_cache_dividend"])
            except Exception:
                pass
    idx, prev = [1.0], close.iloc[0]
    for d, c in close.iloc[1:].items():
        idx.append(idx[-1] * c / (prev - cash.get(d, 0.0)))
        prev = c
    return pd.Series(idx, index=close.index)


def main():
    base = "2026-09-30"
    for i, a in enumerate(sys.argv[1:]):
        if a.startswith("--base"):
            base = a.split("=", 1)[1] if "=" in a else sys.argv[i + 2]

    nav = pd.read_csv(NAV_CSV, index_col=0, encoding="utf-8-sig").iloc[:, 0]
    nav.index = pd.to_datetime(nav.index)
    ours = nav / INIT - 1

    etf = pd.read_csv(ETF_CSV, dtype=str, encoding="utf-8-sig").drop_duplicates("etf_code")
    names = dict(zip(etf["etf_code"], etf["etf_name"]))
    types = dict(zip(etf["etf_code"], etf["type"]))
    names["0050"], types["0050"] = "元大台灣50（對照）", "被動"

    tk = token()
    table, skipped = {"我們（重跑）": ours}, []
    for k, code in enumerate(list(names), 1):
        s = total_return_index(code, base, tk)
        print(f"[{k}/{len(names)}] {code} {names[code]} {'OK' if s is not None else '略過（起點沒有價格）'}")
        if s is None:
            skipped.append(code); continue
        table[f"{code} {names[code]}"] = (s.reindex(ours.index, method="ffill") - 1)
        time.sleep(0.6)

    df = pd.DataFrame(table)
    df.to_csv(OUT_CSV, encoding="utf-8-sig", float_format="%.5f")

    last = df.index[-1]
    final = df.loc[last].dropna().sort_values(ascending=False)
    active = [c for c in final.index if c != "我們（重跑）" and not c.startswith("0050")]
    tw = [c for c in active if types.get(c.split()[0]) == "台股"]
    print(f"\n{'=' * 60}\n{base} 收盤 → {last:%Y-%m-%d} 收盤，累計報酬（ETF 含息）\n{'=' * 60}")
    for r, (c, v) in enumerate(final.items(), 1):
        tag = "  ◀" if c == "我們（重跑）" else ""
        print(f"  {r:>2}. {c:<22}{v:>+8.2%}{tag}")
    me = final["我們（重跑）"]
    beat = sum(final[c] < me for c in active)
    beat_tw = sum(final[c] < me for c in tw)
    print(f"\n我們贏 {beat}/{len(active)} 檔主動式 ETF（台股型 {beat_tw}/{len(tw)} 檔）")
    print(f"主動式 ETF 中位數 {final[active].median():+.2%}、平均 {final[active].mean():+.2%}、"
          f"最好 {final[active].max():+.2%}")
    if skipped:
        print(f"略過（起點前還沒上市或抓不到）：{'、'.join(skipped)}")
    print(f"\n每天的累計報酬存在 {OUT_CSV}")


if __name__ == "__main__":
    main()
