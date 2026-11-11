# -*- coding: utf-8 -*-
"""
esun_ledger.py — 玉山挑戰賽帳本（照主辦方算法記帳）

  · 成交：當日均價（VWAP），全數成交
  · 手續費 0.1425%（買賣都收）、證交稅 0.3%（僅賣出），從現金扣
  · NAV = Σ 持股股數 × 當日收盤價 + 現金
  · 配息：比賽期間不進現金，另外累計，比賽結束才加回（辦法六(十二)註1）
  · 配股：除權當天不加進持股，另外累計股數，初賽結束時以最後一天收盤價計入總資產
    （主辦方 Slack 2026/10/09 回覆）
  · 超過持股上限（主辦方 Slack 2026/10/09 回覆）：
      下單時（以前一日收盤估算）未超過上限，之後因上漲在結算時超過 → 視為「價格波動」，
      有 5 個交易日可以調整；減到上限以下後再次超過，重新起算 5 天。
      只有下單時估算就已超過上限，才算違規。
  · 以官方為準：比賽開始後，每天用排行榜的官方淨值表對帳，差太多就改用官方數字
狀態存在 esun_state/state_YYYY-MM-DD.json，一天一份，方便追查。
代號一律用 4 碼字串（如 "2330"），與主辦方 JSON 一致。
"""
import json, os, copy

STATE_DIR = "./esun_state"
INIT_CASH = 1_000_000_000
FEE, TAX = 0.001425, 0.003
TSMC, CAP_TSMC, CAP_OTHER = "2330", 0.25, 0.10
NAV_TOL = 0.0001          # 對帳容許誤差：淨值差 0.01% 以內視為一致


def new_state(date):
    return {"date": date, "cash": float(INIT_CASH), "holdings": {}, "close": {},
            "nav": float(INIT_CASH), "accrued_dividends": 0.0, "accrued_stock": {},
            "over_cap": {}, "source": "init", "log": []}


def apply_trades(state, orders, vwap, date):
    """orders: [{"ticker","side","shares"}]；vwap: {代號: 當日均價}。回傳新狀態（不改原本的）。"""
    s = copy.deepcopy(state)
    s["date"], s["source"], s["log"] = date, "estimated", []
    for o in orders:
        t, sh, side = str(o["ticker"]), int(o["shares"]), o["side"]
        if t not in vwap or not vwap[t] > 0:
            raise ValueError(f"{t} 沒有當日均價，無法記帳")
        amt = sh * vwap[t]
        fee = amt * FEE
        if side == "BUY":
            s["cash"] -= amt + fee
            s["holdings"][t] = s["holdings"].get(t, 0) + sh
            s["log"].append(f"買 {t} {sh}股 @{vwap[t]:.2f} 手續費{fee:,.0f}")
        elif side == "SELL":
            have = s["holdings"].get(t, 0)
            if sh > have:
                raise ValueError(f"{t} 賣出 {sh} 股，但只持有 {have} 股")
            tax = amt * TAX
            s["cash"] += amt - fee - tax
            s["holdings"][t] = have - sh
            if s["holdings"][t] == 0:
                del s["holdings"][t]
            s["log"].append(f"賣 {t} {sh}股 @{vwap[t]:.2f} 手續費{fee:,.0f} 證交稅{tax:,.0f}")
        else:
            raise ValueError(f"未知的 side：{side}")
    return s


def mark_to_market(state, close):
    """用當日收盤價算淨值。close: {代號: 收盤價}。"""
    missing = [t for t in state["holdings"] if t not in close]
    if missing:
        raise ValueError(f"缺收盤價：{missing}")
    state["close"] = {t: close[t] for t in state["holdings"]}
    state["nav"] = sum(sh * close[t] for t, sh in state["holdings"].items()) + state["cash"]
    return state


def add_dividend(state, ticker, cash_per_share):
    """除息日呼叫：配息累計在旁邊，不進現金。"""
    amt = state["holdings"].get(str(ticker), 0) * cash_per_share
    state["accrued_dividends"] = state.get("accrued_dividends", 0.0) + amt
    state["log"].append(f"{ticker} 除息，累計配息 {amt:,.0f}（比賽結束才加回）")
    return state


def add_stock_dividend(state, ticker, shares_per_share):
    """除權日呼叫：配股不加進持股（也就不會出現零股），股數另外累計，初賽結束才計入。
    shares_per_share：每股配發股數，例如每股配 0.5 元股票股利（面額 10 元）＝ 0.05。"""
    t = str(ticker)
    sh = state["holdings"].get(t, 0) * shares_per_share
    acc = state.setdefault("accrued_stock", {})
    acc[t] = acc.get(t, 0.0) + sh
    state["log"].append(f"{t} 除權，累計配股 {sh:,.1f} 股（不進持股，初賽結束以最後收盤價計入）")
    return state


def final_value(state, close):
    """初賽結束時的總資產：淨值 ＋ 累計配息 ＋ 累計配股 × 最後一天收盤價。"""
    stock = sum(sh * close[t] for t, sh in state.get("accrued_stock", {}).items())
    return state["nav"] + state.get("accrued_dividends", 0.0) + stock


def check_rules(state, bought_today=(), planned=None):
    """收盤後自我檢查，比照辦法六(十四)與主辦方 2026/10/09 回覆。回傳 (違規, 提醒)。
    planned：{代號: 下單時估算的交易後權重}（可省略）。有給且估算時就超過上限 → 違規；
    其餘超過上限一律視為價格波動，記在 state["over_cap"] 倒數 5 個交易日。
    （我們的交易單本來就不會把目標權重設超過上限，檢查器也會擋，所以加碼日超標幾乎都是價格波動。）"""
    bad, note = [], []
    planned = planned or {}
    prev_over = state.get("over_cap", {})
    over = {}
    nav = state["nav"]
    n = len(state["holdings"])
    if not 20 <= n <= 30:
        bad.append(f"持股 {n} 檔，不在 20–30 檔")
    if state["cash"] < 0:
        bad.append(f"現金為負：{state['cash']:,.0f}")
    elif state["cash"] / nav >= 0.25:
        bad.append(f"現金占淨值 {state['cash']/nav:.1%}，不能 ≥ 25%")
    for t, sh in state["holdings"].items():
        w = sh * state["close"][t] / nav
        cap = CAP_TSMC if t == TSMC else CAP_OTHER
        if w <= cap:
            continue
        if t in bought_today and planned.get(t, 0) > cap:
            bad.append(f"{t} 下單時估算權重 {planned[t]:.2%} 就超過 {cap:.0%}")
            continue
        p = prev_over.get(t)
        if p and p.get("last") == state["date"]:          # 同一天重跑：不重複計天數
            rec = p
        elif p:
            rec = {"since": p["since"], "days": p["days"] + 1, "last": state["date"]}
        else:
            rec = {"since": state["date"], "days": 1, "last": state["date"]}
        over[t] = rec
        if rec["days"] > 5:
            bad.append(f"{t} 權重 {w:.2%} 超過 {cap:.0%}，自 {rec['since']} 起已 {rec['days']} 個交易日未減碼")
        else:
            note.append(f"{t} 權重 {w:.2%} 因價格波動超過 {cap:.0%}（第 {rec['days']}/5 個交易日，"
                        f"自 {rec['since']} 起），期限內減碼即可")
    state["over_cap"] = over                                # 減到上限以下就移除，下次超過重新起算
    return bad, note


def reconcile(state, official):
    """official: {"nav","cash","holdings"} 來自排行榜淨值表。差異超過容許值就改用官方。"""
    diffs = []
    if abs(state["nav"] - official["nav"]) > NAV_TOL * official["nav"]:
        diffs.append(f"淨值 自算 {state['nav']:,.0f} vs 官方 {official['nav']:,.0f}")
    if abs(state["cash"] - official["cash"]) > NAV_TOL * official["nav"]:
        diffs.append(f"現金 自算 {state['cash']:,.0f} vs 官方 {official['cash']:,.0f}")
    oh = {str(k): int(v) for k, v in official["holdings"].items()}
    for t in set(state["holdings"]) | set(oh):
        if state["holdings"].get(t, 0) != oh.get(t, 0):
            diffs.append(f"{t} 持股 自算 {state['holdings'].get(t, 0)} vs 官方 {oh.get(t, 0)}")
    if diffs:
        state["nav"], state["cash"], state["holdings"] = official["nav"], official["cash"], oh
        state["source"] = "official"
    else:
        state["source"] = "matched"
    state["log"].append("對帳：" + ("一致" if not diffs else "改用官方數字｜" + "；".join(diffs)))
    return state, diffs


def to_validator_state(state, prices):
    """轉成 dplan_validator 需要的狀態格式。prices：下單用的價格（例如前一日收盤）。"""
    return {"nav": state["nav"], "cash": state["cash"],
            "holdings": dict(state["holdings"]), "prices": dict(prices)}


def save(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"state_{state['date']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return path


def load_latest(before=None):
    """讀取最新（或某日期之前最新）的狀態；沒有就回傳 None。"""
    if not os.path.exists(STATE_DIR):
        return None
    files = sorted(f for f in os.listdir(STATE_DIR) if f.startswith("state_"))
    if before:
        files = [f for f in files if f[6:16] < before]
    if not files:
        return None
    with open(os.path.join(STATE_DIR, files[-1]), encoding="utf-8") as f:
        return json.load(f)