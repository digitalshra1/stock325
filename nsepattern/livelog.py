"""Live READY journal (display only: never changes status, score, checks or alerts).

Records, from the day the system went live, every stock the dashboard showed as READY on the
main list (GOOD / UNKNOWN fundamentals, not excluded), once per fall. Each scan then measures
what happened after, from the daily prices:

* bought = the day's Low reached the buy level (best tested or your method), from the first
  READY day until the day after that fall ended (same rule as the backtest), at min(buy, Open);
* hit = +target reached by the day's High from the next day on, with no time limit (your rule).

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


def empty_log(since: str) -> dict:
    return {"version": 1, "since": since, "days": {}, "entries": {}}


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
    return {"symbol": s, "p0_date": st.get("p0_date"), "p0": st.get("p0"), "first_ready": d, "ready_days": [d],
            "best_level": lv_b, "your_level": lv_y, "flags": fl, "mood": md if isinstance(md, str) else None,
            "grade": a["grade"]["grade"], "industry": a.get("industry"), "rebuilt": rebuilt}


def _record(lg: dict, d: str, ready: dict[str, dict]) -> None:
    lg["days"][d] = sorted(ready)
    for s, e in ready.items():
        k = f"{s}|{e['p0_date']}"
        if k in lg["entries"]:
            if d not in lg["entries"][k]["ready_days"]:
                lg["entries"][k]["ready_days"].append(d)
        else:
            lg["entries"][k] = e


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
    worst = float(min(0.0, lo[f:upto].min() / fill - 1)) * 100
    return {"buy": buy, "bought": True, "date": str(idx[f].date()), "fill": fill, "hit_day": hd,
            "hit_date": str(idx[f + hd].date()) if hd else None, "days": hd if hd else len(seg_h),
            "now_pct": None if hd else float(cl[-1] / fill - 1) * 100, "worst_pct": worst}


def view(lg: dict, prices: dict, cfg: dict) -> dict:
    tgt = cfg["trading"]["target_pct"] / 100
    rows = []
    for e in lg["entries"].values():
        df = prices.get(e["symbol"])
        if df is None:
            continue
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        r = {k: e[k] for k in ("symbol", "p0_date", "p0", "first_ready", "flags", "mood", "grade", "industry", "rebuilt")}
        r["ready_count"] = len(e["ready_days"])
        r["best"] = _outcome(df, e, e.get("best_level"), tgt, cfg)
        r["your"] = _outcome(df, e, e.get("your_level"), tgt, cfg)
        rows.append(r)
    days = [{"date": d, "ready": len(v), "new": sum(1 for e in lg["entries"].values() if e["first_ready"] == d)}
            for d, v in sorted(lg["days"].items())]
    return {"since": lg["since"], "days": days, "rows": rows, "updated": str(date.today())}
