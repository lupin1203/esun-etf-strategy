# -*- coding: utf-8 -*-
"""
factor_strength.py — 因子到底弱不弱：五分位檢定 + 對照 0050

每個月把候選股依因子切 5 組（Q1=最好 … Q5=最差），各組等權重持有一個月：
  · 5 組報酬若依序遞減，且 Q1−Q5 價差的 t 值 > 2 → 因子有效
  · t 值 < 1 → 因子弱
另外比較：整組等權重 vs 抽樣 20 檔（factor_value_v2 全部修正版）vs 0050。
五分位用「未扣成本」的報酬看因子本身；Q1 另外列扣成本後的數字看能不能實際賺。
沿用 factor_value_v2 的所有資料與修正（官方PBR、髒資料抵銷、T+1開盤成交）。
放 SMC- 根目錄（跟 factor_value_v2.py 同目錄）：python factor_strength.py
"""
import os, importlib.util
import numpy as np, pandas as pd

spec = importlib.util.spec_from_file_location(
    "fv", os.path.join(os.path.dirname(os.path.abspath(__file__)), "factor_value_v2.py"))
fv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fv)

BENCH = "0050.TW"
NQ = 5


def monthly_return_matrix(ctx):
    """每檔每月的持有報酬（T+1開盤進、下月T+1開盤出，抵銷不可能跳動）。"""
    EP, CJE = ctx["EP"], ctx["CJE"].reindex(columns=ctx["EP"].columns)
    ep, cj = EP.values, CJE.values
    raw = ep[1:] / ep[:-1] - 1
    f = cj[1:] / cj[:-1]
    ok = np.isfinite(f) & (f > 0)
    adj = np.where(ok, (1 + raw) / np.where(ok, f, 1) - 1, raw)
    adj[~(ep[:-1] > 0) | ~(ep[1:] > 0)] = np.nan
    return pd.DataFrame(adj, index=ctx["months"][1:], columns=EP.columns)


def candidates(ctx, i, liq):
    d = pd.DataFrame({
        "mom": ctx["MOM"].iloc[i], "dm": ctx["DM"].iloc[i], "pb": ctx["PBO"].iloc[i],
        "liq": ctx["LIQ"].iloc[i], "fresh": ctx["FRESH"].iloc[i]})
    d = d[d["mom"].notna()]
    d = d[~((d["mom"] > fv.DIRTY_TRIGGER) & (d["dm"] == True))]
    d = d.drop(index=[BENCH], errors="ignore")          # 0050 是基準，不當成股票
    if liq:
        d = d[(d["liq"] == True) & (d["fresh"] == True)]
    return d


def factor_key(d, mode):
    """回傳 (因子值, 是否越大越好)。"""
    if mode == "mom":
        return d["mom"], True
    if mode == "val":
        d = d[d["pb"] > 0]
        return d["pb"], False
    d = d[(d["pb"] > 0) & d["mom"].notna()]
    return (d["mom"].rank(ascending=False) + d["pb"].rank(ascending=True)) / 2, False


def quintiles(key, best_high):
    if len(key) < NQ * 4:
        return None
    q = pd.qcut(key.rank(method="first"), NQ, labels=False)
    return (NQ - 1 - q) if best_high else q          # 0 = Q1 最好


def ann(rs):
    rs = rs.dropna()
    if len(rs) == 0:
        return np.nan
    return ((1 + rs).prod() ** (12 / len(rs)) - 1) * 100


def sharpe(rs):
    rs = rs.dropna()
    return rs.mean() / rs.std() * np.sqrt(12) if len(rs) > 1 and rs.std() > 0 else np.nan


def tstat(rs):
    rs = rs.dropna()
    return rs.mean() / rs.std() * np.sqrt(len(rs)) if len(rs) > 1 and rs.std() > 0 else np.nan


def analyse(ctx, R, liq):
    months = ctx["months"]
    out = {m: {q: [] for q in range(NQ)} for m in ("mom", "val", "combo")}
    q1_net = {m: [] for m in out}
    prev_w = {m: {} for m in out}
    universe = []
    idx = []
    for i in range(len(months) - 1):
        d = candidates(ctx, i, liq)
        r = R.iloc[i]
        idx.append(R.index[i])
        universe.append(r.reindex(d.index).mean())
        for m in out:
            key, best_high = factor_key(d, m)
            q = quintiles(key, best_high)
            if q is None:
                for k in range(NQ): out[m][k].append(np.nan)
                q1_net[m].append(np.nan); continue
            for k in range(NQ):
                out[m][k].append(r.reindex(q.index[q == k]).mean())
            # Q1 整組等權重，扣換手成本
            members = [t for t in q.index[q == 0] if np.isfinite(r.get(t, np.nan))]
            if members:
                w = {t: 1 / len(members) for t in members}
                turn = sum(abs(w.get(t, 0) - prev_w[m].get(t, 0)) for t in set(w) | set(prev_w[m]))
                q1_net[m].append(np.mean([r[t] for t in members]) - turn * fv.ONESIDE_COST)
                prev_w[m] = w
            else:
                q1_net[m].append(np.nan)
    idx = pd.DatetimeIndex(idx)
    res = {m: {k: pd.Series(v, index=idx) for k, v in out[m].items()} for m in out}
    net = {m: pd.Series(v, index=idx) for m, v in q1_net.items()}
    return res, net, pd.Series(universe, index=idx)


def main():
    print("讀取資料（沿用 factor_value_v2）...")
    ctx = fv.build_context()
    R = monthly_return_matrix(ctx)
    bench = R[BENCH] if BENCH in R.columns else None
    names = {"mom": "純動能", "val": "純價值", "combo": "動能+價值"}
    final_cfg = fv.CONFIGS[-1][1]

    for liq in (True, False):
        title = "有流動性過濾（500張）" if liq else "沒有流動性過濾"
        print("\n" + "=" * 70)
        print(f"【{title}】五分位年化報酬（未扣成本；Q1=最好）")
        print("=" * 70)
        res, net, uni = analyse(ctx, R, liq)
        print(f"  {'因子':<10}" + "".join(f"{'Q'+str(k+1):>8}" for k in range(NQ))
              + f"{'Q1-Q5':>9}{'t值':>7}{'依序?':>7}")
        for m in ("mom", "val", "combo"):
            anns = [ann(res[m][k]) for k in range(NQ)]
            spread = res[m][0] - res[m][NQ - 1]
            mono = all(anns[k] >= anns[k + 1] for k in range(NQ - 1))
            print(f"  {names[m]:<10}" + "".join(f"{a:>7.1f}%" for a in anns)
                  + f"{ann(spread):>8.1f}%{tstat(spread):>7.2f}{'是' if mono else '否':>7}")
        print(f"\n  候選股等權重（整個池子）：年化 {ann(uni):.1f}%、夏普 {sharpe(uni):.2f}")

        if liq:
            print("\n" + "-" * 70)
            print("【最好那組怎麼持有的差別】（皆扣成本）")
            print(f"  {'':<10}{'整組等權重':>14}{'抽樣20檔':>14}")
            for m in ("mom", "val", "combo"):
                _, rs20, _, _ = fv.run(ctx, m, final_cfg)
                print(f"  {names[m]:<10}{ann(net[m]):>7.1f}% / {sharpe(net[m]):.2f}"
                      f"{ann(rs20):>7.1f}% / {sharpe(rs20):.2f}")
            print("  （格式：年化 / 夏普）")
            if bench is not None:
                print(f"\n  0050（同期、同樣 T+1 開盤進出）：年化 {ann(bench):.1f}%、夏普 {sharpe(bench):.2f}")
            print("\n  前後半：Q1−Q5 價差 t 值")
            for m in ("mom", "val", "combo"):
                sp = res[m][0] - res[m][NQ - 1]
                f, b = sp[sp.index < fv.SPLIT], sp[sp.index >= fv.SPLIT]
                print(f"  {names[m]:<10}前半 t={tstat(f):.2f}（{ann(f):.1f}%）  後半 t={tstat(b):.2f}（{ann(b):.1f}%）")

    print("\n" + "=" * 70)
    print("判讀：")
    print("  · Q1→Q5 依序遞減、t > 2 → 因子有效；t < 1 → 因子弱")
    print("  · 整組 ≈ 抽樣20檔 → 問題不在抽樣；整組明顯較好 → 抽樣方式有問題")
    print("  · Q1 扣成本後仍輸 0050 → 就算因子有效，做多這組也不值得")
    print("  · 有過濾 vs 沒過濾差很多 → 因子效果集中在冷門小型股")
    print("⚠ 仍未修：倖存者偏差（兩種設定都受影響）。")


if __name__ == "__main__":
    main()