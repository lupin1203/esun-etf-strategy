# -*- coding: utf-8 -*-
"""
esun_active_share.py — 主動投資比率（Active Share）檢查

規則（辦法六(十三)，2026/10/05 公告放寬 + 主辦方 Slack 回覆）：
  · 對每一檔主動型 ETF：AS = 1/2 × Σ |我們前 15 大權重 − ETF 前 15 大權重|
    （兩邊前 15 大的聯集；不在某一邊前 15 大的，權重當 0）
  · 權重直接用基金公告的原始比例，不重新正規化到 100%
  · 低於 15% 才認定違規（原本前 10 大、20%）→ 我們低於 25% 就警告（留 10 個百分點緩衝）

資料來源（半手動，遵守 e添富／投信官網條款，不用爬蟲）：
  · 每週跑一次 esun_etf_fetch.py，把各 ETF 前 16 大寫進 esun_etf_top10.csv（檔名沿用）
  · 程式每天用最新一份表格計算
用法：
  python esun_active_share.py template   產生空白表格（已存在就不覆蓋）
  python esun_active_share.py            用最新目標組合檢查一次
"""
import os, sys, csv, json, glob
from datetime import datetime

TOP10_FILE = "esun_etf_top10.csv"
TOP_N = 15                       # 2026/10/05 起比較前 15 大（原本 10）
AS_MIN, AS_WARN, STALE_DAYS = 0.15, 0.25, 10

ETFS = [  # 代號, 名稱, 類型
    ("00980A", "主動野村臺灣優選", "台股"), ("00982A", "主動群益台灣強棒", "台股"),
    ("00981A", "主動統一台股成長", "台股"), ("00983A", "主動中信ARK創新", "美國"),
    ("00984A", "主動安聯台灣高息", "台股"), ("00985A", "主動野村台灣50", "台股"),
    ("00986A", "主動台新龍頭成長", "全球"), ("00989A", "主動摩根美國科技", "美國"),
    ("00988A", "主動統一全球創新", "全球"), ("00991A", "主動復華未來50", "台股"),
    ("00990A", "主動元大AI新經濟", "全球"), ("00987A", "主動台新優勢成長", "台股"),
    ("00992A", "主動群益科技創新", "台股"), ("00994A", "主動第一金台股優", "台股"),
    ("00995A", "主動中信台灣卓越", "台股"), ("00993A", "主動安聯台灣", "台股"),
    ("00996A", "主動兆豐台灣豐收", "台股"), ("00400A", "主動國泰動能高息", "台股"),
    ("00401A", "主動摩根台灣鑫收", "台股"), ("00997A", "主動群益美國增長", "美國"),
    ("00999A", "主動野村臺灣高息", "台股"), ("00403A", "主動統一升級50", "台股"),
    ("00402A", "主動安聯美國科技", "美國"), ("00404A", "主動聯博動能50", "台股"),
    ("00405A", "主動富邦台灣龍耀", "台股"), ("00406A", "主動中信台灣收益", "台股"),
    ("00407A", "主動凱基台灣", "台股"), ("00408A", "主動第一金優股息", "台股"),
    ("00410A", "主動永豐科技趨勢", "全球"), ("00409A", "主動復華全球50", "全球"),
]
FIELDS = ["etf_code", "etf_name", "type", "data_date", "rank", "ticker", "weight_pct"]


def make_template():
    if os.path.exists(TOP10_FILE):
        print(f"{TOP10_FILE} 已存在，不覆蓋"); return
    with open(TOP10_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for code, name, typ in ETFS:
            if typ == "美國":
                continue
            for r in range(1, TOP_N + 2):
                w.writerow([code, name, typ, "", r, "", ""])
    print(f"已產生 {TOP10_FILE}：台股型與全球型各 16 列（第 16 名給持股報表剔除台積電後補位用），填 data_date（資料日期 YYYY-MM-DD）、ticker（4 碼代號）、"
          f"weight_pct（例如 8.52 代表 8.52%）")
    print("  全球型：前 15 大若沒有台股，填一列 ticker=NONE 即可；美國型免填")


def load_top10():
    """回傳 {etf: {"date": str, "type": str, "w": {ticker: 權重(小數)}}}"""
    out = {}
    if not os.path.exists(TOP10_FILE):
        return out
    with open(TOP10_FILE, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code, t, wp = row["etf_code"].strip(), row["ticker"].strip().upper(), row["weight_pct"].strip()
            if not t:
                continue
            e = out.setdefault(code, {"date": row["data_date"].strip(), "type": row["type"], "w": {}})
            if row["data_date"].strip():
                e["date"] = row["data_date"].strip()
            if t != "NONE" and wp:
                e["w"][t] = float(wp) / 100
    return out


def top10(weights, n=None):
    """前 N 大（預設 TOP_N＝15；函式名稱沿用以免其他程式要跟著改）"""
    return dict(sorted(weights.items(), key=lambda kv: -kv[1])[:n or TOP_N])


def active_share(ours, etf):
    a, b = top10(ours), top10(etf)
    return 0.5 * sum(abs(a.get(t, 0) - b.get(t, 0)) for t in set(a) | set(b))


def check(our_weights, today=None):
    """回傳 (結果列表, 警告列表)。結果：(代號, 名稱, AS, 資料日期或說明)"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    data = load_top10()
    ours10 = top10(our_weights)
    floor = 0.5 * sum(ours10.values())          # 完全不重疊時的最低值
    res, warns = [], []
    for code, name, typ in ETFS:
        d = data.get(code)
        if typ == "美國" or (d and not d["w"]):
            res.append((code, name, None, "無台股持股（不重疊，AS ≥ %.0f%%）" % (floor * 100)))
            continue
        if not d:
            res.append((code, name, None, "缺資料")); continue
        s = active_share(our_weights, d["w"])
        res.append((code, name, s, d["date"]))
        if d["date"] and (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(d["date"], "%Y-%m-%d")).days > STALE_DAYS:
            warns.append(f"{code} {name}：資料日期 {d['date']} 超過 {STALE_DAYS} 天，請更新")
        if s < AS_MIN:
            warns.append(f"‼ {code} {name}：主動投資比率 {s:.1%} 低於 {AS_MIN:.0%}（違規）")
        elif s < AS_WARN:
            warns.append(f"! {code} {name}：主動投資比率 {s:.1%}，低於 {AS_WARN:.0%} 緩衝")
    return res, warns


def post_trade_weights(state, orders, price):
    """交易後的預估權重（用前日收盤價）"""
    hold = {str(k): int(v) for k, v in state["holdings"].items()}
    for o in orders:
        hold[o["ticker"]] = hold.get(o["ticker"], 0) + (o["shares"] if o["side"] == "BUY" else -o["shares"])
    val = {t: q * price[t] for t, q in hold.items() if q > 0 and price.get(t)}
    nav = state["nav"]
    return {t: v / nav for t, v in val.items()}


def report(res, warns, log=print):
    known = [r for r in res if r[2] is not None]
    if known:
        m = min(known, key=lambda r: r[2])
        log(f"主動投資比率：已檢查 {len(known)} 檔，最低 {m[2]:.1%}（{m[0]} {m[1]}）")
    else:
        log("主動投資比率：表格還沒有資料")
    missing = [r[0] for r in res if r[3] == "缺資料"]
    if missing:
        log(f"  ! 還有 {len(missing)} 檔沒有前 {TOP_N} 大資料，無法檢查：{'、'.join(missing[:6])}{'…' if len(missing) > 6 else ''}")
    for w in warns:
        log("  " + w)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "template":
        make_template(); sys.exit()
    fs = sorted(glob.glob("./esun_targets/targets_*.json"))
    if not fs:
        print("沒有目標組合，先跑 esun_daily.py 或 esun_report.py"); sys.exit()
    tg = json.load(open(fs[-1], encoding="utf-8"))
    res, warns = check(tg["weights"])
    print(f"用 {os.path.basename(fs[-1])} 的目標權重檢查")
    for code, name, s, note in res:
        print(f"  {code} {name:<12}{'' if s is None else f'{s:6.1%}'}  {note}")
    report(res, warns)