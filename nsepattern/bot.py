"""Telegram commands (prompt section 11). Only your own chat is answered.

    /buy SYMBOL PRICE QTY [YYYY-MM-DD] [sl=PRICE] [tgt=PRICE]
    /sell SYMBOL PRICE [QTY] [#ID]
    /positions   /status   /stock SYMBOL   /remove ID   /edit ID PRICE QTY   /help

Trades go into the same encrypted list the dashboard's Trades page uses.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime

from nsepattern.alerts import inr, main_stocks, next_levels, pct
from nsepattern.data.universe import normalise_symbol
from nsepattern.telegram_api import esc

log = logging.getLogger("nsepattern.bot")

HELP = """<b>3.25 by SHRA1 · commands</b>
/buy SYMBOL PRICE QTY [date] — record a buy
   e.g. <code>/buy TCS 3500 10</code> or <code>/buy TCS 3500 10 2026-11-10 sl=3420</code>
/sell SYMBOL PRICE [QTY] [#ID] — record a sell (full or part)
   e.g. <code>/sell TCS 3620</code> · <code>/sell TCS 3620 5 #3</code>
/positions — open trades with gain and next alert levels
/status — market mood + top READY / GETTING CLOSE
/stock SYMBOL — pattern summary for one stock
/remove ID — delete a wrongly entered trade (e.g. <code>/remove 3</code>)
/edit ID PRICE QTY — fix a typo (e.g. <code>/edit 3 3510 10</code>)
/help — this list

Symbols work with or without .NS, any case. Replies come at the next scheduled check
(within ~15 minutes in market hours, ~30 minutes otherwise)."""


def today_ist() -> date:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def _num(s: str) -> float | None:
    try:
        v = float(s.replace(",", "").replace("₹", ""))
        return v if v > 0 else None
    except (ValueError, AttributeError):
        return None


def _id_token(tok: str) -> int | None:
    m = re.fullmatch(r"#?(\d{1,5})", tok.strip())
    return int(m.group(1)) if m else None


def _fmt_date(d: str | None) -> str:
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%d %b %Y")
    except (TypeError, ValueError):
        return d or ""


class Bot:
    def __init__(self, cfg: dict, user_state: dict | None, bot_state: dict, results: dict | None,
                 known_symbols: set[str] | None, price_fn=None):
        self.cfg, self.us, self.bs, self.res = cfg, user_state, bot_state, results or {}
        self.known = known_symbols
        self.price_fn = price_fn or (lambda syms: {})
        self.changed = False  # user state modified -> must be saved

    # ------------------------------------------------------------------ entry point
    def handle(self, text: str) -> str:
        text = (text or "").strip()
        if not text.startswith("/"):
            return "Send /help to see what I can do."
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        args = parts[1:]
        try:
            if cmd in ("/help", "/start"):
                return HELP
            if cmd in ("/buy", "/sell", "/positions", "/remove", "/edit") and self.us is None:
                return "⚠ Trades can't be saved yet: the app password (APP_PASSWORD) is not set up."
            fn = {"/buy": self.buy, "/sell": self.sell, "/positions": self.positions, "/status": self.status,
                  "/stock": self.stock, "/remove": self.remove, "/edit": self.edit}.get(cmd)
            if not fn:
                return f"I don't know {esc(cmd)}. Send /help for the list."
            return fn(args)
        except Exception as e:  # noqa: BLE001 - a bad command must never stop the job
            log.exception("Command failed")
            return f"⚠ Sorry, that failed ({esc(e.__class__.__name__)}). Please check the format: /help"

    # ------------------------------------------------------------------ helpers
    def _symbol(self, tok: str) -> tuple[str | None, str | None]:
        s = normalise_symbol(tok)
        if not re.fullmatch(r"[A-Z0-9&\-]{1,20}", s):
            return None, f"“{esc(tok)}” doesn't look like an NSE symbol."
        if self.known and s not in self.known:
            return None, f"{esc(s)} is not in the NSE equity list. Check the symbol (e.g. RELIANCE, TCS)."
        return s, None

    def _open(self, sym: str | None = None) -> list[dict]:
        return [t for t in self.us["trades"] if t.get("status") == "open" and (sym is None or t["symbol"] == sym)]

    def _find(self, no: int) -> dict | None:
        return next((t for t in self.us["trades"] if t.get("no") == no), None)

    def _next_no(self) -> int:
        return max((t.get("no") or 0 for t in self.us["trades"]), default=0) + 1

    # ------------------------------------------------------------------ commands
    def buy(self, a: list[str]) -> str:
        opts = {k.lower(): v for k, v in (x.split("=", 1) for x in a if "=" in x)}
        pos = [x for x in a if "=" not in x]
        if len(pos) < 3:
            return "Format: /buy SYMBOL PRICE QTY [YYYY-MM-DD]\ne.g. <code>/buy TCS 3500 10</code>"
        sym, err = self._symbol(pos[0])
        if err:
            return "⚠ " + err
        price, qty = _num(pos[1]), _num(pos[2])
        if price is None:
            return f"⚠ “{esc(pos[1])}” is not a valid price."
        if qty is None or qty != int(qty):
            return f"⚠ “{esc(pos[2])}” is not a valid quantity (whole number of shares)."
        d = today_ist()
        if len(pos) > 3:
            try:
                d = datetime.strptime(pos[3], "%Y-%m-%d").date()
            except ValueError:
                return f"⚠ Date “{esc(pos[3])}” must look like 2026-11-10."
            if d > today_ist():
                return "⚠ The buy date can't be in the future."
        sl = _num(opts["sl"]) if "sl" in opts else None
        tgt = _num(opts["tgt"]) if "tgt" in opts else None
        if "sl" in opts and (sl is None or sl >= price):
            return "⚠ sl= must be a price below your buy price."
        if "tgt" in opts and (tgt is None or tgt <= price):
            return "⚠ tgt= must be a price above your buy price."
        target = tgt or round(price * (1 + self.cfg["trading"]["target_pct"] / 100), 2)
        t = {"id": uuid.uuid4().hex[:12], "no": self._next_no(), "symbol": sym, "entry": price, "qty": int(qty),
             "target": target, "stop": sl, "date": d.isoformat(), "strategy": "Down-streak", "notes": "via Telegram",
             "status": "open", "exit_price": None, "exit_date": None, "created": datetime.now().isoformat(timespec="seconds")}
        self.us["trades"].append(t)
        if not any(w["symbol"] == sym for w in self.us["watchlist"]):
            self.us["watchlist"].append({"symbol": sym, "added": d.isoformat(), "note": ""})
        self.changed = True
        lots = len(self._open(sym))
        return (f"✅ Bought <b>#{t['no']} {sym}</b>: {int(qty)} @ {inr(price)} on {_fmt_date(t['date'])}\n"
                f"Invested {inr(price * qty, 0)} · target {inr(target)} ({pct((target / price - 1) * 100)})"
                + (f" · stop {inr(sl)}" if sl else "")
                + f"\nAlerts: +{self.cfg['trading']['target_pct']}% then every +1%; warnings from −{self.cfg['trading']['downside_warn_pct']:g}%."
                + (f"\nYou now hold {lots} open lots of {sym}." if lots > 1 else ""))

    def sell(self, a: list[str]) -> str:
        ids = [_id_token(x) for x in a if x.startswith("#")]
        rest = [x for x in a if not x.startswith("#")]
        if len(rest) < 2:
            return "Format: /sell SYMBOL PRICE [QTY] [#ID]\ne.g. <code>/sell TCS 3620</code>"
        sym, err = self._symbol(rest[0])
        if err:
            return "⚠ " + err
        price = _num(rest[1])
        if price is None:
            return f"⚠ “{esc(rest[1])}” is not a valid price."
        qty = None
        if len(rest) > 2:
            qty = _num(rest[2])
            if qty is None or qty != int(qty):
                return f"⚠ “{esc(rest[2])}” is not a valid quantity."
            qty = int(qty)
        if len(rest) > 3 and not ids:
            ids = [_id_token(rest[3])]
        lots = self._open(sym)
        if not lots:
            return f"⚠ You have no open trade in {sym}."
        if ids and ids[0]:
            lot = next((t for t in lots if t.get("no") == ids[0]), None)
            if not lot:
                return f"⚠ #{ids[0]} is not an open {sym} trade. Your open {sym} lots: " + ", ".join(f"#{t['no']}" for t in lots)
        elif len(lots) == 1:
            lot = lots[0]
        else:
            lines = [f"You have {len(lots)} open lots of {sym}. Which one?"]
            for t in lots:
                lines.append(f"• #{t['no']}: {t['qty']} @ {inr(t['entry'])} ({_fmt_date(t['date'])})")
            lines.append(f"Reply e.g. <code>/sell {sym} {rest[1]} {lots[0]['qty']} #{lots[0]['no']}</code>")
            return "\n".join(lines)
        q = qty or lot["qty"]
        if q > lot["qty"]:
            return f"⚠ #{lot['no']} has only {lot['qty']} shares."
        d = today_ist().isoformat()
        if q < lot["qty"]:  # partial: the sold part becomes its own closed trade
            sold = dict(lot, id=uuid.uuid4().hex[:12], no=self._next_no(), qty=q, status="closed",
                        exit_price=price, exit_date=d, notes=(lot.get("notes") or "") + f" (part of #{lot['no']})",
                        created=datetime.now().isoformat(timespec="seconds"))
            lot["qty"] -= q
            self.us["trades"].append(sold)
            left = f"\n{lot['qty']} shares of #{lot['no']} still open."
        else:
            lot.update(status="closed", exit_price=price, exit_date=d)
            left = ""
        self.changed = True
        g = (price - lot["entry"]) * q
        days = (today_ist() - datetime.strptime(lot["date"], "%Y-%m-%d").date()).days
        emoji = "🟢" if g > 0 else ("🔴" if g < 0 else "⚪")
        return (f"{emoji} Sold <b>{sym}</b> #{lot['no']}: {q} @ {inr(price)}\n"
                f"Gain {inr(g, 0)} ({pct((price / lot['entry'] - 1) * 100)}) · held {days} day(s)" + left)

    def positions(self, a: list[str]) -> str:
        opn = self._open()
        if not opn:
            return "No open trades. Record one with /buy SYMBOL PRICE QTY"
        prices = self.price_fn(sorted({t["symbol"] for t in opn}))
        fired = set(self.bs.get("fired", []))
        lines, tot = ["<b>Open trades</b>"], 0.0
        for t in sorted(opn, key=lambda t: t["no"]):
            p = prices.get(t["symbol"])
            nu, nd = next_levels(t, fired, self.cfg)
            if p is None:
                lines.append(f"#{t['no']} <b>{esc(t['symbol'])}</b> {t['qty']} @ {inr(t['entry'])} · price N/A")
                continue
            g = (p - t["entry"]) * t["qty"]
            tot += g
            lines.append(f"#{t['no']} <b>{esc(t['symbol'])}</b> {t['qty']} @ {inr(t['entry'])} → {inr(p)} "
                         f"({pct((p / t['entry'] - 1) * 100)}, {inr(g, 0)})\n"
                         f"   next ↑ {pct(nu)} · next ↓ {pct(nd)}" + (f" · stop {inr(t['stop'])}" if t.get("stop") else ""))
        lines.append(f"\nTotal unrealised: <b>{inr(tot, 0)}</b>")
        return "\n".join(lines)

    def status(self, a: list[str]) -> str:
        if not self.res.get("stocks"):
            return "No analysis available yet."
        m = self.res.get("mood", {})
        main = main_stocks(self.res, self.cfg)
        def top(s):
            xs = sorted([x for x in main if x["status"].get("status") == s], key=lambda x: -((x.get("rank") or {}).get("score") or 0))
            return xs[:5], len(xs)
        lines = [f"<b>Market: {esc(m.get('mood', 'N/A'))}</b>" + (f" ({esc(', '.join(m.get('reasons') or []))})" if m.get("reasons") else ""),
                 f"Data date {esc(self.res.get('data_date', ''))}"]
        for s, emoji, label in (("READY", "🟢", "Ready to buy"), ("GETTING CLOSE", "🟡", "Getting close")):
            xs, n = top(s)
            lines.append(f"\n{emoji} <b>{label}</b> ({n})")
            for x in xs:
                st = x["status"]
                lines.append(f"• {esc(x['symbol'])}: your {inr((st.get('your') or {}).get('buy_price'))} · "
                             f"best {inr((st.get('best') or {}).get('buy_price'))}")
            if not xs:
                lines.append("none")
        return "\n".join(lines)

    def stock(self, a: list[str]) -> str:
        if not a:
            return "Format: /stock SYMBOL"
        s = normalise_symbol(a[0])
        x = (self.res.get("stocks") or {}).get(s)
        if not x:
            return f"{esc(s)} is not in today's analysis (only liquid fast movers are analysed)."
        st, p, bt, g = x["status"], x["profile"], x["backtest"], x["grade"]
        best, yours = bt.get("best") or {}, bt.get("yours") or {}
        hr = lambda y: "N/A" if y.get("hit_rate") is None else f"{y['hit_rate'] * 100:.0f}% of {y['trades']}"  # noqa: E731
        lines = [f"<b>{esc(s)}</b> · {esc(x['industry'])} · fundamentals {esc(g['grade'])}"
                 + (f" ({esc('; '.join(g['red_flags']))})" if g.get("red_flags") else ""),
                 f"Close {inr(x['last_close'])} ({esc(x['last_date'])})",
                 f"Usual fall: {p['usual_days']:.0f} days, {pct(p['usual_depth_pct'])}",
                 f"Your method level {pct(yours.get('level_pct'))} · hit {hr(yours)}",
                 f"Best tested level {pct(best.get('level_pct'), 1)} · hit {hr(best)} · {best.get('confidence', 'N/A')} confidence"]
        if st.get("in_streak"):
            lines.append(f"Now: <b>{esc(st.get('status'))}</b> · day {st['days_in_streak']} · fall {pct(st['depth_low_pct'])}")
            lines.append(f"Buy: your {inr((st.get('your') or {}).get('buy_price'))} · best {inr((st.get('best') or {}).get('buy_price'))}")
        else:
            lines.append("Not in a down streak today.")
        if x.get("check_summary"):
            lines.append(x["check_summary"]["line"])
        neg = len(((x.get("news") or {}).get("negative")) or [])
        lines.append(f"📰 {neg} negative news item(s) in 14 days" if neg else "📰 No negative news flagged")
        return "\n".join(lines)

    def remove(self, a: list[str]) -> str:
        no = _id_token(a[0]) if a else None
        if not no:
            return "Format: /remove ID   e.g. <code>/remove 3</code> (see /positions for IDs)"
        t = self._find(no)
        if not t:
            return f"⚠ There is no trade #{no}."
        self.us["trades"].remove(t)
        self.changed = True
        return f"🗑 Removed #{no} {esc(t['symbol'])} ({t['qty']} @ {inr(t['entry'])})."

    def edit(self, a: list[str]) -> str:
        if len(a) < 3:
            return "Format: /edit ID PRICE QTY   e.g. <code>/edit 3 3510 10</code>"
        no, price, qty = _id_token(a[0]), _num(a[1]), _num(a[2])
        if not no:
            return "⚠ The first value must be the trade ID (e.g. 3)."
        if price is None or qty is None or qty != int(qty):
            return "⚠ Price and quantity must be positive numbers (quantity a whole number)."
        t = self._find(no)
        if not t:
            return f"⚠ There is no trade #{no}."
        default_tgt = round(t["entry"] * (1 + self.cfg["trading"]["target_pct"] / 100), 2)
        if not t.get("target") or abs(t["target"] - default_tgt) < 0.01:
            t["target"] = round(price * (1 + self.cfg["trading"]["target_pct"] / 100), 2)
        t["entry"], t["qty"] = price, int(qty)
        self.changed = True
        return f"✏️ Updated #{no} {esc(t['symbol'])}: {int(qty)} @ {inr(price)} (target {inr(t['target'])})."


def process_updates(bot: Bot, updates: list[dict], allowed_chat: str, send) -> int | None:
    """Answer messages from your chat only. Returns the next getUpdates offset."""
    nxt = None
    for u in updates:
        nxt = max(nxt or 0, u["update_id"] + 1)
        msg = u.get("message") or u.get("edited_message")
        if not msg or "text" not in msg:
            continue
        if str(msg.get("chat", {}).get("id")) != str(allowed_chat):
            log.warning("Ignored a message from an unauthorised chat")
            continue
        send(bot.handle(msg["text"]))
    return nxt
