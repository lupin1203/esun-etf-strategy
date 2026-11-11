import os, requests, pandas as pd
from dplan_validator import UNIVERSE

def get(url, params=None):
    try:
        r = requests.get(url, params=params, timeout=30)
        return r.json()
    except Exception as e:
        print("  失敗：", type(e).__name__); return None

for name, url in [("證交所 上市公司基本資料", "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"),
                  ("櫃買 上櫃公司基本資料", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O")]:
    print("=" * 60, "\n", name)
    d = get(url)
    if not d: continue
    print("  筆數", len(d), "｜欄位", list(d[0].keys())[:12])
    for rec in d:
        if str(list(rec.values())[1]).strip() in ("2330", "6488") or str(rec.get("公司代號", "")).strip() in ("2330", "6488"):
            print("  ", {k: rec[k] for k in list(rec)[:6]})

print("=" * 60, "\n FinMind TaiwanStockInfo")
p = {"dataset": "TaiwanStockInfo"}
t = os.environ.get("FINMIND_TOKEN", "")
if t: p["token"] = t
d = get("https://api.finmindtrade.com/api/v4/data", p)
if d and d.get("data"):
    df = pd.DataFrame(d["data"])
    df = df[df["stock_id"].isin(UNIVERSE)]
    print("  欄位", list(df.columns))
    print("  2330、6488 的所有列：")
    print(df[df["stock_id"].isin(["2330", "6488"])].to_string())
    print("  150 檔每檔出現幾列：", df.groupby("stock_id").size().value_counts().to_dict())
    print("  產業別分布：", df.drop_duplicates("stock_id")["industry_category"].value_counts().to_dict())