# esun_probe_data.py — 探路：確認證交所、櫃買中心每日行情的欄位（一次性，之後可封存）
import requests, json
from dplan_validator import UNIVERSE

URLS = {
    "證交所": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "櫃買": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
}
for name, url in URLS.items():
    print("=" * 60, "\n", name, url)
    try:
        r = requests.get(url, timeout=30)
        print("  狀態碼", r.status_code)
        data = r.json()
    except Exception as e:
        print("  失敗：", type(e).__name__, str(e)[:200]); continue
    print("  筆數", len(data))
    print("  欄位", list(data[0].keys()))
    code_key = next((k for k in data[0] if "code" in k.lower() or "代號" in k), None)
    codes = {str(d.get(code_key, "")).strip() for d in data}
    print(f"  代號欄位={code_key}；150檔中找得到 {len(UNIVERSE & codes)} 檔")
    for sample in ("2330", "6488"):
        rec = next((d for d in data if str(d.get(code_key, "")).strip() == sample), None)
        if rec:
            print(f"  {sample}：", json.dumps(rec, ensure_ascii=False))