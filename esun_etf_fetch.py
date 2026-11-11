# -*- coding: utf-8 -*-
"""
esun_etf_fetch.py — 從玩股網「成分股」頁抓 ETF 最新持股（投信每日 PCF），更新 esun_etf_top10.csv

用法（在 SMC- 資料夾執行python esun_etf_fetch.py）：
                  # 抓全部
    python esun_etf_fetch.py 00981A 0052  # 只抓指定的

產出：
  · esun_etf_top10.csv            主動式 ETF 前 16 大（含台積電），比賽主動投資比率檢查用；
                                  只在抓到的資料日期比原本新時才取代，原檔先備份成 .bak_日期
  · etf_holdings_latest.csv       每檔 ETF 的完整持股（含被動科技 ETF），傳給 Claude 重建持股報表
  · etf_fetch_log.txt             每檔抓取結果
資料來源：玩股網成分股頁背後的 API https://www.wantgoo.com/stock/etf/<代號>/all-constituent-combine-data
         （網站要求先開該 ETF 的成分股頁；連續太快會回 400，所以每檔間隔約 3 秒）
"""
import os, re, sys, time, shutil, datetime as dt
import requests, pandas as pd

CSV = "esun_etf_top10.csv"
OUT_ALL = "etf_holdings_latest.csv"
LOG = "etf_fetch_log.txt"
TOP_N = 16                     # 比賽比較前 15 大（2026/10/05 公告），多抓 1 檔給剔除台積電的報表補位
PASSIVE = {"0052": "富邦科技", "0053": "元大電子", "00881": "國泰台灣科技龍頭", "00891": "中信關鍵半導體",
           "00892": "富邦台灣半導體", "00904": "台新臺灣半導體30", "00913": "兆豐台灣晶圓製造",
           "00927": "群益半導體收益", "00929": "復華台灣科技優息", "00935": "野村臺灣新科技50",
           "00941": "中信上游半導體", "00946": "群益科技高息成長", "00947": "台新臺灣IC設計"}
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/128.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9", "Referer": "https://www.wantgoo.com/stock/etf/ranking/volume"}


def fetch(sess, code):
    """先開該 ETF 的成分股頁（網站要求先造訪該頁），再呼叫頁面背後的資料 API。
    回傳 (資料日期 'YYYY-MM-DD', [(代號, 名稱, 權重%), ...] 依權重排序)"""
    c = code.lower()
    page = f"https://www.wantgoo.com/stock/etf/{c}/constituent"
    r = sess.get(page, headers=HEAD, timeout=20)
    if r.status_code != 200:
        raise ValueError(f"開頁 HTTP {r.status_code}")
    time.sleep(1.0)
    api = f"https://www.wantgoo.com/stock/etf/{c}/all-constituent-combine-data"
    for attempt in range(5):                       # 偶爾回 400：重開一次頁面、等久一點再試
        r = sess.get(api, headers={**HEAD, "Referer": page, "X-Requested-With": "XMLHttpRequest",
                                   "Accept": "application/json, text/javascript, */*; q=0.01"}, timeout=20)
        if r.status_code == 200:
            break
        time.sleep(5 + 3 * attempt)
        sess.get(page, headers=HEAD, timeout=20)
        time.sleep(1.5)
    if r.status_code != 200:
        raise ValueError(f"資料 API HTTP {r.status_code}")
    j = r.json()
    if not j or not j.get("date") or not j.get("stockHoldings"):
        raise ValueError("網站沒有這檔的股票持股資料（全球型或尚未公布）")
    date = (dt.datetime.utcfromtimestamp(j["date"] / 1000) + dt.timedelta(hours=8)).strftime("%Y-%m-%d")
    rows = [(h["stockCode"], h["stockName"], float(h["weight"])) for h in j.get("stockHoldings") or []]
    if not rows:
        raise ValueError("沒有股票持股資料")
    rows.sort(key=lambda x: -x[2])
    return date, rows


def main():
    want = [a.upper() for a in sys.argv[1:]]
    old = pd.read_csv(CSV, dtype=str, encoding="utf-8-sig")
    meta = old.drop_duplicates("etf_code").set_index("etf_code")[["etf_name", "type"]]
    codes = list(meta.index) + [c for c in PASSIVE if c not in meta.index]
    if want:
        codes = [c for c in codes if c in want] + [c for c in want if c not in codes]
    sess = requests.Session()
    try:
        sess.get("https://www.wantgoo.com/stock/etf/ranking/volume", headers=HEAD, timeout=20)
    except Exception:
        pass
    log, allrows, newtop = [], [], {}
    for k, code in enumerate(codes, 1):
        try:
            date, rows = fetch(sess, code)
            for rk, (t, n, w) in enumerate(rows, 1):
                allrows.append({"etf_code": code, "data_date": date, "rank": rk, "ticker": t, "name": n, "weight_pct": w})
            if code in meta.index:
                prev = old.loc[old["etf_code"] == code, "data_date"].dropna()
                prev = prev.max() if len(prev) else ""
                if date >= (prev or ""):
                    newtop[code] = (date, rows)
                    log.append(f"{code} OK {date}（原本 {prev or '無'}）共 {len(rows)} 檔")
                else:
                    log.append(f"{code} 抓到 {date}，比原本 {prev} 舊，不取代")
            else:
                log.append(f"{code} OK {date}（被動科技，只寫入 {OUT_ALL}）共 {len(rows)} 檔")
        except Exception as e:
            log.append(f"{code} ✗ {e}")
        print(f"[{k}/{len(codes)}] {log[-1]}")
        time.sleep(3)

    if newtop:
        bak = f"{CSV}.bak_{dt.date.today():%Y%m%d}"
        if not os.path.exists(bak):
            shutil.copy(CSV, bak)
        out = []
        for code in meta.index:
            name, typ = meta.loc[code, "etf_name"], meta.loc[code, "type"]
            if code not in newtop:
                out.append(old[old["etf_code"] == code])
                continue
            date, rows = newtop[code]
            top = rows[:TOP_N]
            if typ != "台股":                                   # 全球型：只留前 15 名中的台股
                top = [r for r in rows[:TOP_N - 1] if re.fullmatch(r"\d{4}", r[0])]
            recs = [{"etf_code": code, "etf_name": name, "type": typ, "data_date": date, "rank": i + 1,
                     "ticker": t, "weight_pct": round(w, 2)} for i, (t, n, w) in enumerate(top)]
            recs += [{"etf_code": code, "etf_name": name, "type": typ, "data_date": "", "rank": i + 1,
                      "ticker": "", "weight_pct": ""} for i in range(len(recs), TOP_N)]
            out.append(pd.DataFrame(recs))
        pd.concat(out).to_csv(CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(allrows).to_csv(OUT_ALL, index=False, encoding="utf-8-sig")
    stamp = f"{dt.datetime.now():%Y-%m-%d %H:%M}"
    with open(LOG, "w", encoding="utf-8") as f:
        f.write(f"抓取時間 {stamp}\n" + "\n".join(log) + "\n")
    ok = sum(1 for x in log if " OK " in x)
    print(f"\n完成：{ok}/{len(codes)} 檔成功；{len(newtop)} 檔更新到 {CSV}；完整持股在 {OUT_ALL}")
    bad = [x for x in log if "✗" in x]
    if bad:
        print("失敗的：\n  " + "\n  ".join(bad))


if __name__ == "__main__":
    main()