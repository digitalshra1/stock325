"""Checkpoint 2 report: today's READY / GETTING CLOSE lists with checklists; WEAK list separately."""
from __future__ import annotations

from nsepattern.config import OUTPUT_DIR
from nsepattern.engine.checks import ICON


def _p(x, d=2):
    return "N/A" if x is None else f"{x:+.{d}f}%"


def _rs(x):
    return "N/A" if x is None else f"₹{x:,.2f}"


def _hr(st):
    if not st or st.get("hit_rate") is None:
        return "N/A"
    return f"{st['hit_rate'] * 100:.0f}% of {st['trades']}"


def _row(a, cfg):
    st, bt, pr = a["status"], a["backtest"], a["profile"]
    tgt = cfg["trading"]["target_pct"] / 100
    b, y = st.get("best"), st.get("your")
    best, yours = bt.get("best"), bt.get("yours")
    news_flag = "📰⚠" if a.get("news") and a["news"].get("negative") else ""
    tag = " · data incomplete" if a["grade"]["grade"] == "UNKNOWN" else ""
    level = b or y
    return (f"| **{a['symbol']}**{tag} | {a['industry']} | {st['days_in_streak']} / {pr['usual_days']:.0f} "
            f"| {_p(st['depth_low_pct'], 1)} / {_p(pr['usual_depth_pct'], 1)} "
            f"| {_rs(y['buy_price']) if y else 'N/A'} ({_hr(yours)}) "
            f"| {_rs(b['buy_price']) if b else 'N/A'} at {_p(best['level_pct'], 1) if best else ''} ({_hr(best)}) "
            f"| {_rs(level['buy_price'] * (1 + tgt)) if level else 'N/A'} "
            f"| {best['median_days'] if best and best.get('median_days') is not None else 'N/A'} "
            f"| {best['confidence'] if best else 'N/A'} "
            f"| {a.get('check_summary', {}).get('line', 'N/A')} {news_flag} | {a.get('rank', {}).get('score', 'N/A')} |")


HEAD = ("| Stock | Industry | Days (now / usual) | Fall (now / usual) | Buy: your method | Buy: best tested "
        "| Target | Median days | Confidence | Checks | Score |\n|---|---|---|---|---|---|---|---|---|---|---|")


def _checklist(a):
    L = [f"**{a['symbol']}** ({a['name']}): status best tested **{a['status'].get('status_best')}**, "
         f"your method **{a['status'].get('status_your')}**. Close {_rs(a['status']['close'])}, "
         f"streak from {a['status']['p0_date']} (P0 {_rs(a['status']['p0'])})"]
    for c in a.get("checks", []):
        L.append(f"- {ICON[c['result']]} {c['name']}: {c['detail']}")
    neg = (a.get("news") or {}).get("negative") or []
    for h in neg[:5]:
        L.append(f"  - 📰 {h['date']} [{h['title'][:110]}]({h['link']}) ({', '.join(h['matches'])})")
    return "\n".join(L) + "\n"


def render(res: dict, cfg: dict) -> str:
    stocks = res["stocks"]
    show_unknown = cfg["filters"]["show_unknown_fundamentals_on_main"]
    main_ok = {"GOOD", "UNKNOWN"} if show_unknown else {"GOOD"}

    def pick(status, grades, excluded=False):
        out = [a for a in stocks.values() if a["status"].get("status") == status and a["grade"]["grade"] in grades
               and bool(a["excluded"]) == excluded]
        return sorted(out, key=lambda a: -(a.get("rank") or {}).get("score", 0))

    m = res["mood"]
    L = ["# Checkpoint 2: today's buy lists\n",
         f"Data date **{res['data_date']}** · generated {res['generated']} · universe {res['universe']} EQ stocks, "
         f"{res['with_prices']} with prices, {res['liquid']} liquid (≥ ₹{cfg['filters']['liquidity_min_crore']:.0f} cr/day), "
         f"**{res['movers']} liquid fast movers analysed** (avg range ≥ {cfg['filters']['mover_min_avg_range_pct']}%).\n"]
    L.append(f"## Market mood: {m['mood']} ({m['points']} points)\n")
    L.append("Reasons: " + (", ".join(m["reasons"]) if m["reasons"] else "none fired") + "\n")
    ind = m["indicators"]
    L.append(f"Breadth: {_p(ind.get('breadth_down'), 0).lstrip('+')} of stocks closed down today.\n")
    L.append("| Indicator | Last | 1-day | 5-day |\n|---|---|---|---|")
    for k, t in m["tiles"].items():
        L.append(f"| {k} | N/A | N/A | N/A |" if not t else f"| {k} | {t['last']:,.2f} | {_p(t['chg_1d'])} | {_p(t['chg_5d'])} |")
    L.append("")
    L.append("Data sources: " + ", ".join(f"{k} {'✅' if v else 'N/A'}" for k, v in res["sources"].items()) + "\n")

    counts = {s: len(pick(s, main_ok)) for s in ("READY", "GETTING CLOSE", "FORMING")}
    L.append(f"**Main list counts:** 🟢 READY {counts['READY']} · 🟡 GETTING CLOSE {counts['GETTING CLOSE']} · "
             f"⚪ FORMING {counts['FORMING']}\n")
    for title, status in (("🟢 Ready to buy", "READY"), ("🟡 Getting close", "GETTING CLOSE")):
        rows = pick(status, main_ok)
        L.append(f"## {title} ({len(rows)})\n")
        if rows:
            L.append(HEAD)
            L += [_row(a, cfg) for a in rows]
            L.append("\n### Checklists\n")
            L += [_checklist(a) for a in rows]
        else:
            L.append("None today.\n")

    exc = [a for a in stocks.values() if a["excluded"] and a["status"].get("status") in ("READY", "GETTING CLOSE")
           and a["grade"]["grade"] in main_ok]
    if exc:
        L.append("## Excluded from buy lists (surveillance / tight band)\n")
        for a in exc:
            L.append(f"- {a['symbol']} ({a['status']['status']}): {'; '.join(a['excluded'])}")
        L.append("")

    deep = [a for a in stocks.values() if a["status"].get("status") == "DEEP" or a["status"].get("deep_recent")]
    L.append(f"## Deep-fall watchlist ({len(deep)}) — study only, no buy alerts\n")
    if deep:
        L.append("| Stock | Grade | Depth | Days | Recovery from low | Negative news |\n|---|---|---|---|---|---|")
        for a in deep:
            st = a["status"]
            if st.get("status") == "DEEP":
                dep, days, rec = st["depth_low_pct"], st["days_in_streak"], "still falling"
            else:
                d = st["deep_recent"]
                dep, days, rec = d["depth_low_pct"], d["days"], _p(d["recovery_pct"], 1)
            nn = len((a.get("news") or {}).get("negative") or [])
            L.append(f"| {a['symbol']} | {a['grade']['grade']} | {_p(dep, 1)} | {days} | {rec} | {nn or '-'} |")
        L.append("")

    weak = [a for a in stocks.values() if a["grade"]["grade"] == "WEAK"]
    wc = [a for a in weak if a["status"].get("status") in ("READY", "GETTING CLOSE")]
    L.append(f"## Weak fundamentals (separate section) — {len(weak)} stocks, {len(wc)} READY / GETTING CLOSE\n")
    if wc:
        L.append("| Stock | Red flags | Status | Industry | Days (now/usual) | Fall (now/usual) | Buy best tested | Hit rate | Checks |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for a in sorted(wc, key=lambda a: a["status"]["status"] != "READY"):
            st, b, best = a["status"], a["status"].get("best"), a["backtest"].get("best")
            L.append(f"| {a['symbol']} | {'; '.join(a['grade']['red_flags'])} | {st['status']} | {a['industry']} "
                     f"| {st['days_in_streak']}/{a['profile']['usual_days']:.0f} | {_p(st['depth_low_pct'], 1)}/{_p(a['profile']['usual_depth_pct'], 1)} "
                     f"| {_rs(b['buy_price']) if b else 'N/A'} | {_hr(best)} | {a.get('check_summary', {}).get('line', 'N/A')} |")
        L.append("")
    gcount = {}
    for a in stocks.values():
        gcount[a["grade"]["grade"]] = gcount.get(a["grade"]["grade"], 0) + 1
    L.append("Fundamentals grades among analysed stocks: " + ", ".join(f"{k} {v}" for k, v in sorted(gcount.items())) + "\n")
    L.append("---\n*For analysis only, not investment advice.*")
    md = "\n".join(L)
    (OUTPUT_DIR / "checkpoint2.md").write_text(md, encoding="utf-8")
    return md
