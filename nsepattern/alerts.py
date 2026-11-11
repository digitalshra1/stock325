"""Telegram alert rules (prompt section 12). Pure functions: they take prices + state and return messages.

Price checks use the bars since the last check: the bar HIGH for upside levels and the bar LOW for
downside levels, so brief touches count. Every alert step fires once (keys kept in bot state).
"""
from __future__ import annotations

from nsepattern.telegram_api import esc

# ---------------------------------------------------------------- formatting


def inr(x: float | None, dec: int = 2) -> str:
    """₹ with Indian digit grouping: 12,34,567.89"""
    if x is None:
        return "N/A"
    neg = x < 0
    s = f"{abs(x):.{dec}f}"
    whole, frac = (s.split(".") + [""])[:2]
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if neg else "") + "₹" + whole + ("." + frac if dec else "")


def pct(x: float | None, d: int = 2) -> str:
    return "N/A" if x is None else f"{x:+.{d}f}%"


def _lvl(x: float) -> str:
    """Level label without float noise: 4.25 -> '4.25', 5.0 -> '5'"""
    return f"{x:.2f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------- your trades


def level_lists(cfg: dict, max_up: float = 60.0, max_down: float = 60.0):
    t = cfg["trading"]
    ups, x = [], t["target_pct"]
    while x <= max_up:
        ups.append(round(x, 4))
        x += t["upside_step_pct"]
    downs, y = [], t["downside_warn_pct"]
    while y <= max_down:
        downs.append(round(y, 4))
        y += t["downside_step_pct"]
    return ups, downs


def next_levels(trade: dict, fired: set, cfg: dict) -> tuple[float | None, float | None]:
    """Next upside level (%) and next downside level (negative %) not yet alerted."""
    ups, downs = level_lists(cfg)
    tid = trade["id"]
    nu = next((u for u in ups if f"U:{tid}:{_lvl(u)}" not in fired), None)
    nd = next((-d for d in downs if f"D:{tid}:{_lvl(d)}" not in fired), None)
    return nu, nd


def position_alerts(trade: dict, bar: dict, fired: set, peaks: dict, cfg: dict,
                    usual_dip_pct: float | None = None) -> list[str]:
    """bar = {"high", "low", "last"} since the last check. Mutates fired/peaks. Returns messages."""
    if trade.get("status") != "open" or not bar:
        return []
    e, tid, sym = trade["entry"], trade["id"], trade["symbol"]
    tag = f"<b>{esc(sym)}</b> #{trade.get('no', '')}"
    ups, downs = level_lists(cfg)
    hi_pct = (bar["high"] / e - 1) * 100
    lo_pct = (bar["low"] / e - 1) * 100
    last = bar["last"]
    peaks[tid] = max(peaks.get(tid, e), bar["high"])
    msgs = []

    # upside: +3.25 %, then every further +1 %
    new_up = [u for u in ups if hi_pct >= u - 1e-9 and f"U:{tid}:{_lvl(u)}" not in fired]
    for u in new_up:
        fired.add(f"U:{tid}:{_lvl(u)}")
    if new_up:
        top = max(new_up)
        lvl_price = e * (1 + top / 100)
        if top == ups[0]:
            msgs.append(f"🎯 {tag} reached +{_lvl(top)}% ({inr(lvl_price)}). Now {inr(last)}. Start watching to sell.")
        else:
            hit_target = ups[0] in new_up
            msgs.append(f"{'🎯 Target hit and ' if hit_target else ''}📈 {tag} now +{_lvl(top)}% ({inr(lvl_price)}), "
                        f"peak {inr(peaks[tid])}. Now {inr(last)}.")
    # your own target price, if you set a different one on the trade
    tgt = trade.get("target")
    default_tgt = e * (1 + cfg["trading"]["target_pct"] / 100)
    if tgt and abs(tgt / default_tgt - 1) > 0.002 and bar["high"] >= tgt and f"CT:{tid}" not in fired:
        fired.add(f"CT:{tid}")
        msgs.append(f"🎯 {tag} reached your own target {inr(tgt)} ({pct((tgt / e - 1) * 100)}). Now {inr(last)}.")

    # downside: warning at −2 %, then every further −1 % (no stop-loss selling, warnings only)
    new_dn = [d for d in downs if lo_pct <= -d + 1e-9 and f"D:{tid}:{_lvl(d)}" not in fired]
    for d in new_dn:
        fired.add(f"D:{tid}:{_lvl(d)}")
    if new_dn:
        deep = max(new_dn)
        lvl_price = e * (1 - deep / 100)
        first = deep == downs[0]
        line = (f"⚠ {tag} is −{_lvl(deep)}% below your buy ({inr(lvl_price)})."
                + (" Not following the pattern." if first else "") + f" Now {inr(last)}.")
        if usual_dip_pct is not None:
            normal = deep <= abs(usual_dip_pct)
            line += (f"\nThis stock's usual dip after a buy is up to −{abs(usual_dip_pct):.1f}% (from its history), "
                     f"so this is {'normal' if normal else 'unusual'}.")
        msgs.append(line)
    # re-arm: price back above −2 % -> the downside sequence starts again from −2 %
    if last > e * (1 - downs[0] / 100):
        for d in downs:
            fired.discard(f"D:{tid}:{_lvl(d)}")
    return msgs


# ---------------------------------------------------------------- buy opportunities


def zone_alerts(cands: list[dict], bars: dict, sent_today: set, mood: str | None) -> list[str]:
    """Intraday: a READY / GETTING CLOSE stock's low touched either buy price. Once per stock per day.
    In a STRESSED market these are not sent (buy signals are paused)."""
    if mood == "STRESSED":
        return []
    msgs = []
    for a in cands:
        sym, st = a["symbol"], a["status"]
        b = bars.get(sym)
        if not b or sym in sent_today:
            continue
        best = (st.get("best") or {}).get("buy_price")
        your = (st.get("your") or {}).get("buy_price")
        if any(p and b["low"] <= p for p in (best, your)):
            sent_today.add(sym)
            m = (f"🟢 <b>{esc(sym)}</b> touched your buy level {inr(best)} (best tested) / {inr(your)} (your method). "
                 f"Now {inr(b['last'])}.")
            if mood == "CAUTION":
                m += "\n⚠ Caution market: check it carefully."
            msgs.append(m)
    return msgs


def _ready_block(a: dict, cfg: dict) -> str:
    st, bt, p = a["status"], a["backtest"], a["profile"]
    best, yours = bt.get("best") or {}, bt.get("yours") or {}
    tgt_base = (st.get("best") or st.get("your") or {}).get("buy_price")
    neg = len(((a.get("news") or {}).get("negative")) or [])
    hr = lambda x: "N/A" if x.get("hit_rate") is None else f"{x['hit_rate'] * 100:.0f}%"  # noqa: E731
    return (f"🟢 <b>{esc(a['symbol'])}</b> · {esc(a['industry'])}\n"
            f"Day {st['days_in_streak']} of fall (usual {p['usual_days']:.0f}) · fall {pct(st['depth_low_pct'], 1)} "
            f"(usual {pct(p['usual_depth_pct'], 1)})\n"
            f"Buy (your method): {inr((st.get('your') or {}).get('buy_price'))} · hit {hr(yours)}\n"
            f"Buy (best tested): {inr((st.get('best') or {}).get('buy_price'))} · hit {hr(best)} · {best.get('confidence', 'N/A')} confidence\n"
            f"Target: {inr(tgt_base * (1 + cfg['trading']['target_pct'] / 100) if tgt_base else None)} · "
            f"{(a.get('check_summary') or {}).get('line', 'checks N/A')}"
            + (f" · 📰 {neg} negative news" if neg else ""))


def pooled_mood_hit(stocks: list[dict]) -> dict[str, float]:
    """Pattern hit rate by market mood, pooled over all stocks' best tested level."""
    agg: dict[str, list[float]] = {}
    for a in stocks:
        for m, v in ((a["backtest"].get("best") or {}).get("by_mood") or {}).items():
            x = agg.setdefault(m, [0.0, 0.0])
            x[0] += v["hit_rate"] * v["trades"]
            x[1] += v["trades"]
    return {m: h / n * 100 for m, (h, n) in agg.items() if n}


def main_stocks(results: dict, cfg: dict) -> list[dict]:
    ok = {"GOOD", "UNKNOWN"} if cfg["filters"]["show_unknown_fundamentals_on_main"] else {"GOOD"}
    return [a for a in results.get("stocks", {}).values() if a["grade"]["grade"] in ok and not a.get("excluded")]


def candidates(results: dict, cfg: dict) -> list[dict]:
    return [a for a in main_stocks(results, cfg) if a["status"].get("status") in ("READY", "GETTING CLOSE")]


def evening_messages(results: dict, bs: dict, cfg: dict, user_state: dict | None, prices: dict) -> list[str]:
    """Mood change, newly READY stocks (mood rules applied), and the evening summary."""
    msgs = []
    mood = results.get("mood", {})
    cur = mood.get("mood")
    if bs.get("last_mood") and cur and cur != bs["last_mood"]:
        msgs.append(f"🌡 Market mood changed: <b>{esc(bs['last_mood'])} → {esc(cur)}</b>\n"
                    f"Reasons: {esc(', '.join(mood.get('reasons') or []) or 'none')}")
    main = main_stocks(results, cfg)
    ready = sorted([a for a in main if a["status"].get("status") == "READY"],
                   key=lambda a: -((a.get("rank") or {}).get("score") or 0))
    new_ready = [a for a in ready if a["symbol"] not in set(bs.get("prev_ready") or [])]
    if new_ready:
        if cur == "STRESSED":
            ph = pooled_mood_hit(main)
            msgs.append(f"🛑 <b>Market stressed: buy signals paused.</b>\nYour pattern's history: "
                        f"{ph.get('STRESSED', 0):.0f}% hit rate in stressed markets vs {ph.get('NORMAL', 0):.0f}% normally.\n"
                        f"For reference, newly READY: {esc(', '.join(a['symbol'] for a in new_ready))}")
        else:
            head = f"🟢 <b>New READY stocks ({len(new_ready)})</b>" + (
                "\n⚠ Caution market: check each one carefully." if cur == "CAUTION" else "")
            msgs.append(head + "\n\n" + "\n\n".join(_ready_block(a, cfg) for a in new_ready))
    if cfg["alerts"].get("getting_close_alerts"):
        close = [a for a in main if a["status"].get("status") == "GETTING CLOSE"]
        if close and cur != "STRESSED":
            msgs.append("🟡 <b>Getting close</b>\n" + "\n".join(
                f"{esc(a['symbol'])}: buy {inr((a['status'].get('best') or {}).get('buy_price'))} "
                f"({a['status']['best']['gap_pct']:+.1f}% away)" for a in close[:15] if a["status"].get("best")))
    # summary
    counts = {s: sum(1 for a in main if a["status"].get("status") == s) for s in ("READY", "GETTING CLOSE", "FORMING")}
    lines = [f"🌆 <b>Evening summary · {esc(results.get('data_date', ''))}</b>",
             f"Market: <b>{esc(cur or 'N/A')}</b>" + (f" ({esc(', '.join(mood.get('reasons') or []))})" if mood.get("reasons") else ""),
             f"🟢 READY {counts['READY']} · 🟡 Getting close {counts['GETTING CLOSE']} · ⚪ Forming {counts['FORMING']}"]
    n = cfg.get("telegram", {}).get("evening_top_n", 5)
    if ready:
        lines.append("\n<b>Top READY</b>")
        for a in ready[:n]:
            b = a["status"].get("best") or a["status"].get("your") or {}
            lines.append(f"• {esc(a['symbol'])}: buy {inr(b.get('buy_price'))} · score {(a.get('rank') or {}).get('score', 'N/A')}")
    if user_state:
        open_t = [t for t in user_state["trades"] if t.get("status") == "open"]
        if open_t:
            tot = 0.0
            lines.append("\n<b>Your open trades</b>")
            for t in open_t:
                p = prices.get(t["symbol"])
                if p is None:
                    lines.append(f"• #{t.get('no')} {esc(t['symbol'])}: price N/A")
                    continue
                g = (p - t["entry"]) * t["qty"]
                tot += g
                lines.append(f"• #{t.get('no')} {esc(t['symbol'])}: {pct((p / t['entry'] - 1) * 100)} ({inr(g, 0)})")
            lines.append(f"Unrealised total: {inr(tot, 0)}")
    today = results.get("data_date")
    sent_today = sum(1 for x in bs.get("log", []) if x.get("date") == today)
    lines.append(f"\nAlerts sent today: {sent_today}")
    url = cfg.get("telegram", {}).get("dashboard_url")
    if url:
        lines.append(f"📊 <a href=\"{esc(url)}\">Open the dashboard</a>")
    msgs.append("\n".join(lines))
    bs["prev_ready"] = [a["symbol"] for a in ready]
    bs["last_mood"] = cur
    return msgs


def morning_message(extras: dict, user_state: dict | None) -> str | None:
    g = (extras or {}).get("global") or {}
    o = g.get("outlook") or {}
    if not o.get("available"):
        return None
    sn = g.get("snapshots") or {}
    reg: dict[str, list[float]] = {}
    region = {"us": "US", "de": "Europe", "gb": "Europe", "fr": "Europe", "jp": "Asia", "hk": "Asia", "cn": "Asia"}
    for s in sn.values():
        mv = s.get("chg_done", s.get("chg_1d"))
        if mv is not None and s.get("flag") in region:
            reg.setdefault(region[s["flag"]], []).append(mv)
    parts = [f"{r} {pct(sum(v) / len(v))}" for r, v in reg.items()]
    lines = [f"🌅 <b>Good morning · global cues</b>",
             f"Expected NIFTY open: <b>{pct(o.get('expected_gap_pct'))}</b> ({esc(o.get('direction'))}) · trend {esc(o.get('trend'))}",
             f"{o.get('positive_markets')}/{o.get('markets_used')} markets up · " + " · ".join(parts)]
    if user_state:
        n = sum(1 for t in user_state["trades"] if t.get("status") == "open")
        if n:
            lines.append(f"You have {n} open trade(s). Price alerts start at 9:15.")
    lines.append("<i>A statistical hint from 3 years of data, not a forecast.</i>")
    return "\n".join(lines)
