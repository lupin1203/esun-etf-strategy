# -*- coding: utf-8 -*-
"""
esun_fetch_eps.py — 抓比賽 150 檔的季 EPS（FinMind TaiwanStockFinancialStatements）
  · 日期欄位是季底（已確認），第四季為單季（已確認）
  · 公告期限（防偷看未來）：
      一般公司：Q1 5/15、Q2 8/14、Q3 11/14、Q4 隔年 3/31
      金融業（28 開頭、5880、5876、6023）：Q1 5/31、Q2 8/31、Q3 11/30、Q4 隔年 3/31（較晚，保守）
  · 存到 revenue_cache/eps_history/{代號}.parquet（index=季底，欄位 eps、gm、om、nm、available_date）
    gm／om／nm = 毛利率／營業利益率／稅後淨利率（%），用同一份財報算，給「三率不跌」用
    金融業財報科目不同（2026-10-13 修正，原本金融股三率全部空白）：
      沒有毛利 → gm 空白；營業利益率改用「稅前淨利 ÷ 淨收益」；稅後淨利科目叫 IncomeAfterTax（一般公司是 IncomeAfterTaxes）
  · 只重抓金融股：python esun_fetch_eps.py --fin
  · 斷點續傳；限流或斷網會停下，錯誤訊息不印 token
用法：python esun_fetch_eps.py
"""
import os, time, random
import pandas as pd, requests
from dplan_validator import UNIVERSE

OUT_DIR = "./revenue_cache/eps_history"
DONE_FILE = "eps_done.txt"
URL = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
START = "2019-01-01"
FIN_EXTRA = {"5880", "5876", "6023"}


def is_financial(sid):
    return sid.startswith("28") or sid in FIN_EXTRA


def available_date(q_end, sid):
    y, m = q_end.year, q_end.month
    if is_financial(sid):
        table = {3: (y, 5, 31), 6: (y, 8, 31), 9: (y, 11, 30), 12: (y + 1, 3, 31)}
    else:
        table = {3: (y, 5, 15), 6: (y, 8, 14), 9: (y, 11, 14), 12: (y + 1, 3, 31)}
    return pd.Timestamp(*table[m]) if m in table else q_end + pd.Timedelta(days=75)


def parse(records, sid):
    """回傳 index=季底，欄位 eps、gm/om/nm（毛利率／營益率／稅後淨利率，%）、available_date"""
    d = pd.DataFrame(records)
    if d.empty or "type" not in d.columns:
        return None
    d["value"] = pd.to_numeric(d["value"], errors="coerce")
    p = d.pivot_table(index="date", columns="type", values="value", aggfunc="last")
    if "EPS" not in p.columns:
        return None
    p.index = pd.to_datetime(p.index)
    p = p.sort_index()
    out = pd.DataFrame({"eps": p["EPS"]})
    rev = p["Revenue"].where(p["Revenue"] > 0) if "Revenue" in p.columns else None
    for col, typs in (("gm", ("GrossProfit",)), ("om", ("OperatingIncome", "PreTaxIncome")),
                      ("nm", ("IncomeAfterTaxes", "IncomeAfterTax"))):
        typ = next((t for t in typs if t in p.columns), None)
        if col == "om" and typ == "PreTaxIncome" and not is_financial(sid):
            typ = None                                   # 一般公司沒有營業利益就留空，不拿稅前淨利代替
        out[col] = (p[typ] / rev * 100) if (rev is not None and typ is not None) else float("nan")
    out = out.dropna(subset=["eps"])
    out["available_date"] = [available_date(q, sid) for q in out.index]
    return out


def fetch(sid):
    params = {"dataset": "TaiwanStockFinancialStatements", "data_id": sid, "start_date": START}
    if TOKEN:
        params["token"] = TOKEN
    try:
        data = requests.get(URL, params=params, timeout=20).json()
    except Exception:
        return "NET_ERROR", None
    if data.get("data"):
        out = parse(data["data"], sid)
        return ("OK", out) if out is not None else ("NO_EPS", None)
    msg = str(data.get("msg", ""))
    if "limit" in msg.lower():
        return "RATE_LIMIT", None
    if msg and msg.lower() != "success":                  # token 無效等錯誤：停下，不要記成已完成
        return "ERROR:" + msg[:120], None
    return "EMPTY", None


def expected_quarter(today):
    """財報季應該要出來的那一季：今天往前 25 天之前的最後一個季底（11 月 → 9/30）"""
    t = pd.Timestamp(today).normalize() - pd.Timedelta(days=25)
    return (t - pd.offsets.QuarterEnd(0)) if t.is_quarter_end else (t - pd.offsets.QuarterEnd(1))


def has_quarter(sid, q):
    p = os.path.join(OUT_DIR, f"{sid}.parquet")
    try:
        return os.path.exists(p) and pd.read_parquet(p).index.max() >= q
    except Exception:
        return False


def main(only_missing=None):
    """only_missing=季底日期：只抓還沒有這一季的（財報季晚上補抓用）"""
    os.makedirs(OUT_DIR, exist_ok=True)
    import sys
    done = set(open(DONE_FILE, encoding="utf-8").read().split()) if os.path.exists(DONE_FILE) else set()
    todo = sorted(UNIVERSE - done)
    if only_missing is not None:
        todo = [s for s in todo if not has_quarter(s, only_missing)]
    if "--fin" in sys.argv:                                  # 只重抓金融股（忽略已完成清單）
        todo = sorted(s for s in UNIVERSE if is_financial(s))
    print(f"150 檔｜已完成 {len(done)}｜本次 {len(todo)}")
    ok = bad = 0
    for i, sid in enumerate(todo, 1):
        status, out = fetch(sid)
        if status in ("RATE_LIMIT", "NET_ERROR"):
            print(f"\n⚠ {'限流' if status == 'RATE_LIMIT' else '斷網'}（{sid}），過一陣子再跑會接續"); break
        if status.startswith("ERROR:"):
            print(f"\n⚠ FinMind 回應錯誤（{sid}）：{status[6:]}" + ("（有設定 FINMIND_TOKEN，可能是 token 無效）" if TOKEN else "")); break
        if status == "OK":
            out.to_parquet(os.path.join(OUT_DIR, f"{sid}.parquet")); ok += 1
            print(f"[{i}/{len(todo)}] ✓ {sid} {len(out)}季，最新 {out.index[-1]:%Y-%m} EPS {out['eps'].iat[-1]}")
        else:
            bad += 1; print(f"[{i}/{len(todo)}] ✗ {sid} {status}")
        with open(DONE_FILE, "a", encoding="utf-8") as f:
            f.write(sid + "\n")
        time.sleep(random.uniform(0.3, 0.6))
    print(f"\n本次：成功 {ok}｜無資料 {bad}")
    done = set(open(DONE_FILE, encoding="utf-8").read().split()) if os.path.exists(DONE_FILE) else set()
    if only_missing is not None:
        return all(s in done or has_quarter(s, only_missing) for s in UNIVERSE)
    return len(UNIVERSE - done) == 0


if __name__ == "__main__":
    main()