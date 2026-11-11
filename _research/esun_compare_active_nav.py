# -*- coding: utf-8 -*-
r"""
esun_compare_active_nav.py — 跟 esun_compare_active.py 一樣，但主動式 ETF 改用「淨值（NAV）」算報酬，不用市價

  · 為什麼：ETF 市價有折溢價；淨值是投信用持股收盤價算的，跟我們算自己淨值的方法一樣，比較公平
  · 淨值來源：玩股網「折溢價」頁背後的 API https://www.wantgoo.com/stock/etf/<代號>/discount-premium-data
    （欄位 date＝毫秒時間戳、bookValue＝淨值；網站每天更新到 23:00，晚上太早跑，當天淨值可能還沒出來）
  · 含息：除息日把配息加回（TaiwanStockDividendResult），淨值除息日也會掉
  · 起點：9/30 的淨值；比到「每檔都有淨值」的最後一天，某檔缺最後一天就標出來
  · 同時印出市價算法的結果，對照折溢價造成多少差異
輸出：_research\bt_out_oct\主動ETF比較_NAV.csv
用法（在 SMC- 資料夾）：python _research\esun_compare_active_nav.py
"""
import os, re, sys, time, html
import pandas as pd, requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import esun_compare_active as C            # 共用：token()、get()、total_return_index()、路徑

OUT_CSV = os.path.join(HERE, "bt_out_oct", "主動ETF比較_NAV.csv")
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/128.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9", "Referer": "https://www.wantgoo.com/stock/etf/ranking/volume"}


def cells(row_html, tag):
    return [re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]+>", "", c)))
            for c in re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", row_html, re.S)]


def fetch_nav(sess, code):
    """回傳 DataFrame(index=日期, 欄位 nav)。玩股網折溢價頁背後的 API：/stock/etf/<代號>/discount-premium-data
    （頁面 JS 讀 date＝毫秒時間戳、bookValue＝淨值）；先開頁面再呼叫，代號大小寫都試"""
    last = None
    for cid in (code.upper(), code.lower()):
        page = f"https://www.wantgoo.com/stock/etf/{cid}/discount-premium"
        api = f"https://www.wantgoo.com/stock/etf/{cid}/discount-premium-data"
        for k in range(3):
            sess.get(page, headers=HEAD, timeout=20)
            time.sleep(1.0 + k)
            r = sess.get(api, headers={**HEAD, "Referer": page, "X-Requested-With": "XMLHttpRequest",
                                       "Accept": "application/json, text/javascript, */*; q=0.01"}, timeout=20)
            last = f"HTTP {r.status_code} {r.text[:120]!r}"
            if r.status_code == 200:
                try:
                    j = r.json()
                except ValueError:
                    break
                rows = j if isinstance(j, list) else (j.get("data") or j.get("items") or [])
                data = {}
                for x in rows:
                    try:
                        d = pd.Timestamp(int(x["date"]), unit="ms", tz="UTC").tz_convert("Asia/Taipei").normalize().tz_localize(None)
                        v = float(x["bookValue"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if v > 0:
                        data[d] = v
                if data:
                    return pd.DataFrame({"nav": pd.Series(data)}).sort_index().assign(price=None)
                last = f"回應沒有 date/bookValue：{str(j)[:120]}"
                break
            time.sleep(4 + 3 * k)
    raise ValueError(last)


def with_dividends(s, code, base, tk):
    """淨值序列 → 以 base 當天 = 1 的含息指數"""
    s = s[s.index >= pd.Timestamp(base)]
    if s.empty or s.index[0] != pd.Timestamp(base):
        return None
    div = C.get("TaiwanStockDividendResult", code, base, tk)
    cash = {}
    if not div.empty and "stock_and_cache_dividend" in div:
        for _, r in div.iterrows():
            try:
                cash[pd.Timestamp(r["date"])] = float(r["stock_and_cache_dividend"])
            except Exception:
                pass
    idx, prev = [1.0], s.iloc[0]
    for d, v in s.iloc[1:].items():
        idx.append(idx[-1] * v / (prev - cash.get(d, 0.0)))
        prev = v
    return pd.Series(idx, index=s.index)


def main():
    base = "2026-09-30"
    nav_us = pd.read_csv(C.NAV_CSV, index_col=0, encoding="utf-8-sig").iloc[:, 0]
    nav_us.index = pd.to_datetime(nav_us.index)
    ours = nav_us / C.INIT - 1

    etf = pd.read_csv(C.ETF_CSV, dtype=str, encoding="utf-8-sig").drop_duplicates("etf_code")
    names = dict(zip(etf["etf_code"], etf["etf_name"]))
    names["0050"] = "元大台灣50（對照）"
    tk = C.token()
    sess = requests.Session()
    try:
        sess.get("https://www.wantgoo.com/stock/etf/ranking/volume", headers=HEAD, timeout=20)
    except Exception:
        pass

    navs, prem, bad = {}, {}, []
    for k, code in enumerate(names, 1):
        try:
            df = fetch_nav(sess, code)
            s = with_dividends(df["nav"], code, base, tk)
            if s is None:
                raise ValueError(f"沒有 {base} 的淨值")
            navs[code] = s
            last = df.index[-1]
            if df.loc[last, "price"]:
                prem[code] = df.loc[last, "price"] / df.loc[last, "nav"] - 1
            print(f"[{k}/{len(names)}] {code} {names[code]} OK 淨值到 {last:%Y-%m-%d}")
        except Exception as e:
            bad.append(f"{code}（{e}）")
            print(f"[{k}/{len(names)}] {code} {names[code]} ✗ {e}")
        time.sleep(3)

    last_us = ours.index[-1]
    missing_last = [c for c, s in navs.items() if s.index[-1] < last_us]
    table = {"我們（重跑）": ours}
    for c, s in navs.items():
        table[f"{c} {names[c]}"] = s.reindex(ours.index) - 1        # 不補值：缺的那天就是空的
    df = pd.DataFrame(table)
    df.to_csv(OUT_CSV, encoding="utf-8-sig", float_format="%.5f")

    final = df.loc[last_us].dropna().sort_values(ascending=False)
    active = [c for c in final.index if c != "我們（重跑）" and not c.startswith("0050")]
    print(f"\n{'=' * 64}\n{base} → {last_us:%Y-%m-%d}，累計報酬（ETF 用淨值、含息）\n{'=' * 64}")
    old = None
    if os.path.exists(os.path.join(HERE, "bt_out_oct", "主動ETF比較.csv")):
        old = pd.read_csv(os.path.join(HERE, "bt_out_oct", "主動ETF比較.csv"), index_col=0, encoding="utf-8-sig")
        old.index = pd.to_datetime(old.index)
    for r, (c, v) in enumerate(final.items(), 1):
        code = c.split()[0]
        mk = ""
        if old is not None and c in old.columns and last_us in old.index and c != "我們（重跑）":
            mk = f"   市價算 {old.loc[last_us, c]:+7.2%}"
        pm = f"   最新折溢價 {prem[code]:+.2%}" if code in prem else ""
        print(f"  {r:>2}. {c:<22}{v:>+8.2%}{mk}{pm}{'  ◀' if c == '我們（重跑）' else ''}")
    me = final["我們（重跑）"]
    print(f"\n我們贏 {sum(final[c] < me for c in active)}/{len(active)} 檔主動式 ETF（用淨值）")
    print(f"主動式 ETF 中位數 {final[active].median():+.2%}、最好 {final[active].max():+.2%}")
    if missing_last:
        print(f"! 這些 ETF 還沒有 {last_us:%Y-%m-%d} 的淨值，沒列入排名（晚上 23:00 後再跑）：{'、'.join(missing_last)}")
    if bad:
        print("抓不到的：" + "、".join(bad))
    print(f"\n每天的累計報酬存在 {OUT_CSV}")


if __name__ == "__main__":
    main()
