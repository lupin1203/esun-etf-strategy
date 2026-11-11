# -*- coding: utf-8 -*-
r"""
esun_backtest_combo.py — 第 9 組＋三個小幅有利的改動，合起來測

  1  第 9 組（對照）
  2  第 9 組＋換股門檻＋EPS ÷ 上一季＋放寬營收驚喜（三個一起）
  3  第 9 組＋換股門檻＋放寬營收驚喜（不改 EPS：EPS ÷ 上一季波動較大，單獨看它的貢獻）
輸出：_research\bt_out_combo\，最後印出配對差、t 值
用法（在 SMC- 資料夾）：python _research\esun_backtest_combo.py
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import esun_backtest_ablation as A

B = A.B
A.GROUPS.clear()
A.GROUPS.update({
    "NOSHIP":   ("第9組（對照）", {}),
    "CB_ALL3":  ("第9組＋門檻＋EPS÷上季＋放寬營收", {"buffer": True, "eps": "C", "rev": "relax"}),
    "CB_NOEPS": ("第9組＋門檻＋放寬營收", {"buffer": True, "rev": "relax"}),
})
B.POOLS = {k: v[0] for k, v in A.GROUPS.items()}
B.COMBOS = [(k, "CORE", "new_only") for k in A.GROUPS]
B.OUT_DIR = os.path.join(HERE, "bt_out_combo")

if __name__ == "__main__":
    B.main()
    A.summary()
