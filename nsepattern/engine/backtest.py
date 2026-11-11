"""Buy-level backtest (prompt section 7.3).

For every historical streak and a buy level L (e.g. -0.05):
* buy price = P0 x (1 + L)
* the limit order is live from the day AFTER the first down day through the day
  after the streak's last down day. It fills on the first day with Low <= buy
  price, at min(buy price, that day's Open) (a gap-down fills at the open).
* target = fill x (1 + target). Checked with the day's High from the NEXT day
  after the fill, for up to ``stats_window_days`` days.
* MAE = worst Low from the fill day up to and including the target day (or the
  window end), as % below the fill. Conservative: includes the fill day and the
  target day.
* result = +target if hit, else close at window end vs fill.
* trades whose window runs past the end of data are skipped unless already hit.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from nsepattern.engine.streaks import Streak


def simulate_level(df: pd.DataFrame, streaks: list[Streak], level: float, target: float,
                   window: int, mood: pd.Series | None = None,
                   sma: pd.Series | None = None) -> pd.DataFrame:
    """Simulate one buy level over all streaks. level/target are fractions (-0.05, 0.0325)."""
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    lo = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    dates = df.index
    n = len(c)
    sma_arr = sma.reindex(dates).to_numpy(float) if sma is not None else np.full(n, np.nan)
    mood_arr = mood.reindex(dates).to_numpy(object) if mood is not None else np.full(n, None)
    rows = []
    for s in streaks:
        buy = s.p0 * (1 + level)
        start = s.first_idx + 1
        stop = min(s.end_idx + 1, n - 1)
        fill_idx = None
        for i in range(start, stop + 1):
            if lo[i] <= buy:
                fill_idx = i
                break
        if fill_idx is None:
            continue
        fill = min(buy, o[fill_idx]) if np.isfinite(o[fill_idx]) else buy
        tgt = fill * (1 + target)
        last = fill_idx + window
        hit_idx = None
        for j in range(fill_idx + 1, min(last, n - 1) + 1):
            if h[j] >= tgt:
                hit_idx = j
                break
        if hit_idx is None and last > n - 1:
            continue  # window not finished yet and no hit -> unknown outcome, skip
        end_j = hit_idx if hit_idx is not None else last
        mae = min(0.0, float(np.nanmin(lo[fill_idx:end_j + 1]) / fill - 1))
        result = target if hit_idx is not None else float(c[last] / fill - 1)
        sv = sma_arr[s.p0_idx]
        trend = "Unknown" if not np.isfinite(sv) else ("Uptrend" if s.p0 > sv else "Downtrend")
        md = mood_arr[fill_idx]
        rows.append({
            "p0_date": dates[s.p0_idx], "fill_date": dates[fill_idx],
            "exit_date": dates[end_j], "p0": s.p0, "buy_price": buy, "fill": fill,
            "hit": hit_idx is not None,
            "days_to_target": (hit_idx - fill_idx) if hit_idx is not None else None,
            "mae": mae, "result": result,
            "mood": md if isinstance(md, str) else "Unknown", "trend": trend,
            "streak_days": s.down_days, "streak_depth": s.depth_low,
        })
    return pd.DataFrame(rows)


HIT_DAYS = (5, 10, 15, 20, 30)


def hit_by_days(trades: pd.DataFrame, horizons=HIT_DAYS) -> dict:
    """Extra, reference-only view: % of the same trades that reached the target within N trading days
    (counted from the day after the buy). Does not change any other statistic."""
    n = len(trades)
    if n == 0:
        return {str(h): None for h in horizons}
    d = trades["days_to_target"]
    return {str(h): float(((trades["hit"]) & (d <= h)).sum() / n) for h in horizons}


def level_stats(trades: pd.DataFrame, target: float, years: float) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "hits": 0, "hit_rate": None, "median_days": None, "avg_mae": None,
                "p10_mae": None, "trades_per_year": 0.0, "expectancy": None, "hit_by_days": hit_by_days(trades)}
    hits = int(trades["hit"].sum())
    misses = trades.loc[~trades["hit"], "result"]
    p10_mae = float(np.percentile(trades["mae"], 10))
    # Avg miss result. If there are no misses yet, assume the worst-typical dip (p10 MAE),
    # so a small perfect record is not treated as risk-free.
    avg_miss = float(misses.mean()) if len(misses) else p10_mae
    p_adj = (hits + 2) / (n + 4)
    exp = p_adj * target + (1 - p_adj) * avg_miss
    days = trades.loc[trades["hit"], "days_to_target"]
    return {
        "trades": n, "hits": hits, "hit_rate": hits / n,
        "median_days": float(days.median()) if len(days) else None,
        "avg_mae": float(trades["mae"].mean()), "p10_mae": p10_mae,
        "trades_per_year": n / years if years > 0 else 0.0,
        "expectancy": exp, "avg_miss": avg_miss if len(misses) else None,
        "hit_by_days": hit_by_days(trades),
    }


def split_hit_rate(trades: pd.DataFrame, col: str) -> dict:
    out = {}
    if trades.empty:
        return out
    for k, g in trades.groupby(col):
        out[k] = {"trades": len(g), "hit_rate": float(g["hit"].mean())}
    return out


def confidence_label(n: int, cfg: dict) -> str:
    p = cfg["pattern"]
    if n >= p["confidence_high_trades"]:
        return "High"
    if n >= p["confidence_medium_trades"]:
        return "Medium"
    return "Low"


def test_levels(cfg: dict) -> list[float]:
    p = cfg["pattern"]
    a, b, st = p["test_level_from_pct"], p["test_level_to_pct"], p["test_level_step_pct"]
    k = int(round(abs(b - a) / st))
    return [round(a - i * st, 4) for i in range(k + 1)]


def run_backtests(df: pd.DataFrame, streaks: list[Streak], your_level_pct: float | None,
                  cfg: dict, mood: pd.Series | None, sma: pd.Series | None, years: float) -> dict:
    target = cfg["trading"]["target_pct"] / 100
    window = cfg["pattern"]["stats_window_days"]
    table = []
    trades_by_level = {}
    for lvl in test_levels(cfg):
        tr = simulate_level(df, streaks, lvl / 100, target, window, mood, sma)
        st = level_stats(tr, target, years)
        st["level_pct"] = lvl
        st["kind"] = "tested"
        table.append(st)
        trades_by_level[lvl] = tr

    best = None
    min_n = cfg["pattern"]["min_trades_for_best"]
    for st in table:
        if st["trades"] >= min_n and st["expectancy"] is not None:
            if best is None or st["expectancy"] > best["expectancy"]:
                best = st

    yours = None
    your_trades = pd.DataFrame()
    if your_level_pct is not None and math.isfinite(your_level_pct):
        your_trades = simulate_level(df, streaks, your_level_pct / 100, target, window, mood, sma)
        yours = level_stats(your_trades, target, years)
        yours["level_pct"] = your_level_pct
        yours["kind"] = "your_method"

    def pack(st, tr):
        if st is None:
            return None
        d = dict(st)
        d["confidence"] = confidence_label(st["trades"], cfg)
        d["by_mood"] = split_hit_rate(tr, "mood")
        d["by_trend"] = split_hit_rate(tr, "trend")
        return d

    best_tr = trades_by_level[best["level_pct"]] if best else pd.DataFrame()
    return {
        "table": table,
        "best": pack(best, best_tr),
        "best_trades": best_tr,
        "yours": pack(yours, your_trades),
        "your_trades": your_trades,
    }
