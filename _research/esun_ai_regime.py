# -*- coding: utf-8 -*-
r"""
esun_ai_regime.py — 研究：「非 AI 當道 → AI 當道」轉換的時間點有什麼共通點（2022-01 起）

  · AI 領漲股 = 2026 年 AI 強勢月份的贏家主題（光通訊、PCB/CCL/材料、先進封裝/設備、ASIC、散熱/機構），共 48 檔
    （分類是事後依產業知識分的，有後見之明；用來研究「時機」，不是拿來選股）
  · 其他股 = 150 檔扣掉 AI 領漲股、組裝廠、台積電；兩組都等權、單日漲跌截在 ±11%
  · AI 相對強弱 = AI 領漲股日報酬 − 其他股日報酬（對數），20 日加總 > +8% 且前 20 日曾 ≤ +2% → 一波 AI 行情
    啟動日 = 往回找到 20 日相對強弱最後一次 ≤ +2% 的隔天
  · 輝達財報日用台灣反應日（美國盤後隔天）；2026-05、2026-11 是推估
輸出：_research\bt_out_regime\ 啟動日特徵.csv、財報前後.csv
用法（在 SMC- 資料夾）：python _research\esun_ai_regime.py
"""
import os, json
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "bt_out_regime"); os.makedirs(OUT, exist_ok=True)
T = {"光通訊": "3163 3363 4979 3081 4991 3105 3491", "PCB材料": "8046 3037 3189 4958 2383 6274 8358 1815 1303 2368 3044",
     "封裝設備": "6187 7751 7734 2360 3131 3374 6789 2404 6147 3711 6139 6515 6223 6510 1785 6182",
     "ASIC": "3443 3661 2454 3529", "散熱機構": "3017 3324 3653 2059 6584 2308 1519 3665 6290 3533",
     "組裝": "2317 2382 3231 6669 2356 2376 2357 4938 2301 2324", "記憶體": "2408 2344 2337 8299 2451 3260 6770 5289"}
AI = set(" ".join(T[k] for k in ["光通訊", "PCB材料", "封裝設備", "ASIC", "散熱機構"]).split())
EXCL = AI | set(T["組裝"].split()) | {"2330"}
NV = pd.to_datetime(["2022-02-17", "2022-05-26", "2022-08-25", "2022-11-17", "2023-02-23", "2023-05-25", "2023-08-24",
                     "2023-11-22", "2024-02-22", "2024-05-23", "2024-08-29", "2024-11-21", "2025-02-27", "2025-05-29",
                     "2025-08-28", "2025-11-20", "2026-02-26", "2026-05-21", "2026-08-27", "2026-11-19"])

sec = json.load(open(os.path.join(ROOT, "esun_sector.json"), encoding="utf-8"))
px, val = {}, {}
for s in sec:
    for p in (os.path.join(ROOT, "local_data", f"{s}.TW.parquet"), os.path.join(ROOT, "local_data_otc", f"{s}.TWO.parquet")):
        if os.path.exists(p):
            d = pd.read_parquet(p).loc["2022-01-01":]; px[s] = d["Close"]; val[s] = d["Close"] * d["Volume"]; break
P, V = pd.DataFrame(px), pd.DataFrame(val)
R = P.pct_change(fill_method=None).clip(-0.11, 0.11)
idx = pd.DataFrame({"ai": R[[c for c in R if c in AI]].mean(axis=1), "rest": R[[c for c in R if c not in EXCL]].mean(axis=1),
                    "mem": R[T["記憶體"].split()].mean(axis=1), "tsmc": R["2330"], "ew": R.mean(axis=1)}).dropna()
lr = np.log1p(idx); rel = lr.ai - lr.rest; rel20 = rel.rolling(20).sum()

on, last = [], None
for i in range(40, len(rel20) - 20):
    d = rel20.index[i]
    if rel20.iloc[i] > 0.08 and rel20.iloc[i - 20:i].min() <= 0.02 and (last is None or (d - last).days > 40):
        on.append(d); last = d
starts = []
for d in on:
    j = rel20.index.get_loc(d)
    while j > 0 and rel20.iloc[j] > 0.02:
        j -= 1
    starts.append(rel20.index[j + 1])

share = V[[c for c in V if c in AI]].sum(axis=1) / V.sum(axis=1)
lvl = (1 + idx).cumprod()


def feat(d):
    i = idx.index.get_loc(d); g = lambda c, n: lvl[c].iloc[i - 1] / lvl[c].iloc[i - 1 - n] - 1
    nxt, prv = NV[NV >= d], NV[NV < d]
    return {"之後20日AI相對": rel.iloc[i:i + 20].sum(), "前20日AI相對": rel.iloc[i - 20:i].sum(), "前20日大盤": g("ew", 20),
            "前60日記憶體": g("mem", 60), "前20日台積電": g("tsmc", 20),
            "AI股距60日高": lvl["ai"].iloc[i - 1] / lvl["ai"].iloc[i - 61:i].max() - 1,
            "AI成交占比變化": share.iloc[i - 20:i].mean() / share.iloc[i - 120:i].mean() - 1,
            "距下次輝達財報": (nxt[0] - d).days if len(nxt) else np.nan, "距上次輝達財報": (d - prv[-1]).days if len(prv) else np.nan}


X = pd.DataFrame({d.date(): feat(d) for d in starts}).T
base = pd.DataFrame({d: feat(d) for d in idx.index[140:-21:5]}).T
X.to_csv(os.path.join(OUT, "啟動日特徵.csv"), encoding="utf-8-sig", float_format="%.4f")
print("AI 行情啟動日：\n" + X.round(3).to_string())
print("\n啟動日中位數 vs 平常中位數\n" + pd.DataFrame({"啟動日": X.median(), "平常": base.median()}).round(3).to_string())

dt = pd.Series([(NV[NV >= d][0] - d).days if (NV >= d).any() else np.nan for d in rel.index], index=rel.index)
rows = []
for a, b in [(0, 7), (8, 14), (15, 28), (29, 45), (46, 70), (71, 95)]:
    m = (dt >= a) & (dt <= b)
    rows.append({"距下次輝達財報(天)": f"{a}-{b}", "AI相對每20日": rel[m].mean() * 20, "上漲比例": (rel[m] > 0).mean(), "樣本天數": int(m.sum())})
E2 = pd.DataFrame(rows); E2.to_csv(os.path.join(OUT, "財報前後.csv"), encoding="utf-8-sig", index=False, float_format="%.4f")
print("\n" + E2.round(4).to_string(index=False))
