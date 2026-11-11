"""The 10-point checklist for buy candidates (prompt section 9) and the ranking score.

Each check returns {'name', 'result': PASS/WARN/FAIL/NA, 'detail'}.
Icons: PASS ✅, WARN ⚠, FAIL ❌, NA N/A.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

ICON = {"PASS": "✅", "WARN": "⚠", "FAIL": "❌", "NA": "N/A"}


def _c(name, result, detail):
    return {"name": name, "result": result, "detail": detail}


def check_fundamentals(g: dict | None):
    if not g:
        return _c("Fundamentals", "NA", "No fundamental data")
    if g["grade"] == "GOOD":
        return _c("Fundamentals", "PASS", f"GOOD ({g['known']} metrics checked, no red flags)")
    if g["grade"] == "UNKNOWN":
        return _c("Fundamentals", "WARN", f"Data incomplete (only {g['known']} metrics known)")
    return _c("Fundamentals", "FAIL", "WEAK: " + "; ".join(g["red_flags"]))


def check_why_falling(stock_ret: float | None, industry_ret: float | None, nifty_ret: float | None, cfg: dict):
    """Returns in % over the same days (P0 -> today)."""
    if stock_ret is None:
        return _c("Why is it falling?", "NA", "No data")
    refs = [x for x in (industry_ret, nifty_ret) if x is not None]
    if not refs:
        return _c("Why is it falling?", "NA", "No industry / NIFTY data for comparison")
    ref = min(refs)  # the weaker of industry and market
    excess = stock_ret - ref
    c = cfg["checks"]
    txt = (f"Stock {stock_ret:+.1f}% vs industry {'N/A' if industry_ret is None else f'{industry_ret:+.1f}%'}"
           f", NIFTY {'N/A' if nifty_ret is None else f'{nifty_ret:+.1f}%'} over the same days")
    if excess >= c["why_ok_excess_pct"]:
        return _c("Why is it falling?", "PASS", txt + " → falling with the market/sector")
    if excess >= c["why_bad_excess_pct"]:
        return _c("Why is it falling?", "WARN", txt + f" → falling {abs(excess):.1f}% more than others")
    return _c("Why is it falling?", "FAIL", txt + f" → falling alone ({abs(excess):.1f}% more than others)")


def check_news(news: dict | None):
    if news is None or not news.get("sources_ok"):
        return _c("Negative news", "NA", "News sources not available")
    neg = news["negative"]
    if not neg:
        n = len(news["headlines"]) + len(news["announcements"])
        return _c("Negative news", "PASS", f"No negative keywords in {n} items ({', '.join(news['sources_ok'])})")
    words = sorted({w for h in neg for w in h["matches"]})
    res = "FAIL" if len(neg) >= 3 else "WARN"
    return _c("Negative news", res, f"{len(neg)} item(s) with: {', '.join(words)}")


def check_delivery(fall_avg: float | None, base_avg: float | None, cfg: dict):
    if fall_avg is None or base_avg is None or base_avg <= 0:
        return _c("Delivery % during fall", "NA", "Delivery data not available")
    ratio = fall_avg / base_avg
    txt = f"{fall_avg:.0f}% during the fall vs {base_avg:.0f}% average"
    if ratio > cfg["checks"]["delivery_high_ratio"]:
        return _c("Delivery % during fall", "WARN", txt + " → real investors may be exiting")
    return _c("Delivery % during fall", "PASS", txt + " → mostly trader selling")


def check_volume(fall_vol: float | None, base_vol: float | None, cfg: dict):
    if not fall_vol or not base_vol:
        return _c("Volume during fall", "NA", "Volume not available")
    ratio = fall_vol / base_vol
    txt = f"{ratio:.1f}x the {cfg['checks']['volume_avg_days']}-day average"
    if ratio > cfg["checks"]["panic_volume_ratio"]:
        return _c("Volume during fall", "WARN", txt + " → panic selling")
    return _c("Volume during fall", "PASS", txt)


def check_deals(deals: pd.DataFrame | None, shares_out: float | None, cfg: dict):
    if deals is None:
        return _c("Bulk / block deals", "NA", "Deal data not available")
    if deals.empty:
        return _c("Bulk / block deals", "PASS", f"None in the last {cfg['checks']['bulk_deal_days']} days")
    sold = deals.loc[deals["side"] == "SELL", "qty"].sum()
    bought = deals.loc[deals["side"] == "BUY", "qty"].sum()
    net = sold - bought
    txt = f"{len(deals)} deal(s): bought {bought:,.0f}, sold {sold:,.0f} shares"
    if shares_out and shares_out > 0:
        pct = net / shares_out * 100
        txt += f" (net sell {pct:+.2f}% of all shares)"
        if pct > cfg["checks"]["big_deal_pct_of_shares"]:
            return _c("Bulk / block deals", "WARN", txt + " → large selling")
        return _c("Bulk / block deals", "PASS", txt)
    return _c("Bulk / block deals", "WARN" if net > 0 else "PASS", txt)


def check_pledge(shp: dict | None, cfg: dict):
    if not shp or shp.get("pledge") is None:
        return _c("Promoter pledge", "NA", "Pledge data not available")
    p, chg = shp["pledge"], shp.get("pledge_chg")
    txt = f"{p:.1f}% of promoter shares pledged" + ("" if chg is None else f" (change {chg:+.1f} pts)")
    if p > cfg["fundamentals"]["max_pledge_pct"]:
        return _c("Promoter pledge", "FAIL", txt)
    if chg is not None and chg > 0:
        return _c("Promoter pledge", "WARN", txt + " → increasing")
    return _c("Promoter pledge", "PASS", txt)


def check_support(buy: float | None, bounce_lows: list[tuple[str, float]], low_52w: float | None, cfg: dict):
    if buy is None:
        return _c("Support", "NA", "No buy price")
    band = cfg["checks"]["support_band_pct"]
    near = [(d, v) for d, v in bounce_lows if abs(buy / v - 1) * 100 <= band]
    d52 = None if not low_52w else (buy / low_52w - 1) * 100
    t52 = "" if d52 is None else f"; 52-week low ₹{low_52w:,.2f} is {d52:+.1f}% from the buy price"
    if near:
        d, v = near[-1]
        return _c("Support", "PASS", f"Near earlier bounce low ₹{v:,.2f} ({d}){t52}")
    if d52 is not None and abs(d52) <= cfg["checks"]["near_52w_low_pct"]:
        return _c("Support", "PASS", f"Near the 52-week low{t52}")
    return _c("Support", "WARN", f"No earlier bounce low within ±{band}%{t52}")


def check_results(upcoming: list[date] | None, recent_ann: list[dict] | None, cfg: dict, today: date | None = None):
    today = today or date.today()
    c = cfg["checks"]
    soon = [d for d in (upcoming or []) if today <= d <= today + timedelta(days=c["results_soon_days"])]
    recent = []
    for a in recent_ann or []:
        t = (a.get("title", "") + " " + a.get("text", "")).lower()
        if "financial result" in t or "results" in t:
            try:
                ad = pd.to_datetime(a["date"], format="%d-%b-%Y").date()
            except (ValueError, TypeError):
                continue
            if (today - ad).days <= c["results_recent_days"]:
                recent.append(ad)
    if soon:
        return _c("Results soon", "WARN", f"Results expected on {soon[0]:%d %b %Y}")
    if recent:
        return _c("Results soon", "WARN", f"Results just released ({max(recent):%d %b %Y})")
    if upcoming is None and recent_ann is None:
        return _c("Results soon", "NA", "Results calendar not available")
    nxt = min([d for d in (upcoming or []) if d >= today], default=None)
    return _c("Results soon", "PASS", f"No results in the next {c['results_soon_days']} days"
              + (f" (next: {nxt:%d %b %Y})" if nxt else ""))


def check_surveillance(sym: str, surv: dict | None, bands: dict | None, cfg: dict):
    if surv is None and bands is None:
        return _c("Surveillance / circuit band", "NA", "Lists not available")
    reasons = []
    if surv and sym in surv:
        reasons.append(surv[sym])
    band = (bands or {}).get(sym)
    if band and band.replace(".", "").isdigit() and float(band) in [float(b) for b in cfg["filters"]["exclude_price_bands"]]:
        reasons.append(f"{band}% price band")
    if reasons:
        return _c("Surveillance / circuit band", "FAIL", "; ".join(reasons))
    btxt = "N/A" if not band else (f"{band}%" if band.replace(".", "").isdigit() else band)
    return _c("Surveillance / circuit band", "PASS", f"Not in ASM/GSM; price band {btxt}")


def summarise(checks: list[dict]) -> dict:
    passed = sum(c["result"] == "PASS" for c in checks)
    na = sum(c["result"] == "NA" for c in checks)
    return {"passed": passed, "total": len(checks), "na": na,
            "warn": sum(c["result"] == "WARN" for c in checks),
            "fail": sum(c["result"] == "FAIL" for c in checks),
            "line": f"{passed}/{len(checks)} checks passed" + (f" ({na} N/A)" if na else "")}


# ---------------------------------------------------------------- ranking

def _clip(x):
    return float(min(1.0, max(0.0, x)))


def ranking_score(level_stats: dict | None, mover_score: float | None, trend: str | None,
                  rel_strength: float | None, cfg: dict) -> dict:
    """0-100 score. rel_strength = stock 60-day return minus NIFTY 60-day return (%)."""
    w = cfg["ranking_weights"]
    st = level_stats or {}
    comp = {
        "hit_rate": _clip(st.get("hit_rate") or 0),
        "confidence": _clip((st.get("trades") or 0) / cfg["pattern"]["confidence_high_trades"]),
        "speed": _clip((20 - st["median_days"]) / 19) if st.get("median_days") is not None else 0.0,
        "risk": _clip(1 + st["p10_mae"] / 0.15) if st.get("p10_mae") is not None else 0.0,
        "mover": _clip((mover_score or 0) / 100),
        "trend": 0.5 * (1.0 if trend == "Uptrend" else 0.0)
                 + 0.5 * (_clip((rel_strength + 20) / 40) if rel_strength is not None else 0.0),
    }
    total = sum(w.values()) or 1
    score = sum(comp[k] * w[k] for k in w) / total * 100
    return {"score": round(score, 1), "components": {k: round(v, 3) for k, v in comp.items()}}


def period_return(series: pd.Series, start, end) -> float | None:
    """% change of a close series between two dates (as-of)."""
    s = series.dropna()
    if s.empty:
        return None
    a = s[:start]
    b = s[:end]
    if a.empty or b.empty or a.iloc[-1] <= 0:
        return None
    return float(b.iloc[-1] / a.iloc[-1] - 1) * 100


def mean_or_none(x) -> float | None:
    x = pd.Series(x, dtype=float).dropna()
    return float(x.mean()) if len(x) else None
