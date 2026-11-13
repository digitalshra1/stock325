"""Free NSE data: industry map, surveillance (ASM/GSM), price bands, bulk/block deals,
delivery % (full bhavcopy), shareholding + pledge, announcements, results calendar.

Every function returns an empty/None result on failure (logged), never raises.
Cached copies (last good) are kept under data/cache/nse so a blocked day still works.
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
from datetime import date, datetime, timedelta

import pandas as pd

from nsepattern.config import CACHE_DIR, ROOT
from nsepattern.data.http import PoliteSession

log = logging.getLogger("nsepattern.nse")
NSE_DIR = CACHE_DIR / "nse"
ARCH = "https://nsearchives.nseindia.com"
API = "https://www.nseindia.com/api"


def _save(name: str, text: str):
    NSE_DIR.mkdir(parents=True, exist_ok=True)
    (NSE_DIR / name).write_text(text, encoding="utf-8")


def _load(name: str) -> str | None:
    p = NSE_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else None


def _age_days(name: str) -> float:
    p = NSE_DIR / name
    return (time.time() - p.stat().st_mtime) / 86400 if p.exists() else 1e9


def _get_text(sess: PoliteSession, url: str, cache_name: str, max_age_days: float = 0) -> str | None:
    """Fetch url (or use cache younger than max_age_days). Falls back to last good copy."""
    if max_age_days and _age_days(cache_name) < max_age_days:
        return _load(cache_name)
    r = sess.get(url)
    if r is not None:
        _save(cache_name, r.text)
        return r.text
    old = _load(cache_name)
    if old is not None:
        log.warning("Using last good copy of %s", cache_name)
    return old


def _get_json(sess, url, cache_name, max_age_days=0):
    t = _get_text(sess, url, cache_name, max_age_days)
    if t is None:
        return None
    try:
        return json.loads(t)
    except ValueError:
        log.warning("Bad JSON from %s", url)
        return None


# ---------------------------------------------------------------- industry

def industry_map(sess: PoliteSession, max_age_days: float = 6) -> dict[str, str]:
    """Symbol -> Industry from NSE index constituent files (Total Market covers ~750 stocks)."""
    out: dict[str, str] = {}
    for f in ["ind_niftytotalmarket_list.csv", "ind_nifty500list.csv", "ind_niftymicrocap250_list.csv"]:
        t = _get_text(sess, f"{ARCH}/content/indices/{f}", f, max_age_days)
        if not t:
            continue
        try:
            df = pd.read_csv(io.StringIO(t))
            for s, ind in zip(df["Symbol"].astype(str).str.strip(), df["Industry"].astype(str).str.strip()):
                out.setdefault(s.upper(), ind)
        except Exception as e:  # noqa: BLE001
            log.warning("Bad index file %s: %s", f, e)
    return out


# ---------------------------------------------------------------- surveillance and bands

def surveillance(sess: PoliteSession) -> dict[str, str] | None:
    """Symbol -> reason (ASM / GSM stage). None if both lists are unavailable."""
    out: dict[str, str] = {}
    ok = False
    asm = _get_json(sess, f"{API}/reportASM", "asm.json")
    if isinstance(asm, dict):
        ok = True
        for k in ("longterm", "shortterm"):
            for row in (asm.get(k) or {}).get("data", []) or []:
                sym = str(row.get("symbol", "")).upper()
                if sym:
                    out[sym] = f"{'LT' if k == 'longterm' else 'ST'}-ASM {row.get('asmSurvIndicator', '')}".strip()
    gsm = _get_json(sess, f"{API}/reportGSM", "gsm.json")
    if isinstance(gsm, list):
        ok = True
        for row in gsm:
            sym = str(row.get("symbol", "")).upper()
            if sym:
                out[sym] = (out.get(sym, "") + f" GSM {row.get('gsmStage', '')}").strip()
    return out if ok else None


def price_bands(sess: PoliteSession) -> dict[str, str] | None:
    """Symbol -> band ('2','5','10','20','No Band') for EQ/BE series."""
    t = _get_text(sess, f"{ARCH}/content/equities/sec_list.csv", "sec_list.csv")
    if not t:
        return None
    try:
        df = pd.read_csv(io.StringIO(t))
        df.columns = [c.strip() for c in df.columns]
        return {str(s).strip().upper(): str(b).strip() for s, b in zip(df["Symbol"], df["Band"])}
    except Exception as e:  # noqa: BLE001
        log.warning("Bad price band file: %s", e)
        return None


# ---------------------------------------------------------------- deals

def bulk_block_deals(sess: PoliteSession, days: int = 30, symbol: str | None = None) -> pd.DataFrame | None:
    """Bulk + block deals over the last `days`. Columns: date, symbol, client, side, qty, price, kind."""
    to = date.today()
    fr = to - timedelta(days=days)
    rows = []
    ok = False
    for kind in ("bulk_deals", "block_deals"):
        url = (f"{API}/historicalOR/bulk-block-short-deals?optionType={kind}"
               f"&from={fr:%d-%m-%Y}&to={to:%d-%m-%Y}" + (f"&symbol={symbol}" if symbol else ""))
        d = _get_json(sess, url, f"{kind}_{symbol or 'all'}.json")
        if not isinstance(d, dict):
            continue
        ok = True
        for r in d.get("data", []) or []:
            rows.append({
                "date": pd.to_datetime(r.get("BD_DT_DATE"), format="%d-%b-%Y", errors="coerce"),
                "symbol": str(r.get("BD_SYMBOL", "")).upper(),
                "client": r.get("BD_CLIENT_NAME"), "side": str(r.get("BD_BUY_SELL", "")).upper(),
                "qty": float(r.get("BD_QTY_TRD") or 0), "price": float(r.get("BD_TP_WATP") or 0),
                "kind": "Bulk" if kind == "bulk_deals" else "Block",
            })
    if not ok:
        return None
    return pd.DataFrame(rows, columns=["date", "symbol", "client", "side", "qty", "price", "kind"])


# ---------------------------------------------------------------- delivery %

def delivery_history(sess: PoliteSession, dates: list[pd.Timestamp]) -> pd.DataFrame | None:
    """Delivery % per symbol per date (EQ series), from NSE full bhavcopy files.

    Each day's file is cached as a small CSV, so only new days are downloaded.
    Returns a frame indexed by date, columns = symbols. None if nothing is available.
    """
    bdir = NSE_DIR / "delivery"
    bdir.mkdir(parents=True, exist_ok=True)
    frames = {}
    for d in dates:
        p = bdir / f"{d:%Y%m%d}.csv"
        if p.exists():
            s = pd.read_csv(p, index_col=0).iloc[:, 0]
        else:
            r = sess.get(f"{ARCH}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv")
            if r is None:
                continue
            try:
                df = pd.read_csv(io.StringIO(r.text))
                df.columns = [c.strip() for c in df.columns]
                df["SERIES"] = df["SERIES"].astype(str).str.strip()
                df = df[df["SERIES"] == "EQ"]
                s = pd.to_numeric(df["DELIV_PER"].astype(str).str.strip(), errors="coerce")
                s.index = df["SYMBOL"].astype(str).str.strip().str.upper()
                s.name = "deliv_per"
                s.to_frame().to_csv(p)
            except Exception as e:  # noqa: BLE001
                log.warning("Bad bhavcopy for %s: %s", d.date(), e)
                continue
        frames[pd.Timestamp(d)] = s
    if not frames:
        return None
    return pd.DataFrame(frames).T.sort_index()


# ---------------------------------------------------------------- shareholding + pledge

_SHP_CATS = {
    "promoter": "ShareholdingOfPromoterAndPromoterGroup_ContextI",
    "fii": "InstitutionsForeign_ContextI",
    "dii": "InstitutionsDomestic_ContextI",
}


def _parse_shp_xbrl(text: str) -> dict:
    out = {}
    for key, ctx in _SHP_CATS.items():
        m = re.search(r'<in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares[^>]*contextRef="'
                      + ctx + r'"[^>]*>([^<]+)<', text)
        if m:
            try:
                out[key] = float(m.group(1)) * 100
            except ValueError:
                pass
    return out


SHP_DIR = ROOT / "data" / "shareholding"  # kept in the repository: changes only quarterly, and NSE's API
                                          # blocks cloud servers, so the last good copy must survive


def _shp_saved(symbol: str) -> dict | None:
    f = SHP_DIR / f"{symbol}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except ValueError:
        return None


def shareholding_age(symbol: str) -> float:
    """Days since this stock's shareholding was downloaded (1e9 if never)."""
    d = _shp_saved(symbol)
    try:
        return (date.today() - date.fromisoformat(d["fetched"])).days
    except (TypeError, KeyError, ValueError):
        return 1e9


def shareholding(sess: PoliteSession, symbol: str, max_age_days: float = 30) -> dict:
    """Latest and previous quarter promoter / FII / DII %, and promoter pledge %.

    Keys present only when available (missing -> N/A in the UI). A failed download never
    replaces the saved copy.
    """
    old = _shp_saved(symbol)
    if old is not None and shareholding_age(symbol) < max_age_days:
        return old
    out: dict = {}
    master = _get_json(sess, f"{API}/corporate-share-holdings-master?index=equities&symbol={symbol}",
                       f"shpmaster_{symbol}.json")
    if isinstance(master, list) and master:
        rows = sorted(master, key=lambda r: pd.to_datetime(r.get("date"), format="%d-%b-%Y", errors="coerce"),
                      reverse=True)
        quarters = []
        for r in rows[:2]:
            q = {"quarter": r.get("date")}
            try:
                q["promoter"] = float(r.get("pr_and_prgrp"))
            except (TypeError, ValueError):
                pass
            x = r.get("xbrl")
            if x:
                rx = sess.get(x)
                if rx is not None:
                    q.update({k: v for k, v in _parse_shp_xbrl(rx.text).items() if k != "promoter" or "promoter" not in q})
            quarters.append(q)
        if quarters:
            cur = quarters[0]
            prev = quarters[1] if len(quarters) > 1 else {}
            out["quarter"] = cur.get("quarter")
            for k in ("promoter", "fii", "dii"):
                if k in cur:
                    out[k] = cur[k]
                    if k in prev:
                        out[k + "_chg"] = cur[k] - prev[k]
    pl = _get_json(sess, f"{API}/corporate-pledgedata?index=equities&symbol={symbol}", f"pledge_{symbol}.json")
    if isinstance(pl, dict):
        data = pl.get("data") or []
        vals = []
        for r in data:
            try:
                vals.append((pd.to_datetime(r.get("shp"), format="%d-%b-%Y", errors="coerce"),
                             float(str(r.get("percPromoterShares", "")).strip())))
            except ValueError:
                pass
        vals = sorted([v for v in vals if pd.notna(v[0])], reverse=True)
        if vals:
            out["pledge"] = vals[0][1]
            if len(vals) > 1:
                out["pledge_chg"] = vals[0][1] - vals[1][1]
        elif not data:
            out["pledge"] = 0.0  # NSE lists no pledge disclosure for this company
    # remember the previous pledge value to detect "pledge increased this quarter"
    if "pledge" in out and "pledge_chg" not in out and old and "pledge" in old and old.get("quarter") != out.get("quarter"):
        out["pledge_chg"] = out["pledge"] - old["pledge"]
    if out:  # never save an empty result (NSE unreachable): keep the last good copy instead
        out["fetched"] = date.today().isoformat()
        SHP_DIR.mkdir(parents=True, exist_ok=True)
        (SHP_DIR / f"{symbol}.json").write_text(json.dumps(out), encoding="utf-8")
    elif old:
        return old  # last good copy
    return out


# ---------------------------------------------------------------- announcements and results

def announcements(sess: PoliteSession, symbol: str, days: int = 14) -> list[dict] | None:
    to = date.today()
    fr = to - timedelta(days=days)
    d = _get_json(sess, f"{API}/corporate-announcements?index=equities&symbol={symbol}"
                        f"&from_date={fr:%d-%m-%Y}&to_date={to:%d-%m-%Y}", f"ann_{symbol}.json")
    if not isinstance(d, list):
        return None
    out = []
    for r in d:
        out.append({
            "date": str(r.get("an_dt", ""))[:11], "title": r.get("desc") or "",
            "text": r.get("attchmntText") or "", "link": r.get("attchmntFile") or "",
            "source": "NSE",
        })
    return out


def results_calendar(sess: PoliteSession) -> dict[str, list[date]] | None:
    """Symbol -> upcoming board-meeting dates for results (NSE event calendar)."""
    d = _get_json(sess, f"{API}/event-calendar", "event_calendar.json")
    if not isinstance(d, list):
        return None
    out: dict[str, list[date]] = {}
    for r in d:
        purpose = (str(r.get("purpose", "")) + " " + str(r.get("bm_desc", ""))).lower()
        if "result" not in purpose:
            continue
        try:
            dt = datetime.strptime(r.get("date", ""), "%d-%b-%Y").date()
        except ValueError:
            continue
        out.setdefault(str(r.get("symbol", "")).upper(), []).append(dt)
    return out
