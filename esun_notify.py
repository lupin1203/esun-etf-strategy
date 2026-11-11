# -*- coding: utf-8 -*-
"""
esun_notify.py — Telegram 通知
  · token 與 chat_id 放在 telegram_config.json（不是 esun_ 開頭，版本存檔不會打包進去）
      {"bot_token": "你的 token", "chat_id": "你的 chat id"}
  · 沒有設定檔就安靜略過，不影響每日流程
  · 任何輸出都不會印出 token
用法：python esun_notify.py   （逐步檢查設定並送出測試訊息）
"""
import os, json, requests

CONFIG = "telegram_config.json"


def _load():
    return json.load(open(CONFIG, encoding="utf-8-sig"))


def send(text, verbose=False):
    say = print if verbose else (lambda *a: None)
    if not os.path.exists(CONFIG):
        say(f"✗ 找不到 {CONFIG}（目前資料夾：{os.getcwd()}）"); return False
    try:
        c = _load()
    except Exception as e:
        say(f"✗ {CONFIG} 格式錯誤，不是正確的 JSON：{type(e).__name__}：{str(e)[:80]}"); return False
    tok, cid = str(c.get("bot_token", "")).strip(), str(c.get("chat_id", "")).strip()
    if not tok or not cid:
        say(f"✗ 缺少欄位，目前有的欄位：{list(c.keys())}（要剛好是 bot_token 與 chat_id）"); return False
    say(f"✓ 設定檔讀取成功｜token 長度 {len(tok)}、{'有' if ':' in tok else '沒有'}冒號｜chat_id：{cid}")
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": cid, "text": text[:4000]}, timeout=15)
    except Exception as e:
        say(f"✗ 連不上 Telegram：{type(e).__name__}"); print(f"  ! Telegram 通知失敗：{type(e).__name__}"); return False
    if r.ok:
        say("✓ Telegram 回應成功"); return True
    try:
        desc = r.json().get("description", "")
    except Exception:
        desc = r.text[:100].replace(tok, "***")
    msg = f"✗ Telegram 拒絕（{r.status_code}）：{desc}"
    say(msg)
    if not verbose:
        print(f"  ! Telegram 通知失敗（{r.status_code}）：{desc}")
    return False


if __name__ == "__main__":
    ok = send("【玉山】通知測試：收到這則就代表設定成功", verbose=True)
    print("\n測試訊息已送出，請看手機" if ok else "\n沒有送出，照上面 ✗ 那行的原因處理")