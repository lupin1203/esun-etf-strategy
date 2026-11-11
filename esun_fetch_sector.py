# -*- coding: utf-8 -*-
"""
esun_fetch_sector.py — 抓比賽 150 檔的官方產業別（證交所、櫃買中心公司基本資料）
  · 每家公司只有一個產業別代碼，上市上櫃共用同一套代碼
  · 不用 FinMind：它會把同一檔同時歸到細分類和「電子工業」等大分類
  · 存成 esun_sector.json：{代號: {"code": "24", "name": "半導體業"}}
用法：python esun_fetch_sector.py
"""
import json, requests
from collections import Counter
from dplan_validator import UNIVERSE

TWSE = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
OUT = "esun_sector.json"
NAMES = {"01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械",
         "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業",
         "12": "汽車工業", "14": "建材營造", "15": "航運業", "16": "觀光餐旅", "17": "金融保險",
         "18": "貿易百貨", "19": "綜合", "20": "其他", "21": "化學工業", "22": "生技醫療業",
         "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業",
         "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
         "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務",
         "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活"}


def parse(rows, code_key, ind_key):
    out = {}
    for r in rows:
        sid, code = str(r.get(code_key, "")).strip(), str(r.get(ind_key, "")).strip().zfill(2)
        if sid in UNIVERSE and code:
            out[sid] = {"code": code, "name": NAMES.get(code, f"代碼{code}")}
    return out


def main():
    tw = parse(requests.get(TWSE, timeout=30).json(), "公司代號", "產業別")
    tp = parse(requests.get(TPEX, timeout=30).json(), "SecuritiesCompanyCode", "SecuritiesIndustryCode")
    sec = {**tw, **tp}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(sec, f, ensure_ascii=False, indent=1)
    print(f"150 檔中取得 {len(sec)} 檔（上市 {len(tw)}、上櫃 {len(tp)}）→ {OUT}")
    miss = sorted(UNIVERSE - set(sec))
    if miss:
        print("  缺：", miss)
    print("  產業分布：", dict(Counter(v["name"] for v in sec.values()).most_common()))


if __name__ == "__main__":
    main()