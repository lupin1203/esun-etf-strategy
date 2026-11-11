# -*- coding: utf-8 -*-
"""
factor_capmom.py — 市值權重 + 動能調整（定案規格，跑一次、不再調整）

規格（事先寫死）：
  · 選股池：每月底，流動性過關（20日均量>=500張、7天內有交易）且市值最大的 50 檔
  · 市值 = 官方 PBR × 股東權益（依公告日取值；不用還原價、不假設面額）
  · 基礎權重：市值權重
  · 動能調整：50 檔內依 12-1 動能分 5 組，倍數 1.5 / 1.25 / 1 / 0.75 / 0.5，再重新加總到 100%
    （動能被判定為髒資料者給 ×1）
  · 每月底換股，T+1 開盤成交，扣手續費 0.1425% 與證交稅 0.3%，抵銷不可能跳動
  · 換手成本以「漂移後權重」計算（持有期間股價漲跌會自然改變權重）
四組對照：
  A 純市值（無上限）      B 市值+動能（無上限）
  C 純市值（比賽上限）    D 市值+動能（比賽上限：2330 ≤25%、其他 ≤10%）
A 應接近 0050（檢查市值資料有沒有問題）；B−A、D−C 就是動能調整的貢獻。
結果會自動附加到 trial_log.csv（試驗紀錄）。
放 SMC- 根目錄（跟 factor_value_v2.py、factor_strength.py 同目錄）：python factor_capmom.py
"""
import os, glob, datetime, importlib.util
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
def _load(name, fname):
    s = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
fs = _load("fs", "factor_strength.py")
fv = fs.fv

TOP_N = 50
MULT = [1.5, 1.25, 1.0, 0.75, 0.5]          # Q1(動能最強) … Q5
TSMC, TSMC_CAP, OTHER_CAP = "2330.TW", 0.25, 0.10
BENCH = "0050.TW"
TRIAL_LOG = "trial_log.csv"


def equity_frame(ctx):
    out = {}
    for t in ctx["PBO"].columns:
        p = os.path.join(fv.EQUITY_DIR, f"{fv.sid_of(t)}.parquet")
        if not os.path.exists(p):
            continue
        try:
            e = pd.read_parquet(p)
            s = pd.Series(e["equity"].values, index=pd.to_datetime(e["available_date"])).sort_index()
            s = s[~s.index.duplicated(keep="last")]
            out[t] = s.reindex(ctx["months"], method="ffill")
        except Exception:
            continue
    return pd.DataFrame(out).reindex(index=ctx["months"])


def apply_caps(w, caps):
    w = w.copy()
    for _ in range(200):
        over = w > caps + 1e-12
        if not over.any():
            break
        excess = (w[over] - caps[over]).sum()
        w[over] = caps[over]
        free = w < caps - 1e-12
        if w[free].sum() <= 0:
            break
        w[free] += excess * w[free] / w[free].sum()
    return w / w.sum()


def target_weights(ctx, MC, i, tilt, capped):
    d = pd.DataFrame({"mom": ctx["MOM"].iloc[i], "dm": ctx["DM"].iloc[i], "mc": MC.iloc[i],
                      "liq": ctx["LIQ"].iloc[i], "fresh": ctx["FRESH"].iloc[i]})
    d = d.drop(index=[BENCH], errors="ignore")
    d = d[(d["liq"] == True) & (d["fresh"] == True) & (d["mc"] > 0) & d["mom"].notna()]
    if len(d) < TOP_N:
        return None
    d = d.nlargest(TOP_N, "mc")
    w = d["mc"] / d["mc"].sum()
    if tilt:
        q = pd.qcut(d["mom"].rank(method="first", ascending=False), 5, labels=False)  # 0=最強
        m = q.map(lambda k: MULT[int(k)])
        dirty = (d["mom"] > fv.DIRTY_TRIGGER) & (d["dm"] == True)
        m[dirty] = 1.0
        w = w * m
        w = w / w.sum()
    if capped:
        caps = pd.Series(OTHER_CAP, index=w.index)
        if TSMC in caps.index:
            caps[TSMC] = TSMC_CAP
        w = apply_caps(w, caps)
    return w


def run_port(ctx, MC, R, tilt, capped):
    rets, turns, prev, idx = [], [], pd.Series(dtype=float), []
    for i in range(len(ctx["months"]) - 1):
        w = target_weights(ctx, MC, i, tilt, capped)
        if w is None:
            continue
        r = R.iloc[i].reindex(w.index)
        ok = r.notna()
        if not ok.any():
            continue
        wv = w[ok] / w[ok].sum()
        allk = wv.index.union(prev.index)
        turn = (wv.reindex(allk, fill_value=0) - prev.reindex(allk, fill_value=0)).abs().sum()
        rets.append(float((wv * r[ok]).sum() - turn * fv.ONESIDE_COST))
        turns.append(turn)
        idx.append(R.index[i])
        drifted = wv * (1 + r[ok])            # 持有一個月後，權重隨股價自然漂移
        prev = drifted / drifted.sum()
    return pd.Series(rets, index=pd.DatetimeIndex(idx)), np.mean(turns) / 2 * 12 if turns else np.nan


def dd(rs):
    eq = (1 + rs.dropna()).cumprod()
    return ((eq - eq.cummax()) / eq.cummax()).min() * 100


def excess_stats(a, b):
    """a 相對 b 的超額：年化差、追蹤誤差、資訊比率、t 值。"""
    x = (a - b).dropna()
    te = x.std() * np.sqrt(12) * 100
    ir = x.mean() / x.std() * np.sqrt(12) if x.std() > 0 else np.nan
    return (fs.ann(a.reindex(x.index)) - fs.ann(b.reindex(x.index)), te, ir, fs.tstat(x))


def main():
    print("讀取資料（沿用 factor_value_v2）...")
    ctx = fv.build_context()
    R = fs.monthly_return_matrix(ctx)
    EQ = equity_frame(ctx).reindex(columns=ctx["PBO"].columns)
    MC = ctx["PBO"] * EQ
    last = MC.iloc[-1].dropna().sort_values(ascending=False)
    print(f"  市值檢查（最後一個月前5大，單位兆）："
          + "、".join(f"{t.replace('.TW','')} {v/1e12:.1f}" for t, v in last.head(5).items()))

    wa = target_weights(ctx, MC, len(ctx["months"]) - 2, False, False)
    if wa is not None and TSMC in wa.index:
        print(f"  權重檢查：最後一期 A 組台積電權重 {wa[TSMC]*100:.1f}%（可對照 0050 公布的台積電占比）")

    ports = {}
    labels = [("A 純市值(無上限)", False, False), ("B 市值+動能(無上限)", True, False),
              ("C 純市值(比賽上限)", False, True), ("D 市值+動能(比賽上限)", True, True)]
    for lab, tilt, capped in labels:
        ports[lab] = run_port(ctx, MC, R, tilt, capped)
    bench = R[BENCH].reindex(ports[labels[0][0]][0].index) if BENCH in R.columns else None

    print("\n" + "=" * 72)
    print(f"  {'組合':<22}{'年化':>8}{'夏普':>7}{'回撤':>9}{'年換手':>8}{'對0050超額':>11}{'IR':>7}")
    if bench is not None:
        print(f"  {'0050':<22}{fs.ann(bench):>7.1f}%{fs.sharpe(bench):>7.2f}{dd(bench):>8.1f}%")
    for lab, _, _ in labels:
        rs, tv = ports[lab]
        ex = excess_stats(rs, bench) if bench is not None else (np.nan,) * 4
        print(f"  {lab:<22}{fs.ann(rs):>7.1f}%{fs.sharpe(rs):>7.2f}{dd(rs):>8.1f}%{tv*100:>7.0f}%"
              f"{ex[0]:>10.1f}%{ex[2]:>7.2f}")

    if bench is not None:
        a = ports[labels[0][0]][0]
        c = np.corrcoef(a.values, bench.reindex(a.index).values)[0, 1]
        print(f"\n  資料檢查：A 與 0050 月報酬相關係數 {c:.3f}（應 > 0.95，否則市值資料可能有問題）")

    print("\n" + "=" * 72)
    print("★ 動能調整的貢獻（一次只差一個變數）")
    rows = []
    for (b_lab, a_lab), name in [((labels[1][0], labels[0][0]), "無上限 B−A"),
                                  ((labels[3][0], labels[2][0]), "比賽上限 D−C")]:
        b, a = ports[b_lab][0], ports[a_lab][0]
        x = (b - a).dropna()
        f, s = x[x.index < fv.SPLIT], x[x.index >= fv.SPLIT]
        diff, te, ir, t = excess_stats(b, a)
        print(f"  {name:<12}年化多 {diff:>5.1f}% ｜ 追蹤誤差 {te:.1f}% ｜ IR {ir:.2f} ｜ t {t:.2f}"
              f" ｜ 前半 t {fs.tstat(f):.2f}、後半 t {fs.tstat(s):.2f}")
        rows.append((name, diff, ir, t, fs.tstat(f), fs.tstat(s)))

    print("\n逐年報酬")
    cols = ["0050"] + [lab[:1] for lab, _, _ in labels]
    print("  年    " + "".join(f"{c:>9}" for c in cols))
    series = [bench] + [ports[lab][0] for lab, _, _ in labels]
    yrs = sorted(set(series[1].index.year))
    for y in yrs:
        vals = []
        for s in series:
            if s is None:
                vals.append(np.nan); continue
            ys = s[s.index.year == y].dropna()
            vals.append(((1 + ys).prod() - 1) * 100 if len(ys) else np.nan)
        print(f"  {y}  " + "".join(f"{v:>8.1f}%" for v in vals))

    print("\n判讀：")
    print("  · A 與 0050 相關係數要夠高，後面結果才可信")
    print("  · B−A、D−C 的 t > 2 且前後半都為正 → 動能調整真的有加分")
    print("  · t < 1 → 動能在大型股的市值權重架構下沒有加分")
    print("  · 定案規格：跑完就記錄，不再調整參數")

    new = not os.path.exists(TRIAL_LOG)
    with open(TRIAL_LOG, "a", encoding="utf-8") as fh:
        if new:
            fh.write("時間,腳本,規格,比較,年化差,IR,t,前半t,後半t\n")
        spec = f"top{TOP_N}市值權重+動能倍數{'/'.join(map(str, MULT))}"
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        for name, diff, ir, t, tf, tb in rows:
            fh.write(f"{now},factor_capmom.py,{spec},{name},{diff:.2f},{ir:.2f},{t:.2f},{tf:.2f},{tb:.2f}\n")
    print(f"\n已寫入試驗紀錄 {TRIAL_LOG}")


if __name__ == "__main__":
    main()