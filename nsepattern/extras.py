"""Add-on analytics layer: VCP (pattern 3), global markets, industry analytics and LTP data.

Runs AFTER the main analysis and writes output/extras.json. It never changes results.json,
and a failure here never breaks the main run (the caller wraps it).
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from nsepattern.config import CACHE_DIR, OUTPUT_DIR
from nsepattern.data.fundamentals import grade
from nsepattern.data.prices import get_daily
from nsepattern.engine import global_mkts as gm
from nsepattern.engine import vcp
from nsepattern.engine.mover import liquidity_crore
from nsepattern.pipeline import jsonable

log = logging.getLogger("nsepattern.extras")
STREAK_STATUSES = ("READY", "GETTING CLOSE", "FORMING", "DEEP")


def _day_move(df: pd.DataFrame) -> dict:
    """Single-day fluctuation of the last session (for the Top movers tab)."""
    d = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(d) < 22 or d["Open"].iloc[-1] <= 0:
        return {}
    last, prev = d.iloc[-1], d.iloc[-2]
    rng = (d["High"] - d["Low"]) / d["Open"] * 100
    vol = d["Volume"].fillna(0)
    avg_vol = vol.iloc[-21:-1].mean()
    usual = rng.iloc[-61:-1].mean()
    return {
        "open": float(last["Open"]), "high": float(last["High"]), "low": float(last["Low"]),
        "day_range_pct": float(rng.iloc[-1]),
        "usual_range_pct": float(usual),
        "range_x_usual": float(rng.iloc[-1] / usual) if usual > 0 else None,
        "gap_pct": float(last["Open"] / prev["Close"] - 1) * 100,
        "vol_ratio": float(vol.iloc[-1] / avg_vol) if avg_vol > 0 else None,
        "traded_cr": float(last["Close"] * vol.iloc[-1] / 1e7),
    }


def _quote(df: pd.DataFrame) -> dict:
    c = df["Close"].dropna()
    e50 = c.ewm(span=50, adjust=False).mean()
    return {
        **_day_move(df),
        "ltp": float(c.iloc[-1]), "date": str(c.index[-1].date()),
        "chg_1d": float(c.iloc[-1] / c.iloc[-2] - 1) * 100 if len(c) > 1 else None,
        "ret_20d": float(c.iloc[-1] / c.iloc[-21] - 1) * 100 if len(c) > 21 else None,
        "ret_60d": float(c.iloc[-1] / c.iloc[-61] - 1) * 100 if len(c) > 61 else None,
        "above_ema50": bool(c.iloc[-1] > e50.iloc[-1]),
        "spark": [round(float(x), 2) for x in c.tail(30)],
    }


def global_markets(cfg: dict) -> dict:
    gcfg = cfg["global_markets"]
    tick = dict(gcfg["tickers"])
    px = get_daily(list(tick.values()) + ["^NSEI"], cfg)
    nifty = px.get("^NSEI")
    snaps, rels = {}, {}
    for name, t in tick.items():
        d = px.get(t)
        if d is None:
            log.warning("Global market %s (%s): no data today, left out", name, t)
            continue
        s = gm.snapshot(d["Close"], t)
        r = gm.relationship(d["Close"], nifty, gcfg["history_years"], gcfg["min_move_pct"],
                            gcfg["rolling_corr_days"]) if nifty is not None else {"available": False}
        if not s.get("available"):
            continue  # markets without data are not shown at all
        snaps[name], rels[name] = s, r
    return {"snapshots": snaps, "relationships": rels,
            "nifty": gm.snapshot(nifty["Close"], "^NSEI") if nifty is not None else {"available": False},
            "outlook": gm.outlook(rels, snaps),
            "updated": pd.Timestamp.now(tz="Asia/Kolkata").strftime("%d %b %Y, %H:%M IST")}


def refresh_global(cfg: dict) -> dict:
    """Morning job (~08:45 IST): refresh only the global-markets part of extras.json (about a minute)."""
    p = OUTPUT_DIR / "extras.json"
    ex = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    ex["global"] = jsonable(global_markets(cfg))
    p.write_text(json.dumps(ex, ensure_ascii=False), encoding="utf-8")
    o = ex["global"].get("outlook", {})
    log.info("Global refreshed: expected NIFTY open %s (%s)", o.get("expected_gap_pct"), o.get("direction"))
    return ex


def industry_analytics(results: dict, quotes: dict, vcp_res: dict) -> list[dict]:
    rows: dict[str, dict] = {}
    stocks = results.get("stocks", {})
    for s, a in stocks.items():
        if a["grade"]["grade"] == "WEAK":
            continue  # strict separation: WEAK never feeds main rankings
        ind = a["industry"]
        r = rows.setdefault(ind, {"industry": ind, "stocks": 0, "dist": {}, "hit": [], "exp": [], "ret20": [],
                                  "ret60": [], "above": [], "vcp_hit": []})
        r["stocks"] += 1
        stt = a["status"].get("status")
        if stt:
            r["dist"][stt] = r["dist"].get(stt, 0) + 1
        b = a["backtest"].get("best")
        if b and b.get("hit_rate") is not None:
            r["hit"].append(b["hit_rate"] * 100)
            r["exp"].append(b["expectancy"] * 100)
        q = quotes.get(s)
        if q:
            r["ret20"].append(q["ret_20d"])
            r["ret60"].append(q["ret_60d"])
            r["above"].append(1.0 if q["above_ema50"] else 0.0)
    for s, v in vcp_res.items():
        if v.get("grade") == "WEAK":
            continue
        ind = v.get("industry", "Other")
        r = rows.setdefault(ind, {"industry": ind, "stocks": 0, "dist": {}, "hit": [], "exp": [], "ret20": [],
                                  "ret60": [], "above": [], "vcp_hit": []})
        # VCP is reference-only: it is NOT counted in industry matching/distribution
        hr = (v.get("history") or {}).get("hit_rate")
        if hr is not None:
            r["vcp_hit"].append(hr * 100)
    out = []
    med = lambda x: float(np.nanmedian([y for y in x if y is not None])) if any(y is not None for y in x) else None  # noqa: E731
    for r in rows.values():
        out.append({
            "industry": r["industry"], "stocks": r["stocks"],
            "matching": sum(r["dist"].values()), "distribution": r["dist"],
            "avg_win_rate": float(np.mean(r["hit"])) if r["hit"] else None,
            "avg_expectancy": float(np.mean(r["exp"])) if r["exp"] else None,
            "vcp_accuracy": float(np.mean(r["vcp_hit"])) if r["vcp_hit"] else None,
            "ret_20d": med(r["ret20"]), "ret_60d": med(r["ret60"]),
            "pct_above_ema50": float(np.mean(r["above"]) * 100) if r["above"] else None,
        })
    df = pd.DataFrame(out)
    if not df.empty:
        rk = lambda col: df[col].rank(pct=True).fillna(0.5)  # noqa: E731
        df["momentum_score"] = (0.4 * rk("ret_20d") + 0.3 * rk("ret_60d") + 0.3 * rk("pct_above_ema50")) * 100
        df = df.sort_values("momentum_score", ascending=False)
        df["rank"] = range(1, len(df) + 1)
    return df.to_dict(orient="records")


def run_extras(cfg: dict, prices: dict[str, pd.DataFrame] | None = None) -> dict:
    res_path = OUTPUT_DIR / "results.json"
    results = json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() else {"stocks": {}}
    if prices is None:
        from nsepattern.data.universe import load_universe
        cached = {p.stem for p in (CACHE_DIR / "prices").glob("*.csv")}
        syms = [s for s in load_universe()["SYMBOL"] if s in cached]
        prices = get_daily(syms, cfg)
    liquid = {s: d for s, d in prices.items()
              if len(d) >= 30 and (liquidity_crore(d, cfg) or 0) >= cfg["filters"]["liquidity_min_crore"]}
    log.info("Extras: %d liquid stocks", len(liquid))
    quotes = {s: _quote(d) for s, d in liquid.items()}

    from nsepattern.data.fundamentals import get_fundamentals
    from nsepattern.data import nse
    from nsepattern.data.http import PoliteSession
    fund = get_fundamentals(list(liquid), cfg["fundamentals"].get("max_age_days", 30), cfg["fundamentals"].get("refresh_per_run", 150))
    ind_map = nse.industry_map(PoliteSession(cfg["data"]["nse_sleep_sec"], cfg["data"]["retries"]))
    vres = {}
    for i, (s, d) in enumerate(liquid.items(), 1):
        try:
            r = vcp.detect(d, cfg)
            r["history"] = vcp.history(d, cfg)
        except Exception as e:  # noqa: BLE001 - one bad series must not stop the scan
            log.warning("VCP failed for %s: %s", s, e)
            continue
        a = results["stocks"].get(s)
        ind = a["industry"] if a else (ind_map.get(s) or (fund.get(s) or {}).get("yf_sector") or "Other")
        g = a["grade"] if a else grade(fund.get(s) or {}, None, ind, cfg)
        r.update({"symbol": s, "industry": ind, "grade": g["grade"], "red_flags": g.get("red_flags", []),
                  "name": (a or {}).get("name") or (fund.get(s) or {}).get("name") or s})
        vres[s] = r
        if i % 200 == 0:
            log.info("VCP scanned %d / %d", i, len(liquid))
    try:
        glob = global_markets(cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("Global markets failed: %s", e)
        glob = {"error": str(e)}
    ind = industry_analytics(results, quotes, vres)
    out = jsonable({"generated": pd.Timestamp.now().isoformat(timespec="minutes"), "quotes": quotes,
                    "vcp": vres, "global": glob, "industries": ind})
    (OUTPUT_DIR / "extras.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    n_set = sum(1 for v in vres.values() if v["status"] == "VCP SETUP")
    n_bo = sum(1 for v in vres.values() if v["status"] == "VCP BREAKOUT")
    log.info("Extras done: VCP setups %d, breakouts %d", n_set, n_bo)
    return out
