# -*- coding: utf-8 -*-
"""
esun_rehearsal.py — 三組策略同時紙上彩排（不碰每日流程，也不會被打包）

  5：150 檔｜9.5%+1%｜new_only      （回測第一名）
  1：三產業｜9.5%+1%｜new_only      （目前正式程式的設定）
  3：三產業｜均分 4.5%｜new_only    （回測第三，波動較低）

  · 每組各自一本帳，第一次執行時各有 10 億現金，跟比賽第一天一樣從零建倉
  · 行情用每日流程存的 esun_data/daily_日期.json（同一份均價、收盤價），所以要排在每日流程之後
  · 每次執行：
      1. 記帳：最新快照那天，如果有之前下好的交易單，用當天均價成交、收盤價算淨值
      2. 下單：用最新快照那天的資料，替下一個交易日產生交易單（已產生過就不重做）
      3. 印出三組的淨值、報酬、持股、現金比較，並累積到 summary.csv
  · 選股、交易單直接呼叫 esun_strategy、esun_orders（live 模式），跟正式報告同一套邏輯
  · 150 檔那組需要選股池以外股票的三大法人，每天另外補抓一次（存進 esun_inst，正式程式不會讀到）
  · 均分那組：現金比目標多 2% 以上時，當天不套用無交易帶，把第一天沒買足的部位補齊
  · 出錯時：有持股就不交易（＝全部續抱）；還沒有持股就等下一次
資料：_research\\rehearsal\\<組別>\\  state_日期.json、orders_交易日.json、targets_交易日.json
用法（在 SMC- 資料夾，排在每日流程之後）：python _research\\esun_rehearsal.py
       重做下一日的交易單：python _research\\esun_rehearsal.py --force
"""
import os, sys, json, glob, time, random
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.exists(os.path.join(os.path.dirname(HERE), "esun_strategy.py")) else HERE
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import pandas as pd
import esun_strategy as S
import esun_orders as O
import esun_ledger as L
import esun_data as E
import esun_fetch_inst as INST
from esun_daily import next_trading_day
from dplan_validator import UNIVERSE

BASE = os.path.join(HERE, "rehearsal")
SECTOR = json.load(open(S.SECTOR_FILE, encoding="utf-8"))
ALL_CODES = {v["code"] for v in SECTOR.values()}
VARIANTS = {   # 代號: (名稱, 產業代碼, 核心權重, 小部位權重, 過濾範圍, 均分)
    "5": ("150檔排除航運｜9.5%+1%｜new_only（＝第 9 組，正式設定）", ALL_CODES, 0.095, 0.01, "new_only", False),
    "1": ("三產業｜9.5%+1%｜new_only", {"24", "25", "28"}, 0.095, 0.01, "new_only", False),
    "3": ("三產業｜均分4.5%｜new_only", {"24", "25", "28"}, 0.045, 0.045, "new_only", True),
}


def jload(p):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def latest_state(vdir, before=None):
    fs = sorted(glob.glob(os.path.join(vdir, "state_*.json")))
    if before:
        fs = [f for f in fs if os.path.basename(f)[6:16] < before]
    return jload(fs[-1]) if fs else None


def latest_targets(vdir, before):
    fs = sorted(f for f in glob.glob(os.path.join(vdir, "targets_*.json")) if os.path.basename(f)[8:18] < before)
    return jload(fs[-1]) if fs else None


def extra_inst():
    """150 檔那組要用的：選股池以外股票的三大法人（每天一次）"""
    stamp = os.path.join(BASE, "inst_stamp.txt")
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(stamp) and open(stamp).read().strip() == today:
        return
    extra = sorted(set(UNIVERSE) - set(INST.pool()))
    start = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    os.makedirs(INST.OUT_DIR, exist_ok=True)
    ok = 0
    for sid in extra:
        st, df = INST.fetch_one(sid, start)
        if st in ("RATE_LIMIT", "NET_ERROR"):
            print(f"  ! 三大法人（池外）：{'限流' if st == 'RATE_LIMIT' else '斷網'}，下次再補"); return
        if st == "OK":
            p = os.path.join(INST.OUT_DIR, f"{sid}.parquet")
            if os.path.exists(p):
                old = pd.read_parquet(p)
                df = pd.concat([old[~old.index.isin(df.index)], df]).sort_index()
            df.to_parquet(p)
            ok += 1
        time.sleep(random.uniform(0.2, 0.4))
    os.makedirs(BASE, exist_ok=True)
    open(stamp, "w").write(today)
    print(f"三大法人（選股池以外）：更新 {ok} / {len(extra)} 檔")


def settle(vdir, snap):
    day = snap["date"]
    p = os.path.join(vdir, f"state_{day}.json")
    if os.path.exists(p):
        return jload(p), "已記過"
    prev = latest_state(vdir, before=day)
    od = jload(os.path.join(vdir, f"orders_{day}.json"))
    if prev is None and od is None:
        return None, "尚未開始"
    prev = prev or L.new_state("init")
    orders = (od or {}).get("orders", [])
    vwap, close = E.prices_for(snap["quotes"], "vwap"), E.prices_for(snap["quotes"], "close")
    skipped = [o["ticker"] for o in orders if o["ticker"] not in vwap]
    orders = [o for o in orders if o["ticker"] in vwap]
    st = L.apply_trades(prev, orders, vwap, day)
    for t in st["holdings"]:                                # 沒有收盤價（停牌）就沿用前一天
        close.setdefault(t, prev.get("close", {}).get(t))
    st = L.mark_to_market(st, close)
    bad, note = L.check_rules(st, bought_today={o["ticker"] for o in orders if o["side"] == "BUY"})
    st["rule_bad"], st["rule_note"] = bad, note
    jsave(p, st)
    return st, f"{len(orders)} 筆成交" + (f"（缺均價未成交：{skipped}）" if skipped else "")


def make_next(vdir, v, snap, state, force):
    name, codes, wc, ws, scope, eq = v
    nxt = next_trading_day(datetime.strptime(snap["date"], "%Y-%m-%d")).strftime("%Y-%m-%d")
    p = os.path.join(vdir, f"orders_{nxt}.json")
    if os.path.exists(p) and not force:
        return nxt, jload(p), "已產生過"
    state = state or L.new_state(snap["date"])
    price = {t: q["close"] for t, q in snap["quotes"].items() if q.get("close")}
    d = pd.Timestamp(snap["date"])
    cur_w = {t: sh * price.get(t, 0) / state["nav"] for t, sh in state["holdings"].items()}
    S.AI_CODES, S.W_CORE, S.W_SMALL, S.FILTER_SCOPE = codes, wc, ws, scope
    out = {"trade_date": nxt, "data_date": snap["date"], "orders": [], "kind": "", "error": None}
    try:
        tg, table = S.build_targets(d, latest_targets(vdir, nxt), cur_w, mode="live")
        og = dict(tg, roles={s: ("核心" if r == "小部位" else r) for s, r in tg["roles"].items()}) if eq else tg
        # 均分時第一天只建到約 86%，第二天每檔只差約 0.7%，會被 1.5% 無交易帶擋住、現金一直閒著
        # → 現金比目標多 2% 以上時，這天不套用無交易帶，一次補足
        fill = eq and state["cash"] / state["nav"] > tg["cash_target"] + 0.02
        band0 = O.BAND
        O.BAND = 0.0 if fill else band0
        try:
            _, orders, _, sm = O.make_orders(og, state, price)
        finally:
            O.BAND = band0
        jsave(os.path.join(vdir, f"targets_{nxt}.json"), tg | {"date": nxt})
        out |= {"orders": orders, "kind": "正常", "summary": sm, "pool": len(table),
                "blocked": [[s, r, w] for s, r, w in tg["blocked"]]}
    except Exception as e:
        out |= {"kind": "續抱（出錯）" if state["holdings"] else "沒有建倉（出錯）",
                "error": f"{type(e).__name__}：{str(e)[:150]}"}
    jsave(p, out)
    return nxt, out, "新產生"


def main():
    force = "--force" in sys.argv
    snaps = sorted(glob.glob(os.path.join(E.DATA_DIR, "daily_*.json")))
    if not snaps:
        print("沒有行情快照，先跑 esun_daily.py"); return
    snap = E.load_snapshot(os.path.basename(snaps[-1])[6:16])
    print(f"===== 彩排 {datetime.now():%Y-%m-%d %H:%M}｜最新行情 {snap['date']} =====")
    try:
        extra_inst()
    except Exception as e:
        print(f"  ! 三大法人（池外）失敗：{type(e).__name__}")
    rows = []
    for vid, v in VARIANTS.items():
        vdir = os.path.join(BASE, vid)
        os.makedirs(vdir, exist_ok=True)
        st, msg = settle(vdir, snap)
        nxt, od, msg2 = make_next(vdir, v, snap, st, force)
        print(f"\n[{vid}] {v[0]}")
        print(f"  記帳 {snap['date']}：{msg}")
        if st:
            print(f"  淨值 {st['nav']:,.0f}（{st['nav'] / L.INIT_CASH - 1:+.2%}）｜現金 {st['cash'] / st['nav']:.1%}｜"
                  f"持股 {len(st['holdings'])} 檔")
            for x in st.get("rule_bad", []):
                print(f"  ✗ {x}")
            for x in st.get("rule_note", []):
                print(f"  ! {x}")
        sm = od.get("summary") or {}
        print(f"  {nxt} 交易單（{msg2}）：{od['kind']}，{len(od['orders'])} 筆"
              + (f"｜交易後 {sm['holdings_after']} 檔、預估現金 {sm['est_cash_pct']:.1%}" if sm else "")
              + (f"｜{od['error']}" if od.get("error") else ""))
        if od.get("blocked"):
            print("  被過濾擋下：" + "、".join(f"{s}（第{r}名）" for s, r, _ in od["blocked"][:6]))
        if st:
            rows.append({"date": snap["date"], "組別": vid, "名稱": v[0], "淨值": round(st["nav"]),
                         "累計報酬": round(st["nav"] / L.INIT_CASH - 1, 5), "現金比": round(st["cash"] / st["nav"], 4),
                         "持股數": len(st["holdings"])})

    if rows:
        p = os.path.join(BASE, "summary.csv")
        old = pd.read_csv(p, dtype={"組別": str}) if os.path.exists(p) else pd.DataFrame()
        new = pd.DataFrame(rows)
        allr = pd.concat([old[old["date"] != snap["date"]], new]) if len(old) else new
        allr.to_csv(p, index=False, encoding="utf-8-sig")
        tab = allr.pivot_table(index="date", columns="組別", values="累計報酬").sort_index()
        print("\n累計報酬（%）：")
        print((tab * 100).to_string(float_format=lambda x: f"{x:7.2f}"))
        daily = (1 + tab) / (1 + tab.shift(1)).fillna(1.0) - 1
        print("\n單日報酬（%）：")
        print((daily * 100).to_string(float_format=lambda x: f"{x:7.2f}"))


if __name__ == "__main__":
    main()