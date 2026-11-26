"""Live READY journal (display only: never changes status, score, checks or alerts).

Records, from the day the system went live, every stock the dashboard showed as READY on the
main list (GOOD / UNKNOWN fundamentals, not excluded). One entry = one READY spell: consecutive
READY days count once; if the stock leaves READY and comes back, that is a new entry. Each scan
then measures what happened after, from the daily prices:

* bought = the day's Low reached the buy level (best tested or your method), from the first
  READY day until the day after that fall ended (same rule as the backtest), at min(buy, Open);
* hit = +target reached by the day's High from the next day on, with no time limit (your rule);
* lowest = the lowest Low from the buy (the buy day included: the price passes the buy level
  before it can go lower) until the target day, or until now;
* stop-loss test (2/3/4/5 %): stopped if that lowest point reached the stop before the target
  (a stop and the target on the same day counts as stopped, the cautious choice).

The journal is saved encrypted (APP_PASSWORD) in data/state, like the other state files. If a
scan was missed, the next one rebuilds the missing days with the same rules (marked "rebuilt").
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

from nsepattern.config import ROOT
from nsepattern.engine import research
from nsepattern.engine.profile import analyse_stock
from nsepattern.engine.streaks import find_streaks
from nsepattern.state import _load, _save

log = logging.getLogger("nsepattern.livelog")
MAIN_GRADES = ("GOOD", "UNKNOWN")


def log_path(cfg: dict):
    return ROOT / cfg.get("research", {}).get("live_log_file", "data/state/readylog.enc.json")


LOG_VERSION = 2
STOPS = (2, 3, 4, 5)


def empty_log(since: str) -> dict:
    return {"version": LOG_VERSION, "since": since, "days": {}, "entries": {}, "open": {}}


def _entry(s: str, a: dict, d: str, cfg: dict, nifty5, mood, df: pd.DataFrame, rebuilt: bool) -> dict:
    st, bt = a["status"], a["backtest"]
    lv_b = (bt.get("best") or {}).get("level_pct")
    lv_y = (bt.get("yours") or {}).get("level_pct")
    i = len(df) - 1
    A = research.Arrays(df)
    fl = []
    if lv_b is not None and st.get("best"):
        fl = research.flags_at(A, i, st["best"]["buy_price"], lv_b, research._nifty5_at(nifty5, df.index[i]),
                               bool(st["best"].get("zone_touched_today")))
    md = mood.asof(df.index[i]) if mood is not None and len(mood) else None
    return {"symbol": s, "p0_date": st.get("p0_date"), "p0": st.get("p0"), "first_ready": d, "last_ready": d, "ready_count": 1,
            "best_level": lv_b, "your_level": lv_y, "flags": fl, "mood": md if isinstance(md, str) else None,
            "grade": a["grade"]["grade"], "industry": a.get("industry"), "rebuilt": rebuilt}


def _record(lg: dict, d: str, ready: dict[str, dict]) -> None:
    """Consecutive READY days extend the same entry; a READY day after a break starts a new one."""
    prev = max((x for x in lg["days"] if x < d), default=None)
    lg["days"][d] = sorted(ready)
    for s, e in ready.items():
        k = lg["open"].get(s)
        cur = lg["entries"].get(k) if k else None
        if cur and cur["last_ready"] == d:      # this day was already recorded (scan ran twice)
            continue
        if cur and cur["last_ready"] == prev and cur["p0_date"] == e["p0_date"]:
            cur["last_ready"] = d
            cur["ready_count"] += 1
        else:
            k = f"{s}|{d}"
            lg["entries"][k] = e
            lg["open"][s] = k


def _replay_day(d: pd.Timestamp, prices: dict, stocks: dict, cfg: dict, nifty5, mood) -> dict[str, dict]:
    """Rebuild one missed session: what the scan would have shown as READY that day."""
    p = cfg["pattern"]
    out = {}
    for s, a in stocks.items():
        if a["grade"]["grade"] not in MAIN_GRADES or a.get("excluded"):
            continue
        df = prices[s]
        df = df[df.index <= d].dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) < 250 or df.index[-1] != d:
            continue
        # quick filter: only stocks in a falling streak of at least 2% can be READY
        st = find_streaks(df.tail(40), p["pause_max_green_pct"], p["max_pauses"], p["min_down_days"])
        if not st or not st[-1].ongoing or st[-1].depth_low > -0.02:
            continue
        b = analyse_stock(df, cfg, mood)
        if b["status"].get("status") == "READY":
            b["grade"], b["industry"] = a["grade"], a.get("industry")
            out[s] = _entry(s, b, str(d.date()), cfg, nifty5, mood, df, True)
    return out


def update(prices: dict, stocks: dict, cfg: dict, nifty5, mood, sessions: pd.DatetimeIndex,
           password: str | None, path=None) -> dict | None:
    """Add today's READY stocks (and any missed sessions) to the journal; return the dashboard view."""
    if not password:
        log.info("Live READY journal skipped: no APP_PASSWORD")
        return None
    rc = cfg.get("research", {})
    since = str(rc.get("live_since", "2026-11-11"))
    path = path or log_path(cfg)
    lg = _load(path, password, empty_log(since))
    if lg.get("version") != LOG_VERSION:   # older layout: start again (missed days are rebuilt below)
        lg = empty_log(since)
    lg["since"] = since
    sess = [d for d in sessions if str(d.date()) >= since]
    if not sess:
        return None
    today = sess[-1]
    missing = [d for d in sess[:-1] if str(d.date()) not in lg["days"]][-int(rc.get("max_rebuild_days", 15)):]
    for d in missing:
        log.info("Live READY journal: rebuilding missed session %s", d.date())
        _record(lg, str(d.date()), _replay_day(d, prices, stocks, cfg, nifty5, mood))
    ready = {}
    for s, a in stocks.items():
        if (a["status"].get("status") == "READY" and a["grade"]["grade"] in MAIN_GRADES and not a.get("excluded")
                and a.get("last_date") == str(today.date())):
            ready[s] = _entry(s, a, str(today.date()), cfg, nifty5, mood,
                              prices[s].dropna(subset=["Open", "High", "Low", "Close"]), False)
    _record(lg, str(today.date()), ready)
    _save(path, lg, password)
    return view(lg, prices, cfg)


def _outcome(df: pd.DataFrame, e: dict, level: float | None, tgt: float, cfg: dict) -> dict | None:
    if level is None or e.get("p0") is None:
        return None
    p = cfg["pattern"]
    buy = e["p0"] * (1 + level / 100)
    idx = df.index
    if pd.Timestamp(e["first_ready"]) not in idx:
        return None
    start = idx.get_loc(pd.Timestamp(e["first_ready"]))
    # the fall this entry belongs to: the order stays live until the day after it ends
    end = len(df) - 1
    for s in find_streaks(df, p["pause_max_green_pct"], p["max_pauses"], p["min_down_days"]):
        if str(idx[s.p0_idx].date()) == e["p0_date"]:
            end = min(s.end_idx + 1, len(df) - 1)
            break
    lo, hi, op, cl = (df[k].to_numpy(float) for k in ("Low", "High", "Open", "Close"))
    f = next((i for i in range(start, end + 1) if lo[i] <= buy), None)
    if f is None:
        return {"buy": buy, "bought": False, "closest_pct": float(lo[start:end + 1].min() / buy - 1) * 100,
                "window_open": end == len(df) - 1}
    fill = min(buy, op[f]) if np.isfinite(op[f]) else buy
    seg_h = hi[f + 1:] / fill - 1
    hits = np.flatnonzero(seg_h >= tgt)
    hd = int(hits[0]) + 1 if len(hits) else None
    upto = f + 1 + (hd if hd else len(seg_h))
    dips = lo[f:upto] / fill - 1                      # buy day .. target day (or today)
    worst = float(min(0.0, dips.min())) * 100
    stops = {}
    for sl in STOPS:
        k = np.flatnonzero(dips <= -sl / 100)
        stops[str(sl)] = {"r": "stop", "day": int(k[0])} if len(k) else {"r": "hit" if hd else "open"}
    return {"buy": buy, "bought": True, "date": str(idx[f].date()), "fill": fill, "hit_day": hd,
            "hit_date": str(idx[f + hd].date()) if hd else None, "days": hd if hd else len(seg_h),
            "now_pct": None if hd else float(cl[-1] / fill - 1) * 100, "worst_pct": worst, "stops": stops}


def view(lg: dict, prices: dict, cfg: dict) -> dict:
    tgt = cfg["trading"]["target_pct"] / 100
    rows = []
    for e in lg["entries"].values():
        df = prices.get(e["symbol"])
        if df is None:
            continue
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        r = {k: e[k] for k in ("symbol", "p0_date", "p0", "first_ready", "flags", "mood", "grade", "industry", "rebuilt")}
        r["ready_count"] = e["ready_count"]
        r["last_ready"] = e["last_ready"]
        r["best"] = _outcome(df, e, e.get("best_level"), tgt, cfg)
        r["your"] = _outcome(df, e, e.get("your_level"), tgt, cfg)
        rows.append(r)
    return {"since": lg["since"], "sessions": len(lg["days"]), "stops": list(STOPS), "rows": rows,
            "updated": str(date.today())}
