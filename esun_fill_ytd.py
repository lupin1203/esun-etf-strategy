# -*- coding: utf-8 -*-
"""
esun_fill_ytd.py — 幫「台股科技與主動式ETF持股報表」補上年初至今報酬（含息），並用官方資料核對產業別
  · 報酬：Yahoo Finance 還原股價（含除息），2025/12/31 前最後一個交易日收盤 → 最新收盤
  · 產業別：證交所／櫃買中心 OpenAPI 公司基本資料（官方），不一致時以官方為準
用法：python esun_fill_ytd.py 台股科技與主動式ETF持股報表_2026-10-01.xlsx
"""
import sys, requests
from openpyxl import load_workbook

YTD_BASE = "2025-12-31"
NAMES = {"01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械", "06": "電器電纜",
         "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造",
         "15": "航運業", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨", "19": "綜合", "20": "其他",
         "21": "化學工業", "22": "生技醫療業", "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業",
         "26": "光電業", "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
         "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務", "35": "綠能環保",
         "36": "數位雲端", "37": "運動休閒", "38": "居家生活"}


def official():
    out = {}
    for url, ck, ik, mk in (("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", "公司代號", "產業別", "twse"),
                            ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", "SecuritiesCompanyCode",
                             "SecuritiesIndustryCode", "tpex")):
        try:
            for r in requests.get(url, timeout=30).json():
                code = str(r.get(ik, "")).strip().zfill(2)
                out[str(r.get(ck, "")).strip()] = (mk, NAMES.get(code, f"代碼{code}"))
        except Exception as e:
            print(f"  ! 官方資料抓取失敗（{mk}）：{type(e).__name__}")
    return out


def ytd(tickers, info):
    import yfinance as yf
    sym = {t: f"{t}.TWO" if info.get(t, ("twse",))[0] == "tpex" else f"{t}.TW" for t in tickers}
    data = yf.download(list(sym.values()), start="2025-12-15", auto_adjust=True, progress=False, group_by="ticker")
    out, last = {}, None
    for t, s in sym.items():
        try:
            c = data[s]["Close"].dropna()
            b = c.loc[:YTD_BASE]
            if len(b):
                out[t] = float(c.iat[-1]) / float(b.iat[-1]) - 1
                last = max(last or c.index[-1], c.index[-1])
        except Exception:
            pass
    return out, last


def main(path):
    wb = load_workbook(path)
    hs, ds = wb["熱門持股"], wb["明細"]
    tickers = [str(hs.cell(r, 1).value) for r in range(2, hs.max_row + 1) if hs.cell(r, 1).value]
    info = official()
    print(f"官方產業別 {len(info)} 家")
    fixed = 0
    for r in range(2, hs.max_row + 1):
        t = str(hs.cell(r, 1).value)
        if t in info and hs.cell(r, 3).value != info[t][1]:
            print(f"  產業別更正：{t} {hs.cell(r, 2).value}　{hs.cell(r, 3).value} → {info[t][1]}")
            hs.cell(r, 3).value = info[t][1]; fixed += 1
    for r in range(2, ds.max_row + 1):
        t = str(ds.cell(r, 6).value)
        if t in info:
            ds.cell(r, 8).value, ds.cell(r, 12).value = info[t][1], "官方（OpenAPI）"
    ret, last = ytd(tickers, info)
    for r in range(2, hs.max_row + 1):
        hs.cell(r, 8).value = ret.get(str(hs.cell(r, 1).value))
    miss = [t for t in tickers if t not in ret]
    wb["說明"].append([f"年初至今報酬已於本機更新：2025/12/31 → {last:%Y/%m/%d}（{len(ret)} 檔）"
                      + (f"；抓不到：{'、'.join(miss)}" if miss else "") if last else "年初至今報酬：下載失敗"])
    wb.save(path)
    print(f"報酬 {len(ret)} / {len(tickers)} 檔" + (f"，抓不到：{miss}" if miss else "") + f"｜產業別更正 {fixed} 檔")
    print(f"已更新 {path}，用 Excel 打開後公式會自動重算")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "台股科技與主動式ETF持股報表_2026-10-08.xlsx")