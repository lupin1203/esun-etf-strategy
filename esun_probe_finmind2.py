import os, requests, pandas as pd
URL = "https://api.finmindtrade.com/api/v4/data"
t = os.environ.get("FINMIND_TOKEN", "")
for ds, start in [("TaiwanStockInstitutionalInvestorsBuySell", "2026-09-20"),
                  ("TaiwanStockHoldingSharesPer", "2026-09-01")]:
    print("=" * 60, "\n", ds)
    p = {"dataset": ds, "data_id": "2330", "start_date": start}
    if t: p["token"] = t
    try:
        j = requests.get(URL, params=p, timeout=30).json()
        if not j.get("data"):
            print("  沒有資料：", j.get("msg")); continue
        d = pd.DataFrame(j["data"])
        print("  欄位：", list(d.columns))
        print(d.tail(12).to_string())
    except Exception as e:
        print("  失敗：", type(e).__name__)
        