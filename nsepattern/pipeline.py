"""Full evening analysis over the whole NSE universe.

Produces output/results.json (used by reports, the dashboard and alerts).
Any failing source is logged and shown as N/A; the run continues.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime

import numpy as np
import pandas as pd

from nsepattern.config import OUTPUT_DIR
from nsepattern.data import nse
from nsepattern.data.fundamentals import get_fundamentals, grade, industry_pe
from nsepattern.data.http import PoliteSession
from nsepattern.data.news import collect_news
from nsepattern.data.prices import get_daily
from nsepattern.data.universe import load_universe
from nsepattern.engine import checks as ck
from nsepattern.engine.mood import MOOD_TICKERS, compute_mood_history, reasons_for
from nsepattern.engine.mover import liquidity_crore, mover_scores, mover_stats, sheet_table
from nsepattern.engine.profile import analyse_stock

log = logging.getLogger("nsepattern.pipeline")
CANDIDATE = ("READY", "GETTING CLOSE")
LAST_PRICES: dict[str, pd.DataFrame] = {}


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, pd.DataFrame):
        return jsonable(o.to_dict(orient="records"))
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.isoformat()[:10]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return None if not math.isfinite(v) else round(v, 6)
    if isinstance(o, np.bool_):
        return bool(o)
    if o is pd.NaT:
        return None
    return o


def breadth_series(prices: dict[str, pd.DataFrame], min_stocks: int) -> pd.Series:
    closes = pd.DataFrame({s: d["Close"] for s, d in prices.items()})
    chg = closes.pct_change(fill_method=None)
    n = chg.notna().sum(axis=1)
    down = (chg < 0).sum(axis=1)
    b = down / n.replace(0, np.nan) * 100
    return b.where(n >= min_stocks)


def industry_indices(prices: dict[str, pd.DataFrame], industries: dict[str, str], members: list[str]) -> dict[str, pd.Series]:
    """Industry index = cumulative product of the median daily return of its stocks."""
    by: dict[str, list[str]] = {}
    for s in members:
        by.setdefault(industries.get(s, "Other"), []).append(s)
    out = {}
    for ind, syms in by.items():
        rets = pd.DataFrame({s: prices[s]["Close"].pct_change(fill_method=None) for s in syms})
        med = rets.median(axis=1, skipna=True).fillna(0)
        out[ind] = (1 + med).cumprod()
    return out


def run_full(cfg: dict, weekly: bool = False, limit: int | None = None) -> dict:
    sess = PoliteSession(cfg["data"]["nse_sleep_sec"], cfg["data"]["retries"])
    sources: dict[str, bool] = {}

    uni = load_universe(sess, refresh=weekly)
    sources["NSE stock list"] = not uni.empty
    names = dict(zip(uni["SYMBOL"], uni["NAME"]))
    syms = list(uni["SYMBOL"])[:limit] if limit else list(uni["SYMBOL"])
    log.info("Universe: %d EQ stocks", len(syms))

    ind_map = nse.industry_map(sess, max_age_days=0 if weekly else 6)
    sources["NSE industry lists"] = bool(ind_map)

    prices = get_daily(syms, cfg)
    prices = {s: d for s, d in prices.items() if len(d) >= 30}
    log.info("Daily prices available for %d stocks", len(prices))
    LAST_PRICES.clear()
    LAST_PRICES.update(prices)  # shared with the add-on extras layer (avoids a second download)

    mood_px = get_daily(list(MOOD_TICKERS.values()), cfg)
    mood_closes = {k: mood_px[t]["Close"] for k, t in MOOD_TICKERS.items() if t in mood_px}
    breadth = breadth_series(prices, cfg["market_mood"]["breadth_min_stocks"])
    mood_hist = compute_mood_history(mood_closes, cfg, breadth)
    mood_series = mood_hist["mood"] if not mood_hist.empty else None

    # ---- quick stats for everyone
    quick = {}
    for s, d in prices.items():
        quick[s] = {"liq": liquidity_crore(d, cfg), "mover": mover_stats(d, cfg)}
    mscore = mover_scores({s: q["mover"]["avg_range_pct"] for s, q in quick.items()})
    f = cfg["filters"]
    liquid = [s for s, q in quick.items() if (q["liq"] or 0) >= f["liquidity_min_crore"]]
    movers = [s for s in liquid if (quick[s]["mover"]["avg_range_pct"] or 0) >= f["mover_min_avg_range_pct"]
              and len(prices[s]) >= 250]
    log.info("Liquid (>= %.0f cr): %d; liquid fast movers with 1y+ history: %d",
             f["liquidity_min_crore"], len(liquid), len(movers))

    fund = get_fundamentals(liquid, cfg["fundamentals"].get("max_age_days", 30), cfg["fundamentals"].get("refresh_per_run", 150))
    industries = {s: ind_map.get(s) or (fund.get(s) or {}).get("yf_sector") or "Other" for s in prices}
    ind_pe = industry_pe({s: fund[s] for s in liquid if s in fund}, industries)
    ind_idx = industry_indices(prices, industries, liquid)

    surv = nse.surveillance(sess)
    bands = nse.price_bands(sess)
    sources["NSE ASM/GSM lists"] = surv is not None
    sources["NSE price bands"] = bands is not None
    cal = nse.results_calendar(sess)
    sources["NSE results calendar"] = cal is not None

    # ---- pattern + grade for every liquid fast mover
    stocks: dict[str, dict] = {}
    # Shareholding changes only quarterly: refresh at most N stale stocks per run, so the load on
    # NSE is spread over the week (a burst of 1,000+ requests gets blocked).
    shp_cap = cfg["data"].get("shareholding_refresh_per_run", 250)
    shp_age = cfg["data"].get("shareholding_max_age_days", 7)
    shp_done = 0
    for i, s in enumerate(movers, 1):
        a = analyse_stock(prices[s], cfg, mood_series)
        age = nse._age_days(f"shp_{s}.json")
        if age >= 1e9 or (age > shp_age and shp_done < shp_cap):
            shp = nse.shareholding(sess, s, max_age_days=0)
            shp_done += 1
        else:
            shp = nse.shareholding(sess, s, max_age_days=1e8)  # cached copy
        g = grade(fund.get(s) or {}, shp, industries.get(s), cfg)
        excl = []
        if surv and s in surv:
            excl.append(surv[s])
        b = (bands or {}).get(s)
        if b and b.replace(".", "").isdigit() and float(b) in [float(x) for x in f["exclude_price_bands"]]:
            excl.append(f"{b}% price band")
        a.update({
            "symbol": s, "name": names.get(s, s), "industry": industries.get(s, "Other"),
            "industry_pe": ind_pe.get(industries.get(s, "Other")),
            "fundamentals": fund.get(s) or {}, "shareholding": shp, "grade": g,
            "mover_score": mscore.get(s), "excluded": excl, "band": b,
            "chart": _chart(prices[s], cfg),
            "sheet": sheet_table(prices[s], cfg, 20).reset_index(names="Date"),
        })
        stocks[s] = a
        if i % 50 == 0:
            log.info("Analysed %d / %d", i, len(movers))

    # ---- checklist for candidates (main and weak)
    cands = [s for s, a in stocks.items() if a["status"].get("status") in CANDIDATE + ("DEEP",)
             or a["status"].get("deep_recent")]
    nifty = mood_closes.get("nifty")
    deliv = None
    if cands and nifty is not None:
        dates = list(nifty.dropna().index[-(cfg["checks"]["delivery_days"] + 25):])
        deliv = nse.delivery_history(sess, dates)
    sources["NSE delivery data"] = deliv is not None
    for s in cands:
        a = stocks[s]
        st = a["status"]
        if st.get("status") not in CANDIDATE:  # deep falls: news only
            a["news"] = _news(sess, s, a, cfg, sources)
            a.pop("_ann", None)
            continue
        a["checks"], a["news"] = _checklist(sess, s, a, prices[s], st, nifty, ind_idx, deliv, surv, bands, cal, cfg, sources)
        a["check_summary"] = ck.summarise(a["checks"])
        lvl = a["backtest"]["best"] if cfg["status"]["status_level"] == "best_tested" else a["backtest"]["yours"]
        rs = None if a["ret_60d"] is None or nifty is None or len(nifty) < 61 else \
            a["ret_60d"] - float(nifty.iloc[-1] / nifty.iloc[-61] - 1) * 100
        a["rank"] = ck.ranking_score(lvl or a["backtest"]["yours"], a["mover_score"], a["trend"], rs, cfg)

    # ---- sector heat (liquid stocks)
    heat = []
    for ind, idx in ind_idx.items():
        mem = [s for s in liquid if industries.get(s) == ind]
        in_streak = sum(1 for s in mem if s in stocks and stocks[s]["status"].get("in_streak"))
        heat.append({"industry": ind, "stocks": len(mem),
                     "ret_1d": float(idx.iloc[-1] / idx.iloc[-2] - 1) * 100 if len(idx) > 1 else None,
                     "ret_5d": float(idx.iloc[-1] / idx.iloc[-6] - 1) * 100 if len(idx) > 5 else None,
                     "in_streak": in_streak})

    last = mood_hist.iloc[-1] if not mood_hist.empty else None
    mood = {
        "mood": None if last is None else last["mood"],
        "points": None if last is None else int(last["points"]),
        "reasons": [] if last is None else reasons_for(last),
        "date": None if last is None else str(mood_hist.index[-1].date()),
        "indicators": {} if last is None else {k: last[k] for k in
                                               ["nifty_1d", "nifty_5d", "india_vix", "india_vix_5d", "sp500_1d",
                                                "us_vix", "brent_5d", "usdinr_5d", "breadth_down"]},
        "tiles": _tiles(mood_px),
        "history": None if mood_hist.empty else mood_hist[["points", "mood"]].tail(30),
    }
    for s, a in stocks.items():  # trim large frames for the output
        for k in ("best_trades", "your_trades"):
            a["backtest"][k] = a["backtest"][k].tail(cfg["pattern"]["last_trades_shown"])
    res = {
        "generated": datetime.now().isoformat(timespec="minutes"),
        "data_date": max((a["last_date"] for a in stocks.values()), default=None),
        "universe": len(syms), "with_prices": len(prices), "liquid": len(liquid), "movers": len(movers),
        "sources": sources, "mood": mood, "sector_heat": heat, "stocks": stocks,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = jsonable(res)
    (OUTPUT_DIR / "results.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def _chart(df: pd.DataFrame, cfg: dict, days: int = 130) -> dict:
    """Compact last ~6 months for the price chart: dates + OHLC arrays (2 decimals)."""
    d = df.tail(days)
    r2 = lambda s: [round(float(x), 2) for x in s]  # noqa: E731
    return {"d": [x.strftime("%Y-%m-%d") for x in d.index], "o": r2(d["Open"]), "h": r2(d["High"]),
            "l": r2(d["Low"]), "c": r2(d["Close"])}


def _tiles(mood_px):
    out = {}
    for label, t in [("NIFTY 50", "^NSEI"), ("India VIX", "^INDIAVIX"), ("S&P 500", "^GSPC"),
                     ("Brent crude", "BZ=F"), ("USD/INR", "INR=X")]:
        d = mood_px.get(t)
        if d is None or len(d) < 6:
            out[label] = None
            continue
        c = d["Close"].dropna()
        out[label] = {"last": float(c.iloc[-1]), "chg_1d": float(c.iloc[-1] / c.iloc[-2] - 1) * 100,
                      "chg_5d": float(c.iloc[-1] / c.iloc[-6] - 1) * 100, "date": str(c.index[-1].date())}
    return out


def _news(sess, s, a, cfg, sources):
    ann = nse.announcements(sess, s, cfg["checks"]["news_days"])
    news = collect_news(sess, s, a["name"], cfg, ann)
    sources["News"] = sources.get("News", False) or bool(news["sources_ok"])
    a["_ann"] = ann
    return news


def _checklist(sess, s, a, df, st, nifty, ind_idx, deliv, surv, bands, cal, cfg, sources):
    c = cfg["checks"]
    news = _news(sess, s, a, cfg, sources)
    p0_date = pd.Timestamp(st["p0_date"])
    last_date = df.index[-1]
    stock_ret = (st["close"] / st["p0"] - 1) * 100
    ind_ret = ck.period_return(ind_idx[a["industry"]], p0_date, last_date) if a["industry"] in ind_idx else None
    nifty_ret = ck.period_return(nifty, p0_date, last_date) if nifty is not None else None

    fall = df[df.index > p0_date]
    before = df[df.index <= p0_date]
    fall_d = base_d = None
    if deliv is not None and s in deliv.columns:
        dcol = deliv[s]
        fall_d = ck.mean_or_none(dcol[dcol.index > p0_date])
        base_d = ck.mean_or_none(dcol[dcol.index <= p0_date].tail(c["delivery_days"]))
    fall_v = ck.mean_or_none(fall["Volume"])
    base_v = ck.mean_or_none(before["Volume"].tail(c["volume_avg_days"]))

    deals = nse.bulk_block_deals(sess, c["bulk_deal_days"], s)
    if deals is not None:
        deals = deals[deals["symbol"] == s]
    a["deals"] = deals if deals is not None else None
    level = st.get("best") or st.get("your")
    fd = a["fundamentals"]
    upcoming = None
    if cal is not None:
        upcoming = list(cal.get(s, []))
    if fd.get("earnings_date"):
        try:
            upcoming = (upcoming or []) + [date.fromisoformat(fd["earnings_date"])]
        except ValueError:
            pass
    checks = [
        ck.check_fundamentals(a["grade"]),
        ck.check_why_falling(stock_ret, ind_ret, nifty_ret, cfg),
        ck.check_news(news),
        ck.check_delivery(fall_d, base_d, cfg),
        ck.check_volume(fall_v, base_v, cfg),
        ck.check_deals(deals, fd.get("shares_outstanding"), cfg),
        ck.check_pledge(a["shareholding"], cfg),
        ck.check_support(level["buy_price"] if level else None, a["bounce_lows"], a["low_52w"], cfg),
        ck.check_results(upcoming, a.pop("_ann", None), cfg, today=last_date.date()),
        ck.check_surveillance(s, surv, bands, cfg),
    ]
    return checks, news
