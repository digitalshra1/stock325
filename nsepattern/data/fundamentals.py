"""Fundamentals from yfinance `info` + `calendar` (weekly, cached), and the GOOD / WEAK / UNKNOWN grade.

Units (normalised here, verified against yfinance output):
  profit_margin, roe, revenue_growth, earnings_growth -> %
  debt_to_equity -> ratio (yfinance gives it x100, e.g. 8.6 means 0.086)
  dividend_yield -> % (yfinance already gives percent)
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import date, datetime

import pandas as pd
import yfinance as yf

from nsepattern.config import ROOT

log = logging.getLogger("nsepattern.fund")
FUND_DIR = ROOT / "data" / "fundamentals"  # kept in the repository (changes only quarterly)
FIN_WORDS = ("bank", "financial", "finance", "insurance", "nbfc", "capital markets", "credit")


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _pct(x):
    v = _f(x)
    return None if v is None else v * 100


def _ts_date(x):
    if x is None:
        return None
    try:
        if isinstance(x, (int, float)):
            return datetime.utcfromtimestamp(x).date().isoformat()
        if isinstance(x, (date, datetime)):
            return (x.date() if isinstance(x, datetime) else x).isoformat()
        if isinstance(x, list) and x:
            return _ts_date(x[0])
    except (ValueError, OSError, OverflowError):
        return None
    return None


def fetch_one(symbol: str) -> dict:
    t = yf.Ticker(f"{symbol}.NS")
    out: dict = {"symbol": symbol, "fetched": date.today().isoformat()}
    try:
        i = t.info or {}
    except Exception as e:  # noqa: BLE001
        log.warning("%s: fundamentals not available (%s)", symbol, e)
        i = {}
    dte = _f(i.get("debtToEquity"))
    out.update({
        "name": i.get("longName") or i.get("shortName"),
        "market_cap": _f(i.get("marketCap")),
        "pe": _f(i.get("trailingPE")),
        "eps": _f(i.get("trailingEps")),
        "profit_margin": _pct(i.get("profitMargins")),
        "roe": _pct(i.get("returnOnEquity")),
        "debt_to_equity": None if dte is None else dte / 100,
        "revenue_growth": _pct(i.get("revenueGrowth")),
        "earnings_growth": _pct(i.get("earningsGrowth")),
        "dividend_yield": _f(i.get("dividendYield")),
        "shares_outstanding": _f(i.get("sharesOutstanding")),
        "yf_sector": i.get("sector"),
        "yf_industry": i.get("industry"),
        "ex_dividend_date": _ts_date(i.get("exDividendDate")),
    })
    try:
        cal = t.calendar or {}
        if isinstance(cal, dict):
            out["earnings_date"] = _ts_date(cal.get("Earnings Date"))
            out["ex_dividend_date"] = _ts_date(cal.get("Ex-Dividend Date")) or out["ex_dividend_date"]
    except Exception:  # noqa: BLE001
        pass
    return out


METRICS = ("eps", "profit_margin", "debt_to_equity", "revenue_growth", "earnings_growth", "roe")


def known_count(d: dict | None) -> int:
    return sum(1 for k in METRICS if (d or {}).get(k) is not None)


def _age_days(d: dict | None, key: str) -> float:
    try:
        return (date.today() - date.fromisoformat((d or {}).get(key) or "")).days
    except ValueError:
        return 1e9


def get_fundamentals(symbols: list[str], max_age_days: float = 30, max_refresh: int = 150,
                     retry_days: float = 7, sleep: float = 0.5) -> dict[str, dict]:
    """Fundamentals change quarterly, so they are kept in the repository (data/fundamentals) and refreshed slowly:
    * a stock is refreshed when its data is older than max_age_days, at most max_refresh stocks per run;
    * a refresh that returns FEWER numbers than what is saved never replaces it (Yahoo often refuses
      cloud servers) — the saved data is kept and that stock is retried after retry_days;
    * a failed first download is not saved, so it is retried on the next run.
    Freshness uses the date stored inside the file (file times are meaningless after a git checkout)."""
    FUND_DIR.mkdir(parents=True, exist_ok=True)
    out, tried, upgraded, kept = {}, 0, 0, 0
    for s in symbols:
        p = FUND_DIR / f"{s}.json"
        old = None
        if p.exists():
            try:
                old = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                old = None
        stale = old is None or _age_days(old, "fetched") > max_age_days
        recently_tried = old is not None and _age_days(old, "attempted") < retry_days
        if not stale or recently_tried or tried >= max_refresh:
            if old is not None:
                out[s] = old
            continue
        tried += 1
        new = fetch_one(s)
        time.sleep(sleep)
        if known_count(new) >= max(known_count(old), 1):
            p.write_text(json.dumps(new), encoding="utf-8")
            out[s] = new
            upgraded += 1
        elif old is not None:
            old["attempted"] = date.today().isoformat()  # keep the good data; try again later
            p.write_text(json.dumps(old), encoding="utf-8")
            out[s] = old
            kept += 1
        # brand-new stock whose download failed: nothing saved, retried next run
    if tried:
        log.info("Fundamentals: %d refreshed, %d kept (download had less data), %d tried this run", upgraded, kept, tried)
    return out


def is_financial(industry: str | None, yf_sector: str | None) -> bool:
    txt = f"{industry or ''} {yf_sector or ''}".lower()
    return any(w in txt for w in FIN_WORDS)


def grade(f: dict, shp: dict | None, industry: str | None, cfg: dict) -> dict:
    """Return {'grade': GOOD/WEAK/UNKNOWN, 'red_flags': [...], 'known': n}."""
    c = cfg["fundamentals"]
    flags = []
    known = 0
    eps, pm = f.get("eps"), f.get("profit_margin")
    if eps is not None or pm is not None:
        known += 1
        if (eps is not None and eps < 0) or (pm is not None and pm < 0):
            flags.append("Loss-making")
    fin = is_financial(industry, f.get("yf_sector"))
    dte = f.get("debt_to_equity")
    if dte is not None and not fin:
        known += 1
        if dte > c["max_debt_to_equity"]:
            flags.append(f"High debt (D/E {dte:.2f})")
    rg = f.get("revenue_growth")
    if rg is not None:
        known += 1
        if rg < c["min_revenue_growth_pct"]:
            flags.append(f"Revenue falling ({rg:+.0f}%)")
    eg = f.get("earnings_growth")
    if eg is not None:
        known += 1
        if eg < c["min_earnings_growth_pct"]:
            flags.append(f"Earnings falling ({eg:+.0f}%)")
    roe = f.get("roe")
    if roe is not None:
        known += 1
        if roe < c["min_roe_pct"]:
            flags.append(f"Low ROE ({roe:.1f}%)")
    if shp and shp.get("pledge") is not None:
        known += 1
        if shp["pledge"] > c["max_pledge_pct"]:
            flags.append(f"High promoter pledge ({shp['pledge']:.1f}%)")
        elif (shp.get("pledge_chg") or 0) > 0:
            flags.append(f"Promoter pledge increased ({shp['pledge_chg']:+.1f} pts)")
    if flags:
        g = "WEAK"
    elif known < c["min_known_metrics"]:
        g = "UNKNOWN"
    else:
        g = "GOOD"
    return {"grade": g, "red_flags": flags, "known": known, "financial": fin}


def industry_pe(fund: dict[str, dict], industries: dict[str, str]) -> dict[str, float]:
    """Median P/E (positive values only) of stocks in the same industry."""
    rows = [(industries.get(s, "Other"), f.get("pe")) for s, f in fund.items()]
    df = pd.DataFrame(rows, columns=["ind", "pe"]).dropna()
    df = df[df["pe"] > 0]
    return df.groupby("ind")["pe"].median().to_dict() if not df.empty else {}
