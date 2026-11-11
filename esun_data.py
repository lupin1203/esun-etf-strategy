# -*- coding: utf-8 -*-
"""
esun_data.py — 抓證交所、櫃買中心每日行情（150 檔），給帳本與報告使用

  · 證交所 OpenAPI STOCK_DAY_ALL（主辦方範例用的同一個來源）：均價 = 成交金額 ÷ 成交股數
  · 櫃買中心 OpenAPI 每日收盤行情：直接有 Average（均價），也有隔日漲停價
  · 只保留 150 檔；缺檔、日期不一致、均價不在當日高低之間，都會列出警告
  · 每天存一份快照到 esun_data/daily_YYYY-MM-DD.json，之後可追查、可重算
  · 同時產生報告 JSON 的 sources 欄位（來源、抓取時間）
  · 備援：證交所 OpenAPI 常到隔天才更新。若它的日期落後櫃買，上市股改用 FinMind TaiwanStockPrice
    補「櫃買那一天」的行情（均價 = 成交金額 ÷ 成交股數），必須 100 檔全部補齊，否則停下等下次排程
用法：python esun_data.py   （抓最新一個交易日，印出摘要並存快照）
"""
import json, os, time
from datetime import datetime, timezone, timedelta
import requests
from dplan_validator import UNIVERSE

TZ = timezone(timedelta(hours=8))
DATA_DIR = "./esun_data"
TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"


def num(x):
    """'2,475.00' / '-2.00 ' / '--' / '' → float 或 None"""
    try:
        s = str(x).replace(",", "").strip()
        return float(s) if s not in ("", "--", "---", "----", "X") else None
    except (TypeError, ValueError):
        return None


def roc_to_iso(d):
    """'1150924' → '2026-09-24'"""
    d = str(d).strip()
    return f"{int(d[:-4]) + 1911:04d}-{d[-4:-2]}-{d[-2:]}"


def get_json(url, tries=3):
    last = None
    for k in range(tries):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(2 * (k + 1))
    raise RuntimeError(f"抓取失敗 {url}：{type(last).__name__} {str(last)[:150]}")


def parse_twse(rows):
    out = {}
    for r in rows:
        c = str(r.get("Code", "")).strip()
        if c not in UNIVERSE:
            continue
        vol, val = num(r.get("TradeVolume")), num(r.get("TradeValue"))
        out[c] = {"market": "twse", "name": r.get("Name", "").strip(), "date": roc_to_iso(r["Date"]),
                  "open": num(r.get("OpeningPrice")), "high": num(r.get("HighestPrice")),
                  "low": num(r.get("LowestPrice")), "close": num(r.get("ClosingPrice")),
                  "volume": vol, "value": val,
                  "vwap": (val / vol) if vol and val else None, "next_limit_up": None}
    return out


def parse_tpex(rows):
    out = {}
    for r in rows:
        c = str(r.get("SecuritiesCompanyCode", "")).strip()
        if c not in UNIVERSE:
            continue
        vol, val = num(r.get("TradingShares")), num(r.get("TransactionAmount"))
        avg = num(r.get("Average"))
        out[c] = {"market": "tpex", "name": r.get("CompanyName", "").strip(), "date": roc_to_iso(r["Date"]),
                  "open": num(r.get("Open")), "high": num(r.get("High")), "low": num(r.get("Low")),
                  "close": num(r.get("Close")), "volume": vol, "value": val,
                  "vwap": avg if avg else ((val / vol) if vol and val else None),
                  "next_limit_up": num(r.get("NextLimitUp"))}
    return out


def sanity(quotes):
    """回傳警告清單：缺檔、日期不一致、沒有收盤價或均價、均價不在高低之間。"""
    w = []
    miss = sorted(UNIVERSE - set(quotes))
    if miss:
        w.append(f"150 檔中缺 {len(miss)} 檔：{miss}")
    dates = {q["date"] for q in quotes.values()}
    if len(dates) > 1:
        w.append(f"資料日期不一致：{sorted(dates)}")
    for c, q in sorted(quotes.items()):
        if not q["close"]:
            w.append(f"{c} 沒有收盤價（可能停牌或無成交）")
        if not q["vwap"]:
            w.append(f"{c} 沒有均價")
        elif q["high"] and q["low"] and not (q["low"] - 0.01 <= q["vwap"] <= q["high"] + 0.01):
            w.append(f"{c} 均價 {q['vwap']:.2f} 不在當日 {q['low']}–{q['high']} 之間")
    return w


def fetch_mi_index(codes, date):
    """證交所 OpenAPI 尚未更新時的第一備援：證交所網站「每日收盤行情」（MI_INDEX，收盤後約一小時就有）。
    一次抓全部上市股，均價 = 成交金額 ÷ 成交股數。抓不到就回傳空 dict（改用 FinMind）。"""
    try:
        r = requests.get(MI_INDEX_URL, params={"date": date.replace("-", ""), "type": "ALLBUT0999", "response": "json"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        j = r.json()
    except Exception:
        return {}
    if j.get("stat") != "OK" or str(j.get("date")) != date.replace("-", ""):
        return {}
    tb = next((t for t in (j.get("tables") or []) if "證券代號" in (t.get("fields") or [])), None)
    if not tb:
        return {}
    f = {k: i for i, k in enumerate(tb["fields"])}
    out = {}
    for row in tb.get("data") or []:
        c = str(row[f["證券代號"]]).strip()
        if c not in codes:
            continue
        vol, val = num(row[f["成交股數"]]), num(row[f["成交金額"]])
        out[c] = {"market": "twse", "name": str(row[f["證券名稱"]]).strip(), "date": date,
                  "open": num(row[f["開盤價"]]), "high": num(row[f["最高價"]]), "low": num(row[f["最低價"]]),
                  "close": num(row[f["收盤價"]]), "volume": vol, "value": val,
                  "vwap": (val / vol) if vol and val else None, "next_limit_up": None, "src": "mi_index"}
    return out


def fetch_finmind_twse(codes, date):
    """證交所 OpenAPI 尚未更新時，用 FinMind 補上市股當日行情。"""
    tok = os.environ.get("FINMIND_TOKEN", "")
    out = {}
    for sid in sorted(codes):
        p = {"dataset": "TaiwanStockPrice", "data_id": sid, "start_date": date, "end_date": date}
        if tok:
            p["token"] = tok
        try:
            j = requests.get(FINMIND_URL, params=p, timeout=20).json()
        except Exception:
            continue
        rows = [r for r in j.get("data", []) if r.get("date") == date]
        if not rows:
            if "limit" in str(j.get("msg", "")).lower():
                raise RuntimeError("FinMind 限流，備援中斷")
            continue
        r = rows[-1]
        vol, val = num(r.get("Trading_Volume")), num(r.get("Trading_money"))
        out[sid] = {"market": "twse", "name": "", "date": date, "open": num(r.get("open")),
                    "high": num(r.get("max")), "low": num(r.get("min")), "close": num(r.get("close")),
                    "volume": vol, "value": val, "vwap": (val / vol) if vol and val else None,
                    "next_limit_up": None, "src": "finmind"}
        time.sleep(0.15)
    return out


def fetch_daily():
    """抓最新一個交易日的 150 檔行情。回傳 (quotes, sources, 警告)。"""
    now = datetime.now(TZ).isoformat(timespec="seconds")
    twse = parse_twse(get_json(TWSE_URL))
    t_twse = datetime.now(TZ).isoformat(timespec="seconds")
    tpex = parse_tpex(get_json(TPEX_URL))
    t_tpex = datetime.now(TZ).isoformat(timespec="seconds")
    src_twse = {"source_id": None, "authority": "twse", "url": TWSE_URL,
                "name": "TWSE OpenAPI 上市個股日成交資訊（STOCK_DAY_ALL）"}
    backup_note = None
    d_tw = max((q["date"] for q in twse.values()), default=None)
    d_tp = max((q["date"] for q in tpex.values()), default=None)
    if d_tw and d_tp and d_tw < d_tp:                       # 證交所 OpenAPI 落後 → 先用證交所 MI_INDEX，不齊再用 FinMind
        mi = fetch_mi_index(set(twse), d_tp)
        if not (set(twse) - set(mi)):
            twse, t_twse = mi, datetime.now(TZ).isoformat(timespec="seconds")
            src_twse = {"source_id": None, "authority": "twse", "url": f"{MI_INDEX_URL}?type=ALLBUT0999",
                        "name": "TWSE 每日收盤行情（MI_INDEX，上市股；證交所 OpenAPI 尚未更新時的備援）"}
            backup_note = f"上市行情改用證交所 MI_INDEX 備援（證交所 OpenAPI 停在 {d_tw}）"
            d_tw = d_tp
    if d_tw and d_tp and d_tw < d_tp:                       # MI_INDEX 也不齊 → FinMind 補上市股
        fm = fetch_finmind_twse(twse.keys(), d_tp)
        miss = sorted(set(twse) - set(fm))
        if miss:
            raise RuntimeError(f"證交所停在 {d_tw}、FinMind 備援缺 {len(miss)} 檔 {miss[:5]}，稍後再試")
        for sid in fm:
            fm[sid]["name"] = twse[sid]["name"]
        twse, t_twse = fm, datetime.now(TZ).isoformat(timespec="seconds")
        src_twse = {"source_id": None, "authority": "vendor", "url": f"{FINMIND_URL}?dataset=TaiwanStockPrice",
                    "name": "FinMind 台股日成交（TaiwanStockPrice，上市股；證交所 OpenAPI 尚未更新時的備援）"}
        backup_note = f"上市行情改用 FinMind 備援（證交所 OpenAPI 停在 {d_tw}）"
    quotes = {**twse, **tpex}
    warns = sanity(quotes) + ([backup_note] if backup_note else [])
    d = sorted({q["date"] for q in quotes.values()})[-1] if quotes else None
    as_of = f"{d}T13:30:00+08:00" if d else now
    sources = [
        src_twse | {"content_as_of": as_of, "fetched_at": t_twse},
        {"source_id": None, "authority": "tpex", "url": TPEX_URL,
         "name": "TPEx OpenAPI 上櫃股票每日收盤行情", "content_as_of": as_of, "fetched_at": t_tpex},
    ]
    return quotes, sources, warns


def save_snapshot(quotes, sources, warns):
    os.makedirs(DATA_DIR, exist_ok=True)
    d = sorted({q["date"] for q in quotes.values()})[-1]
    path = os.path.join(DATA_DIR, f"daily_{d}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": d, "sources": sources, "warnings": warns, "quotes": quotes},
                  f, ensure_ascii=False, indent=1)
    return path


def load_snapshot(date):
    with open(os.path.join(DATA_DIR, f"daily_{date}.json"), encoding="utf-8") as f:
        return json.load(f)


def prices_for(quotes, field):
    """整理成帳本要的 {代號: 價格}，field = 'close' 或 'vwap'。"""
    return {c: q[field] for c, q in quotes.items() if q.get(field)}


if __name__ == "__main__":
    quotes, sources, warns = fetch_daily()
    path = save_snapshot(quotes, sources, warns)
    d = sorted({q["date"] for q in quotes.values()})[-1]
    n_tw = sum(q["market"] == "twse" for q in quotes.values())
    print(f"資料日期 {d}｜上市 {n_tw} 檔、上櫃 {len(quotes) - n_tw} 檔｜快照 {path}")
    for c in ("2330", "6488"):
        if c in quotes:
            q = quotes[c]
            print(f"  {c} {q['name']}：收盤 {q['close']}、均價 {q['vwap']:.2f}")
    print("警告：" + ("無" if not warns else ""))
    for w in warns:
        print("  !", w)