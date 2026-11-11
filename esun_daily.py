# -*- coding: utf-8 -*-
"""
esun_daily.py — 每日流程（收盤後執行；可重複執行，同一天跑多次不會重複記帳）

  1. 抓行情快照（官方資料還沒更新到今天就停下，等下一次排程）
  2. 記帳：今天這份報告的交易單用今天均價入帳，收盤價算淨值，檢查規則
  3. 對帳：esun_official 裡有官方資料包（DataPack，後台「文件繳交」下載）就以它為準；
     沒有資料包時，esun_official/official_YYYY-MM-DD.json 存在就對帳（手動填入）
  4. 補 K 線：用今天的官方行情補一根（上市 local_data、上櫃 local_data_otc）
  5. 檢查上市 K 線有沒有跟上，落後就警告
  6. 更新營收（每月 1–12 日）、EPS（財報季每天，其他時間每週一）
  7. 產生下一個交易日的報告（已成功產生過就不重做）
用法：python esun_daily.py            （正常執行）
      python esun_daily.py --force    （強制重做下一日報告）
"""
import os, sys, json, glob
from datetime import datetime, timedelta, timezone
import pandas as pd

import esun_data as E
import esun_ledger as L
import esun_report as R
import esun_strategy as S

TZ = timezone(timedelta(hours=8))
OFFICIAL_DIR = "./esun_official"
LOG_DIR = "./esun_logs"
HOLIDAYS = {"2026-09-25", "2026-09-28", "2026-10-09", "2026-10-23", "2026-10-26", "2026-12-25"}   # 10/23 颱風休市；之後用證交所休市日核對
EPS_SEASON = [((3, 20), (4, 5)), ((5, 1), (5, 31)), ((8, 1), (8, 31)), ((11, 1), (11, 30))]


def is_trading_day(d):
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in HOLIDAYS


def next_trading_day(d):
    d = d + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_trading_day(d):
    """d 之前（不含 d）最近的交易日，回傳字串"""
    d = d.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def next_submit_day(now):
    """現在要準備哪一天的報告：今天是交易日且還沒到 08:55 → 今天；否則下一個交易日。"""
    d = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if is_trading_day(d) and now < d.replace(hour=8, minute=55):
        return d
    return next_trading_day(d)


class Log:
    def __init__(self, day):
        os.makedirs(LOG_DIR, exist_ok=True)
        self.path = os.path.join(LOG_DIR, f"daily_{day}.log")
        self.lines = []

    def __call__(self, msg):
        line = f"{datetime.now(TZ):%H:%M:%S} {msg}"
        print(line)
        self.lines.append(line)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def step_snapshot(today, log):
    quotes, sources, warns = E.fetch_daily()
    dates = sorted({q["date"] for q in quotes.values()})
    if len(dates) != 1:
        raise RuntimeError(f"證交所與櫃買資料日期不一致 {dates}（有一邊還沒更新），不存檔，稍後排程再試")
    snap_date = dates[0]
    E.save_snapshot(quotes, sources, warns)
    log(f"行情快照：資料日期 {snap_date}，{len(quotes)} 檔，警告 {len(warns)} 則")
    for w in warns[:5]:
        log(f"  ! {w}")
    return snap_date, E.load_snapshot(snap_date)


def step_settle(day, snap, log):
    if os.path.exists(os.path.join(L.STATE_DIR, f"state_{day}.json")):
        log(f"記帳：{day} 已記過，略過")
        return L.load_latest()
    prev = L.load_latest(before=day)
    rpt = glob.glob(os.path.join(R.REPORT_DIR, f"D-Plan_{R.TEAM_ID}_{day}.json"))
    other = [os.path.basename(f) for f in glob.glob(os.path.join(R.REPORT_DIR, f"D-Plan_*_{day}.json"))
             if f"_{R.TEAM_ID}_" not in os.path.basename(f)]
    if other:
        log(f"  ! {day} 另有其他隊伍編號的報告 {other}，記帳只用 {R.TEAM_ID} 的")
    if prev is None and not rpt:
        log("記帳：尚未有任何持股或報告，略過")
        return None
    prev = prev or L.new_state("init")
    orders = json.load(open(rpt[0], encoding="utf-8"))["orders"] if rpt else []
    vwap = E.prices_for(snap["quotes"], "vwap")
    close = E.prices_for(snap["quotes"], "close")
    nofill = [o["ticker"] for o in orders if o["ticker"] not in vwap]
    if nofill:                                   # 停牌或沒有成交：視為未成交，不讓整個帳本卡住
        log(f"  ‼ {nofill} 當天沒有均價（停牌或無成交），帳本視為未成交，請以官方排行榜為準")
        orders = [o for o in orders if o["ticker"] in vwap]
    st = L.apply_trades(prev, orders, vwap, day)
    for t in st["holdings"]:
        if t not in close and prev.get("close", {}).get(t):
            close[t] = prev["close"][t]
            log(f"  ! {t} 當天沒有收盤價，淨值暫用前一日收盤 {close[t]}")
    st = L.mark_to_market(st, close)
    bad, note = L.check_rules(st, bought_today={o["ticker"] for o in orders if o["side"] == "BUY"})
    L.save(st)
    log(f"記帳：{len(orders)} 筆成交，淨值 {st['nav']:,.0f}，現金 {st['cash']/st['nav']:.1%}，持股 {len(st['holdings'])} 檔")
    for x in bad:
        log(f"  ✗ 規則違反：{x}")
    for x in note:
        log(f"  ! {x}")
    return st


def load_datapack(day, log):
    """esun_official 裡找 doc_type=DataPack、as_of_date=day 的資料包（檔名不限），轉成對帳格式。
    nav 用 account.nav_tradable（官方下單、現金比、權重的分母，不含應收配息配股）；
    持股只算 shares > 0（應收配股不能賣、不計入檔數）。"""
    fs = sorted(glob.glob(os.path.join(OFFICIAL_DIR, "*.json")), key=os.path.getmtime, reverse=True)
    for f in fs:
        try:
            dp = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(dp, dict) or dp.get("doc_type") != "DataPack":
            continue
        if dp.get("team_id") not in (None, R.TEAM_ID):
            log(f"  ! 資料包 {os.path.basename(f)} 是 {dp.get('team_id')} 的，略過"); continue
        if "prev_settlement" in (dp.get("missing") or []) or dp.get("account") is None:
            if dp.get("trade_date", "") > day:
                log(f"  ‼ 資料包 {os.path.basename(f)}：前一交易日結算還沒產生（account 為 null），結算後請重新下載")
            continue
        if dp.get("as_of_date") != day:
            continue
        acc = dp["account"]
        hold = {str(h["code"]): int(h["shares"]) for h in (dp.get("holdings") or []) if int(h.get("shares") or 0) > 0}
        recv = {str(h["code"]): int(h["receivable_shares"]) for h in (dp.get("holdings") or [])
                if int(h.get("receivable_shares") or 0) > 0}
        po = dp.get("prev_day_orders") or {}
        if po.get("outcome") == "voided":
            log(f"  ‼ 官方：前一交易日委託整批作廢，原因 {po.get('void_reasons')}")
        if po.get("excluded_codes"):
            log(f"  ! 官方：前一交易日這些股票沒成交 {po['excluded_codes']}")
        comp = dp.get("compliance") or {}
        if comp.get("violations") or comp.get("notices"):
            log(f"  ‼ 官方合規：違規 {comp.get('violations', 0)} 天、提示 {comp.get('notices', 0)} 列；"
                + "；".join(f"{x.get('date')} {x.get('check_code')} {x.get('status')}" for x in (comp.get("incidents") or [])[-3:]))
        if "prev_close" in (dp.get("missing") or []):
            log("  ! 資料包缺 prev_close（官方收盤價還沒匯入），nav 照用")
        if recv:
            log(f"  ! 應收配股（不能賣、不計檔數）：{recv}")
        return {"nav": float(acc["nav_tradable"]), "cash": float(acc["cash"]), "holdings": hold,
                "confirm": True, "_file": os.path.basename(f)}
    return None


def step_reconcile(day, st, log):
    p = os.path.join(OFFICIAL_DIR, f"official_{day}.json")
    dp = load_datapack(day, log)
    if dp is not None:
        log(f"對帳：使用官方資料包 {dp['_file']}（{day} 結算：可交易淨值 {dp['nav']:,.0f}、持股 {len(dp['holdings'])} 檔）")
        if st is None:
            st = L.new_state(day)
        else:
            st, diffs = L.reconcile(st, dp)
            if diffs:
                log("  估算與官方差異：" + "；".join(diffs))
        st["nav"], st["cash"], st["holdings"] = dp["nav"], dp["cash"], dp["holdings"]
        st["source"] = "official"
        L.save(st)
        return st
    if st is None:                               # 帳本還沒開始：有官方數字就直接用它建立帳本
        if os.path.exists(p):
            off = json.load(open(p, encoding="utf-8"))
            st = L.new_state(day)
            st["nav"], st["cash"] = float(off["nav"]), float(off["cash"])
            st["holdings"] = {str(k): int(v) for k, v in off["holdings"].items()}
            st["source"] = "official"
            L.save(st)
            log(f"對帳：帳本原本不存在，已用官方數字建立（淨值 {st['nav']:,.0f}、持股 {len(st['holdings'])} 檔）")
        return st
    if not os.path.exists(p):
        os.makedirs(OFFICIAL_DIR, exist_ok=True)
        tpl = p.replace(".json", "_範本.json")
        with open(tpl, "w", encoding="utf-8") as f:
            json.dump({"說明": "從排行榜抄入官方數字後，另存為 official_日期.json（刪掉這行）",
                       "nav": round(st["nav"]), "cash": round(st["cash"]), "holdings": st["holdings"]},
                      f, ensure_ascii=False, indent=1)
        log(f"對帳：還沒有官方數字，暫用自己的估算（範本已存 {tpl}）")
        if day >= "2026-10-27":
            log("  ‼ 正式比賽期間：交易單必須用官方資料包的淨值與持股推導，請到後台「文件繳交」下載資料包，"
                f"放進 {OFFICIAL_DIR} 後再執行一次（python esun_daily.py --force）")
        return st
    off = json.load(open(p, encoding="utf-8"))
    nav_gap = abs(off["nav"] - st["nav"]) / st["nav"]
    oh = {str(k): int(v) for k, v in off["holdings"].items()}
    mism = [t for t in set(oh) | set(st["holdings"]) if oh.get(t, 0) != st["holdings"].get(t, 0)]
    if (nav_gap > 0.01 or len(mism) > 3) and not off.get("confirm"):
        log(f"  ‼ 對帳：官方數字與估算差太多（淨值差 {nav_gap:.2%}、{len(mism)} 檔持股不同），"
            f"可能抄錯日期或數字，本次不套用。確認無誤請在 {p} 加上 \"confirm\": true")
        return st
    st, diffs = L.reconcile(st, off)
    st["nav"], st["cash"], st["holdings"] = float(off["nav"]), float(off["cash"]), oh   # 通過防呆就一律以官方為準
    st["source"] = "official"
    L.save(st)
    log("對帳：已採用官方數字" + ("（與估算幾乎一致）" if not diffs else "｜差異：" + "；".join(diffs)))
    return st


def _append_bar(p, day, q):
    """在 K 線檔補一根 day 的 K 線；檔案原本的欄位大小寫、型別、時區照舊。已有這天就不動。回傳是否有寫入"""
    ts = pd.Timestamp(day)
    if os.path.exists(p):
        df = pd.read_parquet(p)
        idx = pd.to_datetime(df.index)
        tz = idx.tz
        naive = idx.tz_localize(None) if tz is not None else idx
        if ts in naive:
            old = df[naive == ts]
            oc = {k.lower(): k for k in df.columns}
            o_close = pd.to_numeric(old[oc.get("close", "Close")], errors="coerce").iloc[-1]
            o_vol = pd.to_numeric(old[oc.get("volume", "Volume")], errors="coerce").iloc[-1]
            same_close = pd.notna(o_close) and abs(o_close / q["close"] - 1) < 1e-3
            same_vol = (not q.get("volume")) or (pd.notna(o_vol) and o_vol > 0 and abs(o_vol / q["volume"] - 1) < 0.01)
            if same_close and same_vol:
                return False
            df = df[naive != ts]          # 已有這天但跟官方行情不同（例如 yfinance 盤中抓的不完整 K 線）→ 改用官方的
    else:
        df, tz = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]), None
    cols = {c.lower(): c for c in df.columns}
    val = {"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"]}
    row = pd.DataFrame({cols.get(k, k.capitalize()): [v] for k, v in val.items()},
                       index=pd.DatetimeIndex([ts.tz_localize(tz) if tz is not None else ts], name=df.index.name))
    for c in df.columns:
        if c not in row.columns:
            row[c] = pd.NA
    if len(df):
        row = row[df.columns].astype(df.dtypes.to_dict(), errors="ignore")
    out = pd.concat([df, row])
    out.index = pd.to_datetime(out.index)
    out[~out.index.duplicated(keep="first")].sort_index().to_parquet(p)
    return True


def step_otc_kline(day, snap, log):
    """用今天的官方行情補一根 K 線：上櫃 → local_data_otc，上市 → local_data（不再依賴 SMC 的資料更新）"""
    n = {"tpex": 0, "twse": 0}
    for sid, q in snap["quotes"].items():
        mk = q.get("market")
        if mk not in n or not all(q.get(k) for k in ("open", "high", "low", "close")):
            continue
        p = (os.path.join(S.OTC_DIR, f"{sid}.TWO.parquet") if mk == "tpex"
             else os.path.join(S.LOCAL_DATA, f"{sid}.TW.parquet"))
        if mk == "twse" and not os.path.exists(p):
            continue                                   # 上市沒有歷史檔的不憑一根K線新建
        try:
            n[mk] += _append_bar(p, day, q)
        except Exception as e:
            log(f"  ! {sid} K 線補寫失敗：{type(e).__name__} {str(e)[:80]}")
    msg = ""
    try:                                               # 0050 不在 150 檔行情裡，另外抓一根（回測對照組用）
        p50 = os.path.join(S.LOCAL_DATA, "0050.TW.parquet")
        if os.path.exists(p50):
            q50 = E.fetch_finmind_twse({"0050"}, day).get("0050")
            if q50 and all(q50.get(k) for k in ("open", "high", "low", "close")):
                msg = "、0050" if _append_bar(p50, day, q50) else ""
            else:
                msg = "（0050 FinMind 還沒有這天）"
    except Exception as e:
        msg = f"（0050 失敗：{type(e).__name__}）"
    log(f"K 線：補上 {day} 上市 {n['twse']} 檔、上櫃 {n['tpex']} 檔" + msg)
    fixed = 0                                          # 前 5 個交易日也用存下來的官方快照校正一次
    for f in sorted(glob.glob(os.path.join(E.DATA_DIR, "daily_????-??-??.json")))[-6:]:   # （SMC 的 yfinance 更新會蓋掉，盤中抓的量不完整）
        d0 = os.path.basename(f)[6:16]
        if d0 >= day:
            continue
        try:
            q0 = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if len({q["date"] for q in q0["quotes"].values()}) != 1:
            continue                                   # 舊版快照上市／上櫃日期不一致，不拿來校正
        for sid, q in q0["quotes"].items():
            mk = q.get("market")
            if mk not in n or not all(q.get(k) for k in ("open", "high", "low", "close")):
                continue
            p = (os.path.join(S.OTC_DIR, f"{sid}.TWO.parquet") if mk == "tpex"
                 else os.path.join(S.LOCAL_DATA, f"{sid}.TW.parquet"))
            if not os.path.exists(p):
                continue
            try:
                fixed += _append_bar(p, d0, q)
            except Exception as e:
                log(f"  ! {sid} {d0} K 線校正失敗：{type(e).__name__} {str(e)[:80]}")
    if fixed:
        log(f"K 線：前幾個交易日有 {fixed} 根跟官方行情不同，已改成官方的")


def step_freshness(day, log):
    sec = json.load(open(S.SECTOR_FILE, encoding="utf-8"))
    stale = []
    for sid, v in sec.items():
        if v["code"] not in S.AI_CODES or v["name"] in S.EXCLUDE_IND:
            continue
        p = os.path.join(S.LOCAL_DATA, f"{sid}.TW.parquet")
        if not os.path.exists(p):
            continue
        idx = pd.to_datetime(pd.read_parquet(p, columns=[]).index)
        if len(idx) == 0 or idx.max().strftime("%Y-%m-%d") < day:
            stale.append(sid)
    if stale:
        log(f"  ‼ 上市 K 線還是落後：{len(stale)} 檔，例如 {stale[:5]}（行情快照裡沒有這些股票？）")
        log("    中間漏掉的日子可以跑 python _research\\esun_fix_kline_gaps.py 補齊")
    else:
        log("上市 K 線：已跟上")
    return stale


def step_gap_check(prev_state, snap_date, log):
    """檢查上一次記帳到這次之間，有沒有交易日漏記（當天有報告、卻沒抓到當天行情）。"""
    if prev_state is None:                      # 帳本還沒開始：最早一份報告之後的都算
        rpts = sorted(os.path.basename(f)[-15:-5] for f in glob.glob(os.path.join(R.REPORT_DIR, "D-Plan_*_????-??-??.json")))
        rpts = [r for r in rpts if r < snap_date]
        if not rpts:
            return []
        d = datetime.strptime(rpts[0], "%Y-%m-%d")
    else:
        d = datetime.strptime(prev_state["date"], "%Y-%m-%d") + timedelta(days=1)
    end = datetime.strptime(snap_date, "%Y-%m-%d")
    missed = []
    while d < end:
        ds = d.strftime("%Y-%m-%d")
        if is_trading_day(d) and glob.glob(os.path.join(R.REPORT_DIR, f"D-Plan_{R.TEAM_ID}_{ds}.json")):
            missed.append(ds)
        d += timedelta(days=1)
    if missed:
        log(f"  ‼ 有 {len(missed)} 個交易日的交易沒有入帳：{missed}（當天沒抓到行情）")
        log(f"    帳本已經不準。請到排行榜抄 {snap_date} 的官方持股、現金、淨值，"
            f"存成 esun_official/official_{snap_date}.json，並加上 \"confirm\": true，再執行一次")
    return missed


def step_tdcc(log):
    """集保股權分散表：每次執行都檢查一次（一個請求），有新的一週就存下"""
    import esun_fetch_tdcc as T
    T.fetch(log)


def step_inst(day, log):
    """三大法人：要抓到最新交易日（day = 行情快照日期）那天的資料才算完成；還沒有就下次再試
    （舊版用「執行當天」當戳記：清晨抓完就標成今天已更新，晚上就不再抓當天收盤後的資料，過濾會晚一天）"""
    stamp_path = os.path.join(LOG_DIR, "last_fundamentals.json")
    stamp = json.load(open(stamp_path, encoding="utf-8")) if os.path.exists(stamp_path) else {}
    if stamp.get("inst_data") == day:
        log(f"三大法人：已更新到 {day}，略過"); return
    import esun_fetch_inst as I
    ok = I.main()
    s = I.load("2330")
    last = s.index.max().strftime("%Y-%m-%d") if len(s) else "無資料"
    if ok and last >= day:
        stamp["inst_data"] = day
        with open(stamp_path, "w", encoding="utf-8") as f:
            json.dump(stamp, f)
        log(f"三大法人：已更新到 {day}")
    elif not ok:
        log("  ‼ 三大法人：未全部更新（限流或斷線），報告的法人過濾可能用到舊資料，下次執行會接續")
    else:
        log(f"  ‼ 三大法人：FinMind 只到 {last}，還沒有 {day}，報告的法人過濾少了最新一天，下次執行會再試")


def near_eps_deadline(d):
    """財報公告期限前 7 天到後 2 天（一般公司 3/31、5/15、8/14、11/14）：晚上也補抓還沒公布的"""
    for m, dd in ((3, 31), (5, 15), (8, 14), (11, 14)):
        dl = pd.Timestamp(d.year, m, dd)
        if dl - pd.Timedelta(days=7) <= pd.Timestamp(d).normalize() <= dl + pd.Timedelta(days=2):
            return True
    return False


FHC = {"2880", "2881", "2882", "2883", "2884", "2885", "2886", "2887", "2890", "2891", "2892", "5880"}   # 金控（季報期限較晚）
EPS_DEADLINE = {3: (5, 15), 6: (8, 14), 9: (11, 14), 12: (3, 31)}        # 一般上市櫃公司
EPS_DEADLINE_FHC = {3: (5, 30), 6: (8, 31), 9: (11, 29), 12: (3, 31)}    # 金控


def eps_deadline(q_exp, fhc=False):
    """季報法定公告期限；遇例假日順延到下一個上班日（例：2026-11-14 週六 → 11-16 週一）"""
    md = (EPS_DEADLINE_FHC if fhc else EPS_DEADLINE).get(q_exp.month)
    if md is None:
        return None
    d = pd.Timestamp(q_exp.year + (q_exp.month == 12), *md)
    while not is_trading_day(d):
        d += pd.Timedelta(days=1)
    return d


def eps_check_start(q_exp, fhc=False):
    """季報覆蓋率檢查從期限（已順延）後 2 天開始，給 FinMind 收錄的時間"""
    d = eps_deadline(q_exp, fhc)
    return None if d is None else d + pd.Timedelta(days=2)


def step_integrity(day, today, log):
    """每天產生報告前，直接檢查選股要用的資料本身（不管程式邏輯，只看資料對不對）。
    有問題寫 ‼ 資料檢查，報告會標成「資料不完整、先不要上傳」。"""
    probs, notes = [], []
    snap = E.load_snapshot(day)
    quotes = snap["quotes"]
    if len(quotes) != 150 or len({q["date"] for q in quotes.values()}) != 1:
        probs.append(f"行情快照 {len(quotes)} 檔、日期 {sorted({q['date'] for q in quotes.values()})}")
    pool = set(S.pool_sids())
    t_day = pd.Timestamp(day)
    # 1. K 線：每檔都要有今天這根，而且收盤、成交量跟官方快照一致；最近 60 天不能有非交易日的 K 線
    stale, diff, fake = [], [], []
    for sid in sorted(pool):
        px = S._read_px(sid)
        q = quotes.get(sid)
        if px is None or q is None:
            stale.append(sid); continue
        if px.index.max() != t_day:
            stale.append(sid); continue
        r = px.loc[t_day]
        if q.get("close") and abs(float(r["Close"]) / q["close"] - 1) > 0.002:
            diff.append(f"{sid} 收盤 {float(r['Close']):g}≠{q['close']:g}")
        elif q.get("volume") and abs(float(r["Volume"]) / q["volume"] - 1) > 0.02:
            diff.append(f"{sid} 量 {float(r['Volume']):,.0f}≠{q['volume']:,.0f}")
        recent = px.index[px.index > t_day - pd.Timedelta(days=90)]
        bad = [d.strftime("%m/%d") for d in recent if not is_trading_day(d)]
        if bad:
            fake.append(f"{sid}:{','.join(bad[:3])}")
    if stale:
        probs.append(f"K 線沒有 {day} 的有 {len(stale)} 檔，例如 {stale[:5]}")
    if diff:
        probs.append(f"K 線跟官方行情不一致 {len(diff)} 檔，例如 {diff[:3]}")
    if fake:
        probs.append(f"K 線出現非交易日（休市日或週末）的資料 {len(fake)} 檔，例如 {fake[:3]}（成交量 0 的會被略過，其他要查）")
    # 2. 三大法人：選股池要有今天的資料
    import esun_fetch_inst as I
    lag = [s for s in sorted(pool) if len(I.load(s)) == 0 or I.load(s).index.max() < t_day]
    if len(lag) > 3:
        probs.append(f"三大法人沒有 {day} 的有 {len(lag)} 檔，例如 {lag[:5]}")
    elif lag:
        notes.append(f"三大法人沒有 {day} 的 {lag}（少數，可能當天無資料）")
    # 3. 月營收：每月 12 日以後，選股池九成以上要有上個月的營收
    if today.day >= 12:
        exp = (pd.Timestamp(today).normalize().replace(day=1) - pd.DateOffset(months=1))
        have = 0
        for s in pool:
            p = os.path.join(S.REV_DIR, f"{s}.parquet")
            try:
                have += os.path.exists(p) and pd.read_parquet(p).index.max() >= exp
            except Exception:
                pass
        if have < 0.9 * len(pool):
            probs.append(f"月營收：選股池只有 {have}/{len(pool)} 檔有 {exp:%Y-%m} 的資料")
    # 4. 季報：法定公告期限（遇假日順延）2 天後，九成以上要有最新一季；金控期限較晚，分開檢查
    import esun_fetch_eps as FE
    q_exp = FE.expected_quarter(today)
    t_now = pd.Timestamp(today).normalize()
    for name, grp, fhc in (("一般公司", pool - FHC, False), ("金控", pool & FHC, True)):
        start = eps_check_start(q_exp, fhc)
        if start is None or t_now < start or not grp:
            continue
        have = sum(1 for s in grp if FE.has_quarter(s, q_exp))
        if have < 0.9 * len(grp):
            probs.append(f"季報（{name}）：只有 {have}/{len(grp)} 檔有 {q_exp:%Y-%m} 季的 EPS")
    for p in probs:
        log(f"  ‼ 資料檢查：{p}")
    for n in notes:
        log(f"  資料檢查（提醒）：{n}")
    if not probs:
        log(f"資料檢查：K 線、三大法人、營收、季報都正常（{len(pool)} 檔都到 {day}，跟官方行情一致）")
    return probs


def in_season(d):
    return any(pd.Timestamp(d.year, *a) <= pd.Timestamp(d) <= pd.Timestamp(d.year, *b) for a, b in EPS_SEASON)


REV_LAST_DAY = 20


def step_fundamentals(today, log):
    """營收：每月 1–20 日（金控月營收在 FinMind 常晚於 10 日才出現，延長到 20 日才抓得到）；EPS：財報季每天或週一。
    抓完才記成「今天已更新」；被限流中斷時，同一天重跑會從中斷處接續。"""
    stamp_path = os.path.join(LOG_DIR, "last_fundamentals.json")
    stamp = json.load(open(stamp_path, encoding="utf-8")) if os.path.exists(stamp_path) else {}
    day = today.strftime("%Y-%m-%d")

    def save_stamp():
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(stamp_path, "w", encoding="utf-8") as f:
            json.dump(stamp, f)

    pm = today.hour >= 17                      # 晚上那幾次：只補抓「還沒有最新月份／最新一季」的，當天傍晚公布的也能用上
    if today.day <= REV_LAST_DAY:
        import esun_fetch_revenue as FR
        if not pm:
            if stamp.get("revenue") == day:
                log("營收：今天已完整更新，略過")
            else:
                log(f"營收：每月 1–{REV_LAST_DAY} 日更新")
                if FR.main():
                    stamp["revenue"] = day
                    log("營收：150 檔已完整更新")
                else:
                    log("  ! 營收：未全部更新（可能被限流或 FinMind 錯誤），下次執行會接續")
                save_stamp()
        elif 8 <= today.day <= 12:                  # 公布高峰（法定期限 10 日前後）晚上再補抓一次，其他天省下 FinMind 額度
            n = FR.main(since=today - timedelta(hours=2))
            if n is not False:
                log(f"營收：晚上補抓還沒有上個月營收的 {n} 檔，完成")
            else:
                log("  ! 營收：晚上補抓未完成（可能被限流或 FinMind 錯誤），下次執行會接續")
    if (in_season(today) or today.weekday() == 0) and not (pm and not in_season(today)):   # 財報季（含 11 月整月）早晚各抓一次
        import esun_fetch_eps as FE
        key = "eps_pm" if pm else "eps"
        if stamp.get(key) == day:
            log("EPS：今天已完整更新，略過" if not pm else "EPS：今晚已補抓過，略過")
        else:
            if stamp.get(key + "_started") != day:          # 這個時段第一次：清掉清單，從頭抓
                if os.path.exists(FE.DONE_FILE):
                    os.remove(FE.DONE_FILE)
                stamp[key + "_started"] = day
                save_stamp()
            log("EPS：財報季或週一更新" if not pm else "EPS：財報季晚上補抓還沒有最新一季的")
            if FE.main(only_missing=FE.expected_quarter(today) if pm else None):
                stamp[key] = day
                log("EPS：150 檔已完整更新" if not pm else "EPS：晚上補抓完成")
            else:
                log("  ! EPS：未全部更新（可能被限流或 FinMind 錯誤），下次執行會接續")
            save_stamp()


def window_open(trade_day):
    """提交窗口開啟時間：交易日前一天 19:30（台灣時間）"""
    d = datetime.strptime(trade_day, "%Y-%m-%d") - timedelta(days=1)
    return d.replace(hour=19, minute=30, tzinfo=TZ)


def _built_on_old_nav(rpt):
    """帳本已改用官方資料包，但現有報告的淨值跟它不同 → 報告是用估算值做的，交易單股數會對不上官方推導"""
    try:
        st = L.load_latest()
        if not st or st.get("source") != "official":
            return False
        o1 = json.load(open(rpt, encoding="utf-8"))["observations"][0]["values"]
        return abs(float(o1.get("nav", st["nav"])) - float(st["nav"])) > 1
    except Exception:
        return False


DATA_KEYS = ("三大法人", "K 線", "最新行情", "營收", "EPS", "資料檢查")


def data_problems(log):
    """這次執行中，跟「選股用的資料」有關的警告（資料不完整時，報告不能算定稿）"""
    return [l[9:].strip() for l in log.lines
            if any(m in l for m in ("‼", "✗", " ! ")) and any(k in l for k in DATA_KEYS)]


def step_report(next_day, force, log, problems=()):
    chk = os.path.join(R.REPORT_DIR, f"D-Plan_{R.TEAM_ID}_{next_day}_check.json")
    rpt = chk.replace("_check.json", ".json")
    if os.path.exists(chk) and os.path.exists(rpt) and not force:
        c = json.load(open(chk, encoding="utf-8"))
        done_at = datetime.fromisoformat(json.load(open(rpt, encoding="utf-8"))["agent_metadata"]["run_completed_at"])
        if done_at < window_open(next_day):
            log(f"報告：{next_day} 現有的是窗口開啟前（{done_at:%m/%d %H:%M}）產生的草稿，進入窗口後重新產生")
        elif _built_on_old_nav(rpt):
            log(f"報告：{next_day} 現有的是用自己估算的淨值產生的，已有官方資料包 → 重新產生")
        elif c.get("incomplete"):
            log(f"報告：{next_day} 現有的是資料不完整時產生的，重新產生")
        elif c.get("kind") == "正常" and not c.get("reject"):
            log(f"報告：{next_day} 已在提交窗口內成功產生，不重做（要重做加 --force）")
            return False
    plan, rej = R.run(next_day)
    log(f"報告：{next_day} 產生完成，拒收 {len(rej)} 項" if plan else f"✗ 報告：{next_day} 未產生（{rej}）")
    if plan and os.path.exists(chk):
        c = json.load(open(chk, encoding="utf-8"))
        if problems:
            c["incomplete"], c["incomplete_reasons"] = True, list(problems)[:6]
        else:
            c.pop("incomplete", None); c.pop("incomplete_reasons", None)
        with open(chk, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        if problems:
            log(f"‼ 報告：{next_day} 產生時資料不完整（{problems[0][:60]}），先不要上傳；下次排程會重新產生。"
                f"如果到 08:30 還是不完整，再上傳這份（用的是能取得的最新資料）")
    return True


def notify(log, next_day, generated):
    """報告新產生 → 摘要；有 ✗ 或 ‼ → 警告；其他不傳。"""
    import esun_notify as N
    problems = [l[9:] for l in log.lines if "✗" in l or "‼" in l]
    chk = os.path.join(R.REPORT_DIR, f"D-Plan_{R.TEAM_ID}_{next_day}_check.json")
    msg = []
    if generated and os.path.exists(chk):
        c = json.load(open(chk, encoding="utf-8"))
        sm = c.get("summary") or {}
        ok = c.get("kind") == "正常" and not c.get("reject")
        head = f"【玉山】{next_day} 報告：{c.get('kind')}" + ("，可以上傳" if ok and not problems else "")
        msg.append(head)
        if sm.get("holdings_after") is not None:
            asm = sm.get("active_share_min")
            msg.append(f"持股 {sm['holdings_after']} 檔｜預估現金 {sm['est_cash_pct']:.1%}"
                       + (f"｜主動投資比率最低 {asm:.1%}" if asm is not None else ""))
    if problems:
        if not msg:
            msg.append(f"‼【玉山】{next_day} 每日流程有問題")
        msg.append("需要處理：")
        msg += problems[:8]
    if msg:
        N.send("\n".join(msg))


def main(today=None):
    force = "--force" in sys.argv
    today = today or datetime.now(TZ).replace(tzinfo=None)
    log = Log(today.strftime("%Y-%m-%d"))
    log("=" * 40 + " 每日流程開始")
    try:
        snap_date, snap = step_snapshot(today, log)
    except Exception as e:
        log(f"✗ 行情抓取失敗：{type(e).__name__} {str(e)[:100]}（稍後排程會再試）")
        if today.hour < 9:                               # 清晨最後一次機會也失敗 → 通知
            import esun_notify as N
            N.send(f"‼【玉山】清晨執行失敗，今天的報告可能沒有更新：{str(e)[:150]}")
        return
    # 先把抓到的那一天記帳（就算今天的資料還沒出來，前一個交易日也要記上）
    prev_state = L.load_latest(before=snap_date)
    try:
        st = step_settle(snap_date, snap, log)
    except Exception as e:
        log(f"✗ 記帳失敗：{type(e).__name__} {str(e)[:100]}")
        st = L.load_latest()
    step_gap_check(prev_state, snap_date, log)
    # 交易日收盤後（14:00 起）才等今天的行情；清晨／盤前執行時，最新的本來就是前一個交易日 → 照常產生今天的報告
    today_s = today.strftime("%Y-%m-%d")
    if is_trading_day(today) and today.hour >= 14 and snap_date < today_s:
        log(f"官方資料還停在 {snap_date}，今天的還沒出來，稍後排程再試")
        return
    expect = today_s if (is_trading_day(today) and today.hour >= 14) else prev_trading_day(today)
    if snap_date < expect:
        log(f"  ‼ 最新行情是 {snap_date}，應該要有 {expect}，報告會用比較舊的資料")
    for name, fn in [("對帳", lambda: step_reconcile(snap_date, st, log)),
                     ("K 線", lambda: step_otc_kline(snap_date, snap, log)),
                     ("上市 K 線檢查", lambda: step_freshness(snap_date, log)),
                     ("營收／EPS", lambda: step_fundamentals(today, log)),
                     ("集保股權分散表", lambda: step_tdcc(log)),
                     ("三大法人", lambda: step_inst(snap_date, log)),
                     ("資料檢查", lambda: step_integrity(snap_date, today, log))]:
        try:
            fn()
        except Exception as e:
            log(f"✗ {name}失敗：{type(e).__name__} {str(e)[:100]}")
    nxt = next_submit_day(today).strftime("%Y-%m-%d")
    generated = False
    try:
        generated = step_report(nxt, force, log, data_problems(log))
    except Exception as e:
        log(f"✗ 報告失敗：{type(e).__name__} {str(e)[:100]}")
    log("每日流程結束")
    notify(log, nxt, generated)


if __name__ == "__main__":
    main()