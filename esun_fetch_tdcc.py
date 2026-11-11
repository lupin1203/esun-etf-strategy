# -*- coding: utf-8 -*-
"""
esun_fetch_tdcc.py — 每週存一份集保戶股權分散表（集保結算所開放資料，政府資料開放授權）
  · 來源只提供「最新一週」，每週六早上更新 → 要自己每週存，才有歷史
  · 只存比賽 150 檔，存到 esun_tdcc/tdcc_YYYYMMDD.csv（同一週存過就略過）
  · 持股分級 12–15 = 400,001 股以上（400 張以上大戶）；16 = 差異數調整、17 = 合計，不計入
用法：python esun_fetch_tdcc.py   （每日流程也會自動呼叫，一天一次請求）
"""
import os, io, glob
import pandas as pd, requests
from dplan_validator import UNIVERSE

URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
OUT_DIR = "./esun_tdcc"
BIG_LEVELS = {12, 13, 14, 15}


def parse(text):
    d = pd.read_csv(io.StringIO(text.lstrip("\ufeff")), dtype=str)
    d.columns = [c.strip() for c in d.columns]
    d = d.rename(columns={"資料日期": "date", "證券代號": "sid", "持股分級": "level", "人數": "holders",
                          "股數": "shares", d.columns[-1]: "pct"})
    d["sid"] = d["sid"].str.strip()
    d = d[d["sid"].isin(UNIVERSE)].copy()
    d["level"] = pd.to_numeric(d["level"], errors="coerce")
    for c in ("holders", "shares", "pct"):
        d[c] = pd.to_numeric(d[c].str.replace(",", ""), errors="coerce")
    return d


def big_ratio(d):
    """每檔 400 張以上大戶持股比例（%）"""
    return d[d["level"].isin(BIG_LEVELS)].groupby("sid")["pct"].sum()


def fetch(log=print):
    os.makedirs(OUT_DIR, exist_ok=True)
    r = requests.get(URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.encoding = "utf-8"
    d = parse(r.text)
    if d.empty:
        log("集保股權分散表：抓到的資料是空的"); return None
    date = d["date"].iat[0].strip()
    path = os.path.join(OUT_DIR, f"tdcc_{date}.csv")
    if os.path.exists(path):
        log(f"集保股權分散表：{date} 已存過")
    else:
        d.to_csv(path, index=False, encoding="utf-8-sig")
        log(f"集保股權分散表：存下 {date}，{d['sid'].nunique()} 檔")
    return date


def history():
    """回傳 DataFrame：index=日期，columns=代號，值=400 張以上大戶比例"""
    cols = {}
    for f in sorted(glob.glob(os.path.join(OUT_DIR, "tdcc_*.csv"))):
        d = pd.read_csv(f, dtype={"sid": str, "date": str})
        cols[os.path.basename(f)[5:13]] = big_ratio(d)
    return pd.DataFrame(cols).T.sort_index()


if __name__ == "__main__":
    fetch()
    h = history()
    print(f"目前累積 {len(h)} 週：{list(h.index)}")
    if "2330" in h.columns:
        print("2330 台積電 400 張以上大戶比例：", ", ".join(f"{i} {v:.2f}%" for i, v in h["2330"].items()))