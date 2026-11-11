import json, os
sec = json.load(open("esun_sector.json", encoding="utf-8"))
AI_CODES = {"24": "半導體", "25": "電腦週邊", "28": "電子零組件"}
pool = sorted((v["code"], sid) for sid, v in sec.items() if v["code"] in AI_CODES)
no_rev, no_eps = [], []
for code, sid in pool:
    rev = os.path.exists(f"revenue_cache/history/{sid}.parquet")
    eps = os.path.exists(f"revenue_cache/eps_history/{sid}.parquet")
    if not rev: no_rev.append(sid)
    if not eps: no_eps.append(sid)
for code in AI_CODES:
    print(f"{AI_CODES[code]}：", " ".join(sid for c, sid in pool if c == code))
print(f"\n選股池共 {len(pool)} 檔")
print(f"缺營收資料 {len(no_rev)} 檔：{no_rev}")
print(f"缺 EPS 資料 {len(no_eps)} 檔：{no_eps}")

other = {sid: v["name"] for sid, v in sec.items() if v["code"] in ("27", "31", "26", "05")}
print("\n其他電子相關產業（沒納入，看看有沒有該補的）：")
print(" ".join(f"{sid}{n[:2]}" for sid, n in sorted(other.items())))