# -*- coding: utf-8 -*-
"""
影子紀錄（不下單）：如果季報「公司一公布就用」（含董事會通過後尚未正式申報、自結損益），排名會怎麼變？

  · 不在正式流程裡：不 import 進 esun_daily，檔名也不是 esun_*（不改 code_version 指紋）
  · 資料：_research/shadow_q3/q3_selfreported.csv（從公開資訊觀測站重大訊息整理的第三季數字）
          加上 revenue_cache/eps_history（FinMind 正式財報）
  · 做法：把還沒有正式第三季的公司，暫時補上一列 2026-09-30（EPS、三率用自結數，available_date＝公告日），
          用 EPS_TIMING="live" 跑一次 build_targets，跟正式流程（EPS_TIMING="deadline"）比較
  · 輸出：shadow_log.csv（每個選股日一列：正式核心、影子核心、差異、排名變化最大的股票）

用法（放在 SMC- 根目錄執行，或用 --root 指定）：
    python _research/shadow_q3/shadow_rank.py 2026-10-30
"""
import os, sys, json, shutil, tempfile, argparse
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", help="選股資料日（最近收盤日，例 2026-10-30）")
    ap.add_argument("--root", default=os.path.abspath(os.path.join(HERE, "..", "..")))
    ap.add_argument("--plan", default=None, help="D-Plan 交易日（只用來記錄）")
    a = ap.parse_args()
    os.chdir(a.root)
    sys.path.insert(0, a.root)
    import esun_strategy as S

    d = pd.Timestamp(a.date)
    q3 = pd.read_csv(os.path.join(HERE, "q3_selfreported.csv"), dtype={"sid": str})
    q3["announce_date"] = pd.to_datetime(q3["announce_date"])
    q3 = q3[q3["announce_date"] <= d]

    # 目前持股權重（交易前），用最近一份官方資料包
    cur_w = {}
    off = sorted(f for f in os.listdir("esun_official") if f.startswith("DataPack_"))
    if off:
        dp = json.load(open(os.path.join("esun_official", off[-1]), encoding="utf-8"))
        nav = float(dp["account"]["nav_tradable"])
        for h in dp["holdings"]:
            cur_w[str(h["code"])] = float(h["shares"]) * float(h["close_price"]) / nav
    plan = a.plan or ""
    prev = S.load_prev_targets(plan or "9999-99-99")

    # 正式流程（季報依法定期限）
    S.EPS_TIMING = "deadline"
    off_t, off_tab = S.build_targets(d, prev, cur_w)
    off_rank = off_tab["排名"]

    # 影子：正式財報＋自結數，公布就用
    eps_dir0 = S.EPS_DIR
    tmp = tempfile.mkdtemp(prefix="shadow_eps_")
    try:
        for f in os.listdir(S.EPS_DIR):
            shutil.copy(os.path.join(S.EPS_DIR, f), tmp)
        used = []
        for _, r in q3.iterrows():
            p = os.path.join(tmp, f"{r.sid}.parquet")
            if not os.path.exists(p):
                continue
            e = pd.read_parquet(p)
            q = pd.Timestamp("2026-09-30")
            if q in e.index:                       # 已有正式財報：以正式為準
                continue
            row = {c: (float(r[c]) if c in r and pd.notna(r[c]) else float("nan")) for c in ("eps", "gm", "om", "nm")}
            row["available_date"] = r.announce_date
            e.loc[q] = pd.Series(row)
            e.sort_index().to_parquet(p)
            used.append(r.sid)
        S.EPS_DIR, S.EPS_TIMING = tmp, "live"
        sh_t, sh_tab = S.build_targets(d, prev, cur_w)
        sh_rank = sh_tab["排名"]
    finally:
        S.EPS_DIR, S.EPS_TIMING = eps_dir0, "deadline"
        shutil.rmtree(tmp, ignore_errors=True)

    oc, sc = set(off_t["core"]), set(sh_t["core"])
    chg = (off_rank - sh_rank.reindex(off_rank.index)).dropna().astype(int)
    big = chg[chg.abs() >= 5].sort_values()
    rec = {"資料日": f"{d:%Y-%m-%d}", "D-Plan": plan, "自結補上": len(used), "自結代號": " ".join(used),
           "正式核心": " ".join(off_t["core"]), "影子核心": " ".join(sh_t["core"]),
           "影子多": " ".join(sorted(sc - oc)), "影子少": " ".join(sorted(oc - sc)),
           "排名變動≥5": " ".join(f"{s}:{int(off_rank[s])}→{int(sh_rank[s])}" for s in big.index)}
    out = os.path.join(HERE, "shadow_log.csv")
    log = pd.read_csv(out, dtype=str) if os.path.exists(out) else pd.DataFrame()
    log = pd.concat([log[log.get("資料日", pd.Series(dtype=str)) != rec["資料日"]] if len(log) else log,
                     pd.DataFrame([rec])], ignore_index=True)
    log.to_csv(out, index=False, encoding="utf-8-sig")
    for k, v in rec.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
