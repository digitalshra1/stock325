"""Demo mode: the full pipeline on synthetic data, offline. Builds output/demo/index.html
with a DEMO DATA banner so the dashboard can be checked without network access."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from nsepattern.config import OUTPUT_DIR
from nsepattern.engine import checks as ck
from nsepattern.engine.mood import compute_mood_history, reasons_for
from nsepattern.engine.mover import mover_scores, sheet_table
from nsepattern.engine.profile import analyse_stock
from nsepattern.data.fundamentals import grade
from nsepattern.pipeline import _chart, jsonable

INDUSTRIES = ["Capital Goods", "Healthcare", "Information Technology", "Chemicals", "Automobile and Auto Components",
              "Financial Services", "Consumer Durables", "Metals & Mining"]


def synth_prices(rng, n=900, start=500.0, vol=0.022, drift=0.0004, force_tail=None) -> pd.DataFrame:
    idx = pd.bdate_range(end=date.today() - timedelta(days=1), periods=n)
    r = rng.normal(drift, vol, n)
    # plant some multi-day falls followed by bounces (the pattern)
    for k in rng.choice(np.arange(30, n - 40), size=n // 25, replace=False):
        days = rng.integers(2, 6)
        r[k:k + days] = -abs(rng.normal(0.018, 0.008, days))
        r[k + days:k + days + 3] = abs(rng.normal(0.02, 0.01, 3))
    if force_tail is not None:
        r[-len(force_tail):] = force_tail
    close = start * np.exp(np.cumsum(r))
    opn = close / np.exp(r) * np.exp(rng.normal(0, 0.003, n))
    hi = np.maximum(opn, close) * (1 + np.abs(rng.normal(0.008, 0.006, n)))
    lo = np.minimum(opn, close) * (1 - np.abs(rng.normal(0.008, 0.006, n)))
    vol_ = rng.integers(200_000, 3_000_000, n).astype(float)
    return pd.DataFrame({"Open": opn, "High": hi, "Low": lo, "Close": close, "Volume": vol_}, index=idx)


def run_demo(cfg: dict) -> dict:
    rng = np.random.default_rng(7)
    nifty = synth_prices(rng, start=24000, vol=0.008, drift=0.0003)["Close"]
    vix = pd.Series(14 + rng.normal(0, 1.5, len(nifty)), index=nifty.index).clip(10, 30)
    mood_hist = compute_mood_history({"nifty": nifty, "india_vix": vix}, cfg)
    stocks = {}
    tails = {0: [-0.02, -0.025, -0.022, -0.02], 1: [-0.018, -0.02, -0.015], 2: [-0.03, -0.035, -0.04, -0.03],
             3: [-0.015, -0.012], 4: [-0.025, -0.03, -0.02, -0.028, -0.02]}
    raw = {}
    for i in range(40):
        sym = f"DEMO{i + 1:02d}"
        raw[sym] = synth_prices(rng, start=float(rng.uniform(80, 3000)), vol=float(rng.uniform(0.015, 0.03)),
                                force_tail=tails.get(i % 8))
    DEMO_RAW.clear()
    DEMO_RAW.update(raw)
    DEMO_RAW["^NSEI"] = synth_prices(np.random.default_rng(11), start=24000, vol=0.008, drift=0.0003)
    ms = mover_scores({s: ((d["High"] - d["Low"]) / d["Open"]).tail(60).mean() * 100 for s, d in raw.items()})
    for i, (sym, df) in enumerate(raw.items()):
        a = analyse_stock(df, cfg, mood_hist["mood"])
        fund = {"eps": float(rng.normal(20, 15)), "profit_margin": float(rng.normal(10, 6)), "roe": float(rng.normal(15, 7)),
                "debt_to_equity": float(abs(rng.normal(0.6, 0.6))), "revenue_growth": float(rng.normal(10, 12)),
                "earnings_growth": float(rng.normal(8, 20)), "pe": float(abs(rng.normal(30, 10))),
                "market_cap": float(rng.uniform(2e10, 5e12)), "dividend_yield": float(abs(rng.normal(1, 0.8)))}
        shp = {"promoter": float(rng.uniform(30, 75)), "pledge": float(max(0, rng.normal(2, 6))), "fii": 15.0, "dii": 12.0}
        ind = INDUSTRIES[i % len(INDUSTRIES)]
        g = grade(fund, shp, ind, cfg)
        a.update({"symbol": sym, "name": f"Demo Company {i + 1}", "industry": ind, "industry_pe": 28.0,
                  "fundamentals": fund, "shareholding": shp, "grade": g, "mover_score": ms[sym], "excluded": [],
                  "band": "20", "chart": _chart(df, cfg), "sheet": sheet_table(df, cfg, 20).reset_index(names="Date")})
        st = a["status"]
        if st.get("status") in ("READY", "GETTING CLOSE"):
            lvl = st.get("best") or st.get("your")
            news = {"headlines": [{"date": str(date.today()), "title": "Demo headline: quarterly update", "link": "#",
                                   "source": "Demo", "matches": []}], "announcements": [], "negative": [],
                    "sources_ok": ["Demo"]}
            if i % 5 == 0:
                h = {"date": str(date.today()), "title": "Demo headline: SEBI probe into demo company", "link": "#",
                     "source": "Demo", "matches": ["probe", "sebi"]}
                news["headlines"].append(h)
                news["negative"].append(h)
            a["news"] = news
            a["checks"] = [
                ck.check_fundamentals(g), ck.check_why_falling(st["depth_close_pct"], -2.0, -1.0, cfg),
                ck.check_news(news), ck.check_delivery(38, 45, cfg), ck.check_volume(1.2e6, 1e6, cfg),
                ck.check_deals(pd.DataFrame(columns=["side", "qty"]), 1e8, cfg), ck.check_pledge(shp, cfg),
                ck.check_support(lvl["buy_price"] if lvl else None, a["bounce_lows"], a["low_52w"], cfg),
                ck.check_results([], [], cfg), ck.check_surveillance(sym, {}, {sym: "20"}, cfg)]
            a["check_summary"] = ck.summarise(a["checks"])
            a["rank"] = ck.ranking_score(a["backtest"]["best"] or a["backtest"]["yours"], ms[sym], a["trend"], 2.0, cfg)
        for k in ("best_trades", "your_trades"):
            a["backtest"][k] = a["backtest"][k].tail(cfg["pattern"]["last_trades_shown"])
        stocks[sym] = a
    last = mood_hist.iloc[-1]
    heat = [{"industry": ind, "stocks": 5, "ret_1d": float(rng.normal(0, 1)), "ret_5d": float(rng.normal(0, 3)),
             "in_streak": int(rng.integers(0, 4))} for ind in INDUSTRIES]
    tile = lambda v: {"last": v, "chg_1d": float(rng.normal(0, 0.8)), "chg_5d": float(rng.normal(0, 2)), "date": str(date.today())}  # noqa: E731
    res = {
        "demo": True, "generated": pd.Timestamp.now().isoformat(timespec="minutes"),
        "data_date": str(nifty.index[-1].date()), "universe": 40, "with_prices": 40, "liquid": 40, "movers": 40,
        "sources": {"Demo data": True},
        "mood": {"mood": last["mood"], "points": int(last["points"]), "reasons": reasons_for(last),
                 "date": str(nifty.index[-1].date()), "indicators": {"breadth_down": 45.0},
                 "tiles": {"NIFTY 50": tile(float(nifty.iloc[-1])), "India VIX": tile(float(vix.iloc[-1])),
                           "S&P 500": tile(7500.0), "Brent crude": tile(85.0), "USD/INR": tile(88.5)}},
        "sector_heat": heat, "stocks": stocks,
    }
    return jsonable(res)


DEMO_RAW: dict[str, pd.DataFrame] = {}


def demo_extras(res: dict, cfg: dict) -> dict:
    """Synthetic add-on data (VCP, global markets, industries, LTP) for the offline demo."""
    from nsepattern.engine import global_mkts as gm
    from nsepattern.engine import vcp
    from nsepattern.extras import _quote, industry_analytics
    rng = np.random.default_rng(3)
    quotes, vres = {}, {}
    syms = [s for s in DEMO_RAW if not s.startswith("^")]
    for i, s in enumerate(syms):
        d = DEMO_RAW[s]
        quotes[s] = _quote(d)
        r = vcp.detect(d, cfg)
        r["history"] = vcp.history(d, cfg)
        a = res["stocks"][s]
        r.update({"symbol": s, "industry": a["industry"], "grade": a["grade"]["grade"],
                  "red_flags": a["grade"]["red_flags"], "name": a["name"]})
        vres[s] = r
    nifty = DEMO_RAW["^NSEI"]
    snaps, rels = {}, {}
    names = list(cfg["global_markets"]["tickers"])
    for k, name in enumerate(names):
        noise = rng.normal(0, 1, len(nifty))
        lag = np.r_[nifty["Close"].pct_change().fillna(0).to_numpy()[1:] * 100, 0]
        g_ret = (0.35 + 0.05 * k % 0.3) * lag + noise * 0.9
        g = pd.Series(1000 * np.cumprod(1 + g_ret / 100), index=nifty.index)
        snaps[name] = gm.snapshot(g, cfg["global_markets"]["tickers"][name])
        rels[name] = gm.relationship(g, nifty, 3, 0.0, cfg["global_markets"]["rolling_corr_days"])
    glob = {"snapshots": snaps, "relationships": rels, "nifty": gm.snapshot(nifty["Close"], "^NSEI"),
            "outlook": gm.outlook(rels, snaps)}
    from nsepattern.pipeline import jsonable
    return jsonable({"generated": str(pd.Timestamp.now())[:16], "quotes": quotes, "vcp": vres, "global": glob,
                     "industries": industry_analytics(res, quotes, vres)})


def build_demo(cfg: dict, password: str | None = None):
    from nsepattern.dashboard.build import build
    res = run_demo(cfg)
    return build(res, cfg, password, positions=None, out_dir=OUTPUT_DIR / "site" / "demo",
                 use_saved_password=False, extras=demo_extras(res, cfg))
