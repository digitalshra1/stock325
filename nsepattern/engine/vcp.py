"""Pattern 3: Volatility Contraction Pattern (VCP), Mark Minervini style.

Independent add-on: it does not touch the down-streak engine. Conditions (all thresholds
in config.yaml -> vcp):
  1. EMA20 > EMA50 > EMA200 (trend stack)
  2. Higher highs  (last 60-day high above the 60 days before it)
  3. Higher lows   (last two swing lows rising)
  4. Shrinking pullbacks (>= 2 contractions, each no bigger than the previous x tolerance)
  5. Declining ATR (ATR now < ATR some days ago)
  6. Declining volume during the consolidation (recent average < 50-day average)
  7. Tight price structure (recent high-low range small, last pullback shallow)
  8. Strong breakout volume (close above the pivot on >= 1.5 x average volume)

Status: "VCP BREAKOUT" (setup + breakout in the last few days), "VCP SETUP" (enough of
conditions 1-7, waiting for the breakout), or None.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NAMES = {
    "ema_stack": "EMA20 > EMA50 > EMA200",
    "higher_highs": "Higher highs",
    "higher_lows": "Higher lows",
    "shrinking": "Shrinking pullbacks",
    "atr_declining": "Declining ATR",
    "volume_dryup": "Volume drying up in the base",
    "tight": "Tight price structure",
    "breakout_volume": "Breakout on strong volume",
}


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def swing_points(high: np.ndarray, low: np.ndarray, w: int) -> list[tuple[int, str, float]]:
    """Confirmed swing highs ('H') and lows ('L') in time order: (index, kind, price)."""
    pts = []
    n = len(high)
    for i in range(w, n - w):
        if high[i] == np.max(high[i - w:i + w + 1]):
            pts.append((i, "H", float(high[i])))
        if low[i] == np.min(low[i - w:i + w + 1]):
            pts.append((i, "L", float(low[i])))
    # collapse consecutive same-kind points, keeping the extreme one
    out: list[tuple[int, str, float]] = []
    for p in pts:
        if out and out[-1][1] == p[1]:
            if (p[1] == "H" and p[2] >= out[-1][2]) or (p[1] == "L" and p[2] <= out[-1][2]):
                out[-1] = p
            continue
        out.append(p)
    return out


def contractions(pts: list[tuple[int, str, float]]) -> list[dict]:
    """Each swing high followed by the next swing low = one pullback (depth in %)."""
    out = []
    for a, b in zip(pts, pts[1:]):
        if a[1] == "H" and b[1] == "L" and a[2] > 0:
            out.append({"high_idx": a[0], "high": a[2], "low_idx": b[0], "low": b[2],
                        "depth_pct": (a[2] - b[2]) / a[2] * 100})
    return out


def detect(df: pd.DataFrame, cfg: dict) -> dict:
    """Evaluate the VCP on the last row of df. Never raises on short data (returns status None)."""
    v = cfg["vcp"]
    d = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(d) < 220:
        return {"status": None, "score": 0, "conditions": {}, "reason": "less than ~1 year of data"}
    c, h, lo, vol = d["Close"], d["High"], d["Low"], d["Volume"].fillna(0)
    e20, e50, e200 = ema(c, 20).iloc[-1], ema(c, 50).iloc[-1], ema(c, 200).iloc[-1]
    a = atr(d, v["atr_days"])
    avg50 = vol.rolling(v["volume_avg_days"]).mean().shift(1)

    base = d.tail(v["lookback_days"])
    off = len(d) - len(base)
    pts = swing_points(base["High"].to_numpy(float), base["Low"].to_numpy(float), v["pivot_window"])
    cons = contractions(pts)
    lows = [p for p in pts if p[1] == "L"]
    highs = [p for p in pts if p[1] == "H"]
    pivot = highs[-1][2] if highs else float(base["High"].iloc[:-1].max())

    # breakout: close above pivot on strong volume within the last few days
    rec = v["breakout_recent_days"]
    bo_idx = None
    for i in range(len(d) - rec, len(d)):
        if highs and i <= highs[-1][0] + off:
            continue  # must happen after the pivot high itself
        if c.iloc[i] > pivot and avg50.iloc[i] > 0 and vol.iloc[i] >= v["breakout_volume_ratio"] * avg50.iloc[i]:
            bo_idx = i
            break
    # consolidation checks use the days before a breakout (the breakout itself is wide and loud)
    end = bo_idx if bo_idx is not None else len(d)
    pre = d.iloc[:end]

    cond = {}
    cond["ema_stack"] = bool(e20 > e50 > e200)
    hh_now, hh_prev = h.iloc[-60:].max(), h.iloc[-120:-60].max()
    cond["higher_highs"] = bool(hh_now > hh_prev)
    cond["higher_lows"] = bool(len(lows) >= 2 and lows[-1][2] > lows[-2][2])
    # run of shrinking pullbacks ending at the latest one (e.g. 18% -> 9% -> 4%); older, unrelated
    # swings before the base do not spoil it
    run = 1 if cons else 0
    for i in range(len(cons) - 1, 0, -1):
        if cons[i]["depth_pct"] <= cons[i - 1]["depth_pct"] * v["contraction_tolerance"]:
            run += 1
        else:
            break
    last_k = cons[-min(max(run, 1), 4):] if cons else []
    cond["shrinking"] = bool(run >= v["min_contractions"])
    pa = a.iloc[:end]
    cond["atr_declining"] = bool(len(pa) > v["atr_compare_days"] + 1 and pa.iloc[-1] < pa.iloc[-1 - v["atr_compare_days"]])
    pv = pre["Volume"].fillna(0)
    dry = pv.tail(v["dryup_days"]).mean()
    long = pv.tail(v["volume_avg_days"]).mean()
    cond["volume_dryup"] = bool(long > 0 and dry < long)
    t = pre.tail(v["tight_days"])
    rng = (t["High"].max() - t["Low"].min()) / t["Close"].iloc[-1] * 100 if len(t) else 99
    last_depth = cons[-1]["depth_pct"] if cons else 99
    cond["tight"] = bool(rng <= v["max_tight_range_pct"] and last_depth <= v["max_last_contraction_pct"])
    cond["breakout_volume"] = bo_idx is not None

    setup_score = sum(cond[k] for k in cond if k != "breakout_volume")
    core = cond["ema_stack"] and cond["shrinking"]  # no uptrend or no contraction = not a VCP
    is_setup = core and setup_score >= v["setup_min_score"]
    status = None
    if core and cond["breakout_volume"] and setup_score >= v["setup_min_score"] - 1:
        status = "VCP BREAKOUT"
    elif is_setup:
        status = "VCP SETUP"
    stop = cons[-1]["low"] if cons else None
    details = {
        "ema_stack": f"EMA20 {e20:,.1f} / EMA50 {e50:,.1f} / EMA200 {e200:,.1f}",
        "higher_highs": f"60-day high {hh_now:,.1f} vs previous {hh_prev:,.1f}",
        "higher_lows": (f"last swing lows {lows[-2][2]:,.1f} → {lows[-1][2]:,.1f}" if len(lows) >= 2 else "fewer than 2 swing lows"),
        "shrinking": ("pullbacks " + " → ".join(f"{x['depth_pct']:.1f}%" for x in last_k)) if last_k else "no pullbacks found",
        "atr_declining": (f"ATR {pa.iloc[-1]:,.2f} vs {pa.iloc[-1 - v['atr_compare_days']]:,.2f} {v['atr_compare_days']} days ago"
                          if len(pa) > v["atr_compare_days"] + 1 else "not enough data"),
        "volume_dryup": f"{v['dryup_days']}-day avg {dry:,.0f} vs {v['volume_avg_days']}-day avg {long:,.0f}",
        "tight": f"{v['tight_days']}-day range {rng:.1f}%, last pullback {last_depth:.1f}%",
        "breakout_volume": (f"closed above pivot {pivot:,.2f} on {d.index[bo_idx].date()} with "
                            f"{vol.iloc[bo_idx] / avg50.iloc[bo_idx]:.1f}x volume") if bo_idx is not None
        else f"waiting: pivot {pivot:,.2f} (needs ≥ {v['breakout_volume_ratio']}x volume)",
    }
    return {
        "status": status, "score": int(sum(cond.values())), "setup_score": int(setup_score),
        "conditions": {k: {"ok": cond[k], "name": NAMES[k], "detail": details[k]} for k in NAMES},
        "pivot": float(pivot), "stop": stop,
        "breakout_date": str(d.index[bo_idx].date()) if bo_idx is not None else None,
        "contractions": [round(x["depth_pct"], 2) for x in cons[-4:]],
        "distance_to_pivot_pct": float(c.iloc[-1] / pivot - 1) * 100 if pivot else None,
    }


def history(df: pd.DataFrame, cfg: dict, years: int = 3) -> dict:
    """Past VCP breakouts and what happened next (buy at the breakout close, +target within the window)."""
    v = cfg["vcp"]
    d = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(d) < 260:
        return {"signals": 0, "hits": 0, "hit_rate": None, "median_days": None, "avg_result": None, "trades": []}
    c, h, lo, vol = d["Close"], d["High"], d["Low"], d["Volume"].fillna(0)
    avg50 = vol.rolling(v["volume_avg_days"]).mean().shift(1)
    stack = (ema(c, 20) > ema(c, 50)) & (ema(c, 50) > ema(c, 200))
    prior_high = h.rolling(60).max().shift(1)
    quick = stack & (c > prior_high) & (vol >= v["breakout_volume_ratio"] * avg50)
    start = d.index.max() - pd.DateOffset(years=years)
    tgt, win = v["target_pct"] / 100, v["stats_window_days"]
    trades, last_sig = [], -999
    for i in np.flatnonzero(quick.to_numpy()):
        if d.index[i] < start or i < 220 or i - last_sig < 10:
            continue
        r = detect(d.iloc[:i + 1], cfg)
        if r["status"] != "VCP BREAKOUT":
            continue
        last_sig = i
        entry = float(c.iloc[i])
        hit = None
        for j in range(i + 1, min(i + win, len(d) - 1) + 1):
            if h.iloc[j] >= entry * (1 + tgt):
                hit = j
                break
        if hit is None and i + win > len(d) - 1:
            continue
        end = hit if hit is not None else i + win
        trades.append({"date": str(d.index[i].date()), "entry": entry, "hit": hit is not None,
                       "days": (hit - i) if hit is not None else None,
                       "mae": float(min(0.0, lo.iloc[i + 1:end + 1].min() / entry - 1)),
                       "result": tgt if hit is not None else float(c.iloc[end] / entry - 1)})
    n = len(trades)
    hits = sum(t["hit"] for t in trades)
    days = [t["days"] for t in trades if t["days"] is not None]
    return {"signals": n, "hits": hits, "hit_rate": hits / n if n else None,
            "median_days": float(np.median(days)) if days else None,
            "avg_result": float(np.mean([t["result"] for t in trades])) if n else None,
            "trades": trades[-10:]}
