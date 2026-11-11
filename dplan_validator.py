# -*- coding: utf-8 -*-
"""
dplan_validator.py — 玉山挑戰賽「當日 Agent 決策報告及交易書」(D-Plan JSON) 自我檢查器

主辦方未公開正式規格，本檢查器依官方正例(TEAM_042)與反例(TEAM_043)反推：
  【拒收】格式缺欄位、編號重複、引用不存在的編號（孤兒決策）、標的不在150檔、股數非整張
  【警告】股數與官方公式不符、宣告姿態與實際淨買賣矛盾、預估現金落在宣告區間外
  【比賽規則自保】持股20~30檔、單檔上限(含緩衝)、最壞情況現金不為負、現金<25%、
                  主動投資比率：前 15 大、不能低於 15%（辦法六(十三)，2026/10/05 公告；有提供主動型ETF資料時才檢查）
需要「前一日組合狀態」檔（淨值、現金、持股、價格）才能檢查股數與規則。
用法：python dplan_validator.py 報告.json 狀態.json
"""
import json, math, sys
from datetime import datetime

UNIVERSE = set("1101,1216,1301,1303,1326,1402,1504,1519,1590,1605,1785,1802,1815,2002,2059,2207,2301,2303,2308,2313,2317,2324,2327,2330,2337,2344,2345,2347,2356,2357,2360,2368,2376,2377,2379,2382,2383,2395,2404,2408,2409,2412,2449,2451,2454,2603,2609,2610,2615,2618,2801,2812,2834,2880,2881,2882,2883,2884,2885,2886,2887,2890,2891,2892,2912,3008,3017,3034,3036,3037,3044,3045,3081,3105,3131,3163,3189,3211,3227,3231,3260,3264,3293,3324,3363,3374,3443,3481,3491,3529,3533,3653,3661,3665,3680,3702,3711,3718,4123,4749,4772,4904,4938,4958,4966,4979,4991,5274,5289,5347,5483,5536,5871,5876,5880,5903,6023,6121,6139,6147,6182,6187,6223,6239,6274,6290,6415,6446,6488,6505,6510,6515,6548,6584,6669,6770,6789,6919,7734,7750,7751,7769,7828,8046,8069,8210,8299,8358,8415,8932".split(","))
FEE, TAX = 0.001425, 0.003
TSMC = "2330"
CAP_TSMC, CAP_OTHER = 0.25, 0.10
BUF_TSMC, BUF_OTHER = 0.2255, 0.0905    # 買進時自己用的上限（2026-11-10 起留漲停緩衝：主辦方用成交日收盤價算加碼後權重）
LIMIT_UP = 1.10                          # 最壞情況：買進成交在漲停價
HOLD_TOL = 0.02                          # 宣告 hold 時可接受的淨買賣（占淨值）—反推假設
MIN_AS, AS_TOP_N = 0.15, 15             # 主動投資比率：前 15 大、低於 15% 違規（原本前 10 大、20%）

REQ = {
    "top": ["schema_version", "doc_type", "team_id", "trade_date", "sources", "observations",
            "market_view", "inferences", "decisions", "no_trade_decisions", "orders", "agent_metadata"],
    "sources": ["source_id", "authority", "name", "url", "content_as_of", "fetched_at"],
    "observations": ["obs_id", "source_ref", "topic", "statement", "values"],
    "market_view": ["basis_refs", "logic", "regime", "stance", "posture", "counter_evidence"],
    "inferences": ["inf_id", "premise_refs", "logic", "conclusion", "counter_evidence", "confidence"],
    "decisions": ["decision_id", "ticker", "action", "target_weight", "inference_refs", "risk_check"],
    "no_trade_decisions": ["ticker", "reason_refs", "reason"],
    "orders": ["ticker", "side", "shares", "decision_ref", "derivation_detail"],
    "agent_metadata": ["model_provider", "model_version", "run_started_at", "run_completed_at",
                       "input_tokens", "output_tokens", "tool_use_count", "code_version"],
}


def _schema_errors(plan, sch):
    defs = sch.get("$defs", {})
    errs = []
    def deref(d):
        while isinstance(d, dict) and "$ref" in d:
            d = defs[d["$ref"].split("/")[-1]]
        return d
    def chk(obj, sc, path):
        sc = deref(sc)
        t = sc.get("type")
        if t == "object" or "properties" in sc:
            if not isinstance(obj, dict):
                errs.append(f"{path} 應為 object"); return
            for r in sc.get("required", []):
                if r not in obj:
                    errs.append(f"{path}.{r} 缺必填欄位")
            props = sc.get("properties", {})
            if sc.get("additionalProperties") is False:
                for k in obj:
                    if k not in props:
                        errs.append(f"{path}.{k} 是規格外欄位（規格不允許）")
            for k, v in obj.items():
                if k in props:
                    chk(v, props[k], f"{path}.{k}")
        elif t == "array":
            if not isinstance(obj, list):
                errs.append(f"{path} 應為 array"); return
            for i, it in enumerate(obj):
                chk(it, sc["items"], f"{path}[{i}]")
        else:
            if "const" in sc and obj != sc["const"]:
                errs.append(f"{path} 應為 {sc['const']}")
            if "enum" in sc and obj not in sc["enum"]:
                errs.append(f"{path} 值「{obj}」不在允許清單")
    chk(plan, sch, "")
    for i, o in enumerate(plan.get("observations") or []):      # values 只收數字（規格 additionalProperties: number）
        for k, x in (o.get("values") or {}).items():
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                errs.append(f"observations[{i}].values.{k} = {x!r} 不是數字")
        if not o.get("values"):
            errs.append(f"observations[{i}].values 不能是空的")
    return errs

BUY_ACTIONS, SELL_ACTIONS = {"BUY", "ADD"}, {"TRIM", "SELL_ALL"}


def ts(s):
    return datetime.fromisoformat(s) if s else None


def derive_shares(w, nav, price):
    return math.floor(w * nav / price / 1000 + 1e-9) * 1000


def active_share(port, etf):
    """兩邊各取前 AS_TOP_N 大（權重用原始比例，不重新正規化），聯集裡不在某一邊的權重當 0"""
    a = dict(sorted(port.items(), key=lambda x: -x[1])[:AS_TOP_N])
    b = dict(sorted(etf.items(), key=lambda x: -x[1])[:AS_TOP_N])
    return 0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in set(a) | set(b))


def validate(plan, state=None, strict_holdings=True, etf_top10=None):
    rej, warn, info = [], [], []

    # ---- 0. 官方 schema 結構檢查（v4.3 優先）（若 schema 檔在同目錄）----
    import os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _sp = next((x for x in (_os.path.join(_here, "D-Plan_schema_v4_3.json"), _os.path.join(_here, "D-Plan_schema_v4_2.json"))
                if _os.path.exists(x)), None)
    if _sp:
        _sch = json.load(open(_sp, encoding="utf-8"))
        rej += _schema_errors(plan, _sch)
        try:                                   # 有裝 jsonschema 就做完整檢查（格式、長度、筆數上限）
            import jsonschema
            for e in jsonschema.Draft202012Validator(_sch).iter_errors(plan):
                rej.append("規格：" + "/".join(map(str, e.absolute_path)) + " " + e.message[:150])
        except ImportError:
            info.append("沒有安裝 jsonschema，規格只做基本檢查（pip install jsonschema 可完整檢查）")
        if rej:
            return rej, warn, info

    # ---- 1. 格式 ----
    for k in REQ["top"]:
        if k not in plan:
            rej.append(f"缺少頂層欄位 {k}")
    if rej:
        return rej, warn, info
    if plan["doc_type"] != "D-Plan":
        rej.append("doc_type 必須是 D-Plan")
    for sec in ["sources", "observations", "inferences", "decisions", "no_trade_decisions", "orders"]:
        for i, item in enumerate(plan[sec]):
            for f in REQ[sec]:
                if f not in item:
                    rej.append(f"{sec}[{i}] 缺少 {f}")
    for f in REQ["market_view"]:
        if f not in plan["market_view"]:
            rej.append(f"market_view 缺少 {f}")
    for f in ["net_exposure_intent", "target_cash_pct_range"]:
        if f not in plan["market_view"].get("posture", {}):
            rej.append(f"market_view.posture 缺少 {f}")
    for f in REQ["agent_metadata"]:
        if f not in plan["agent_metadata"]:
            rej.append(f"agent_metadata 缺少 {f}")
    if rej:
        return rej, warn, info

    # ---- 2. 編號唯一與引用完整（孤兒決策 = 拒收）----
    def ids(sec, key):
        v = [x[key] for x in plan[sec]]
        dup = {x for x in v if v.count(x) > 1}
        if dup:
            rej.append(f"{sec} 編號重複：{sorted(dup)}")
        return set(v)
    S, O = ids("sources", "source_id"), ids("observations", "obs_id")
    I, D = ids("inferences", "inf_id"), ids("decisions", "decision_id")

    def check_refs(where, refs, pool, pool_name):
        for r in refs:
            if r not in pool:
                rej.append(f"{where} 引用不存在的 {pool_name} {r}（孤兒引用）")
    for o in plan["observations"]:
        check_refs(f"觀察 {o['obs_id']}", o["source_ref"], S, "來源")
    check_refs("market_view", plan["market_view"]["basis_refs"], O, "觀察")
    for x in plan["inferences"]:
        check_refs(f"推論 {x['inf_id']}", x["premise_refs"], O | I, "觀察/推論")
        c = x["confidence"]
        if c is not None and not (0 <= c <= 1):
            rej.append(f"推論 {x['inf_id']} confidence 需在 0~1")
    for d in plan["decisions"]:
        check_refs(f"決策 {d['decision_id']}", d["inference_refs"], I, "推論")
        check_refs(f"決策 {d['decision_id']} funding_for", d.get("funding_for", []), D, "決策")
    for n in plan["no_trade_decisions"]:
        check_refs(f"不交易 {n['ticker']}", n["reason_refs"], I, "推論")
    dec_by_id = {d["decision_id"]: d for d in plan["decisions"]}
    for o in plan["orders"]:
        check_refs(f"交易單 {o['ticker']}", [o["decision_ref"]], D, "決策")

    # ---- 3. 標的、整張、決策與交易單一致 ----
    for sec in ["decisions", "no_trade_decisions", "orders"]:
        for x in plan[sec]:
            if str(x["ticker"]) not in UNIVERSE:
                rej.append(f"{sec} 的 {x['ticker']} 不在 150 檔清單內")
    for o in plan["orders"]:
        if o["side"] not in ("BUY", "SELL"):
            rej.append(f"交易單 {o['ticker']} side 必須是 BUY/SELL")
        if not isinstance(o["shares"], int) or o["shares"] <= 0 or o["shares"] % 1000:
            rej.append(f"交易單 {o['ticker']} 股數 {o['shares']} 不是正的整張(1000股倍數)")
        d = dec_by_id.get(o["decision_ref"])
        if d:
            if d["ticker"] != o["ticker"]:
                rej.append(f"交易單 {o['ticker']} 對應的決策 {d['decision_id']} 是 {d['ticker']}")
            if (d["action"] in BUY_ACTIONS and o["side"] != "BUY") or (d["action"] in SELL_ACTIONS and o["side"] != "SELL"):
                warn.append(f"交易單 {o['ticker']} 方向 {o['side']} 與決策 {d['action']} 不一致")
    for d in plan["decisions"]:
        if not (0 <= d["target_weight"] <= 1):
            rej.append(f"決策 {d['decision_id']} target_weight 需在 0~1")

    # ---- 4. 時間：來源不能晚於執行完成、執行不能晚於 08:55 ----
    md = plan["agent_metadata"]
    done = ts(md["run_completed_at"])
    deadline = datetime.fromisoformat(plan["trade_date"] + "T08:55:00+08:00")
    if done and done > deadline:
        rej.append(f"執行完成時間 {md['run_completed_at']} 晚於當日 08:55")
    for s in plan["sources"]:
        f = ts(s["fetched_at"])
        if done and f and f > done:
            warn.append(f"來源 {s['source_id']} 抓取時間晚於執行完成")
        p = ts(s.get("published_at"))
        if p and f and p > f:
            warn.append(f"來源 {s['source_id']} 發布時間晚於抓取時間")

    if state is None:
        info.append("未提供組合狀態檔：略過股數公式、姿態一致性與比賽規則檢查")
        return rej, warn, info

    # ---- 5. 股數要符合官方公式 ----
    nav, cash = state["nav"], state["cash"]
    hold = {str(k): v for k, v in state["holdings"].items()}
    price = {str(k): v for k, v in state["prices"].items()}
    for o in plan["orders"]:
        d = dec_by_id.get(o["decision_ref"])
        t = o["ticker"]
        if not d or t not in price:
            if t not in price:
                warn.append(f"狀態檔缺 {t} 的價格，無法驗算股數")
            continue
        target = derive_shares(d["target_weight"], nav, price[t])
        expect = target - hold.get(t, 0)
        actual = o["shares"] if o["side"] == "BUY" else -o["shares"]
        if actual != expect:
            warn.append(f"{t} 委託 {o['side']} {o['shares']} 股 ≠ 公式推導 "
                        f"{'BUY' if expect > 0 else 'SELL'} {abs(expect)} 股（目標 {target} − 現持 {hold.get(t, 0)}）")

    # ---- 6. 宣告姿態 vs 實際淨買賣、預估現金 ----
    buy_amt = sum(o["shares"] * price.get(o["ticker"], 0) for o in plan["orders"] if o["side"] == "BUY")
    sell_amt = sum(o["shares"] * price.get(o["ticker"], 0) for o in plan["orders"] if o["side"] == "SELL")
    net = buy_amt - sell_amt
    est_cash = cash - net
    intent = plan["market_view"]["posture"]["net_exposure_intent"]
    lo, hi = plan["market_view"]["posture"]["target_cash_pct_range"]
    msg = []
    if intent == "reduce" and net > 0:
        msg.append(f"宣告 reduce 但淨買超 {net/1e4:+,.0f} 萬")
    if intent == "increase" and net < 0:
        msg.append(f"宣告 increase 但淨賣超 {net/1e4:+,.0f} 萬")
    if intent == "hold" and abs(net) > HOLD_TOL * nav:
        msg.append(f"宣告 hold 但淨買賣 {net/1e4:+,.0f} 萬（>{HOLD_TOL:.0%} 淨值）")
    if not (lo <= est_cash / nav <= hi):
        msg.append(f"預估現金 {est_cash/nav:.2%} 不在宣告區間 {lo:.0%}–{hi:.0%}")
    if msg:
        warn.append("姿態與行動不一致：" + "；".join(msg))

    # ---- 7. 比賽規則自保（主辦方會用官方資料重算）----
    new = dict(hold)
    for o in plan["orders"]:
        new[o["ticker"]] = new.get(o["ticker"], 0) + (o["shares"] if o["side"] == "BUY" else -o["shares"])
    if any(v < 0 for v in new.values()):
        rej.append("有股票賣出超過持有張數")
    held = {k: v for k, v in new.items() if v > 0}
    if strict_holdings:
        n = len(held)
        if not (20 <= n <= 30):
            rej.append(f"交易後持股 {n} 檔，不在 20–30 檔")
        covered = {d["ticker"] for d in plan["decisions"]} | {x["ticker"] for x in plan["no_trade_decisions"]}
        miss = sorted(set(hold) - covered)
        if miss:
            warn.append(f"續抱持股缺少不交易理由：{miss}")
    worst_cash = cash - buy_amt * LIMIT_UP * (1 + FEE) + sell_amt / LIMIT_UP * (1 - FEE - TAX)
    if worst_cash < 0:
        rej.append(f"最壞情況（買在漲停、賣在跌停）現金 {worst_cash/1e4:,.0f} 萬 < 0")
    if est_cash / nav >= 0.25:
        rej.append(f"預估現金 {est_cash/nav:.1%} ≥ 25%")
    bought = {o["ticker"] for o in plan["orders"] if o["side"] == "BUY"}
    for t, sh in held.items():
        w = sh * price.get(t, 0) / nav
        cap, buf = (CAP_TSMC, BUF_TSMC) if t == TSMC else (CAP_OTHER, BUF_OTHER)
        if t in bought and w > buf:
            rej.append(f"{t} 買進後權重 {w:.2%} 超過自訂上限 {buf:.1%}（規則上限 {cap:.0%}）")
        elif w > cap:
            warn.append(f"{t} 權重 {w:.2%} 已超過 {cap:.0%}（非加碼造成，5 個交易日內要減碼）")
    if etf_top10:
        vals = {t: sh * price.get(t, 0) / nav for t, sh in held.items()}
        for etf, w in etf_top10.items():
            a = active_share(vals, w)
            if a < MIN_AS:
                rej.append(f"與 {etf} 的主動投資比率 {a:.1%} < {MIN_AS:.0%}（前 {AS_TOP_N} 大）")
        info.append(f"主動投資比率：已比對 {len(etf_top10)} 檔主動型 ETF 前 {AS_TOP_N} 大")
    else:
        info.append(f"未提供主動型ETF前{AS_TOP_N}大資料：略過主動投資比率檢查")
    return rej, warn, info


def main():
    plan = json.load(open(sys.argv[1], encoding="utf-8"))
    state = json.load(open(sys.argv[2], encoding="utf-8")) if len(sys.argv) > 2 else None
    strict = "--sample" not in sys.argv
    rej, warn, info = validate(plan, state, strict_holdings=strict)
    print(f"檢查 {sys.argv[1]}")
    print(f"【拒收 {len(rej)} 項】"); [print("  ✗", x) for x in rej]
    print(f"【警告 {len(warn)} 項】"); [print("  !", x) for x in warn]
    for x in info: print("  ·", x)
    print("結論：", "不可提交" if rej else ("可提交，但有警告" if warn else "通過"))
    return 1 if rej else 0


if __name__ == "__main__":
    sys.exit(main())