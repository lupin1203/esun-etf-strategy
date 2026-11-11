# -*- coding: utf-8 -*-
"""
esun_strategy_doc.py — 由 AI Agent（Claude）產生「ETF 投資策略說明文件」並填入官方範本

  · 策略內容不是手寫的：程式直接讀 esun_strategy.py 的實際參數（產業、權重、因子、過濾條件）組成說明，
    交給 Claude 撰寫名稱、主題、理念 → 確保宣告與每天的交易一致
  · 程式檢查官方限制，不合格就把原因回饋給 Claude 重寫（最多 4 次）：
      名稱 ≤ 20 字、開頭為「主動」、只能中文或英文；主題 ≤ 50 字；理念 100–300 字
  · 填入官方 Word 範本（標楷體 9.5 號），另存一份產製紀錄（模型、時間、提示詞、回覆、用量）備查
  · API 金鑰放在 anthropic_config.json（不是 esun_ 開頭，版本存檔不會打包）：
      {"api_key": "sk-ant-...", "model": "claude-sonnet-5-5"}
用法：python esun_strategy_doc.py 範本.docx
"""
import os, re, sys, json
from datetime import datetime, timezone, timedelta
import requests

import esun_strategy as S
import esun_orders as O

CONFIG = "anthropic_config.json"
OUT_DIR = "./esun_docs"
TZ = timezone(timedelta(hours=8))
API = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TRY = 4


def strategy_facts():
    """從程式的實際參數組出策略說明（不是手寫的宣傳文字）"""
    excl = "、".join(sorted(S.EXCLUDE_IND)) or "無"
    scope = ("只限制尚未持有的股票；已持有的股票（包括小部位）不受過濾限制，不會因此賣出，也可依綜合排名加碼或升級為核心"
             if S.FILTER_SCOPE == "new_only" else "已持有的不通過也會賣出")
    return f"""【選股範圍】比賽指定的 150 檔，不限產業，但排除{excl}（運價受外部衝擊影響大，營收與獲利的暴增不代表可持續的成長，而本策略的營收與 EPS 評分容易在運價高點給出高分）；20 日均量 {S.MIN_VOL_LOTS} 張以上。台積電另外處理，不參加排名。
【綜合評分（三項百分位排名平均）】
1. 營收驚喜：最新月營收年增率，相較前三個月平均年增率的加速幅度
2. EPS 增幅：最新一季 EPS 較去年同季的增加金額除以股價
3. 三率：毛利率、營業利益率、稅後淨利率較上一季沒有下跌的項數
【進場過濾（{scope}）】
- 5、10、20 日均線三條全部下彎時，不買進尚未持有的股票
- 個股出現爆量下跌（單日跌 3% 以上且成交量達 20 日均量 2 倍）後，須出現量能更大的上漲日、確認賣壓消化，才買進尚未持有的股票
- 三大法人連續 3 個交易日賣超時，不買進尚未持有的股票
【組合結構】
- 台積電固定約 {S.W_TSMC:.0%}，刻意低於比賽上限 25%，以與市值型配置拉開差異
- 核心 {S.N_CORE} 檔、各約 {S.W_CORE:.1%}；其中記憶體相關個股最多 {S.MAX_MEMORY_CORE} 檔、同一產業最多 {S.MAX_INDUSTRY_CORE} 檔，避免組合過度集中於單一景氣循環
- 小部位 {S.N_SMALL} 檔、各約 {S.W_SMALL:.0%}；現金約 3%
【投資目標與風險屬性】
- 積極型：核心股集中持有、台積電低配；選股跨產業，與大盤連動程度低於市值型配置，但個股波動較大
- 目標是在比賽期間追求優於大盤的超額報酬，同時遵守單一持股與現金比例限制；不是追求穩健或低波動
【換股與風控】
- 每個交易日收盤後依最新資料重新評分（月營收、季報公布後資料更新，股價與進場過濾條件每天變動）；已是核心的股票，排名仍在前 {S.CORE_KEEP_RANK} 名就保留，減少不必要的換手
- 權重偏離目標不大時不交易；但預估現金比目標多 2 個百分點以上（或達 {O.CASH_REBAL:.1%}）時，當天所有持股一律調回目標權重，避免現金閒置
- 單一持股漲超過 10% 上限時一律減碼
- 下單前以漲停價估算最壞情況現金，確保現金不為負
【例外處理】
- 通過進場過濾的股票不足以湊滿核心與小部位時（例如大跌日建倉），依綜合排名用未通過過濾的股票補足，確保持股至少 20 檔
- 防守模式：整個選股池通過過濾的少於 {S.DEF_MAX_PASS + 1} 檔，視為全市場行情偏弱，台積電提高到 {S.W_TSMC_DEF:.0%}、
  核心各降為 {S.W_CORE_DEF:.3%}、小部位不變、現金約 {S.CASH_DEF:.0%}；通過過濾超過 {S.DEF_EXIT_PASS} 檔才恢復一般配置
【差異化】
- 持股以營收與獲利加速的公司為主（記憶體、電腦週邊、金融等），與市面主動式 ETF 常見的 AI 伺服器、IC 設計龍頭重疊少"""


SYSTEM = """你是台灣主動式 ETF 的產品經理，要依照基金經理人提供的「實際投資規則」撰寫 ETF 投資策略說明文件。
只能描述規則裡真的有的內容，不可以加入規則沒有的選股方法、不可以承諾報酬、不可以誇大。
寫好後「一定要」呼叫 submit_strategy_doc 工具提交三個欄位（name、theme、philosophy），不要只用文字回覆。"""

TOOL = {"name": "submit_strategy_doc", "description": "提交 ETF 投資策略說明文件的三個欄位",
        "input_schema": {"type": "object", "required": ["name", "theme", "philosophy"],
                         "properties": {"name": {"type": "string", "description": "ETF 名稱"},
                                        "theme": {"type": "string", "description": "投資主題"},
                                        "philosophy": {"type": "string", "description": "投資理念"}}}}


def user_prompt(feedback=None):
    p = f"""請依下列實際投資規則撰寫三個欄位。

{strategy_facts()}

官方格式限制（必須全部符合）：
- name：20 字以內，開頭必須是「主動」兩個字，只能使用中文或英文字母（不可有數字、標點、符號）
- theme：50 字以內，一句話說明主要投資方向或核心策略
- philosophy：100～300 字，說明為何選擇此主題、主要投資邏輯（要提到三項評分因子與進場過濾的精神）、希望追求的投資目標
- philosophy 必須用一句話提到防守模式：全市場轉弱時提高台積電權重、保留較多現金（主辦單位會比對防守期間的交易）
- 提到進場過濾時要寫清楚適用範圍：只擋「尚未持有」的股票；已持有的股票（包括 1% 的小部位）不受過濾限制，可以加碼或升級為核心（主辦單位會看到均線下彎時仍加碼已持有股票的交易）
- 主辦單位會檢查每天的交易是否與這份文件一致，所以內容必須完全對得上上面的規則
- 用字要通順、使用一般常見的說法，不要自創詞語；不要用「穩健」「低波動」「長期持有」這類與集中、積極的實際做法不符的形容"""
    if feedback:
        p += "\n\n上一版不符合以下限制，請修正後重新輸出完整 JSON：\n- " + "\n- ".join(feedback)
    return p


def count(s):
    return len(re.sub(r"\s", "", s or ""))


def check(d):
    bad = []
    n, t, ph = d.get("name", ""), d.get("theme", ""), d.get("philosophy", "")
    if not n.startswith("主動"):
        bad.append(f"name 開頭必須是「主動」（目前：{n}）")
    if count(n) > 20:
        bad.append(f"name 超過 20 字（目前 {count(n)} 字）")
    if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z ]+", n or ""):
        bad.append(f"name 只能有中文或英文字母（目前：{n}）")
    if not t or count(t) > 50:
        bad.append(f"theme 必須 1～50 字（目前 {count(t)} 字）")
    if not 100 <= count(ph) <= 300:
        bad.append(f"philosophy 必須 100～300 字（目前 {count(ph)} 字）")
    if S.FILTER_SCOPE == "new_only" and ph and not ("已持有" in ph and ("加碼" in ph or "升級" in ph)):
        bad.append("philosophy 提到進場過濾時，必須用一句話寫明「已持有的股票（含小部位）不受過濾限制，可加碼或升級為核心」，"
                   "不能只寫尚未持有的會被擋")
    return bad


def call_claude(cfg, messages):
    r = requests.post(API, timeout=120, headers={"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01",
                                                "content-type": "application/json"},
                      json={"model": cfg.get("model", DEFAULT_MODEL), "max_tokens": 1500, "system": SYSTEM,
                            "messages": messages, "tools": [TOOL], "tool_choice": {"type": "auto"}})
    if not r.ok:
        raise RuntimeError(f"Claude API 錯誤 {r.status_code}：{r.text[:200]}")
    j = r.json()
    return j.get("content", []), j.get("usage", {}), j.get("model")


def parse_json(text):
    t = text.replace("```json", "").replace("```", "")
    i = t.find("{")
    return json.JSONDecoder().raw_decode(t[i:])[0] if i >= 0 else {}


def save_log(log, msgs, tag):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"產製紀錄_{tag}_{datetime.now(TZ):%Y%m%d_%H%M}.json")
    json.dump({"attempts": log, "messages": msgs, "strategy_facts": strategy_facts()},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return path


def generate():
    cfg = json.load(open(CONFIG, encoding="utf-8-sig"))
    log, msgs = [], [{"role": "user", "content": user_prompt()}]
    for k in range(1, MAX_TRY + 1):
        content, usage, model = call_claude(cfg, msgs)
        msgs.append({"role": "assistant", "content": content})
        tu = next((b for b in content if b.get("type") == "tool_use"), None)
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        if tu:
            d = tu.get("input", {})
        else:
            try:
                d = parse_json(text)
            except Exception as e:
                d = {}
        bad = check(d) if d else ["沒有用工具提交，也找不到有效的 JSON"]
        log.append({"try": k, "at": datetime.now(TZ).isoformat(timespec="seconds"), "model": model,
                    "usage": usage, "result": d, "raw_text": text, "problems": bad})
        print(f"第 {k} 次：{'通過' if not bad else '不符合 → ' + '；'.join(bad)}")
        if not bad:
            return d, log, msgs
        if not tu and text:
            print(f"    （Claude 回覆開頭：{text[:120]}）")
        fb = "上一版不符合以下限制，請修正後用 submit_strategy_doc 工具重新提交：\n- " + "\n- ".join(bad)
        if tu:
            msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu["id"],
                                                      "content": fb, "is_error": True}]})
        else:
            msgs.append({"role": "user", "content": fb})
    path = save_log(log, msgs, "失敗")
    raise RuntimeError(f"重試 {MAX_TRY} 次仍不符合，最後問題：{bad}｜完整紀錄：{path}")


def fill(template, d, out_path):
    from docx import Document
    from docx.shared import Pt
    from docx.oxml.ns import qn
    doc = Document(template)
    rows = doc.tables[0].rows
    for row, text in zip(rows, (d["name"], d["theme"], d["philosophy"])):
        cell = row.cells[1]
        p = cell.paragraphs[0]
        for r in p.runs:
            r.text = ""
        run = p.add_run(text)
        run.font.name, run.font.size = "標楷體", Pt(9.5)
        run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "標楷體")
    doc.save(out_path)


def main(template):
    os.makedirs(OUT_DIR, exist_ok=True)
    d, log, msgs = generate()
    stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M")
    team = getattr(__import__("esun_report"), "TEAM_ID", "TEAM")
    out = os.path.join(OUT_DIR, f"ETF投資策略說明文件_{team}_{stamp}.docx")
    fill(template, d, out)
    rec = os.path.join(OUT_DIR, f"產製紀錄_{stamp}.json")
    json.dump({"generated_at": datetime.now(TZ).isoformat(timespec="seconds"), "result": d,
               "strategy_facts": strategy_facts(), "attempts": log, "messages": msgs,
               "code_version": __import__("esun_version").archive()},
              open(rec, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nETF 名稱（{count(d['name'])} 字）：{d['name']}")
    print(f"投資主題（{count(d['theme'])} 字）：{d['theme']}")
    print(f"投資理念（{count(d['philosophy'])} 字）：{d['philosophy']}")
    print(f"\n已輸出 {out}\n產製紀錄 {rec}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python esun_strategy_doc.py 官方範本.docx"); sys.exit(1)
    main(sys.argv[1])