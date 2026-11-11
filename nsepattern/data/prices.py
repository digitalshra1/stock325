"""Daily OHLCV from yfinance (auto-adjusted for splits, bonus and dividends), cached locally.

Cache: data/cache/prices/<SYMBOL>.csv. On later runs only recent days are fetched.
Because adjusted prices change after every dividend/split, the overlap between the
cache and the new download is compared; if it differs, the full history is re-fetched.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from nsepattern.config import CACHE_DIR

log = logging.getLogger("nsepattern.prices")
PRICE_DIR = CACHE_DIR / "prices"
COLS = ["Open", "High", "Low", "Close", "Volume"]


def yahoo_symbol(sym: str) -> str:
    """NSE symbol -> Yahoo symbol. Indices (^), FX/futures (=) and symbols that already carry an
    exchange suffix (e.g. 000001.SS for Shanghai) are left unchanged."""
    if sym.startswith("^") or "=" in sym or re.search(r"\.[A-Z]{1,3}$", sym):
        return sym
    return f"{sym}.NS"


def _cache_path(sym: str):
    safe = sym.replace("^", "_").replace("=", "_").replace("/", "_")
    return PRICE_DIR / f"{safe}.csv"


def _read_cache(sym: str) -> pd.DataFrame | None:
    p = _cache_path(sym)
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        return df[COLS] if not df.empty else None
    except Exception as e:  # noqa: BLE001
        log.warning("Bad cache for %s (%s); will re-download", sym, e)
        return None


def _write_cache(sym: str, df: pd.DataFrame):
    PRICE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(_cache_path(sym))


def _download(tickers: list[str], start: date, retries: int) -> dict[str, pd.DataFrame]:
    """Download several Yahoo tickers. Returns ticker -> frame (missing ones omitted)."""
    out: dict[str, pd.DataFrame] = {}
    for attempt in range(1, retries + 1):
        try:
            raw = yf.download(tickers, start=start.isoformat(), auto_adjust=True, actions=False,
                              group_by="ticker", threads=True, progress=False)
        except Exception as e:  # noqa: BLE001
            log.warning("yfinance download failed (attempt %d): %s", attempt, e)
            time.sleep(2 ** attempt)
            continue
        if raw is None or raw.empty:
            time.sleep(2 ** attempt)
            continue
        for t in tickers:
            try:
                d = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                d = d[COLS].dropna(subset=["Close"])
                if not d.empty:
                    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
                    out[t] = d
            except KeyError:
                pass
        break
    return out


def get_daily(symbols: list[str], cfg: dict, use_cache: bool = True) -> dict[str, pd.DataFrame]:
    """Return symbol -> daily OHLCV frame. Failures are logged and simply left out."""
    dc = cfg["data"]
    full_start = date.today() - timedelta(days=int(365.25 * dc["history_years"]) + dc["extra_history_days"])
    result: dict[str, pd.DataFrame] = {}
    need_full: list[str] = []
    need_inc: dict[str, pd.DataFrame] = {}
    for s in symbols:
        c = _read_cache(s) if use_cache else None
        if c is None or c.index.min().date() > full_start + timedelta(days=10):
            need_full.append(s)
        else:
            need_inc[s] = c

    chunk = dc["download_chunk_size"]
    # incremental: fetch the last ~15 days and merge
    inc_syms = list(need_inc)
    for i in range(0, len(inc_syms), chunk):
        part = inc_syms[i:i + chunk]
        got = _download([yahoo_symbol(s) for s in part], date.today() - timedelta(days=15), dc["retries"])
        for s in part:
            new = got.get(yahoo_symbol(s))
            old = need_inc[s]
            if new is None:
                log.warning("%s: no fresh data, using cache up to %s", s, old.index.max().date())
                result[s] = old
                continue
            overlap = old.index.intersection(new.index)
            if len(overlap) and (abs(old.loc[overlap, "Close"] / new.loc[overlap, "Close"] - 1) > 0.002).any():
                need_full.append(s)  # adjustment changed (dividend/split) -> refetch all
                continue
            merged = pd.concat([old[~old.index.isin(new.index)], new]).sort_index()
            merged = merged[merged.index.date >= full_start]
            _write_cache(s, merged)
            result[s] = merged
        if i + chunk < len(inc_syms):
            time.sleep(dc["download_sleep_sec"])

    for i in range(0, len(need_full), chunk):
        part = need_full[i:i + chunk]
        got = _download([yahoo_symbol(s) for s in part], full_start, dc["retries"])
        for s in part:
            d = got.get(yahoo_symbol(s))
            if d is None:
                log.warning("%s: no data from Yahoo (N/A)", s)
                continue
            _write_cache(s, d)
            result[s] = d
        if i + chunk < len(need_full):
            time.sleep(dc["download_sleep_sec"])
    return result
