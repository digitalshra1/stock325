"""Market mood: NORMAL / CAUTION / STRESSED (prompt section 8).

Computed for every NSE trading day so past trades can be tagged.

No look-ahead: the Indian evening run happens before US markets open, so for an
Indian date D the US / global values used are the last ones known strictly
before D (S&P 500, US VIX, Brent). NIFTY, India VIX and USD/INR use day D itself.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MOOD_TICKERS = {
    "nifty": "^NSEI",
    "india_vix": "^INDIAVIX",
    "sp500": "^GSPC",
    "us_vix": "^VIX",
    "brent": "BZ=F",
    "usdinr": "INR=X",
}
GLOBAL_KEYS = {"sp500", "us_vix", "brent"}  # traded after Indian close -> use previous value


def _asof(series: pd.Series, dates: pd.DatetimeIndex, strictly_before: bool) -> pd.Series:
    s = series.dropna().sort_index()
    if s.empty:
        return pd.Series(np.nan, index=dates)
    idx = s.index.searchsorted(dates, side="left" if strictly_before else "right") - 1
    vals = np.where(idx >= 0, s.to_numpy()[np.clip(idx, 0, None)], np.nan)
    return pd.Series(vals, index=dates)


def compute_mood_history(closes: dict[str, pd.Series], cfg: dict,
                         breadth_down_pct: pd.Series | None = None) -> pd.DataFrame:
    """closes: key (see MOOD_TICKERS) -> daily close series. NIFTY dates drive the calendar.

    Returns a frame indexed by date with each indicator, which rules fired, points and mood.
    Missing inputs just mean that rule cannot fire (shown as N/A in the reasons).
    """
    m = cfg["market_mood"]
    nifty = closes.get("nifty")
    if nifty is None or nifty.dropna().empty:
        return pd.DataFrame()
    dates = pd.DatetimeIndex(nifty.dropna().index)
    al = {}
    for key in MOOD_TICKERS:
        if key in closes and closes[key] is not None:
            al[key] = _asof(closes[key], dates, strictly_before=key in GLOBAL_KEYS)
        else:
            al[key] = pd.Series(np.nan, index=dates)

    def chg(s: pd.Series, k: int) -> pd.Series:
        return (s / s.shift(k) - 1) * 100

    f = pd.DataFrame(index=dates)
    f["nifty_1d"] = chg(al["nifty"], 1)
    f["nifty_5d"] = chg(al["nifty"], 5)
    f["india_vix"] = al["india_vix"]
    f["india_vix_5d"] = chg(al["india_vix"], 5)
    f["sp500_1d"] = chg(al["sp500"], 1)
    f["us_vix"] = al["us_vix"]
    f["brent_5d"] = chg(al["brent"], 5)
    f["usdinr_5d"] = chg(al["usdinr"], 5)
    f["breadth_down"] = breadth_down_pct.reindex(dates) if breadth_down_pct is not None else np.nan

    rules = {
        "NIFTY 1-day fall": (f["nifty_1d"] <= m["nifty_1d_drop_pct"]),
        "NIFTY 5-day fall": (f["nifty_5d"] <= m["nifty_5d_drop_pct"]),
        "India VIX high": (f["india_vix"] >= m["india_vix_level"]),
        "India VIX jump (5d)": (f["india_vix_5d"] >= m["india_vix_5d_rise_pct"]),
        "S&P 500 1-day fall": (f["sp500_1d"] <= m["sp500_1d_drop_pct"]),
        "US VIX high": (f["us_vix"] >= m["us_vix_level"]),
        "Crude oil spike (5d)": (f["brent_5d"] >= m["brent_5d_rise_pct"]),
        "Rupee weakening (5d)": (f["usdinr_5d"] >= m["usdinr_5d_rise_pct"]),
        "Broad selling (breadth)": (f["breadth_down"] >= m["breadth_down_pct"]),
    }
    pts = pd.Series(0, index=dates, dtype=int)
    for name, fired in rules.items():
        fired = fired.fillna(False).astype(bool)
        f["rule:" + name] = fired
        pts += fired.astype(int)
    f["points"] = pts
    f["mood"] = np.where(pts >= m["stressed_points"], "STRESSED",
                         np.where(pts >= m["caution_points"], "CAUTION", "NORMAL"))
    return f


def reasons_for(row: pd.Series) -> list[str]:
    return [c[5:] for c in row.index if c.startswith("rule:") and bool(row[c])]
