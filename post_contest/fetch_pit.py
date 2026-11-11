# -*- coding: utf-8 -*-
r"""
post_contest\fetch_pit.py — 賽後研究：建立「當時的」選股池（上市前 100＋上櫃前 50），補齊成員的歷史資料

  · 只寫 post_contest\data\，比賽的資料夾（esun_*、revenue_cache、local_data…）只讀不寫
  · 不在 esun_version 的打包範圍內，不影響比賽報告的 code_version
  · 每季底定一次選股池，下個月起生效（只用當時看得到的資料）
      上櫃市值 = 櫃買中心當天收盤 × 發行股數（一天一個請求，不用 FinMind 額度）
      上市市值 = 官方 PBR × 已公告的股東權益；已下市的上市公司 = 收盤 × 股本
  · FinMind 限速：每 7.5 秒一個請求（每小時 480 個，額度 600）；比賽排程前後的時段自動暫停，把額度留給比賽
  · 可重複執行：已抓過的略過
用法（在 SMC- 資料夾）：python post_contest\fetch_pit.py
"""
import os, sys, glob, json, time
from datetime import datetime
import pandas as pd, requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import esun_fetch_eps as FE
import esun_fetch_inst as FI
from dplan_validator import UNIVERSE

URL = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
DATA = os.path.join(HERE, "data")
GAP = 7.5
START = "2022-01-01"
QUARTERS = pd.date_range("2022-12-31", "2026-09-30", freq="QE")
UA = {"User-Agent": "Mozilla/5.0"}
BLACKOUT = [(5, 45, 7, 15), (19, 45, 21, 30), (22, 45, 23, 59)]     # 比賽與彩排排程前後不用 FinMind


def wait_blackout():
    while True:
        n = datetime.now()
        now = n.hour * 60 + n.minute
        if not any(a * 60 + b <= now <= c * 60 + d for a, b, c, d in BLACKOUT):
            return
        print(f"  {n:%H:%M} 比賽排程時段，暫停", flush=True)
        time.sleep(300)


_last = [0.0]


def fm(dataset, start=START, **kw):
    """回傳 list；沒資料回傳 []；限流會等一小時後重試"""
    params = {"dataset": dataset, "start_date": start, **kw}
    if TOKEN:
        params["token"] = TOKEN
    for attempt in range(6):
        wait_blackout()
        dt = GAP - (time.time() - _last[0])
        if dt > 0:
            time.sleep(dt)
        _last[0] = time.time()
        try:
            j = requests.get(URL, params=params, timeout=60).json()
        except Exception as e:
            print(f"  ! 連線失敗 {type(e).__name__}，稍後再試", flush=True)
            time.sleep(60)
            continue
        if j.get("data"):
            return j["data"]
        msg = str(j.get("msg", ""))
        if "limit" in msg.lower():
            print("  ! 限流，等 30 分鐘", flush=True)
            time.sleep(1800)
            continue
        return []
    raise RuntimeError(f"{dataset} 重試仍失敗")


def tpex_snapshot(q):
    """q 月的最後一個有資料的交易日：{代號: 市值}"""
    p = os.path.join(DATA, "snap", f"tpex_{q:%Y-%m}.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    d = q
    for _ in range(10):
        if d.weekday() < 5:
            u = f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={d:%Y/%m/%d}&type=EW&response=json"
            t = requests.get(u, headers=UA, timeout=30).json().get("tables", [{}])[0]
            time.sleep(1.5)
            if t.get("data"):
                f = [x.strip() for x in t["fields"]]
                ic, ip, iv = f.index("代號"), f.index("收盤"), f.index("發行股數")
                out = {}
                for r in t["data"]:
                    c = r[ic].strip()
                    try:
                        if len(c) == 4 and c.isdigit() and not c.startswith("00"):
                            out[c] = float(r[ip].replace(",", "")) * float(r[iv].replace(",", ""))
                    except ValueError:
                        pass
                json.dump(out, open(p, "w", encoding="utf-8"))
                return out
        d -= pd.Timedelta(days=1)
    raise RuntimeError(f"{q:%Y-%m} 櫃買沒有資料")


def listed_caps():
    """上市：PBR × 已公告股東權益，每季底一列"""
    kl = {os.path.basename(f).split(".")[0] for f in glob.glob("local_data/*.TW.parquet")}
    cap = {}
    for s in kl:
        pf, ef = f"revenue_cache/per_history/{s}.parquet", f"revenue_cache/equity_history/{s}.parquet"
        if not (os.path.exists(pf) and os.path.exists(ef) and len(s) == 4 and not s.startswith("00")):
            continue
        p = pd.read_parquet(pf)["PBR"]
        p.index = pd.to_datetime(p.index)
        e = pd.read_parquet(ef)
        eq = pd.Series(e["equity"].values, index=pd.to_datetime(e["available_date"])).sort_index()
        eq = eq[~eq.index.duplicated(keep="last")]
        cap[s] = p[p > 0].reindex(QUARTERS, method="ffill", tolerance=pd.Timedelta(days=10)) * eq.reindex(QUARTERS, method="ffill")
    return pd.DataFrame(cap)


def raw_px(sid):
    p = os.path.join(DATA, "raw", f"{sid}.parquet")
    if os.path.exists(p):
        return pd.read_parquet(p)
    d = pd.DataFrame(fm("TaiwanStockPrice", data_id=sid))
    if d.empty:
        out = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    else:
        d.index = pd.to_datetime(d["date"])
        out = pd.DataFrame({"Open": pd.to_numeric(d["open"]), "High": pd.to_numeric(d["max"]), "Low": pd.to_numeric(d["min"]),
                            "Close": pd.to_numeric(d["close"]), "Volume": pd.to_numeric(d["Trading_Volume"])})
        out = out[out["Close"] > 0].sort_index()
    out.to_parquet(p)
    return out


def delisted_listed(otc_ever):
    """2022 起下市、不曾出現在櫃買名單的（＝上市公司）：收盤 × 股本"""
    p = os.path.join(DATA, "snap", "delisted_caps.pkl")
    if os.path.exists(p):
        return pd.read_pickle(p)
    rows = [r for r in fm("TaiwanStockDelisting", start="2000-01-01") if r["date"] >= "2022-01-01"]
    json.dump(rows, open(os.path.join(DATA, "snap", "delisted.json"), "w", encoding="utf-8"), ensure_ascii=False)
    sids = sorted({r["stock_id"] for r in rows if len(r["stock_id"]) == 4 and r["stock_id"] not in otc_ever})
    print(f"已下市的上市公司 {len(sids)} 檔，查市值", flush=True)
    cap = {}
    for s in sids:
        b = pd.DataFrame(fm("TaiwanStockBalanceSheet", start="2021-01-01", data_id=s))
        if b.empty:
            continue
        typ = next((x for x in ("CapitalStock", "OrdinaryShare") if x in set(b["type"])), None)   # 金控科目叫 OrdinaryShare（例：新光金）
        b = b[b["type"] == typ]
        if b.empty:
            continue
        shares = pd.Series(pd.to_numeric(b["value"]).values / 10, index=pd.to_datetime(b["date"])).sort_index()
        c = raw_px(s)["Close"]
        if not len(c):
            continue
        last = c.index.max()
        cap[s] = (c.reindex(QUARTERS, method="ffill", tolerance=pd.Timedelta(days=10)) *
                  shares.reindex(QUARTERS, method="ffill")).where(QUARTERS <= last)
    out = pd.DataFrame(cap, index=QUARTERS)
    out.to_pickle(p)
    return out


def members():
    otc = pd.DataFrame({q: pd.Series(tpex_snapshot(q)) for q in QUARTERS}).T
    otc_ever = set(otc.columns)
    lst = pd.concat([listed_caps(), delisted_listed(otc_ever)], axis=1)
    lst = lst.loc[:, ~lst.columns.duplicated()]
    mem = {}
    for q in QUARTERS:
        mem[f"{q:%Y-%m-%d}"] = {"listed": sorted(lst.loc[q].dropna().nlargest(100).index),
                                "otc": sorted(otc.loc[q].dropna().nlargest(50).index)}
    json.dump(mem, open(os.path.join(DATA, "members.json"), "w", encoding="utf-8"), indent=0)
    return mem


def save_kline(sid, is_otc):
    """還原 K 線：有現成的就用；上櫃用 yfinance（跟比賽資料同來源）；已下市的用 FinMind 還原價，沒有權限就用原始價"""
    p = os.path.join(DATA, "adj", f"{sid}.parquet")
    if os.path.exists(p) or os.path.exists(f"local_data/{sid}.TW.parquet") or os.path.exists(f"local_data_otc/{sid}.TWO.parquet"):
        return "有"
    if is_otc:
        try:
            import yfinance as yf
            df = yf.Ticker(f"{sid}.TWO").history(start="2021-06-01", auto_adjust=True)
            time.sleep(0.5)
            if df is not None and len(df):
                df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index
                df[["Open", "High", "Low", "Close", "Volume"]].sort_index().to_parquet(p)
                return "yf"
        except Exception as e:
            print(f"  ! {sid} yfinance {type(e).__name__}", flush=True)
    d = pd.DataFrame(fm("TaiwanStockPriceAdj", data_id=sid))
    if len(d):
        d.index = pd.to_datetime(d["date"])
        out = pd.DataFrame({"Open": pd.to_numeric(d["open"]), "High": pd.to_numeric(d["max"]), "Low": pd.to_numeric(d["min"]),
                            "Close": pd.to_numeric(d["close"]), "Volume": pd.to_numeric(d["Trading_Volume"])})
        out[out["Close"] > 0].sort_index().to_parquet(p)
        return "adj"
    raw_px(sid).to_parquet(p)
    return "原始價"


def main():
    for d in ("snap", "adj", "raw", "eps", "inst", "rev"):
        os.makedirs(os.path.join(DATA, d), exist_ok=True)
    mem = members()
    otc_ever = {s for m in mem.values() for s in m["otc"]}
    ever = {s for m in mem.values() for s in m["listed"] + m["otc"]}
    extras = sorted(ever - UNIVERSE)
    print(f"歷來成員 {len(ever)} 檔，不在比賽 150 檔的 {len(extras)} 檔", flush=True)
    for i, s in enumerate(extras, 1):
        kl = save_kline(s, s in otc_ever)
        raw_px(s)
        ep = os.path.join(DATA, "eps", f"{s}.parquet")
        if not os.path.exists(ep):
            r = fm("TaiwanStockFinancialStatements", start="2019-01-01", data_id=s)
            out = FE.parse(r, s) if r else None
            (out if out is not None else pd.DataFrame(columns=["eps", "gm", "om", "nm", "available_date"])).to_parquet(ep)
        ip = os.path.join(DATA, "inst", f"{s}.parquet")
        if not os.path.exists(ip):
            r = fm("TaiwanStockInstitutionalInvestorsBuySell", data_id=s)
            out = FI.parse(r) if r else None
            (out if out is not None else pd.DataFrame(columns=["net"])).to_parquet(ip)
        rp = os.path.join(DATA, "rev", f"{s}.parquet")
        if not os.path.exists(rp):
            r = pd.DataFrame(fm("TaiwanStockMonthRevenue", start="2020-06-01", data_id=s))
            if len(r):
                r["rev_month"] = pd.to_datetime(dict(year=r["revenue_year"], month=r["revenue_month"], day=1))
                o = r.drop_duplicates("rev_month", keep="last").set_index("rev_month").sort_index()[["revenue"]].astype(float)
                prev = o["revenue"].reindex(o.index - pd.DateOffset(years=1)).values
                o["yoy"] = [(a / b - 1) * 100 if b and b > 0 else float("nan") for a, b in zip(o["revenue"], prev)]
                o["available_date"] = o.index + pd.DateOffset(months=1) + pd.Timedelta(days=9)
            else:
                o = pd.DataFrame(columns=["revenue", "yoy", "available_date"])
            o.to_parquet(rp)
        if i % 10 == 0 or i == len(extras):
            print(f"  [{i}/{len(extras)}] {s} K線:{kl}｜{datetime.now():%H:%M}", flush=True)
    print("完成", flush=True)


if __name__ == "__main__":
    main()
