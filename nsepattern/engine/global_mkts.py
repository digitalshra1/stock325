"""Global markets and their relationship with the next Indian session (add-on module).

No look-ahead: for an Indian trading day D we only use a global index's last session that
CLOSED BEFORE India opened on D, i.e. its session dated strictly before D (US/Europe close
after Indian hours; Asian sessions on D overlap India, so their day D is not used).

For every index we measure, over the last N years:
  * P(NIFTY up | index was up)  and  P(NIFTY down | index was down)  (close-to-close)
  * the same for the NIFTY opening gap
  * Pearson correlation of the index move with the NIFTY gap and with the NIFTY day move
  * a simple regression slope -> expected NIFTY opening from today's global move
Confidence = how sure we are the hit rate is above a coin flip (one-sided binomial z-test).
"""
from __future__ import annotations

import math
from datetime import datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


def _norm_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def confidence(hits: int, n: int) -> float | None:
    """% confidence that the true hit rate is > 50 % (normal approximation)."""
    if n < 10:
        return None
    p = hits / n
    z = (p - 0.5) / math.sqrt(0.25 / n)
    return round(_norm_cdf(z) * 100, 1)


# Yahoo symbol -> (country, flag code, exchange time zone, local closing time)
MARKETS = {
    "^DJI": ("United States", "us", "America/New_York", "16:00"),
    "^IXIC": ("United States", "us", "America/New_York", "16:00"),
    "^GSPC": ("United States", "us", "America/New_York", "16:00"),
    "^RUT": ("United States", "us", "America/New_York", "16:00"),
    "^GDAXI": ("Germany", "de", "Europe/Berlin", "17:30"),
    "^FTSE": ("United Kingdom", "gb", "Europe/London", "16:30"),
    "^FCHI": ("France", "fr", "Europe/Paris", "17:30"),
    "^N225": ("Japan", "jp", "Asia/Tokyo", "15:30"),
    "^HSI": ("Hong Kong", "hk", "Asia/Hong_Kong", "16:00"),
    "000001.SS": ("China", "cn", "Asia/Shanghai", "15:00"),
    "^NSEI": ("India", "in", "Asia/Kolkata", "15:30"),
}
IST = ZoneInfo("Asia/Kolkata")


def session_close_ist(ticker: str, day) -> datetime | None:
    """When the session dated `day` closes, in IST (handles daylight saving automatically)."""
    meta = MARKETS.get(ticker)
    if not meta:
        return None
    hh, mm = map(int, meta[3].split(":"))
    local = datetime.combine(pd.Timestamp(day).date(), dtime(hh, mm), tzinfo=ZoneInfo(meta[2]))
    return local.astimezone(IST)


def snapshot(close: pd.Series, ticker: str | None = None, now: datetime | None = None) -> dict:
    c = close.dropna()
    if len(c) < 30:
        return {"available": False}
    now = now or datetime.now(IST)
    meta = MARKETS.get(ticker or "", (None, None, None, None))
    close_at = session_close_ist(ticker, c.index[-1]) if ticker else None
    complete = close_at is None or now >= close_at
    # change of the last COMPLETED session (used for the expected-opening model)
    done = c if complete else c.iloc[:-1]
    chg_done = float(done.iloc[-1] / done.iloc[-2] - 1) * 100 if len(done) > 1 else None
    r = c.pct_change()
    ema20, ema50 = c.ewm(span=20, adjust=False).mean().iloc[-1], c.ewm(span=50, adjust=False).mean().iloc[-1]
    last = c.iloc[-1]
    trend = "Uptrend" if last > ema20 > ema50 else ("Downtrend" if last < ema20 < ema50 else "Sideways")

    def chg(k):
        return float(last / c.iloc[-1 - k] - 1) * 100 if len(c) > k else None

    return {"available": True, "last": float(last), "date": str(c.index[-1].date()),
            "country": meta[0], "flag": meta[1], "complete": complete, "chg_done": chg_done,
            "close_ist": close_at.strftime("%H:%M IST, %d %b") if close_at else None,
            "chg_1d": chg(1), "chg_1w": chg(5), "chg_1m": chg(21), "trend": trend,
            "volatility_pct": float(r.tail(20).std() * math.sqrt(252) * 100),
            "spark": [round(float(x), 2) for x in c.tail(30)]}


def relationship(glob: pd.Series, nifty: pd.DataFrame, years: int, min_move: float = 0.0,
                 roll: int = 60) -> dict:
    """glob: global index daily close. nifty: NIFTY OHLC frame (Open, Close)."""
    g = glob.dropna().sort_index()
    n = nifty.dropna(subset=["Open", "Close"]).sort_index()
    if len(g) < 60 or len(n) < 60:
        return {"available": False}
    g_ret = g.pct_change() * 100
    dates = n.index
    # last global session strictly before each Indian date
    pos = g_ret.index.searchsorted(dates, side="left") - 1
    valid = pos >= 1
    gm = pd.Series(np.where(valid, g_ret.to_numpy()[np.clip(pos, 0, None)], np.nan), index=dates)
    gap = (n["Open"] / n["Close"].shift(1) - 1) * 100
    day = (n["Close"] / n["Close"].shift(1) - 1) * 100
    f = pd.DataFrame({"g": gm, "gap": gap, "day": day}).dropna()
    f = f[f.index >= f.index.max() - pd.DateOffset(years=years)]
    if len(f) < 60:
        return {"available": False}
    up, dn = f[f["g"] > min_move], f[f["g"] < -min_move]
    up_hits, dn_hits = int((up["day"] > 0).sum()), int((dn["day"] < 0).sum())
    upg_hits, dng_hits = int((up["gap"] > 0).sum()), int((dn["gap"] < 0).sum())
    corr_day = float(f["g"].corr(f["day"]))
    corr_gap = float(f["g"].corr(f["gap"]))
    var = float(f["g"].var())
    slope = float(f["g"].cov(f["gap"]) / var) if var > 0 else 0.0
    icpt = float(f["gap"].mean() - slope * f["g"].mean())
    rc = f["g"].rolling(roll).corr(f["day"]).dropna()
    # monthly sampling keeps the chart light
    rc_m = rc.resample("ME").last().dropna()
    return {
        "available": True, "days": len(f),
        "up_days": len(up), "up_then_up": up_hits / len(up) * 100 if len(up) else None,
        "up_conf": confidence(up_hits, len(up)),
        "down_days": len(dn), "down_then_down": dn_hits / len(dn) * 100 if len(dn) else None,
        "down_conf": confidence(dn_hits, len(dn)),
        "up_then_gap_up": upg_hits / len(up) * 100 if len(up) else None,
        "down_then_gap_down": dng_hits / len(dn) * 100 if len(dn) else None,
        "corr_day": corr_day, "corr_gap": corr_gap, "slope": slope, "intercept": icpt,
        "r2_gap": corr_gap ** 2,
        "rolling_corr": {"d": [str(x.date()) for x in rc_m.index], "v": [round(float(x), 3) for x in rc_m]},
    }


def outlook(rel: dict[str, dict], snaps: dict[str, dict], india_date: pd.Timestamp | None = None) -> dict:
    """Combine every index's latest move into an expected NIFTY opening (R²-weighted regression average)."""
    preds, w, votes = [], [], []
    used = []
    for name, r in rel.items():
        s = snaps.get(name) or {}
        move = s.get("chg_done", s.get("chg_1d"))  # only a session that has already closed
        if not r.get("available") or not s.get("available") or move is None:
            continue
        preds.append(r["intercept"] + r["slope"] * move)
        w.append(max(r["r2_gap"], 1e-6))
        prob = (r["up_then_up"] if move > 0 else r["down_then_down"]) or 50
        votes.append((1 if move > 0 else -1) * (prob - 50) * max(r["corr_day"], 0))
        used.append({"name": name, "move": move, "pred_gap": preds[-1], "weight": w[-1]})
    if not preds:
        return {"available": False}
    exp_gap = float(np.average(preds, weights=w))
    # weighted average edge in percentage points above a coin flip (+ = markets point up)
    cw = sum(max(rel[u["name"]]["corr_day"], 0) for u in used)
    score = float(np.sum(votes) / cw) if cw > 0 else 0.0
    direction = "Positive" if exp_gap > 0.1 else ("Negative" if exp_gap < -0.1 else "Flat")
    trend = "Bullish" if score > 3 else ("Bearish" if score < -3 else "Neutral")
    pos = sum(1 for u in used if u["move"] > 0)
    return {"available": True, "expected_gap_pct": exp_gap, "direction": direction, "trend": trend,
            "trend_score": score, "positive_markets": pos, "markets_used": len(used), "inputs": used,
            "avg_r2": float(np.mean(w))}
