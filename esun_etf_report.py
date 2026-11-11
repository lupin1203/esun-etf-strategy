# -*- coding: utf-8 -*-
"""
esun_etf_report.py — 台股科技 ETF＋主動式 ETF 前十大持股報表（剔除台積電）

資料來源：
  · 持股：你手動抄的表格（遵守 e添富／投信官網條款）
      主動式：esun_etf_top10.csv（主動投資比率用的同一份）
      被動式科技：esun_tech_etf_top11.csv（本程式產生範本）
    每檔抄到第 11 名，剔除台積電後剛好 10 檔
  · 產業別、公司簡稱：證交所／櫃買中心 OpenAPI 公司基本資料（開放資料）
  · 年初至今報酬：Yahoo Finance 還原股價（含除息），起點 = 2025 最後一個交易日收盤
輸出：esun_etf_report_YYYY-MM-DD.xlsx
  明細｜熱門持股｜產業彙總｜換股｜說明
換股：每次執行都存一份持股快照到 esun_etf_history/，自動和上一份比對
用法：
  python esun_etf_report.py template   產生被動式科技 ETF 的空白表格
  python esun_etf_report.py            產生報表
"""
import os, sys, csv, json, glob, shutil
from datetime import datetime
import pandas as pd
import requests

ACTIVE_FILE, TECH_FILE = "esun_etf_top10.csv", "esun_tech_etf_top11.csv"
HIST_DIR = "./esun_etf_history"
YTD_BASE = "2025-12-31"
EXCLUDE = {"2330"}
FIELDS = ["etf_code", "etf_name", "type", "data_date", "rank", "ticker", "weight_pct"]
TECH_ETFS = [
    ("0052", "富邦科技"), ("0053", "元大電子"), ("00881", "國泰台灣科技龍頭"), ("00891", "中信關鍵半導體"),
    ("00892", "富邦台灣半導體"), ("00904", "台新臺灣半導體30"), ("00913", "兆豐台灣晶圓製造"),
    ("00927", "群益半導體收益"), ("00929", "復華台灣科技優息"), ("00935", "野村臺灣新科技50"),
    ("00941", "中信上游半導體"), ("00946", "群益科技高息成長"), ("00947", "台新臺灣IC設計"),
]
TWSE_INFO = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_INFO = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"


# ---------------- 範本 ----------------
def make_template():
    if os.path.exists(TECH_FILE):
        print(f"{TECH_FILE} 已存在，不覆蓋"); return
    with open(TECH_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerow(["範例", "（這列是格式示範，程式會略過）", "被動科技", "2026-09-30", 1, "2454", "7.55"])
        for code, name in TECH_ETFS:
            for r in range(1, 12):
                w.writerow([code, name, "被動科技", "", r, "", ""])
    print(f"已產生 {TECH_FILE}：{len(TECH_ETFS)} 檔 × 11 列。填 data_date、ticker（4 碼）、weight_pct（8.52 代表 8.52%）")
    print("  每檔抄到第 11 名（剔除台積電後剛好 10 檔）。代號若有錯或已下市，整檔留空即可")


# ---------------- 讀持股 ----------------
def load_holdings():
    rows = []
    for path, default_type in ((ACTIVE_FILE, "主動"), (TECH_FILE, "被動科技")):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                t = (r.get("ticker") or "").strip().upper()
                if not t or t == "NONE" or r.get("etf_code") == "範例":
                    continue
                rows.append({"etf_code": r["etf_code"].strip(), "etf_name": r["etf_name"].strip(),
                             "type": "主動" if default_type == "主動" else "被動科技",
                             "data_date": (r.get("data_date") or "").strip(),
                             "rank": int(r["rank"]) if str(r.get("rank", "")).strip().isdigit() else 99,
                             "ticker": t, "weight": float(r["weight_pct"]) / 100 if r.get("weight_pct") else None})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[~df["ticker"].isin(EXCLUDE)].sort_values(["etf_code", "rank"])
    return df.groupby("etf_code", sort=False).head(10).reset_index(drop=True)


# ---------------- 換股 ----------------
def snapshot_and_changes(df, today):
    os.makedirs(HIST_DIR, exist_ok=True)
    prev_files = sorted(f for f in glob.glob(os.path.join(HIST_DIR, "holdings_*.csv"))
                        if os.path.basename(f)[9:19] < today)
    df.to_csv(os.path.join(HIST_DIR, f"holdings_{today}.csv"), index=False, encoding="utf-8-sig")
    if not prev_files:
        return pd.DataFrame(), None
    prev = pd.read_csv(prev_files[-1], dtype={"ticker": str, "etf_code": str})
    base = os.path.basename(prev_files[-1])[9:19]
    out = []
    for code, g in df.groupby("etf_code", sort=False):
        old = set(prev.loc[prev["etf_code"] == code, "ticker"])
        new = set(g["ticker"])
        if not old:
            continue
        add, rem = sorted(new - old), sorted(old - new)
        if add or rem:
            out.append({"etf_code": code, "etf_name": g["etf_name"].iat[0], "add": add, "remove": rem})
    return pd.DataFrame(out), base


# ---------------- 產業別、名稱 ----------------
def company_info():
    from esun_fetch_sector import NAMES
    info = {}
    for url, code_k, name_k, ind_k, mk in ((TWSE_INFO, "公司代號", "公司簡稱", "產業別", "twse"),
                                          (TPEX_INFO, "SecuritiesCompanyCode", "CompanyAbbreviation",
                                           "SecuritiesIndustryCode", "tpex")):
        try:
            for r in requests.get(url, timeout=30).json():
                c = str(r.get(code_k, "")).strip()
                code = str(r.get(ind_k, "")).strip().zfill(2)
                info[c] = {"name": str(r.get(name_k, "")).strip(), "industry": NAMES.get(code, f"代碼{code}"),
                           "market": mk}
        except Exception as e:
            print(f"  ! 公司基本資料抓取失敗（{mk}）：{type(e).__name__}")
    return info


# ---------------- 年初至今報酬（含息）----------------
def ytd_returns(tickers, info):
    import yfinance as yf
    sym = {t: f"{t}.TWO" if info.get(t, {}).get("market") == "tpex" else f"{t}.TW" for t in tickers}
    out = {}
    try:
        data = yf.download(list(sym.values()), start="2025-12-15", auto_adjust=True, progress=False,
                           group_by="ticker", threads=True)
    except Exception as e:
        print(f"  ! 股價下載失敗：{type(e).__name__}"); return out
    for t, s in sym.items():
        try:
            c = (data[s]["Close"] if len(sym) > 1 else data["Close"]).dropna()
            base = c.loc[:YTD_BASE]
            if len(base) and len(c):
                out[t] = (float(c.iat[-1]) / float(base.iat[-1]) - 1, c.index[-1].strftime("%Y-%m-%d"))
        except Exception:
            continue
    return out


def our_roles():
    fs = sorted(glob.glob("./esun_targets/targets_*.json"))
    return json.load(open(fs[-1], encoding="utf-8")).get("roles", {}) if fs else {}


# ---------------- 寫 Excel ----------------
def write_excel(df, changes, base_date, info, ytd, roles, today):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    F, FB = Font(name="Arial"), Font(name="Arial", bold=True)
    HEAD = PatternFill("solid", start_color="D9E1F2")
    PCT = "0.0%;-0.0%;-"

    def sheet(ws, headers, widths):
        ws.append(headers)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
            ws.cell(1, i).font, ws.cell(1, i).fill = FB, HEAD
        ws.freeze_panes = "A2"

    # 明細
    ws = wb.active
    ws.title = "明細"
    sheet(ws, ["ETF代號", "ETF名稱", "類型", "資料日期", "排名", "股票代號", "股票名稱", "產業別", "權重",
               "年初至今報酬(含息)", "我們的持股"], [9, 18, 9, 11, 6, 9, 12, 16, 8, 14, 10])
    for r in df.itertuples():
        i = info.get(r.ticker, {})
        y = ytd.get(r.ticker)
        ws.append([r.etf_code, r.etf_name, r.type, r.data_date, r.rank, r.ticker, i.get("name", ""),
                   i.get("industry", "未知"), r.weight, y[0] if y else None, roles.get(r.ticker, "—")])
    n = ws.max_row
    for row in ws.iter_rows(min_row=2, max_row=n):
        for c in row:
            c.font = F
        row[8].number_format = row[9].number_format = PCT
    ws.auto_filter.ref = f"A1:K{n}"

    # 熱門持股（不重複股票，被持有檔數用公式算）
    uniq = df.groupby("ticker").size().sort_values(ascending=False).index.tolist()
    hs = wb.create_sheet("熱門持股")
    sheet(hs, ["股票代號", "股票名稱", "產業別", "被幾檔ETF持有", "平均權重", "年初至今報酬(含息)", "我們的持股"],
          [9, 12, 16, 13, 10, 14, 10])
    for k, t in enumerate(uniq, start=2):
        i = info.get(t, {})
        y = ytd.get(t)
        hs.append([t, i.get("name", ""), i.get("industry", "未知"),
                   f"=COUNTIF('明細'!$F$2:$F${n},A{k})", f"=AVERAGEIF('明細'!$F$2:$F${n},A{k},'明細'!$I$2:$I${n})",
                   y[0] if y else None, roles.get(t, "—")])
    m = hs.max_row
    for row in hs.iter_rows(min_row=2, max_row=m):
        for c in row:
            c.font = F
        row[4].number_format = row[5].number_format = PCT
    hs.auto_filter.ref = f"A1:G{m}"

    # 產業彙總（以不重複股票計）
    ins = wb.create_sheet("產業彙總")
    sheet(ins, ["產業別", "股票檔數", "被持有總次數", "平均年初至今報酬(含息)"], [18, 10, 13, 20])
    inds = sorted({info.get(t, {}).get("industry", "未知") for t in uniq},
                  key=lambda x: -sum(info.get(t, {}).get("industry", "未知") == x for t in uniq))
    for k, ind in enumerate(inds, start=2):
        ins.append([ind, f"=COUNTIF('熱門持股'!$C$2:$C${m},A{k})",
                    f"=SUMIF('熱門持股'!$C$2:$C${m},A{k},'熱門持股'!$D$2:$D${m})",
                    f"=IFERROR(AVERAGEIF('熱門持股'!$C$2:$C${m},A{k},'熱門持股'!$F$2:$F${m}),\"-\")"])
    for row in ins.iter_rows(min_row=2, max_row=ins.max_row):
        for c in row:
            c.font = F
        row[3].number_format = PCT

    # 換股
    cs = wb.create_sheet("換股")
    sheet(cs, ["ETF代號", "ETF名稱", "比較基準日", "新增", "剔除"], [9, 18, 12, 40, 40])
    if changes.empty:
        cs.append(["—", "第一次執行，下次更新表格後才會有換股比對" if base_date is None else "這段期間沒有換股",
                   base_date or "", "", ""])
    else:
        for r in changes.itertuples():
            fmt = lambda ts: "、".join(f"{t} {info.get(t, {}).get('name', '')}" for t in ts)
            cs.append([r.etf_code, r.etf_name, base_date, fmt(r.add), fmt(r.remove)])
    for row in cs.iter_rows(min_row=2, max_row=cs.max_row):
        for c in row:
            c.font, c.alignment = F, Alignment(wrap_text=True, vertical="top")

    # 說明
    ds = wb.create_sheet("說明")
    last = max((v[1] for v in ytd.values()), default="—")
    for line in [
        f"報表日期：{today}",
        "持股來源：手動抄錄自各投信官網／證交所 e添富（前 11 名，剔除台積電後取前 10）。資料日期見「明細」D 欄。",
        "產業別、公司簡稱：證交所與櫃買中心 OpenAPI 公司基本資料（官方產業別代碼）。",
        f"年初至今報酬：Yahoo Finance 還原股價（含除息），{YTD_BASE} 前最後一個交易日收盤 → {last} 收盤。",
        "熱門持股的「被幾檔ETF持有」「平均權重」與產業彙總皆為公式，修改「明細」後會自動更新。",
        "我們的持股：取自 esun_targets 最新一份目標組合。",
    ]:
        ds.append([line])
        ds.cell(ds.max_row, 1).font = F
    ds.column_dimensions["A"].width = 110

    path = f"esun_etf_report_{today}.xlsx"
    wb.save(path)
    return path


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    df = load_holdings()
    if df.empty:
        print(f"沒有持股資料。先填 {ACTIVE_FILE}（主動式）或 {TECH_FILE}（被動科技，用 template 產生）"); return
    print(f"讀到 {df['etf_code'].nunique()} 檔 ETF、{len(df)} 筆持股（已剔除台積電），不重複股票 {df['ticker'].nunique()} 檔")
    changes, base = snapshot_and_changes(df, today)
    info = company_info()
    print(f"  公司基本資料 {len(info)} 家")
    ytd = ytd_returns(sorted(df["ticker"].unique()), info)
    miss = sorted(set(df["ticker"]) - set(ytd))
    print(f"  年初至今報酬 {len(ytd)} 檔" + (f"，抓不到：{miss}" if miss else ""))
    path = write_excel(df, changes, base, info, ytd, our_roles(), today)
    print(f"已輸出 {path}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "template":
        make_template()
    else:
        main()