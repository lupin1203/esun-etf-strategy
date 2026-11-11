# -*- coding: utf-8 -*-
"""
esun_orders.py — 目標權重 → 交易單（照主辦方公式），並確保不違規

  · 股數公式（主辦方範例）：目標股數 = floor(目標權重 × 淨值 ÷ 股價 ÷ 1000) × 1000，交易 = 目標 − 現持
    淨值、股價暫定用前一交易日收盤（等主辦方確認）
  · 無交易帶：|目前權重 − 目標權重| < min(1.5%, 目標權重的一半) → 不交易，寫入不交易理由
    例外：權重已超過 9.9%（台積電 24.9%）時一律減回目標（辦法六(十四)註3：漲超上限須 5 日內減碼）
  · 不在目標裡的持股 → 全部賣出
  · 目標權重 > 0 但捨去後是 0 張 → 調成剛好 1 張（避免持股檔數掉到 20 以下）
  · 最壞情況現金（買在漲停 +10%、賣在跌停 −10%，含手續費與證交稅）必須 >= 淨值 0.5%
    不夠時：先把小部位的買進縮到各 1 張；還不夠，才等比例縮減核心與台積電的買進
  · 交易後持股必須 20–30 檔，否則丟出錯誤（由上層改送「全部續抱」）
  · 只能整股交易：現持若含零股（例如配股），委託取整張，零股保留不動
  · 【2026-11-10 起】有買進的股票，買完後權重最多 9.0%（台積電 22.5%）；已達上限就不再加碼。
    主辦方用「成交日收盤價」算加碼後權重，11/10 台玻、欣興加碼到 9.5% 後當天漲停，被算成 10.2%／10.1%，
    整批委託作廢並記警告一次。留 1% 緩衝：就算當天漲停 +10%，也只到約 9.9%。只限制買進，不影響賣出與選股。
  · 預估現金比目標多 2% 以上，或 >= 23.5% 時，當天不套用無交易帶，全部調回目標
    （例如：第一天建倉沒買足、防守模式切回一般、防守模式下持股下跌使現金比重被動變高而逼近 25%）
"""
import math

FEE, TAX = 0.001425, 0.003
LIMIT = 0.10
BAND, BAND_FRAC = 0.015, 0.5
MIN_CASH_BUFFER = 0.005
CASH_REBAL = 0.235
MIN_HOLD, MAX_HOLD = 20, 30
TSMC, CAP_TSMC, CAP_OTHER, CAP_TRIGGER_MARGIN = "2330", 0.25, 0.10, 0.001
BUY_CAP_TSMC, BUY_CAP_OTHER = 0.225, 0.09     # 買進後權重上限（留漲停緩衝），見上方說明


def buy_cap(t):
    return BUY_CAP_TSMC if t == TSMC else BUY_CAP_OTHER


def lots(w, nav, price):
    return math.floor(w * nav / price / 1000 + 1e-9) * 1000


def one_lot_weight(nav, price):
    return 1000 * price / nav * 1.0001


def worst_cash(cash, trades, price):
    buy = sum(q * price[t] for t, q in trades.items() if q > 0)
    sell = sum(-q * price[t] for t, q in trades.items() if q < 0)
    return cash - buy * (1 + LIMIT) * (1 + FEE) + sell * (1 - LIMIT) * (1 - FEE - TAX)


def make_orders(targets, state, price, band=None):
    """targets：esun_strategy 的輸出（weights, roles）；state：{"nav","cash","holdings"}；price：{代號: 參考價}"""
    band = BAND if band is None else band
    nav, cash = state["nav"], state["cash"]
    hold = {str(k): int(v) for k, v in state["holdings"].items() if int(v) > 0}
    tw = {str(k): float(v) for k, v in targets["weights"].items()}
    roles = dict(targets.get("roles", {}))
    names = sorted(set(tw) | set(hold))
    missing = [t for t in names if not price.get(t)]
    if missing:
        raise ValueError(f"缺參考價：{missing}")

    cur_w = {t: hold.get(t, 0) * price[t] / nav for t in names}
    plan, no_trade = {}, []
    for t in names:
        w = tw.get(t, 0.0)
        if w > 0 and lots(w, nav, price[t]) == 0:
            w = one_lot_weight(nav, price[t])
        cap = CAP_TSMC if t == TSMC else CAP_OTHER
        over_cap = cur_w[t] > cap - CAP_TRIGGER_MARGIN
        if t in hold and w > 0 and not over_cap and abs(cur_w[t] - w) < min(band, BAND_FRAC * w):
            no_trade.append({"ticker": t, "reason": "band", "current_weight": cur_w[t], "target_weight": w})
            continue
        if w > cur_w[t]:                                       # 買進：買完後權重不超過 buy_cap（已達上限就不加碼）
            w = cur_w[t] if cur_w[t] >= buy_cap(t) else min(w, buy_cap(t))
        plan[t] = w

    def whole(q):
        """委託取整張（朝 0 捨去），零股不交易"""
        return int(math.copysign(abs(q) // 1000 * 1000, q)) if q else 0

    def trades_of(p):
        return {t: whole(lots(w, nav, price[t]) - hold.get(t, 0)) for t, w in p.items()}

    notes = []
    tr = trades_of(plan)
    if worst_cash(cash, tr, price) < MIN_CASH_BUFFER * nav:
        for t in list(plan):                                   # 第一步：小部位買進縮到 1 張（或不加碼）
            if roles.get(t) == "小部位" and tr[t] > 0:
                plan[t] = cur_w[t] if t in hold else one_lot_weight(nav, price[t])
        tr = trades_of(plan)
        notes.append("現金不足：小部位買進已縮減")
    f = 1.0
    while worst_cash(cash, tr, price) < MIN_CASH_BUFFER * nav and f > 0.05:
        f *= 0.95                                              # 第二步：核心／台積電買進等比例縮減
        for t in list(plan):
            if roles.get(t) in ("核心", "台積電") and tw.get(t, 0) > cur_w[t]:
                plan[t] = min(cur_w[t] + f * (tw[t] - cur_w[t]), max(cur_w[t], buy_cap(t)))
        tr = trades_of(plan)
    if f < 1.0:
        notes.append(f"現金不足：核心買進縮為原本的 {f:.0%}")

    decisions, orders = [], []
    for t, w in sorted(plan.items()):
        tgt, cur = lots(w, nav, price[t]), hold.get(t, 0)
        q = whole(tgt - cur)
        odd = cur % 1000
        if q == 0:
            no_trade.append({"ticker": t, "reason": "at_target", "current_weight": cur_w[t], "target_weight": w})
            continue
        action = ("ADD" if cur > 0 else "BUY") if q > 0 else ("SELL_ALL" if cur + q == 0 else "TRIM")
        # 報告上寫的權重取「該張數的正中間」，前後各留半張緩衝，避免主辦方算法的小數誤差讓捨去結果不同
        w_out = 0.0 if tgt == 0 else round((tgt / 1000 + 0.5) * 1000 * price[t] / nav, 6)
        assert lots(w_out, nav, price[t]) == tgt, (t, w_out, tgt)
        decisions.append({"ticker": t, "action": action, "target_weight": w_out,
                          "_role": roles.get(t, "出清" if w == 0 else ""), "_current_weight": cur_w[t]})
        orders.append({"ticker": t, "side": "BUY" if q > 0 else "SELL", "shares": abs(q),
                       "derivation_detail": f"target_shares = floor({w_out:.6f}×{nav:,.0f} / {price[t]:g} / 1000)×1000"
                                            f" = {tgt:,}；現持 {cur:,} → {'BUY' if q > 0 else 'SELL'} {abs(q):,}"
                                            + (f"（現持含零股 {odd} 股，零股不可交易，委託取整張）" if odd else "")})

    after = {t: hold.get(t, 0) + tr.get(t, 0) for t in names if t in plan}
    after.update({x["ticker"]: hold[x["ticker"]] for x in no_trade if x["ticker"] in hold})
    n_after = sum(1 for v in after.values() if v > 0)
    buy_amt = sum(o["shares"] * price[o["ticker"]] for o in orders if o["side"] == "BUY")
    sell_amt = sum(o["shares"] * price[o["ticker"]] for o in orders if o["side"] == "SELL")
    summary = {"holdings_after": n_after, "buy_amt": buy_amt, "sell_amt": sell_amt,
               "est_cash_pct": (cash - buy_amt * (1 + FEE) + sell_amt * (1 - FEE - TAX)) / nav,
               "worst_cash_pct": worst_cash(cash, {o["ticker"]: (o["shares"] if o["side"] == "BUY" else -o["shares"])
                                                   for o in orders}, price) / nav,
               "notes": notes}
    ct = targets.get("cash_target")
    if band > 0 and (summary["est_cash_pct"] >= CASH_REBAL or
                     (ct is not None and summary["est_cash_pct"] > ct + 0.02)):   # 現金偏高：不套用無交易帶，全部調回目標
        d2, o2, n2, s2 = make_orders(targets, state, price, band=0.0)
        if [(o["ticker"], o["shares"]) for o in o2] != [(o["ticker"], o["shares"]) for o in orders]:
            s2["notes"] = s2["notes"] + [f"預估現金 {summary['est_cash_pct']:.1%} 偏高，本日不套用無交易帶"]
            return d2, o2, n2, s2
    if not MIN_HOLD <= n_after <= MAX_HOLD:
        raise ValueError(f"交易後持股 {n_after} 檔，不在 {MIN_HOLD}–{MAX_HOLD} 檔")
    if summary["est_cash_pct"] >= 0.25:
        raise ValueError(f"交易後預估現金 {summary['est_cash_pct']:.1%}，不能 >= 25%")
    return decisions, orders, no_trade, summary