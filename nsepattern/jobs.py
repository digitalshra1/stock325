"""Scheduled jobs (run by GitHub Actions once online; runnable by hand now):

  evening   full scan → dashboard → evening alerts + summary → answer commands      (~18:45 IST)
  monitor   answer commands → price alerts for your trades, stops, watchlist alerts,
            "zone touched" for candidates                                            (every 15 min, 09:15–15:30)
  listen    answer commands only                                                     (every 30 min otherwise)
  morning   global markets refresh → dashboard → morning message                     (~08:45 IST)

Add --dry-run to print messages instead of sending them.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd

from nsepattern import alerts as al
from nsepattern.bot import Bot, process_updates
from nsepattern.config import OUTPUT_DIR
from nsepattern.secrets import get_secret, save_secret
from nsepattern.state import (check_price_alerts, check_stop_losses, empty_bot_state, load_bot_state, load_state,
                              save_bot_state, save_state)
from nsepattern.telegram_api import Telegram

log = logging.getLogger("nsepattern.jobs")
IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def in_market_hours(cfg: dict, t: datetime | None = None) -> bool:
    t = t or now_ist()
    tg = cfg.get("telegram", {})
    o = dtime(*map(int, tg.get("market_open", "09:15").split(":")))
    c = dtime(*map(int, tg.get("market_close", "15:30").split(":")))
    return t.weekday() < 5 and o <= t.time() <= c


def _read(name: str) -> dict:
    p = OUTPUT_DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@dataclass
class Ctx:
    cfg: dict
    tg: Telegram
    pw: str | None
    us: dict | None
    bs: dict
    results: dict
    extras: dict
    known: set | None
    us_changed: bool = False
    sent: list = field(default_factory=list)


def dashboard_url(cfg: dict) -> str:
    """Config value, or (online) the GitHub Pages address of this repository."""
    import os
    u = (cfg.get("telegram") or {}).get("dashboard_url")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not u and repo and "/" in repo:
        owner, name = repo.split("/", 1)
        u = f"https://{owner.lower()}.github.io/{name}/"
    return u or ""


def context(cfg: dict, dry: bool) -> Ctx:
    cfg.setdefault("telegram", {})["dashboard_url"] = dashboard_url(cfg)
    pw = get_secret("APP_PASSWORD")
    tg = Telegram(get_secret("TELEGRAM_BOT_TOKEN"), get_secret("TELEGRAM_CHAT_ID"), dry_run=dry,
                  max_len=cfg["alerts"].get("telegram_max_len", 4000))
    if tg.dry_run and not dry:
        log.warning("Telegram token or chat ID missing: messages are printed instead of sent")
    us = load_state(cfg, pw) if pw else None
    bs = load_bot_state(cfg, pw) if pw else empty_bot_state()
    if not pw:
        log.warning("APP_PASSWORD not set: trades and alert memory are unavailable")
    try:
        from nsepattern.data.universe import load_universe
        known = set(load_universe()["SYMBOL"]) or None
    except Exception:  # noqa: BLE001
        known = None
    return Ctx(cfg, tg, pw, us, bs, _read("results.json"), _read("extras.json"), known)


def send(ctx: Ctx, msgs: list[str], kind: str):
    today = now_ist().date().isoformat()
    for m in msgs:
        ctx.tg.send(m)
        ctx.bs.setdefault("log", []).append({"date": today, "time": now_ist().strftime("%H:%M"), "kind": kind,
                                             "text": m[:80]})
        ctx.sent.append(m)


def prune_bot_state(bs: dict, user_state: dict | None) -> None:
    """Forget alert memory and peaks of trades that no longer exist (e.g. after /remove)."""
    if user_state is None:
        return
    ids = {t["id"] for t in user_state["trades"]}
    bs["fired"] = [k for k in bs.get("fired", []) if len(k.split(":")) < 2 or k.split(":")[1] in ids]
    bs["peaks"] = {k: v for k, v in (bs.get("peaks") or {}).items() if k in ids}


def save(ctx: Ctx):
    if not ctx.pw:
        return
    prune_bot_state(ctx.bs, ctx.us)
    if ctx.us_changed and ctx.us is not None:
        save_state(ctx.cfg, ctx.us, ctx.pw)
    save_bot_state(ctx.cfg, ctx.bs, ctx.pw)


# ---------------------------------------------------------------- prices


def fetch_bars(symbols: list[str], since: datetime | None) -> dict[str, dict]:
    """High / low / last of today's 5-minute bars after `since` (Yahoo, ~15 min delayed)."""
    import yfinance as yf
    out: dict[str, dict] = {}
    if not symbols:
        return out
    try:
        raw = yf.download([f"{s}.NS" for s in symbols], period="1d", interval="5m", group_by="ticker",
                          progress=False, threads=True, auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        log.warning("Intraday download failed: %s", e)
        return out
    for s in symbols:
        try:
            d = raw[f"{s}.NS"] if isinstance(raw.columns, pd.MultiIndex) else raw
            d = d.dropna(subset=["Close"])
            if d.empty:
                continue
            idx = d.index.tz_convert(IST) if d.index.tz is not None else d.index.tz_localize(IST)
            d = d.set_axis(idx)
            if since is not None:
                d = d[d.index > since]
            if d.empty:
                continue
            out[s] = {"high": float(d["High"].max()), "low": float(d["Low"].min()), "last": float(d["Close"].iloc[-1]),
                      "time": d.index[-1].isoformat()}
        except KeyError:
            continue
    return out


def last_prices(ctx: Ctx, symbols: list[str], live: bool = True) -> dict[str, float]:
    prices = {}
    q = (ctx.extras or {}).get("quotes") or {}
    st = (ctx.results or {}).get("stocks") or {}
    for s in symbols:
        if s in q:
            prices[s] = q[s]["ltp"]
        elif s in st:
            prices[s] = st[s]["last_close"]
    if live and in_market_hours(ctx.cfg):
        for s, b in fetch_bars(symbols, None).items():
            prices[s] = b["last"]
    return prices


# ---------------------------------------------------------------- pieces


def answer_commands(ctx: Ctx) -> bool:
    """Returns True if any Telegram update was read (so state must be saved)."""
    chat = get_secret("TELEGRAM_CHAT_ID")
    if ctx.tg.dry_run or not chat:
        return False  # in dry run, commands are tested with: python -m nsepattern cmd "/buy …"
    updates = ctx.tg.get_updates(ctx.bs.get("tg_offset") or 0)
    if not updates:
        return False
    bot = Bot(ctx.cfg, ctx.us, ctx.bs, ctx.results, ctx.known, lambda syms: last_prices(ctx, syms))
    nxt = process_updates(bot, updates, chat, lambda m: send(ctx, [m], "reply"))
    if nxt:
        ctx.bs["tg_offset"] = nxt
    ctx.us_changed |= bot.changed
    return True


def usual_dip(ctx: Ctx, sym: str) -> float | None:
    a = ((ctx.results or {}).get("stocks") or {}).get(sym)
    b = (a or {}).get("backtest", {}).get("best") or {}
    return b.get("p10_mae") * 100 if b.get("p10_mae") is not None else None


def price_checks(ctx: Ctx, bars: dict[str, dict]):
    if ctx.us is None:
        return
    fired = set(ctx.bs.get("fired", []))
    peaks = ctx.bs.setdefault("peaks", {})
    msgs = []
    for t in ctx.us["trades"]:
        if t.get("status") == "open" and t["symbol"] in bars:
            msgs += al.position_alerts(t, bars[t["symbol"]], fired, peaks, ctx.cfg, usual_dip(ctx, t["symbol"]))
    msgs += check_stop_losses(ctx.us, bars, fired)
    before = sum(1 for a in ctx.us["alerts"] if a.get("active", True))
    msgs += check_price_alerts(ctx.us, bars, now_ist().strftime("%Y-%m-%d %H:%M"))
    if sum(1 for a in ctx.us["alerts"] if a.get("active", True)) != before:
        ctx.us_changed = True
    if ctx.cfg.get("telegram", {}).get("zone_alerts", True) and ctx.results.get("stocks"):
        today = now_ist().date().isoformat()
        zs = ctx.bs.get("zone_sent") or {}
        sent_today = set(zs.get(today, []))
        msgs += al.zone_alerts(al.candidates(ctx.results, ctx.cfg), bars, sent_today,
                               (ctx.results.get("mood") or {}).get("mood"))
        ctx.bs["zone_sent"] = {today: sorted(sent_today)}
    ctx.bs["fired"] = sorted(fired)
    send(ctx, msgs, "alert")


def watch_symbols(ctx: Ctx) -> list[str]:
    syms = set()
    if ctx.us:
        syms |= {t["symbol"] for t in ctx.us["trades"] if t.get("status") == "open"}
        syms |= {a["symbol"] for a in ctx.us["alerts"] if a.get("active", True)}
    if ctx.results.get("stocks"):
        syms |= {a["symbol"] for a in al.candidates(ctx.results, ctx.cfg)}
    return sorted(syms)[:150]


# ---------------------------------------------------------------- jobs


def job_listen(cfg: dict, dry: bool = False) -> Ctx:
    ctx = context(cfg, dry)
    if answer_commands(ctx):  # nothing new -> don't rewrite the files
        save(ctx)
    return ctx


def job_monitor(cfg: dict, dry: bool = False, force: bool = False) -> Ctx:
    ctx = context(cfg, dry)
    answer_commands(ctx)
    if force or in_market_hours(cfg):
        since = None
        if ctx.bs.get("last_check"):
            lc = datetime.fromisoformat(ctx.bs["last_check"])
            since = lc if lc.date() == now_ist().date() else None
        bars = fetch_bars(watch_symbols(ctx), since)
        price_checks(ctx, bars)
        if bars:
            ctx.bs["last_check"] = max(b["time"] for b in bars.values())
    else:
        log.info("Outside market hours: price checks skipped")
    save(ctx)
    return ctx


def job_evening(cfg: dict, dry: bool = False, run: bool = True, if_missed: bool = False,
                weekly: bool = False) -> Ctx | None:
    if if_missed:  # backup run (~20:30 IST): only work if today's evening scan didn't happen
        pw = get_secret("APP_PASSWORD")
        bs = load_bot_state(cfg, pw) if pw else empty_bot_state()
        if bs.get("last_evening_run") == now_ist().date().isoformat():
            log.info("Today's evening scan already ran: backup run not needed")
            return None
    if run:
        from nsepattern.dashboard.build import build
        from nsepattern.extras import run_extras
        from nsepattern.pipeline import LAST_PRICES, run_full
        res = run_full(cfg, weekly=weekly)
        try:
            run_extras(cfg, prices=dict(LAST_PRICES) or None)
        except Exception as e:  # noqa: BLE001
            log.warning("Extras layer failed (main results are fine): %s", e)
        build(res, cfg)
    ctx = context(cfg, dry)
    answer_commands(ctx)
    if ctx.results.get("stocks"):
        syms = [t["symbol"] for t in (ctx.us or {}).get("trades", []) if t.get("status") == "open"]
        msgs = al.evening_messages(ctx.results, ctx.bs, cfg, ctx.us, last_prices(ctx, syms, live=False))
        send(ctx, msgs, "evening")
        ctx.bs["last_evening"] = ctx.results.get("data_date")
    if run:
        ctx.bs["last_evening_run"] = now_ist().date().isoformat()
    save(ctx)
    return ctx


def job_morning(cfg: dict, dry: bool = False, refresh: bool = True) -> Ctx:
    if refresh:
        from nsepattern.dashboard.build import build
        from nsepattern.extras import refresh_global
        refresh_global(cfg)
        build(_read("results.json"), cfg)
    ctx = context(cfg, dry)
    answer_commands(ctx)
    if cfg.get("telegram", {}).get("morning_outlook", True):
        m = al.morning_message(ctx.extras, ctx.us)
        if m:
            send(ctx, [m], "morning")
    ctx.bs["last_morning"] = now_ist().date().isoformat()
    save(ctx)
    return ctx


# ---------------------------------------------------------------- local testing helpers


def job_cmd(cfg: dict, text: str, dry: bool = True) -> Ctx:
    """Run one command as if you had sent it from your chat (for testing, and Checkpoint 4)."""
    ctx = context(cfg, dry)
    bot = Bot(cfg, ctx.us, ctx.bs, ctx.results, ctx.known, lambda syms: last_prices(ctx, syms))
    send(ctx, [bot.handle(text)], "reply")
    ctx.us_changed |= bot.changed
    save(ctx)
    return ctx


def job_sim(cfg: dict, symbol: str, high: float, low: float, last: float, dry: bool = True) -> Ctx:
    """Pretend the market printed this bar for one stock, and run every price alert."""
    ctx = context(cfg, dry)
    price_checks(ctx, {symbol.upper(): {"high": high, "low": low, "last": last, "time": now_ist().isoformat()}})
    if not ctx.sent:
        print("(no alert for this price move)")
    save(ctx)
    return ctx


def telegram_setup(cfg: dict) -> str:
    """Find your chat ID: send any message to your bot first, then run this."""
    token = get_secret("TELEGRAM_BOT_TOKEN")
    if not token:
        return "No TELEGRAM_BOT_TOKEN found. Put it in .secrets.env as TELEGRAM_BOT_TOKEN=... first."
    tg = Telegram(token, None)
    ups = tg.get_updates(0)
    chats = {}
    for u in ups:
        m = u.get("message") or {}
        c = m.get("chat") or {}
        if c.get("type") == "private":
            chats[str(c["id"])] = c.get("first_name") or c.get("username") or "you"
    if not chats:
        return "No messages found. Open your bot in Telegram, press Start (or send 'hi'), then run this again."
    if len(chats) > 1:
        return "More than one person has messaged this bot: " + ", ".join(f"{n} ({i})" for i, n in chats.items()) + \
               ". Add the right one to .secrets.env as TELEGRAM_CHAT_ID=..."
    cid, name = next(iter(chats.items()))
    save_secret("TELEGRAM_CHAT_ID", cid)
    Telegram(token, cid).send(f"✅ Hi {name}! 3.25 by SHRA1 is connected. Send /help to see the commands.")
    return f"Connected to {name}'s chat. Chat ID saved to .secrets.env, and a test message was sent."
