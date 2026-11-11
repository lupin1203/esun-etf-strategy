# -*- coding: utf-8 -*-
r"""
esun_backtest_all.py — 金融股資料修正後，把今天測過的全部重跑一次

今天的修正（已在 esun_strategy / esun_fetch_eps）：
  · 營收驚喜：前 3 個月 YoY 有 2 個月就算（金控去年負營收的月份不再讓整項空白）
  · 金融股三率：稅前淨利率、稅後淨利率（原本科目名稱對不上，全部空白）
  · 大戶不列入排名
所以「第 9 組」現在＝三項（營收驚喜、EPS 增幅、三率），金融股也有三項分數

組別（其他都跟第 9 組一樣，只改列出的那件事）：
  1  第 9 組（修正後）
  2  ＋MoM ≥10% 加分
  3  EPS 改 ÷ 上一季
  4  三率 → 營收／毛利／淨利成長 >10% 項數
  5  ＋換股門檻（新股票要比第 17 名好才換）
  6  ＋換股門檻＋EPS ÷ 上一季
  7  新方法 C 全部
先跑：python esun_fetch_eps.py --fin        （重抓金融股財報，補上三率）
再跑：python _research\esun_backtest_all.py --fresh   （--fresh：舊的分數快取裡金融股沒有三率，要重算）
輸出：_research\bt_out_all\，最後印出每組對第 9 組的配對差與 t 值
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import esun_backtest_ablation as A

B = A.B
A.GROUPS.clear()
A.GROUPS.update({
    "NOSHIP":   ("第9組（修正後）", {}),
    "AL_MOM":   ("＋MoM加分", {"rev": "mom"}),
    "AL_EPSC":  ("EPS÷上一季", {"eps": "C"}),
    "AL_GROW":  ("三率→成長項數", {"margin": "grow"}),
    "AL_BUF":   ("＋換股門檻", {"buffer": True}),
    "AL_BUFC":  ("＋換股門檻＋EPS÷上一季", {"buffer": True, "eps": "C"}),
    "MTG_C":    ("新方法C全部", None),
})
B.POOLS = {k: v[0] for k, v in A.GROUPS.items()}
B.COMBOS = [(k, "CORE", "new_only") for k in A.GROUPS]
B.OUT_DIR = os.path.join(HERE, "bt_out_all")


def fin_check():
    """回測最後一天，金融股各因子有沒有值（確認修正有生效）"""
    import glob
    import pandas as pd
    fs = sorted(glob.glob(os.path.join(HERE, "bt_cache", "scores_*", "raw_*.pkl")), key=os.path.getmtime)
    if not fs:
        return
    raw = pd.read_pickle(fs[-1])
    fin = [s for s in raw.index if s.startswith("28") or s in ("5876", "5880", "6023")]
    if not fin:
        return
    t = raw.loc[fin, ["營收驚喜", "EPS增幅", "三率"]].astype(float)
    print(f"\n金融股資料檢查（{os.path.basename(fs[-1])[4:14]}，{len(fin)} 檔）：")
    print("  有值的檔數：" + "、".join(f"{c} {int(t[c].notna().sum())}" for c in t.columns))
    if t["三率"].notna().sum() == 0:
        print("  ! 金融股三率還是空的：先跑 python esun_fetch_eps.py --fin，再用 --fresh 重跑回測")


if __name__ == "__main__":
    B.main()
    A.summary()
    fin_check()
