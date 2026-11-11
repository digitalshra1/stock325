"""Fast daily mover statistics (prompt section 6.3) and liquidity (6.2).

This is the Python version of the old Google Sheet +/- columns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def mover_stats(df: pd.DataFrame, cfg: dict) -> dict:
    f = cfg["filters"]
    d = df.dropna(subset=["Open", "High", "Low", "Close"]).tail(f["mover_days"])
    d = d[d["Open"] > 0]
    if len(d) < 10:
        return {"days": len(d), "avg_range_pct": None, "up_hits": {}, "down_hits": {}}
    rng = (d["High"] - d["Low"]) / d["Open"] * 100
    up = {lv: float((d["High"] >= d["Open"] * (1 + lv / 100)).mean() * 100) for lv in f["mover_up_levels"]}
    dn = {lv: float((d["Low"] <= d["Open"] * (1 - lv / 100)).mean() * 100) for lv in f["mover_down_levels"]}
    return {"days": len(d), "avg_range_pct": float(rng.mean()), "up_hits": up, "down_hits": dn}


def liquidity_crore(df: pd.DataFrame, cfg: dict) -> float | None:
    d = df.dropna(subset=["Close", "Volume"]).tail(cfg["filters"]["liquidity_days"])
    if d.empty:
        return None
    return float((d["Close"] * d["Volume"]).mean() / 1e7)


def mover_scores(avg_ranges: dict[str, float | None]) -> dict[str, float | None]:
    """Percentile rank (0-100) of average range across the universe."""
    s = pd.Series({k: v for k, v in avg_ranges.items() if v is not None}, dtype=float)
    out: dict[str, float | None] = {k: None for k in avg_ranges}
    if s.empty:
        return out
    r = s.rank(pct=True) * 100
    out.update({k: float(v) for k, v in r.items()})
    return out


def sheet_table(df: pd.DataFrame, cfg: dict, days: int = 20) -> pd.DataFrame:
    """Day-by-day table like the old sheet: CHG%, LOW/HIGH DIFF, DAY DIFF and level hits."""
    d = df.tail(days + 1).copy()
    prev = d["Close"].shift(1)
    t = pd.DataFrame(index=d.index)
    t["Open"], t["High"], t["Low"], t["Close"] = d["Open"], d["High"], d["Low"], d["Close"]
    t["CHG%"] = (d["Close"] / prev - 1) * 100
    t["LOW DIFF%"] = (d["Low"] / d["Open"] - 1) * 100
    t["HIGH DIFF%"] = (d["High"] / d["Open"] - 1) * 100
    t["LOW DIFF PC%"] = (d["Low"] / prev - 1) * 100
    t["HIGH DIFF PC%"] = (d["High"] / prev - 1) * 100
    t["DAY DIFF%"] = (d["High"] - d["Low"]) / d["Open"] * 100
    for lv in cfg["filters"]["mover_down_levels"]:
        t[f"-{lv}%"] = d["Low"] <= d["Open"] * (1 - lv / 100)
    for lv in cfg["filters"]["mover_up_levels"]:
        t[f"+{lv}%"] = d["High"] >= d["Open"] * (1 + lv / 100)
    return t.iloc[1:]
