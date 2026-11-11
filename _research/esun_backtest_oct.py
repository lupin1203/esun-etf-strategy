# -*- coding: utf-8 -*-
r"""
esun_backtest_oct.py — 用「現在的正式第 9 組」從 10 月 1 日重跑一次，看到最新收盤的淨值

  · 正式設定：150 檔排除航運｜核心 8×9.5%＋小部位 11×1%＋台積電 10%｜new_only｜
    三項評分（營收驚喜、EPS 增幅、三率，不含大戶）｜記憶體股、同一產業核心各最多 3 檔
  · 10/1 用 10 億現金開始建倉（用 9/30 收盤後的資料選股），之後每天收盤後選股、隔天成交
  · 跟回測同一套：資料依公告期限才使用（營收 10 日後），成交價用（高＋低＋收）÷ 3、含手續費與證交稅
  · 跟預演帳本的差別：預演帳本是每天用「當時的程式版本」一路累積，中間改過評分、修過資料；
    這裡全部用目前的程式，從頭算一次
需要：local_data（上市）、local_data_otc（上櫃）K 線已更新到最新收盤
輸出：_research\bt_out_oct\  每日淨值.csv、回測明細.xlsx；畫面印出每天淨值與最後持股
用法（在 SMC- 資料夾）：python _research\esun_backtest_oct.py
       換起點：python _research\esun_backtest_oct.py --start 2026-10-05
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if not any(a.startswith("--start") for a in sys.argv[1:]):
    sys.argv += ["--start", "2026-10-01"]

import esun_backtest as B
import pandas as pd

S = B.S
B.COMBOS = [("NOSHIP", "CORE", "new_only")]
B.OUT_DIR = os.path.join(HERE, "bt_out_oct")
B.tdcc_hist = lambda: None                     # 正式評分不含大戶
print("產業上限：" + ("同一產業核心最多 %d 檔" % S.MAX_INDUSTRY_CORE if getattr(S, "EXTRA_GROUP_CAPS", None)
                   else "沒有（esun_strategy.py 還是舊版？）"))

_cap = {}
_orig_export = B.export_excel


def export_excel(states, *a, **k):
    _cap["states"] = states
    return _orig_export(states, *a, **k)


B.export_excel = export_excel


def report():
    st = _cap.get("states", {}).get(B.COMBOS[0])
    if not st or not st["navs"]:
        print("沒有結果"); return
    names = B.stock_names()
    nav = pd.Series(dict(st["navs"]))
    print("\n" + "=" * 60 + "\n正式第 9 組從頭重跑：每日淨值（成交日收盤後）\n" + "=" * 60)
    prev = B.INIT
    for d, v in nav.items():
        print(f"  {d:%Y-%m-%d}  {v:>15,.0f}  當日 {v / prev - 1:>+6.2%}  累計 {v / B.INIT - 1:>+6.2%}")
        prev = v
    led = st["led"]
    px = {t: led["holdings"][t] for t in led["holdings"]}
    last = max(st["mend"])
    w = st["mend"][last]["w"]
    roles = st["mend"][last]["roles"]
    print(f"\n最後一天 {nav.index[-1]:%Y-%m-%d}：淨值 {led['nav']:,.0f}、現金 {led['cash']:,.0f}"
          f"（{led['cash'] / led['nav']:.1%}）、持股 {len(px)} 檔")
    order = {"台積電": 0, "核心": 1, "小部位": 2}
    for t in sorted(w, key=lambda t: (order.get(roles.get(t, ""), 3), -w[t])):
        print(f"  {t:<6}{names.get(t, ''):<8}{roles.get(t, '') or '—':<6}{w[t]:>7.2%}  {px[t] // 1000:>7,} 張")
    if st["err"]:
        print(f"\n  ! 出錯 {st['err']} 天：{st['err_msg']}")


if __name__ == "__main__":
    B.main()
    report()
