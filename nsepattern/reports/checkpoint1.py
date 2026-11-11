"""Checkpoint 1: pattern profile and both buy levels for a few stocks, as a readable report."""
from __future__ import annotations

import logging

import pandas as pd

from nsepattern.config import OUTPUT_DIR
from nsepattern.data.prices import get_daily
from nsepattern.engine.mood import MOOD_TICKERS, compute_mood_history
from nsepattern.engine.mover import mover_scores
from nsepattern.engine.profile import analyse_stock

log = logging.getLogger("nsepattern.cp1")

DEFAULT = ["RELIANCE", "TATASTEEL", "HDFCBANK", "INFY"]
MIDCAP_CANDIDATES = ["DIXON", "KAYNES", "BSE", "CDSL", "SUZLON", "PERSISTENT", "POLYCAB", "COFORGE"]


def _pct(x, d=2):
    return "N/A" if x is None else f"{x:+.{d}f}%"


def _rate(st):
    if not st or st.get("hit_rate") is None:
        return "N/A"
    return f"{st['hit_rate'] * 100:.0f}% ({st['hits']}/{st['trades']})"


def _num(x, d=1):
    return "N/A" if x is None else f"{x:.{d}f}"


def _rs(x):
    return "N/A" if x is None else f"₹{x:,.2f}"


def load_mood(cfg) -> pd.Series | None:
    data = get_daily(list(MOOD_TICKERS.values()), cfg)
    closes = {k: data[t]["Close"] for k, t in MOOD_TICKERS.items() if t in data}
    missing = [t for t in MOOD_TICKERS.values() if t not in data]
    if missing:
        log.warning("Market mood inputs not available: %s", ", ".join(missing))
    hist = compute_mood_history(closes, cfg)  # breadth needs the full universe -> N/A here
    return hist["mood"] if not hist.empty else None


def run(cfg, symbols: list[str] | None = None) -> str:
    syms = symbols or DEFAULT
    mood = load_mood(cfg)
    extra = [] if symbols else MIDCAP_CANDIDATES
    data = get_daily(syms + extra, cfg)
    results = {s: analyse_stock(df, cfg, mood) for s, df in data.items() if len(df) > 250}
    if extra:  # pick the biggest daily mover among the mid-cap candidates
        ranges = {s: results[s]["mover"]["avg_range_pct"] for s in extra if s in results}
        ranges = {k: v for k, v in ranges.items() if v is not None}
        if ranges:
            pick = max(ranges, key=ranges.get)
            syms = syms + [pick]
            log.info("Mid-cap high mover chosen: %s (avg range %.2f%%). Candidates: %s", pick, ranges[pick],
                     ", ".join(f"{k} {v:.2f}%" for k, v in sorted(ranges.items(), key=lambda kv: -kv[1])))
    scores = mover_scores({s: r["mover"]["avg_range_pct"] for s, r in results.items()})
    return render(cfg, [s for s in syms if s in results], results, scores, mood,
                  [s for s in syms if s not in results])


def render(cfg, syms, results, scores, mood, missing) -> str:
    tgt = cfg["trading"]["target_pct"]
    L = []
    L.append("# Checkpoint 1: pattern profiles and buy levels\n")
    any_r = results[syms[0]] if syms else None
    if any_r:
        L.append(f"Data up to **{any_r['last_date']}** (Yahoo daily, adjusted for splits, bonus and dividends). "
                 f"History used: last {cfg['data']['history_years']} years. Target +{tgt}%. "
                 f"Stats window {cfg['pattern']['stats_window_days']} trading days.\n")
    if mood is not None:
        L.append(f"Market mood today (without breadth, which needs the full universe): **{mood.iloc[-1]}**\n")
    if missing:
        L.append(f"Not available from Yahoo: {', '.join(missing)}\n")

    L.append("## Summary\n")
    L.append("| Stock | Close | Usual days (median / p75) | Usual depth (median) | Deeper (p75 / p90) | Meaningful streaks | Deep falls ≤-9% "
             "| Your method level | Hit rate | Best tested level | Hit rate | Expectancy | Confidence |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in syms:
        r = results[s]
        p, bt = r["profile"], r["backtest"]
        y, b = bt["yours"], bt["best"]
        L.append(
            f"| **{s}** | {_rs(r['last_close'])} | {_num(p['usual_days'])} / {_num(p['usual_days_p75'])} "
            f"| {_pct(p['usual_depth_pct'])} | {_pct(p['depth_p75_pct'])} / {_pct(p['depth_p90_pct'])} "
            f"| {p['meaningful']} | {p['deep_falls']} "
            f"| {_pct(y['level_pct'] if y else None)} | {_rate(y)} "
            f"| {_pct(b['level_pct'] if b else None, 1)} | {_rate(b)} "
            f"| {_pct(b['expectancy'] * 100 if b else None)} | {b['confidence'] if b else 'N/A'} |")
    L.append("")
    L.append("*Expectancy = average result per trade using a small-sample-protected hit rate; "
             "higher is better. Best tested = highest expectancy among levels with at least "
             f"{cfg['pattern']['min_trades_for_best']} past trades.*\n")

    for s in syms:
        r = results[s]
        p, bt, st, mv = r["profile"], r["backtest"], r["status"], r["mover"]
        L.append(f"## {s}\n")
        L.append(f"- Last close {_rs(r['last_close'])} on {r['last_date']}; trend vs 200-day average: {r['trend'] or 'N/A'}")
        L.append(f"- Avg daily traded value (60d): {_num(r['liquidity_crore'], 0)} crore; "
                 f"avg daily range (60d): {_pct(mv['avg_range_pct'])}; mover score (among these stocks): "
                 f"{_num(scores.get(s), 0)}")
        if st.get("in_streak"):
            L.append(f"- **Now in a down streak**: day {st['days_in_streak']} since {st['p0_date']} (P0 {_rs(st['p0'])}), "
                     f"fall {_pct(st['depth_low_pct'])} (low) / {_pct(st['depth_close_pct'])} (close). "
                     f"Status (best tested): **{st['status_best'] or 'N/A'}**, (your method): **{st['status_your'] or 'N/A'}**")
            for lab, k in (("Best tested", "best"), ("Your method", "your")):
                if st.get(k):
                    L.append(f"  - {lab} buy price {_rs(st[k]['buy_price'])}, target {_rs(st[k]['buy_price'] * (1 + tgt / 100))}, "
                             f"close is {st[k]['gap_pct']:+.2f}% from it")
        else:
            L.append("- Not in a down streak today.")
        if st.get("deep_recent"):
            d = st["deep_recent"]
            L.append(f"- Deep fall recently: {_pct(d['depth_low_pct'])} over {d['days']} days, ended {d['ended']}, "
                     f"recovered {_pct(d['recovery_pct'])} from the low")
        L.append("")
        L.append("| Buy level | Trades | Hit rate | Median days | Avg MAE | Worst-typical MAE (p10) | Trades/yr | Expectancy | |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        rows = list(bt["table"])
        if bt["yours"]:
            rows.append(bt["yours"])
        rows.sort(key=lambda x: -x["level_pct"])
        best_lvl = bt["best"]["level_pct"] if bt["best"] else None
        for t in rows:
            tag = "← your method" if t["kind"] == "your_method" else ("← **best tested**" if t["level_pct"] == best_lvl else "")
            L.append(f"| {_pct(t['level_pct'])} | {t['trades']} | {_rate(t) if t['trades'] else 'N/A'} "
                     f"| {_num(t['median_days'])} | {_pct(t['avg_mae'] * 100 if t['avg_mae'] is not None else None)} "
                     f"| {_pct(t['p10_mae'] * 100 if t['p10_mae'] is not None else None)} | {t['trades_per_year']:.1f} "
                     f"| {_pct(t['expectancy'] * 100 if t['expectancy'] is not None else None)} | {tag} |")
        L.append("")
        for lab, k in (("Best tested", "best"), ("Your method", "yours")):
            x = bt[k]
            if x:
                bm = ", ".join(f"{m}: {v['hit_rate'] * 100:.0f}% of {v['trades']}" for m, v in x["by_mood"].items())
                btr = ", ".join(f"{m}: {v['hit_rate'] * 100:.0f}% of {v['trades']}" for m, v in x["by_trend"].items())
                L.append(f"- {lab} hit rate by market mood → {bm or 'N/A'}; by trend → {btr or 'N/A'}")
        L.append("")
        tr = bt["best_trades"]
        if not tr.empty:
            L.append(f"Last {cfg['pattern']['last_trades_shown']} historical trades at the best tested level:\n")
            L.append("| Start (P0) | Fill date | Fill | Hit | Days | MAE | Result | Mood | Trend |")
            L.append("|---|---|---|---|---|---|---|---|---|")
            for _, t in tr.tail(cfg["pattern"]["last_trades_shown"]).iterrows():
                L.append(f"| {t['p0_date'].date()} | {t['fill_date'].date()} | {_rs(t['fill'])} | {'✅' if t['hit'] else '❌'} "
                         f"| {'' if pd.isna(t['days_to_target']) else int(t['days_to_target'])} | {t['mae'] * 100:+.2f}% "
                         f"| {t['result'] * 100:+.2f}% | {t['mood']} | {t['trend']} |")
            L.append("")
        if mv["avg_range_pct"] is not None:
            L.append(f"Your old sheet's ± columns (last {mv['days']} days, % of days the level was reached from the open):\n")
            ups = " | ".join(f"+{k}%: {v:.0f}" for k, v in mv["up_hits"].items())
            dns = " | ".join(f"-{k}%: {v:.0f}" for k, v in mv["down_hits"].items())
            L.append(f"- Up: {ups}")
            L.append(f"- Down: {dns}\n")
    L.append("---\n*For analysis only, not investment advice.*")
    md = "\n".join(L)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "checkpoint1.md").write_text(md, encoding="utf-8")
    return md
