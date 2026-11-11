"""Free news: Google News RSS + yfinance Ticker.news, with a negative-keyword flag."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser
import yfinance as yf

from nsepattern.data.http import PoliteSession

log = logging.getLogger("nsepattern.news")
_SUFFIX = re.compile(r"\b(limited|ltd\.?|ltd|india|\(india\)|corporation|corp\.?|company|co\.)\s*$", re.I)


def short_name(name: str) -> str:
    n = (name or "").strip()
    for _ in range(3):
        n2 = _SUFFIX.sub("", n).strip(" .,")
        if n2 == n:
            break
        n = n2
    return n or name


def keyword_regex(keywords: list[str]) -> re.Pattern:
    parts = [r"\b" + re.escape(k.strip().lower()).replace(r"\ ", r"\s+") + r"\b" for k in keywords if k.strip()]
    return re.compile("|".join(parts), re.I)


def match_keywords(text: str, rx: re.Pattern) -> list[str]:
    return sorted({m.group(0).lower() for m in rx.finditer(text or "")})


def google_news(sess: PoliteSession, name: str, days: int) -> list[dict] | None:
    q = f'"{short_name(name)}" share OR stock when:{days}d'
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"
    r = sess.get(url)
    if r is None:
        return None
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries[:40]:
        try:
            dt = parsedate_to_datetime(e.get("published"))
        except (TypeError, ValueError):
            dt = None
        src = (e.get("source") or {}).get("title") if isinstance(e.get("source"), dict) else None
        out.append({"date": dt.date().isoformat() if dt else "", "title": e.get("title", ""),
                    "link": e.get("link", ""), "source": src or "Google News", "_dt": dt})
    return out


def yahoo_news(symbol: str) -> list[dict] | None:
    try:
        items = yf.Ticker(f"{symbol}.NS").news or []
    except Exception as e:  # noqa: BLE001
        log.warning("%s: Yahoo news failed (%s)", symbol, e)
        return None
    out = []
    for it in items:
        c = it.get("content", it)
        try:
            dt = datetime.fromisoformat(str(c.get("pubDate", "")).replace("Z", "+00:00"))
        except ValueError:
            dt = None
        link = ((c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else None) or ""
        prov = (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else None
        out.append({"date": dt.date().isoformat() if dt else "", "title": c.get("title", ""),
                    "link": link, "source": prov or "Yahoo", "_dt": dt})
    return out


def collect_news(sess: PoliteSession, symbol: str, name: str, cfg: dict,
                 nse_ann: list[dict] | None) -> dict:
    """Headlines from the last N days with negative-keyword matches.

    Returns {'headlines': [...], 'announcements': [...], 'negative': [...], 'sources_ok': [...]}.
    """
    days = cfg["checks"]["news_days"]
    rx = keyword_regex(cfg["checks"]["negative_keywords"])
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    heads, ok = [], []
    for label, items in (("Google News", google_news(sess, name, days)), ("Yahoo", yahoo_news(symbol))):
        if items is None:
            continue
        ok.append(label)
        for it in items:
            dt = it.pop("_dt", None)
            if dt is not None and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt is not None and dt < cutoff:
                continue
            heads.append(it)
    seen, uniq = set(), []
    for h in sorted(heads, key=lambda x: x["date"], reverse=True):
        k = re.sub(r"\W+", "", h["title"].lower())[:80]
        if k in seen:
            continue
        seen.add(k)
        h["matches"] = match_keywords(h["title"], rx)
        uniq.append(h)
    anns = []
    if nse_ann is not None:
        ok.append("NSE")
        for a in nse_ann:
            a = dict(a)
            a["matches"] = match_keywords(f"{a['title']} {a['text']}", rx)
            anns.append(a)
    neg = [h for h in uniq + anns if h["matches"]]
    return {"headlines": uniq, "announcements": anns, "negative": neg, "sources_ok": ok}
