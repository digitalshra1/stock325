"""Your personal data (watchlist, trades, price alerts) and the bot's own bookkeeping, both encrypted.

Files (paths in config.yaml -> user_state), encrypted with APP_PASSWORD:
  userstate.enc.json  watchlist, alerts, trades, settings   (written by the dashboard AND by Telegram commands)
  botstate.enc.json   Telegram offset, alerts already sent, peaks, last mood ... (written only by the scheduled jobs)
Keeping them apart means the dashboard and the bot rarely touch the same file.

userstate schema (version 1):
  watchlist: [{symbol, added, note}]
  alerts:    [{id, symbol, kind: "above"|"below", price, note, created, active, triggered_at, triggered_price}]
  trades:    [{id, no, symbol, entry, qty, target, stop, date, strategy, notes, status: "open"|"closed",
               exit_price, exit_date, created}]
  settings:  {capital}
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from nsepattern.config import ROOT
from nsepattern.crypto import decrypt_json, encrypt_json


def empty_state() -> dict:
    return {"version": 1, "watchlist": [], "alerts": [], "trades": [], "settings": {"capital": 0}, "updated": None}


def empty_bot_state() -> dict:
    return {"version": 1, "tg_offset": 0, "fired": [], "peaks": {}, "last_check": None, "last_mood": None,
            "prev_ready": [], "zone_sent": {}, "log": [], "last_evening": None, "last_morning": None}


def state_path(cfg: dict) -> Path:
    return ROOT / cfg["user_state"]["file"]


def bot_state_path(cfg: dict) -> Path:
    f = cfg["user_state"].get("bot_file") or str(Path(cfg["user_state"]["file"]).with_name("botstate.enc.json"))
    return ROOT / f


def _load(p: Path, password: str, base: dict) -> dict:
    if not p.exists():
        return base
    st = decrypt_json(json.loads(p.read_text(encoding="utf-8")), password)
    base.update(st or {})
    return base


def _save(p: Path, obj: dict, password: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    obj = dict(obj)
    obj["updated"] = datetime.now().isoformat(timespec="seconds")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(encrypt_json(obj, password)), encoding="utf-8")
    tmp.replace(p)
    return p


def load_state(cfg: dict, password: str) -> dict:
    st = _load(state_path(cfg), password, empty_state())
    number_trades(st)
    return st


def save_state(cfg: dict, state: dict, password: str) -> Path:
    number_trades(state)
    return _save(state_path(cfg), state, password)


def load_bot_state(cfg: dict, password: str) -> dict:
    return _load(bot_state_path(cfg), password, empty_bot_state())


def save_bot_state(cfg: dict, bs: dict, password: str) -> Path:
    bs["log"] = bs.get("log", [])[-300:]
    return _save(bot_state_path(cfg), bs, password)


def number_trades(state: dict) -> None:
    """Every trade gets a short number (#1, #2 …) that is easy to type in Telegram."""
    used = {t["no"] for t in state["trades"] if t.get("no")}
    nxt = max(used, default=0) + 1
    for t in sorted(state["trades"], key=lambda t: t.get("created") or ""):
        if not t.get("no"):
            t["no"] = nxt
            nxt += 1


# ---------------------------------------------------------------- alert checks (pure functions)

def check_price_alerts(state: dict, bars: dict[str, dict], now: str) -> list[str]:
    """bars: symbol -> {"high", "low", "last"} since the last check.
    Fires each active alert once and deactivates it. Returns Telegram messages."""
    msgs = []
    for a in state["alerts"]:
        if not a.get("active", True):
            continue
        b = bars.get(a["symbol"])
        if not b:
            continue
        hit = (a["kind"] == "above" and b["high"] >= a["price"]) or (a["kind"] == "below" and b["low"] <= a["price"])
        if hit:
            a["active"] = False
            a["triggered_at"] = now
            a["triggered_price"] = b["last"]
            arrow = "⬆️ rose above" if a["kind"] == "above" else "⬇️ fell below"
            note = f"\n📝 {a['note']}" if a.get("note") else ""
            msgs.append(f"🔔 <b>{a['symbol']}</b> {arrow} your alert ₹{a['price']:,.2f} (now ₹{b['last']:,.2f}).{note}")
    return msgs


def check_stop_losses(state: dict, bars: dict[str, dict], fired: set) -> list[str]:
    """Telegram alert when an open trade touches its stop loss. Fires once; re-arms if the
    price later recovers above the stop (same idea as the downside re-arm rule)."""
    msgs = []
    for t in state["trades"]:
        if t.get("status") != "open" or not t.get("stop"):
            continue
        b = bars.get(t["symbol"])
        if not b:
            continue
        key = f"SL:{t['id']}"
        if key in fired and b["last"] > t["stop"]:
            fired.discard(key)  # recovered above the stop -> re-arm
        if key not in fired and b["low"] <= t["stop"]:
            fired.add(key)
            loss = (t["stop"] / t["entry"] - 1) * 100
            msgs.append(f"🛑 <b>{t['symbol']}</b> #{t.get('no', '')} touched your stop loss ₹{t['stop']:,.2f} "
                        f"({loss:+.2f}% from entry ₹{t['entry']:,.2f}). Now ₹{b['last']:,.2f}. "
                        f"The tool never sells: your decision.")
    return msgs
