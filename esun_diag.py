CODES = ["2337", "3260", "5289"]
import pandas as pd, os, requests
for sid in CODES:
    print("=" * 50, sid)
    e = pd.read_parquet(f"revenue_cache/eps_history/{sid}.parquet").tail(6)
    print("近六季 EPS：", ", ".join(f"{i:%Y-%m} {v:.2f}" for i, v in e["eps"].items()))
    r = pd.read_parquet(f"revenue_cache/esun_revenue/{sid}.parquet").tail(5)
    print("近五月營收 YoY：", ", ".join(f"{i:%Y-%m} {v:+.0f}%" for i, v in r["yoy"].items()))
    p = {"dataset": "TaiwanStockFinancialStatements", "data_id": sid, "start_date": "2025-04-01"}
    t = os.environ.get("FINMIND_TOKEN", "")
    if t: p["token"] = t
    try:
        d = pd.DataFrame(requests.get("https://api.finmindtrade.com/api/v4/data", params=p, timeout=20).json()["data"])
        d = d[d["type"].isin(["OperatingIncome", "TotalNonoperatingIncomeAndExpense", "IncomeAfterTaxes"])]
        print(d.pivot(index="date", columns="type", values="value").div(1e8).round(1).to_string())
        print("（單位：億元；營業利益 vs 業外收支，業外很大代表一次性收益多）")
    except Exception as ex:
        print("財報抓取失敗：", type(ex).__name__)