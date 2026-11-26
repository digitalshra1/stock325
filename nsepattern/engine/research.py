"""Research layer (display only: nothing here changes status, score, checks or alerts).

1. Loser study - five warning signs known on the buy day, and how often trades with each
   count of signs failed to reach the target within 30 days (3 years of history).
2. Exit test - what different exit rules would have returned on the same past trades.
3. Ready history - every recent time a stock's price reached its buy level (best tested and
   your method), and what happened next (no time limit, like your own rule).

Definitions (same as the backtest): buy price = P0 x (1 + level); fill on the first day the Low
reaches it, at min(buy, Open); the target is checked from the next day with the day's High.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nsepattern.engine.streaks import find_streaks

FLAG_NAMES = {
    "vol": "Volume 2x+ its 20-day average on the buy day",
    "gap": "Opened below the buy price (gap-down fill)",
    "range": "Usual daily range under 3% (slow mover)",
    "alone": "Falling alone: NIFTY not down 2%+ over 5 days",
    "shallow": "Buy level shallower than -5%",
}
HOLD_CAP = 250          # trading days followed for the "hold until target" rule (about a year)
COST = 0.0025           # brokerage + taxes + slippage per round trip, used in the exit test
EXIT_RULES = [          # (name, stop loss fraction, max days)
    ("Hold until target (your rule)", None, None),
    ("Exit after 60 days if not hit", None, 60),
    ("Exit after 30 days if not hit", None, 30),
    ("Exit after 15 days if not hit", None, 15),
    ("Stop loss -10%", 0.10, None),
    ("Stop loss -15%", 0.15, None),
    ("Stop loss -20%", 0.20, None),
    ("Stop loss -15% + exit after 30 days", 0.15, 30),
]


class Arrays:
    """Per-stock arrays shared by the research helpers."""

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.o, self.h, self.lo, self.c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
        v = df["Volume"].fillna(0).to_numpy(float) if "Volume" in df else np.zeros(len(df))
        self.v = v
        self.vavg = pd.Series(v).rolling(20).mean().shift(1).to_numpy()
        self.range60 = ((df["High"] - df["Low"]) / df["Open"] * 100).rolling(60).mean().shift(1).to_numpy()
        self.n = len(df)


def flags_at(A: Arrays, i: int, buy: float, level_pct: float, nifty5: float | None, filled: bool) -> list[str]:
    """Warning signs on day i (the buy day). Only data known that day is used."""
    out = []
    if A.vavg[i] > 0 and A.v[i] / A.vavg[i] >= 2:
        out.append("vol")
    if filled and np.isfinite(A.o[i]) and A.o[i] < buy:
        out.append("gap")
    if np.isfinite(A.range60[i]) and A.range60[i] < 3:
        out.append("range")
    if nifty5 is not None and np.isfinite(nifty5) and nifty5 > -2:
        out.append("alone")
    if level_pct > -5:
        out.append("shallow")
    return out


def _fill(A: Arrays, s, level_pct: float):
    buy = s.p0 * (1 + level_pct / 100)
    stop = min(s.end_idx + 1, A.n - 1)
    for i in range(s.first_idx + 1, stop + 1):
        if A.lo[i] <= buy:
            return i, buy, (min(buy, A.o[i]) if np.isfinite(A.o[i]) else buy)
    return None, buy, None


def _exit(seg_h, seg_l, seg_c, tgt: float, sl: float | None, tmax: int | None):
    n = min(len(seg_c), tmax or HOLD_CAP)
    if n == 0:
        return None
    hits = np.flatnonzero(seg_h[:n] >= tgt)
    hit = int(hits[0]) if len(hits) else None
    if sl is not None:
        stops = np.flatnonzero(seg_l[:n] <= -sl)
        if len(stops) and (hit is None or stops[0] <= hit):   # same day: assume the stop came first
            return -sl, int(stops[0]) + 1
    if hit is not None:
        return tgt, hit + 1
    return float(seg_c[n - 1]), n


def _nifty5_at(nifty5: pd.Series | None, date) -> float | None:
    if nifty5 is None:
        return None
    v = nifty5.asof(date) if len(nifty5) else None
    return None if v is None or pd.isna(v) else float(v)


def stock_research(df: pd.DataFrame, a: dict, cfg: dict, nifty5: pd.Series | None,
                   mood: pd.Series | None, recent_days: int) -> dict:
    """History rows for the loser study / exit test, recent buy signals, and today's warning signs."""
    p = cfg["pattern"]
    tgt = cfg["trading"]["target_pct"] / 100
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    A = Arrays(df)
    bt = a.get("backtest") or {}
    lv = {"best": (bt.get("best") or {}).get("level_pct"), "your": (bt.get("yours") or {}).get("level_pct")}
    streaks = find_streaks(df, p["pause_max_green_pct"], p["max_pauses"], p["min_down_days"])
    start = df.index.max() - pd.DateOffset(years=cfg["data"]["history_years"])
    hist_rows, recent = [], []
    for s in streaks:
        p0_date = df.index[s.p0_idx]
        is_recent = s.first_idx >= A.n - recent_days
        if p0_date < start and not is_recent:
            continue
        sig = {}
        for kind, level in lv.items():
            if level is None:
                continue
            f, buy, fill = _fill(A, s, level)
            if f is None:
                continue
            fd = df.index[f]
            n5 = _nifty5_at(nifty5, fd)
            fl = flags_at(A, f, buy, level, n5, True)
            seg_h = A.h[f + 1:] / fill - 1
            seg_l = A.lo[f + 1:] / fill - 1
            seg_c = A.c[f + 1:] / fill - 1
            hits = np.flatnonzero(seg_h >= tgt)
            hit_day = int(hits[0]) + 1 if len(hits) else None
            upto = hit_day if hit_day is not None else len(seg_l)
            worst = float(min(0.0, A.lo[f] / fill - 1, float(seg_l[:upto].min()) if upto else 0.0))
            if kind == "best" and p0_date >= start:
                row = {"flags": len(fl), "fl": fl, "hit30": hit_day is not None and hit_day <= p["stats_window_days"],
                       "known30": hit_day is not None or len(seg_c) >= p["stats_window_days"],
                       "res30": tgt if (hit_day is not None and hit_day <= p["stats_window_days"]) else
                       (float(seg_c[p["stats_window_days"] - 1]) if len(seg_c) >= p["stats_window_days"] else None)}
                if len(seg_c) >= HOLD_CAP:  # a full year after the buy: usable for the exit test
                    row["exits"] = [_exit(seg_h, seg_l, seg_c, tgt, sl, tm) for _, sl, tm in EXIT_RULES]
                hist_rows.append(row)
            if is_recent:
                md = mood.asof(fd) if mood is not None and len(mood) else None
                sig[kind] = {
                    "date": str(fd.date()), "buy": buy, "fill": fill, "flags": fl,
                    "mood": md if isinstance(md, str) else None,
                    "hit_day": hit_day, "hit_date": str(df.index[f + hit_day].date()) if hit_day else None,
                    "days": hit_day if hit_day else len(seg_c),
                    "now_pct": None if hit_day else float(A.c[-1] / fill - 1) * 100,
                    "worst_pct": worst * 100,
                }
        if is_recent and sig:
            # first day the stock showed READY at the best tested level (touched, or closed within 0.75% above)
            ready = None
            if lv["best"] is not None:
                b = s.p0 * (1 + lv["best"] / 100)
                for i in range(s.first_idx, min(s.end_idx + 1, A.n - 1) + 1):
                    if A.lo[i] <= b or A.c[i] <= b * (1 + cfg["status"]["ready_within_pct"] / 100):
                        ready = str(df.index[i].date())
                        break
            recent.append({"p0_date": str(p0_date.date()), "p0": s.p0, "ready_date": ready,
                           "depth_pct": s.depth_low * 100, "ongoing": s.ongoing, **sig})
    # today's warning signs for a stock in the buy zone (READY / GETTING CLOSE) at the best tested level
    today = None
    st = a.get("status") or {}
    if lv["best"] is not None and st.get("best"):
        last = A.n - 1
        buy = st["best"]["buy_price"]
        fl = flags_at(A, last, buy, lv["best"], _nifty5_at(nifty5, df.index[last]), bool(st["best"].get("zone_touched_today")))
        today = {"flags": fl, "n": len(fl)}
    return {"hist": hist_rows, "recent": recent, "today": today}


def summarise(rows: list[dict], target_pct: float) -> dict:
    """Aggregate all stocks' history rows into the loser-study and exit-test tables."""
    known = [r for r in rows if r["known30"] and r["res30"] is not None]
    out = {"trades": len(known)}
    if not known:
        return out

    def stats(rs):
        if not rs:
            return None
        loss = sum(1 for r in rs if not r["hit30"])
        return {"trades": len(rs), "loss_rate": loss / len(rs) * 100,
                "avg_result": float(np.mean([r["res30"] for r in rs])) * 100}

    out["overall"] = stats(known)
    out["by_count"] = [{"n": k, "label": f"{k}" if k < 4 else "4+",
                        **(stats([r for r in known if (r["flags"] == k if k < 4 else r["flags"] >= 4)]) or {"trades": 0})}
                       for k in range(5)]
    out["by_flag"] = [{"flag": f, "name": FLAG_NAMES[f],
                       "with": stats([r for r in known if f in r["fl"]]),
                       "without": stats([r for r in known if f not in r["fl"]])} for f in FLAG_NAMES]
    ex = [r for r in rows if "exits" in r and all(e is not None for e in r["exits"])]
    tab = []
    for k, (name, sl, tm) in enumerate(EXIT_RULES):
        if not ex:
            break
        res = np.array([r["exits"][k][0] for r in ex]) - COST
        days = np.array([r["exits"][k][1] for r in ex], dtype=float)
        tab.append({"rule": name, "win_rate": float((res > 0).mean()) * 100, "avg_result": float(res.mean()) * 100,
                    "avg_days": float(days.mean()), "per_100_days": float(res.sum() / days.sum()) * 100 * 100,
                    "worst5": float(np.percentile(res, 5)) * 100,
                    "still_waiting_60": float(np.mean([r["exits"][0][1] > 60 for r in ex])) * 100 if k == 0 else None,
                    "never_1y": float(np.mean([r["exits"][0][0] < target_pct / 100 and r["exits"][0][1] >= HOLD_CAP for r in ex])) * 100 if k == 0 else None})
    out["exit_trades"] = len(ex)
    out["exits"] = tab
    out["cost_pct"] = COST * 100
    return out
