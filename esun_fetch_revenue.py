# -*- coding: utf-8 -*-
"""
esun_fetch_revenue.py — 抓比賽 150 檔月營收（FinMind TaiwanStockMonthRevenue），給比賽專用
  · 不沿用 SMC 的 revenue_cache/history（只有上市股，且公告期限晚一個月）
  · FinMind 的 date 是「公布月份」，revenue_year / revenue_month 才是營收所屬月份（已確認）
  · 公告期限 = 公布月的 10 日（回測用，保守）；比賽時資料抓得到就代表已公布
  · 年增率自己算：本月營收 ÷ 去年同月營收 − 1（去年同月營收 <= 0 視為無法計算）
  · 存到 revenue_cache/esun_revenue/{代號}.parquet
    index = 營收所屬月份（月初），欄位 revenue, yoy, pub_month, available_date
用法：python esun_fetch_revenue.py（每月 10 日後再跑一次即可更新）
"""
import os, time, random
import pandas as pd, requests
from dplan_validator import UNIVERSE

OUT_DIR = "./revenue_cache/esun_revenue"
URL = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
START = "2019-01-01"


def parse(records):
    d = pd.DataFrame(records)
    if d.empty or "revenue_month" not in d.columns:
        return None
    d["rev_month"] = pd.to_datetime(dict(year=d["revenue_year"].astype(int), month=d["revenue_month"].astype(int), day=1))
    d["pub_month"] = pd.to_datetime(d["date"])
    d = d.sort_values("pub_month").drop_duplicates("rev_month", keep="last").set_index("rev_month").sort_index()
    rev = pd.to_numeric(d["revenue"], errors="coerce")
    last_year = rev.reindex(rev.index - pd.DateOffset(years=1)).values
    yoy = (rev.values / last_year - 1) * 100
    yoy[~(last_year > 0)] = float("nan")
    out = pd.DataFrame({"revenue": rev, "yoy": yoy, "pub_month": d["pub_month"]})
    out["available_date"] = [pd.Timestamp(p.year, p.month, 10) for p in out["pub_month"]]
    return out


def fetch(sid):
    params = {"dataset": "TaiwanStockMonthRevenue", "data_id": sid, "start_date": START}
    if TOKEN:
        params["token"] = TOKEN
    try:
        data = requests.get(URL, params=params, timeout=20).json()
    except Exception:
        return "NET_ERROR", None
    if data.get("data"):
        out = parse(data["data"])
        return ("OK", out) if out is not None else ("BAD_FORMAT", None)
    msg = str(data.get("msg", ""))
    if "limit" in msg.lower():
        return "RATE_LIMIT", None
    if msg and msg.lower() != "success":                  # token 無效等錯誤：停下，不要當成沒資料
        return "ERROR:" + msg[:120], None
    return "EMPTY", None


def updated_today(sid):
    p = os.path.join(OUT_DIR, f"{sid}.parquet")
    return os.path.exists(p) and pd.Timestamp(os.path.getmtime(p), unit="s").tz_localize("UTC") \
        .tz_convert("Asia/Taipei").strftime("%Y-%m-%d") == pd.Timestamp.now(tz="Asia/Taipei").strftime("%Y-%m-%d")


def latest_month(sid):
    p = os.path.join(OUT_DIR, f"{sid}.parquet")
    try:
        return pd.read_parquet(p).index.max() if os.path.exists(p) else None
    except Exception:
        return None


def expected_month():
    """應該要有的最新營收月份 = 上個月（月初）"""
    t = pd.Timestamp.now(tz="Asia/Taipei").tz_localize(None).normalize()
    return t.replace(day=1) - pd.DateOffset(months=1)


def fetched_since(sid, since):
    p = os.path.join(OUT_DIR, f"{sid}.parquet")
    return os.path.exists(p) and pd.Timestamp(os.path.getmtime(p), unit="s").tz_localize("UTC") \
        .tz_convert("Asia/Taipei").tz_localize(None) >= pd.Timestamp(since)


def main(since=None):
    """since=None：每天一次全部更新，回傳 True 代表 150 檔今天都已更新（中斷後重跑會從沒更新的繼續）。
    since=時間：晚上補抓，只抓「還沒有上個月營收」且 since 之後還沒抓過的；成功回傳補抓檔數，失敗回傳 False。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    if since is not None:
        exp = expected_month()
        todo = sorted(s for s in UNIVERSE
                      if not fetched_since(s, since) and not ((latest_month(s) or pd.Timestamp(0)) >= exp))
    else:
        todo = sorted(s for s in UNIVERSE if not updated_today(s))
    print(f"150 檔｜今天已更新 {len(UNIVERSE) - len(todo)}｜本次 {len(todo)}")
    ok = 0
    for i, sid in enumerate(todo, 1):
        status, out = fetch(sid)
        if status in ("RATE_LIMIT", "NET_ERROR"):
            print(f"\n⚠ {'限流' if status == 'RATE_LIMIT' else '斷網'}（{sid}），過一陣子再跑")
            return False if since is not None else False
        if status.startswith("ERROR:"):
            print(f"\n⚠ FinMind 回應錯誤（{sid}）：{status[6:]}" + ("（有設定 FINMIND_TOKEN，可能是 token 無效）" if TOKEN else ""))
            return False
        if status == "OK":
            out.to_parquet(os.path.join(OUT_DIR, f"{sid}.parquet")); ok += 1
            last = out.iloc[-1]
            print(f"[{i}/{len(todo)}] ✓ {sid} 最新 {out.index[-1]:%Y-%m} 營收（{last['pub_month']:%Y-%m} 公布）YoY {last['yoy']:+.1f}%")
        else:
            print(f"[{i}/{len(todo)}] ✗ {sid} {status}")
        time.sleep(random.uniform(0.3, 0.6))
    print(f"\n成功 {ok} / {len(todo)}")
    if since is not None:
        return len(todo)
    missing = [s for s in UNIVERSE if not updated_today(s)]
    return len(missing) <= 1          # 容許 1 檔（例如 3718 這類新上市、沒有資料的）


if __name__ == "__main__":
    main()