# -*- coding: utf-8 -*-
"""
esun_report.py — 組出「當日 Agent 決策報告及交易書」（D-Plan JSON），並在出錯時改送保底報告

  · build_report：來源 → 觀察 → 市場看法 → 推論 → 決策 → 交易單，每一層用編號引用上一層
  · build_hold_report：全部續抱（每檔持股都有不交易理由，並引用同一條推論）
  · run(trade_date)：每日流程（目前未接 Claude，文字由規則產生）
      1. 讀帳本狀態與最新行情快照  2. 選股  3. 產生交易單  4. 組報告  5. 檢查器
      任何一步失敗 → 有持股就送「全部續抱」；第一天沒持股 → 用最後一份存下的目標組合建倉
  · 報告存到 esun_reports/D-Plan_{隊伍}_{日期}.json，檢查結果一併存檔
用法：python esun_report.py YYYY-MM-DD   （交易日；會用該日之前最新的資料）
"""
import os, sys, json, glob, traceback
from datetime import datetime, timezone, timedelta
import pandas as pd

import esun_ledger as L
import esun_orders as O
import esun_strategy as S
from dplan_validator import validate

TEAM_ID = "TEAM_11358"                     # 報名後改成正式隊伍編號


def code_version():
    """版本號統一由 esun_version 計算並打包存檔（策略文件、每日報告用同一套，才對得上）"""
    import esun_version
    return esun_version.archive()


REPORT_DIR = "./esun_reports"
TZ = timezone(timedelta(hours=8))
FINMIND = "https://api.finmindtrade.com/api/v4/data"


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def mtime(path_glob):
    fs = glob.glob(path_glob)
    t = max(os.path.getmtime(f) for f in fs) if fs else datetime.now().timestamp()
    return datetime.fromtimestamp(t, TZ).isoformat(timespec="seconds")


def latest_avail(path_glob):
    """資料內容的最新公告期限（已過的），當作 content_as_of"""
    best = None
    today = pd.Timestamp.now().normalize()
    for f in glob.glob(path_glob):
        try:
            a = pd.to_datetime(pd.read_parquet(f, columns=["available_date"])["available_date"])
            a = a[a <= today]
            if len(a) and (best is None or a.max() > best):
                best = a.max()
        except Exception:
            continue
    return f"{best:%Y-%m-%d}T00:00:00+08:00" if best is not None else now()


def base_sources(snapshot):
    src = []
    for i, s in enumerate(snapshot["sources"], 1):
        src.append({"source_id": f"S{i}", "authority": s["authority"], "name": s["name"], "url": s["url"],
                    "content_as_of": s["content_as_of"], "fetched_at": s["fetched_at"]})
    n = len(src)
    src.append({"source_id": f"S{n+1}", "authority": "vendor", "name": "FinMind 月營收（TaiwanStockMonthRevenue）",
                "url": f"{FINMIND}?dataset=TaiwanStockMonthRevenue", "content_as_of": latest_avail(f"{S.REV_DIR}/*.parquet"),
                "fetched_at": mtime(f"{S.REV_DIR}/*.parquet")})
    src.append({"source_id": f"S{n+2}", "authority": "vendor", "name": "FinMind 綜合損益表 EPS（TaiwanStockFinancialStatements）",
                "url": f"{FINMIND}?dataset=TaiwanStockFinancialStatements", "content_as_of": latest_avail(f"{S.EPS_DIR}/*.parquet"),
                "fetched_at": mtime(f"{S.EPS_DIR}/*.parquet")})
    return src


def posture(summary, nav):
    net = summary["buy_amt"] - summary["sell_amt"]
    intent = "increase" if net > 0.02 * nav else ("reduce" if net < -0.02 * nav else "hold")
    c = summary["est_cash_pct"]
    return {"net_exposure_intent": intent,
            "target_cash_pct_range": [round(max(0.0, c - 0.03), 3), round(min(0.24, c + 0.03), 3)]}



def concentration_text(decisions, no_trade):
    """依交易後目標權重算產業集中度，寫成 market_view 的反面證據（不寫死）"""
    w = {d["ticker"]: d["target_weight"] for d in decisions if d["target_weight"] > 0}
    w.update({x["ticker"]: x["target_weight"] for x in no_trade if x["target_weight"] > 0})
    if not w:
        return None
    try:
        sec = json.load(open(S.SECTOR_FILE, encoding="utf-8"))
    except Exception:
        sec = {}
    ind = {}
    for t, x in w.items():
        name = (sec.get(t) or {}).get("name") or "其他"
        ind[name] = ind.get(name, 0) + x
    top = sorted(ind.items(), key=lambda kv: -kv[1])
    tot = sum(w.values())
    head = "、".join(f"{n}約 {v:.0%}" for n, v in top[:2])
    big = max(w.items(), key=lambda kv: kv[1])
    txt = f"交易後持股 {len(w)} 檔、股票部位約 {tot:.0%}，其中{head}"
    if len(top) >= 2:
        txt += f"（前兩大產業合計約 {top[0][1] + top[1][1]:.0%}）"
    txt += f"；最大單一持股 {big[0]} 約 {big[1]:.0%}。持股集中在少數產業，這些產業景氣反轉時組合波動會放大"
    return txt

def build_report(trade_date, state, snapshot, targets, table, decisions, orders, no_trade, summary, started):
    src = base_sources(snapshot)
    px_src = [s["source_id"] for s in src[:-2]]
    rev_src, eps_src = src[-2]["source_id"], src[-1]["source_id"]
    ovr = targets.get("override")
    ovr_refs, ovr_txt = [], ""
    if ovr:
        sid = f"S{len(src) + 1}"
        src.append({"source_id": sid, "authority": "media", "name": ovr["source_name"], "url": ovr["source_url"],
                    "content_as_of": started, "fetched_at": started})
        ovr_refs = [sid]
        ovr_txt = (f"{ovr['set_on']} {ovr['set_by']}啟用臨時防守設定，原因：{ovr['reason']}；"
                   "這份配置由操作者指定，不是排名規則或防守模式門檻自動產生")
    obs = [{"obs_id": "O1", "source_ref": px_src, "topic": "portfolio",
            "statement": f"前一交易日（{snapshot['date']}）收盤後：組合淨值 {state['nav']:,.0f} 元、現金占 "
                         f"{state['cash']/state['nav']:.1%}、持股 {len(state['holdings'])} 檔",
            "values": {"nav": round(state["nav"]), "cash_pct": round(state["cash"] / state["nav"], 4),
                       "n_holdings": len(state["holdings"])}},
           {"obs_id": "O2", "source_ref": [rev_src, eps_src] + px_src + ovr_refs, "topic": "screen",
            "statement": f"主辦方 150 檔標的（排除航運業）中符合流動性者 {len(table)} 檔，"
                         f"依營收驚喜、EPS 增幅、三率排名"
                         + ("；排名前列但未通過過濾而不新買進：" + "、".join(
                             f"{b[0]}（第{b[1]}名，{'、'.join(b[2])}）" for b in targets.get("blocked", [])[:6])
                            if targets.get("blocked") else "")
                         + (f"；{targets.get('def_reason')}" if targets.get("defensive") else "")
                         + ("；通過過濾者不足 19 檔，依排名以未通過者補足：" + "、".join(targets["filled"])
                            if targets.get("filled") else "")
                         + (f"；{ovr_txt}" if ovr else ""),
            "values": {"pool_size": len(table), "blocked": len(targets.get("blocked", [])),
                       "n_pass_filter": targets.get("n_pass"), "defensive": bool(targets.get("defensive"))}}]
    dfn = bool(targets.get("defensive"))
    w_tsmc = float(ovr["tsmc"]) if ovr else (S.W_TSMC_DEF if dfn else S.W_TSMC)
    obs_of = {}
    tickers = [d["ticker"] for d in decisions]
    for t in tickers:
        oid = f"O{len(obs) + 1}"
        if t in table.index:
            r = table.loc[t]
            ri, ei = r["營收資料"] or {}, r["EPS資料"] or {}
            st = (f"{t}：{ri.get('月份', '—')} 營收 YoY {ri.get('YoY', '—')}%，較前三月平均變化 "
                  f"{r['營收驚喜']:+.1f} 個百分點；" if pd.notna(r["營收驚喜"]) else f"{t}：營收驚喜無資料；")
            st += (f"{ei.get('季', '—')} EPS {ei.get('EPS')} 元（去年同季 {ei.get('去年同季')}），增幅相當股價 "
                   f"{r['EPS增幅']*100:+.2f}%；" if pd.notna(r["EPS增幅"]) else "EPS 增幅無資料；")
            mi, bi = r.get("三率資料") or {}, r.get("大戶資料") or {}
            if mi:
                st += f"{mi['季']} 三率較上季 {mi['不跌項數']}/{mi['比較項數']} 項未下跌；"
            st += f"綜合排名 {int(r['排名'])}/{len(table)}"
            vals = {"rev_surprise_pp": None if pd.isna(r["營收驚喜"]) else round(float(r["營收驚喜"]), 2),
                    "eps_delta_to_price": None if pd.isna(r["EPS增幅"]) else round(float(r["EPS增幅"]), 5),
                    "margins_not_down": (mi or {}).get("不跌項數"),
                    "rank": int(r["排名"])}
            refs = [rev_src, eps_src] + px_src
        elif t == S.TSMC:
            q = snapshot["quotes"].get(t, {})
            st = ((f"{t}：臨時防守設定配置約 {w_tsmc:.0%}，不參與綜合排名；" if ovr else
                   f"{t}：防守模式依規則配置約 {w_tsmc:.0%}，不參與綜合排名；" if dfn else
                   f"{t}：依規則固定配置約 {w_tsmc:.0%}（低於其市值權重），不參與綜合排名；")
                  + f"前一日收盤 {q.get('close')} 元")
            vals, refs = {"fixed_weight": w_tsmc, "close": q.get("close")}, px_src
        else:
            q = snapshot["quotes"].get(t, {})
            st = (f"{t}：臨時防守設定指定持有（前一日收盤 {q.get('close')} 元）" if t in targets.get("weights", {})
                  else f"{t}：不在本日目標組合內（前一日收盤 {q.get('close')} 元）")
            vals, refs = {"in_targets": False, "close": q.get("close")}, px_src
        obs.append({"obs_id": oid, "source_ref": refs, "topic": "stock", "statement": st, "values": vals})
        obs_of[t] = oid

    infs, decs, ords, ntd = [], [], [], []
    for i, d in enumerate(decisions, 1):
        t, iid, did = d["ticker"], f"I{i}", f"D{i}"
        role = d["_role"] or ("出清" if d["target_weight"] == 0 else "調整")
        if ovr:
            verb = "出清" if d["action"] == "SELL_ALL" else ("減碼至" if d["action"] == "TRIM" else "買進至")
            logic = f"{ovr_txt}；{t} 依此設定調整"
            concl = f"出清 {t}" if d["action"] == "SELL_ALL" else f"{t} {verb} {d['target_weight']:.2%}"
        elif t == S.TSMC and d["action"] != "SELL_ALL":
            if dfn:
                logic = f"防守模式（{targets.get('def_reason')}）：台積電調整至 {w_tsmc:.0%}"
            elif d["_current_weight"] > S.TSMC_BAND[1] + 0.02:
                logic = (f"選股池通過過濾 {targets.get('n_pass')} 檔，已超過 {S.DEF_EXIT_PASS} 檔，"
                         f"結束防守模式，台積電調回 {w_tsmc:.0%}")
            else:
                logic = f"台積電固定配置規則：目前權重在 {S.TSMC_BAND[0]:.0%}–{S.TSMC_BAND[1]:.0%} 之外，調整至 {w_tsmc:.0%}"
            concl = f"{t} {'減碼' if d['action'] == 'TRIM' else '買進'}至 {d['target_weight']:.2%}"
        elif d["action"] == "SELL_ALL":
            logic, concl = "排名跌出核心保留範圍或不在本日目標組合內，依規則出清", f"出清 {t}"
        elif d["action"] == "TRIM" and dfn and role == "核心":
            logic = f"防守模式：核心權重依規則降至各 {S.W_CORE_DEF:.3%}，提高現金"
            concl = f"{t} 減碼至 {d['target_weight']:.2%}"
        elif d["action"] == "TRIM":
            logic, concl = f"權重偏離目標超過無交易帶，依規則調回{role}目標權重", f"{t} 減碼至 {d['target_weight']:.2%}"
        elif role == "小部位" and d["target_weight"] < S.W_SMALL * 0.9 and any("小部位" in n for n in summary["notes"]):
            logic = ("綜合排名符合小部位條件；本日以漲停價估算最壞情況現金不足，依規則先建立 1 張，"
                     f"後續交易日補足至 {S.W_SMALL:.0%}")
            concl = f"{t} 先買進 1 張（{d['target_weight']:.2%}）"
        elif role == "核心" and d["target_weight"] < targets["weights"].get(t, 0) * 0.98 and any("核心買進縮為" in n for n in summary["notes"]):
            logic = "綜合排名符合核心條件；本日最壞情況現金不足，核心買進依比例縮減，後續補足"
            concl = f"{t} 買進至 {d['target_weight']:.2%}"
        elif t in (targets.get("filled") or []):
            rk = int(table.loc[t, "排名"]) if t in table.index else None
            logic = (f"綜合排名第 {rk} 名；通過過濾者不足 19 檔，依規則以排名補足為{role}，維持持股 20 檔" if rk
                     else f"通過過濾者不足 19 檔，依規則以排名補足為{role}，維持持股 20 檔")
            concl = f"{t} 買進至 {d['target_weight']:.2%}"
        else:
            logic, concl = f"綜合排名符合{role}條件，依規則建立或補足部位", f"{t} 買進至 {d['target_weight']:.2%}"
        ce = ("台積電為最大權值股，低配使組合在其大漲時落後大盤" if t == S.TSMC
              else "營收驚喜與 EPS 增幅在大型股的預測力未達統計顯著，屬方向性調整")
        if ovr:
            ce = "以當日均價成交，開盤跳空的跌幅無法避開，若隨後反彈會賣在低點；提高台積電也會增加半導體曝險；防守配置未經回測驗證"
        infs.append({"inf_id": iid, "premise_refs": ["O2", obs_of[t]], "logic": logic, "conclusion": concl,
                     "counter_evidence": ce, "confidence": 0.55})
        cap = 0.25 if t == "2330" else 0.10
        decs.append({"decision_id": did, "ticker": t, "action": d["action"], "target_weight": d["target_weight"],
                     "inference_refs": [iid],
                     "risk_check": f"目標 {d['target_weight']:.2%} ≤ 上限 {cap:.0%}；交易後持股 {summary['holdings_after']} 檔；"
                                   f"最壞情況現金 {summary['worst_cash_pct']:.1%}"})
    for o in orders:
        did = next(x["decision_id"] for x in decs if x["ticker"] == o["ticker"])
        ords.append({"ticker": o["ticker"], "side": o["side"], "shares": o["shares"], "decision_ref": did,
                     "derivation_detail": o["derivation_detail"]})
    if no_trade:
        iid = f"I{len(infs) + 1}"
        infs.append({"inf_id": iid, "premise_refs": ["O1", "O2"],
                     "logic": "目前權重與目標權重差距小於無交易帶（1.5% 與目標一半取小），調整的交易成本高於效益",
                     "conclusion": "續抱，不交易", "counter_evidence": None, "confidence": 0.8})
        for x in no_trade:
            ntd.append({"ticker": x["ticker"], "reason_refs": [iid],
                        "reason": f"目前 {x['current_weight']:.2%}、目標 {x['target_weight']:.2%}，在無交易帶內"})

    mv = {"basis_refs": ["O1", "O2"],
          "logic": (f"防守模式：{targets.get('def_reason')}（過濾含均線、量能、法人），"
                    f"視為全市場行情偏弱，依規則台積電提高至 {S.W_TSMC_DEF:.0%}、核心降至各 {S.W_CORE_DEF:.3%}、現金約 {S.CASH_DEF:.0%}"
                    if dfn else
                    "本 ETF 為規則型多因子選股策略（營收驚喜、EPS 增幅、三率），平時不依短期市場訊號擇時；"
                    f"僅在選股池通過過濾的檔數少於 {S.DEF_MAX_PASS + 1} 檔時轉為防守配置，超過 {S.DEF_EXIT_PASS} 檔才恢復；"
                    "依目標權重與現金規則調整部位"),
          "regime": "neutral", "stance": "neutral", "posture": posture(summary, state["nav"]),
          "counter_evidence": concentration_text(decisions, no_trade), "confidence": 0.6}
    if ovr:
        mv |= {"logic": f"{ovr_txt}；台積電約 {w_tsmc:.0%}，中信金、國泰金、中華電加大部位，其餘持股各約 1%",
               "regime": "risk_off", "stance": "defensive", "confidence": 0.5}
    return {"schema_version": "4.3", "doc_type": "D-Plan", "team_id": TEAM_ID, "trade_date": trade_date,
            "sources": src, "observations": obs, "market_view": mv, "inferences": infs, "decisions": decs,
            "no_trade_decisions": ntd, "orders": ords,
            "agent_metadata": {"model_provider": "other", "model_version": "rule-based-v0", "run_started_at": started,
                               "run_completed_at": now(), "input_tokens": 0, "output_tokens": 0,
                               "tool_use_count": 0, "code_version": code_version()}}


def build_hold_report(trade_date, state, snapshot, reason, started):
    src = base_sources(snapshot)[:-2] if snapshot else [
        {"source_id": "S1", "authority": "internal", "name": "Agent 前一交易日組合紀錄", "url": "local://esun_state",
         "content_as_of": started, "fetched_at": started}]
    nav, cash = state["nav"], state["cash"]
    c = cash / nav
    obs = [{"obs_id": "O1", "source_ref": [s["source_id"] for s in src], "topic": "portfolio",
            "statement": f"前一交易日收盤後：淨值 {nav:,.0f} 元、現金占 {c:.1%}、持股 {len(state['holdings'])} 檔",
            "values": {"nav": round(nav), "cash_pct": round(c, 4), "n_holdings": len(state["holdings"])}}]
    inf = {"inf_id": "I1", "premise_refs": ["O1"],
           "logic": f"本日決策流程未完成（{reason}），依風控規則不在資訊不完整下交易",
           "conclusion": "全部續抱，維持前一交易日持股", "counter_evidence": None, "confidence": 0.9}
    return {"schema_version": "4.3", "doc_type": "D-Plan", "team_id": TEAM_ID, "trade_date": trade_date,
            "sources": src, "observations": obs,
            "market_view": {"basis_refs": ["O1"], "logic": "決策流程未完成，本日不改變曝險", "regime": "neutral",
                            "stance": "neutral", "posture": {"net_exposure_intent": "hold",
                                                             "target_cash_pct_range": [round(max(0, c - 0.02), 3),
                                                                                       round(min(0.24, c + 0.02), 3)]},
                            "counter_evidence": None, "confidence": 0.9},
            "inferences": [inf], "decisions": [],
            "no_trade_decisions": [{"ticker": t, "reason_refs": ["I1"], "reason": "決策流程未完成，維持持股"}
                                   for t in sorted(state["holdings"])],
            "orders": [],
            "agent_metadata": {"model_provider": "other", "model_version": "rule-based-v0", "run_started_at": started,
                               "run_completed_at": now(), "input_tokens": 0, "output_tokens": 0,
                               "tool_use_count": 0, "code_version": code_version()}}


def latest_snapshot(before):
    fs = sorted(f for f in glob.glob("./esun_data/daily_*.json") if os.path.basename(f)[6:16] < before)
    return json.load(open(fs[-1], encoding="utf-8")) if fs else None


def save(plan, check):
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"D-Plan_{plan['team_id']}_{plan['trade_date']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    with open(path.replace(".json", "_check.json"), "w", encoding="utf-8") as f:
        json.dump(check, f, ensure_ascii=False, indent=2)
    return path


def _clean(plan):
    """observations.values 規格只收數字：布林轉 0/1、空值（None、NaN）和文字拿掉；拿光了補一個 0"""
    import math
    for o in plan.get("observations", []):
        v = {}
        for k, x in (o.get("values") or {}).items():
            if isinstance(x, bool):
                v[k] = int(x)
            elif isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x)):
                v[k] = x
        o["values"] = v or {"n": 0}
    return plan


def etf_holdings():
    """各主動型 ETF 的前 15 大權重（esun_etf_top10.csv），給檢查器擋主動投資比率；
    沒有台股持股的（美國型、全球型填 NONE）不比對；讀不到就回傳 None（檢查器略過這項）"""
    try:
        import esun_active_share as AS
        return {code: d["w"] for code, d in AS.load_top10().items() if d["w"]} or None
    except Exception as e:
        print(f"   ! 主動型 ETF 持股資料讀取失敗：{type(e).__name__} {e}")
        return None


def run(trade_date):
    started = now()
    td = pd.Timestamp(trade_date)
    if td.weekday() >= 5:
        print(f"{trade_date} 是週末，不是交易日"); return None, ["非交易日"]
    if datetime.now(TZ) > datetime(td.year, td.month, td.day, 8, 55, tzinfo=TZ):
        print(f"交易日 {trade_date} 的提交期限（當天 08:55）已過，請輸入下一個交易日"); return None, ["期限已過"]
    summ = {}
    state = L.load_latest(before=trade_date) or L.new_state("init")
    snap = latest_snapshot(trade_date)
    vstate = None
    try:
        if snap is None:
            raise RuntimeError("沒有行情快照")
        price = {t: q["close"] for t, q in snap["quotes"].items() if q.get("close")}
        d = pd.Timestamp(snap["date"])
        cur_w = {t: sh * price.get(t, 0) / state["nav"] for t, sh in state["holdings"].items()}
        tg, table = S.build_targets(d, S.load_prev_targets(trade_date), cur_w)
        tg = S.apply_override(tg, table, trade_date, cur_w)
        S.save_targets(tg | {"date": trade_date})
        dec, od, nt, sm = O.make_orders(tg, state, price)
        plan = build_report(trade_date, state, snap, tg, table, dec, od, nt, sm, started)
        vstate = L.to_validator_state(state, price)
        rej, warn, info = validate(_clean(plan), vstate, strict_holdings=True, etf_top10=etf_holdings())
        if rej:
            raise RuntimeError("檢查器拒收：" + "；".join(rej))
        kind, summ = "正常", sm
        try:                                              # 主動投資比率（不影響報告產生，只記錄與警告）
            import esun_active_share as AS
            res, aw = AS.check(AS.post_trade_weights(state, od, price), trade_date)
            known = [r for r in res if r[2] is not None]
            summ = dict(summ, active_share_min=min((r[2] for r in known), default=None),
                        active_share_warn=aw)
            AS.report(res, aw, log=lambda m: print("   " + m))
        except Exception as e:
            print(f"   ! 主動投資比率檢查失敗：{type(e).__name__} {e}")
    except Exception as e:
        reason = f"{type(e).__name__}: {str(e)[:120]}"
        traceback.print_exc()
        if state["holdings"]:
            plan = build_hold_report(trade_date, state, snap, reason, started)
            kind = "保底：全部續抱"
            rej, warn, info = validate(_clean(plan), vstate, strict_holdings=True) if vstate else validate(_clean(plan), None)
        else:                                            # 第一天沒有持股：沿用最近一份存下的目標組合建倉
            prev_tg = S.load_prev_targets(trade_date)
            if prev_tg is None or snap is None:
                raise RuntimeError(f"第一天出錯，且沒有可沿用的目標組合或行情快照：{reason}")
            price = {t: q["close"] for t, q in snap["quotes"].items() if q.get("close")}
            dec, od, nt, sm = O.make_orders(prev_tg, state, price)
            plan = build_report(trade_date, state, snap, prev_tg, pd.DataFrame(), dec, od, nt, sm, started)
            plan["observations"][1]["statement"] = f"本日選股流程未完成（{reason}），沿用 {prev_tg['date']} 的目標組合"
            plan["observations"][1]["values"] = {"fallback_targets_date": int(prev_tg["date"].replace("-", ""))}
            vstate = L.to_validator_state(state, price)
            kind, summ = "保底：沿用最近目標組合建倉", sm
            rej, warn, info = validate(_clean(plan), vstate, strict_holdings=True)
    path = save(plan, {"kind": kind, "reject": rej, "warn": warn, "info": info, "summary": summ})
    print(f"[{kind}] {path}｜決策 {len(plan['decisions'])}、交易單 {len(plan['orders'])}、"
          f"不交易 {len(plan['no_trade_decisions'])}｜拒收 {len(rej)}、警告 {len(warn)}")
    if summ:
        print(f"   交易後持股 {summ['holdings_after']} 檔｜預估現金 {summ['est_cash_pct']:.1%}｜"
              f"最壞情況現金 {summ['worst_cash_pct']:.1%}" + (f"｜{'；'.join(summ['notes'])}" if summ["notes"] else ""))
    for x in rej + warn:
        print("   ", x)
    return plan, rej


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python esun_report.py YYYY-MM-DD（交易日）"); sys.exit(1)
    run(sys.argv[1])