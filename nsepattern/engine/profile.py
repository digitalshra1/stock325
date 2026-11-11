"""Per-stock profile (7.2) and full analysis of one stock (7.3, 7.4)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from nsepattern.engine.backtest import run_backtests
from nsepattern.engine.mover import liquidity_crore, mover_stats
from nsepattern.engine.streaks import Streak, find_streaks


def build_profile(streaks: list[Streak], cfg: dict) -> dict:
    p = cfg["pattern"]
    done = [s for s in streaks if not s.ongoing]
    meaningful = [s for s in done if s.depth_low * 100 <= p["meaningful_depth_pct"]]
    deep = [s for s in done if s.depth_low * 100 <= p["deep_fall_pct"]]
    if not meaningful:
        return {"streaks_total": len(done), "meaningful": 0, "deep_falls": len(deep),
                "usual_days": None, "usual_days_p75": None, "usual_depth_pct": None,
                "depth_p75_pct": None, "depth_p90_pct": None, "depths_pct": []}
    days = np.array([s.down_days for s in meaningful], float)
    depths = np.array([s.depth_low * 100 for s in meaningful], float)
    # Usual length: by default measured on the deeper half of the falls (the ones that reach buy
    # levels), because short shallow 2–3 day dips would otherwise make almost every stock "3 days".
    if p.get("usual_days_from", "deeper_half") == "deeper_half":
        days = days[depths <= np.median(depths)]
    return {
        "streaks_total": len(done),
        "meaningful": len(meaningful),
        "deep_falls": len(deep),
        "usual_days": float(np.median(days)),
        "usual_days_p75": float(np.percentile(days, 75)),
        "usual_depth_pct": float(np.median(depths)),
        # "deeper" values: 75th / 90th percentile of the fall size -> 25th / 10th of the signed depth
        "depth_p75_pct": float(np.percentile(depths, 25)),
        "depth_p90_pct": float(np.percentile(depths, 10)),
        "depths_pct": [round(float(x), 2) for x in depths],
    }


def current_status(df: pd.DataFrame, streaks: list[Streak], profile: dict, bt: dict, cfg: dict) -> dict:
    """Status of the stock on the last row of df (7.4)."""
    p, st = cfg["pattern"], cfg["status"]
    n = len(df)
    last = n - 1
    res = {"in_streak": False, "status": None, "status_your": None}
    cur = streaks[-1] if streaks and streaks[-1].ongoing else None
    # Deep-fall watchlist: ongoing deep, or ended within the last N trading days and deep
    deep_recent = None
    for s in reversed(streaks):
        if s.ongoing:
            continue
        if last - s.end_idx > p["deep_recent_days"]:
            break
        if s.depth_low * 100 <= p["deep_fall_pct"]:
            deep_recent = s
            break
    if deep_recent is not None:
        res["deep_recent"] = {
            "p0": deep_recent.p0, "depth_low_pct": deep_recent.depth_low * 100,
            "days": deep_recent.down_days, "ended": str(df.index[deep_recent.end_idx].date()),
            "recovery_pct": float(df["Close"].iloc[-1] / df["Low"].iloc[deep_recent.low_idx] - 1) * 100,
        }
    if cur is None:
        return res
    # Note: if the streak is ongoing but its last row is a pending pause day,
    # cur.end_idx < last. Depth still uses the streak's own down days.
    close = float(df["Close"].iloc[-1])
    low_today = float(df["Low"].iloc[-1])
    res.update({
        "in_streak": True, "p0": cur.p0, "p0_date": str(df.index[cur.p0_idx].date()),
        "days_in_streak": cur.down_days,
        "depth_close_pct": (close / cur.p0 - 1) * 100,
        "depth_low_pct": cur.depth_low * 100,
        "close": close,
    })
    usual_days = profile.get("usual_days")

    def classify(level_pct):
        if level_pct is None:
            return None, None
        buy = cur.p0 * (1 + level_pct / 100)
        gap = (close / buy - 1) * 100  # % above the buy price
        touched = low_today <= buy
        if cur.depth_low * 100 <= p["deep_fall_pct"]:
            s = "DEEP"
        elif touched or gap <= st["ready_within_pct"]:
            s = "READY"
        elif gap <= st["close_within_pct"] or (
                usual_days is not None and cur.down_days >= usual_days - st["close_days_before_usual"]):
            s = "GETTING CLOSE"
        else:
            s = "FORMING"
        return s, {"buy_price": buy, "gap_pct": gap, "zone_touched_today": touched,
                   "touched_in_streak": bool(cur.depth_low <= level_pct / 100)}

    best_lvl = bt["best"]["level_pct"] if bt.get("best") else None
    your_lvl = bt["yours"]["level_pct"] if bt.get("yours") else None
    sb, db = classify(best_lvl)
    sy, dy = classify(your_lvl)
    res["best"], res["your"] = db, dy
    res["status_best"], res["status_your"] = sb, sy
    res["status"] = sy if cfg["status"]["status_level"] == "your_method" else sb
    if res["status"] is None:
        res["status"] = sy or sb
    return res


def analyse_stock(df: pd.DataFrame, cfg: dict, mood: pd.Series | None = None) -> dict:
    """Run streaks, profile, backtests, mover stats and current status for one stock."""
    p = cfg["pattern"]
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    sma = df["Close"].rolling(p["trend_sma_days"]).mean()
    # Only learn from the last N years (older rows are kept just for the 200-day average)
    start = df.index.max() - pd.DateOffset(years=cfg["data"]["history_years"])
    streaks = find_streaks(df, p["pause_max_green_pct"], p["max_pauses"], p["min_down_days"])
    hist = [s for s in streaks if df.index[s.p0_idx] >= start]
    years = max((df.index.max() - max(start, df.index.min())).days / 365.25, 0.01)
    prof = build_profile(hist, cfg)
    your_lvl = prof["usual_depth_pct"] * p["your_method_factor"] if prof["usual_depth_pct"] is not None else None
    bt = run_backtests(df, hist, your_lvl, cfg, mood, sma, years)
    status = current_status(df, streaks, prof, bt, cfg)
    sv = sma.iloc[-1]
    one_year = df.index.max() - pd.DateOffset(years=1)
    bounce_lows = [(str(df.index[s.low_idx].date()), float(df["Low"].iloc[s.low_idx]))
                   for s in hist if not s.ongoing and df.index[s.low_idx] >= one_year
                   and s.depth_low * 100 <= p["meaningful_depth_pct"]]
    last_year = df[df.index >= one_year]
    ret60 = float(df["Close"].iloc[-1] / df["Close"].iloc[-61] - 1) * 100 if len(df) > 61 else None
    return {
        "bounce_lows": bounce_lows,
        "low_52w": float(last_year["Low"].min()) if not last_year.empty else None,
        "high_52w": float(last_year["High"].max()) if not last_year.empty else None,
        "ret_60d": ret60,
        "ret_1d": float(df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100 if len(df) > 1 else None,
        "ret_5d": float(df["Close"].iloc[-1] / df["Close"].iloc[-6] - 1) * 100 if len(df) > 5 else None,
        "last_date": str(df.index[-1].date()),
        "last_close": float(df["Close"].iloc[-1]),
        "rows": len(df),
        "years": years,
        "profile": prof,
        "backtest": bt,
        "status": status,
        "mover": mover_stats(df, cfg),
        "liquidity_crore": liquidity_crore(df, cfg),
        "trend": None if not np.isfinite(sv) else ("Uptrend" if df["Close"].iloc[-1] > sv else "Downtrend"),
    }
