"""All NSE equity symbols from NSE's EQUITY_L.csv (EQ series), with a cached last-good copy."""
from __future__ import annotations

import io
import logging

import pandas as pd

from nsepattern.config import CACHE_DIR
from nsepattern.data.http import PoliteSession

log = logging.getLogger("nsepattern.universe")
URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
CACHE = CACHE_DIR.parent / "universe" / "EQUITY_L.csv"  # kept in repo (data/universe)


def load_universe(session: PoliteSession | None = None, refresh: bool = False) -> pd.DataFrame:
    """Returns columns SYMBOL, NAME, SERIES, ISIN, LISTING_DATE (EQ series only)."""
    df = None
    if refresh or not CACHE.exists():
        sess = session or PoliteSession()
        r = sess.get(URL)
        if r is not None:
            try:
                df = pd.read_csv(io.BytesIO(r.content))
                df.columns = [c.strip().upper() for c in df.columns]
                if "SYMBOL" not in df.columns:
                    raise ValueError("unexpected file format")
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(CACHE, index=False)
                log.info("Downloaded NSE stock list: %d rows", len(df))
            except Exception as e:  # noqa: BLE001 - never crash on a bad file
                log.warning("Could not parse EQUITY_L.csv: %s", e)
                df = None
        if df is None:
            log.warning("NSE stock list download failed; using last good copy if any")
    if df is None:
        if not CACHE.exists():
            return pd.DataFrame(columns=["SYMBOL", "NAME", "SERIES", "ISIN", "LISTING_DATE"])
        df = pd.read_csv(CACHE)
        df.columns = [c.strip().upper() for c in df.columns]
    df = df.rename(columns={"NAME OF COMPANY": "NAME", "DATE OF LISTING": "LISTING_DATE",
                            "ISIN NUMBER": "ISIN"})
    df["SERIES"] = df["SERIES"].astype(str).str.strip()
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip().str.upper()
    df = df[df["SERIES"] == "EQ"]
    return df[[c for c in ["SYMBOL", "NAME", "SERIES", "ISIN", "LISTING_DATE"] if c in df.columns]].reset_index(drop=True)


def normalise_symbol(sym: str) -> str:
    s = sym.strip().upper()
    if s.endswith(".NS"):
        s = s[:-3]
    return s
