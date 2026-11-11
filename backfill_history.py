# -*- coding: utf-8 -*-
"""
backfill_history.py — 全市場K線補到2011【正式版】
方案一：全重抓 + 慢抓 + 斷點續傳 + 開跑前備份。

安全機制：
  1. 開跑前自動備份 local_data → local_data_backup
  2. 斷點續傳：已完成的記在 backfill_done.txt，再跑會跳過，可分次跑完
  3. 慢抓：每檔隨機 sleep，避免 yfinance 限流
  4. 邊補邊抽查：異常跳動記到 backfill_warnings.txt
出事復原：刪掉 local_data，把 local_data_backup 改名回 local_data 即可。
"""
import os, glob, time, random, shutil
import yfinance as yf
import pandas as pd

DATA_DIR = "./local_data"
BACKUP_DIR = "./local_data_backup"
DONE_FILE = "backfill_done.txt"
WARN_FILE = "backfill_warnings.txt"
START = "2011-01-01"

def backup_once():
    """開跑前備份一次。若備份已存在（代表之前跑過），不覆蓋，保留最原始的備份。"""
    if not os.path.exists(BACKUP_DIR):
        print(f"備份 {DATA_DIR} → {BACKUP_DIR} ...")
        shutil.copytree(DATA_DIR, BACKUP_DIR)
        print("  備份完成。出事就把 local_data_backup 改名回 local_data 復原。\n")
    else:
        print(f"（{BACKUP_DIR} 已存在，沿用原始備份，不覆蓋）\n")

def load_done():
    if os.path.exists(DONE_FILE):
        with open(DONE_FILE, encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def mark_done(ticker):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(ticker + "\n")

def mark_warn(msg):
    with open(WARN_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def backfill_one(ticker):
    """重抓單一檔 2011~今天，整檔覆蓋。回傳 (成功, 訊息)。"""
    try:
        df = yf.Ticker(ticker).history(start=START, auto_adjust=True)
        if df.empty:
            return False, "抓不到"
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        cols = [c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]
        df = df[cols].sort_index()
        df = df.dropna(subset=["Close"])
        if df.empty:
            return False, "清理後空白"

        # 抽查：單日跳動>40%
        jumps = df["Close"].pct_change().abs()
        n_jump = (jumps > 0.40).sum()

        # 整檔覆蓋
        df.to_parquet(os.path.join(DATA_DIR, f"{ticker}.parquet"))

        msg = f"{df.index.min().date()}~{df.index.max().date()} {len(df)}筆"
        if n_jump > 0:
            mark_warn(f"{ticker}: 單日跳動>40% 有 {n_jump} 處（需人工確認是否除權息髒資料）")
            msg += f" ⚠跳動{n_jump}處"
        return True, msg
    except Exception as e:
        return False, f"錯誤:{e}"

def main():
    if not os.path.exists(DATA_DIR):
        print(f"[X] 找不到 {DATA_DIR}")
        return

    backup_once()

    # 取得所有要補的代號（從現有 local_data 檔名）
    files = glob.glob(os.path.join(DATA_DIR, "*.parquet"))
    all_tickers = [os.path.basename(f).replace(".parquet","") for f in files]
    done = load_done()
    todo = [t for t in all_tickers if t not in done]

    print(f"全市場 {len(all_tickers)} 檔｜已完成 {len(done)} 檔｜本次要補 {len(todo)} 檔")
    print("（中途斷了沒關係，再跑一次會自動接著補）\n")

    ok, fail = 0, 0
    for i, ticker in enumerate(todo, 1):
        success, msg = backfill_one(ticker)
        if success:
            ok += 1
            mark_done(ticker)
            print(f"[{i}/{len(todo)}] ✓ {ticker}  {msg}")
        else:
            fail += 1
            print(f"[{i}/{len(todo)}] ✗ {ticker}  {msg}")
        time.sleep(random.uniform(0.3, 0.8))  # 慢抓，避免限流

    print(f"\n{'='*50}")
    print(f"本次完成：成功 {ok}｜失敗 {fail}｜累計完成 {len(done)+ok}/{len(all_tickers)}")
    if len(done)+ok < len(all_tickers):
        print("還沒補完（可能限流或斷線）→ 過一陣子再跑一次 python backfill_history.py，會自動接續")
    else:
        print("全部補完！")
        if os.path.exists(WARN_FILE):
            print(f"⚠ 有警示，請看 {WARN_FILE}（那些檔單日跳動>40%，要確認是不是髒資料）")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()