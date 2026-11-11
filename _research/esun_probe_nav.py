# -*- coding: utf-8 -*-
r"""
esun_probe_nav.py — 抓一次玩股網 00981A 折溢價頁，把原始內容存檔，給 Claude 看網頁實際長什麼樣
用法（在 SMC- 資料夾）：python _research\esun_probe_nav.py
輸出：_research\probe_nav\ 底下的 page.html、candidates.txt
"""
import os, re, requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "probe_nav")
os.makedirs(OUT, exist_ok=True)
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/128.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9", "Referer": "https://www.wantgoo.com/stock/etf/ranking/volume"}
s = requests.Session()
s.get("https://www.wantgoo.com/stock/etf/ranking/volume", headers=HEAD, timeout=20)
page = "https://www.wantgoo.com/stock/etf/00981a/discount-premium"
r = s.get(page, headers=HEAD, timeout=20)
open(os.path.join(OUT, "page.html"), "w", encoding="utf-8").write(r.text)
lines = [f"status {r.status_code}  length {len(r.text)}  有<table> {'<table' in r.text}  有「淨值」 {'淨值' in r.text}"]
lines += sorted(set(re.findall(r"""["'](/[^"'\s]*(?:data|premium|discount|nav|net)[^"'\s]*)["']""", r.text, re.I)))
lines += sorted(set(re.findall(r"""<script[^>]+src=["']([^"']+)["']""", r.text)))
for guess in ["discount-premium-data", "discount-premium-chart-data", "premium-discount-data", "net-value-data"]:
    u = f"https://www.wantgoo.com/stock/etf/00981a/{guess}"
    try:
        g = s.get(u, headers={**HEAD, "Referer": page, "X-Requested-With": "XMLHttpRequest",
                              "Accept": "application/json, text/javascript, */*; q=0.01"}, timeout=20)
        lines.append(f"試 {guess}: {g.status_code} {g.text[:300]!r}")
        if g.status_code == 200:
            open(os.path.join(OUT, f"{guess}.txt"), "w", encoding="utf-8").write(g.text)
    except Exception as e:
        lines.append(f"試 {guess}: {e}")
open(os.path.join(OUT, "candidates.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines[:40]))
