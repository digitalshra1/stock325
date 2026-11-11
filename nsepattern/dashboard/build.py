"""Build the static dashboard: output/site/index.html (+ details/*.json, logo.svg).

The analysis data is embedded in the page. With a password it is AES-GCM encrypted
(see nsepattern/crypto.py) and the browser decrypts it after you type the password.

Password source (first found): --password, env APP_PASSWORD, file .app_password in the project root.
Without one, an UNLOCKED local preview is built with a red warning banner (never publish that).

For large runs the heavy per-stock detail (charts, day table, trade history, news) is split
into details/<bucket>.json files (encrypted the same way) and loaded only when a stock page
is opened, so the main page stays fast on a phone.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from nsepattern.config import OUTPUT_DIR, ROOT
from nsepattern.crypto import encrypt_json

TEMPLATE = Path(__file__).with_name("template.html")
ASSETS = Path(__file__).with_name("assets")
SITE_DIR = OUTPUT_DIR / "site"
HEAVY = ("chart", "sheet")
HEAVY_BT = ("best_trades", "your_trades", "table")
SPLIT_ABOVE = 150  # stocks


def find_password(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    if os.environ.get("APP_PASSWORD"):
        return os.environ["APP_PASSWORD"]
    p = ROOT / ".app_password"
    if p.exists():
        v = p.read_text(encoding="utf-8").strip()
        return v or None
    return None


def dashboard_cfg(cfg: dict) -> dict:
    return {
        "target": cfg["trading"]["target_pct"], "goal": cfg["trading"]["monthly_trade_goal"],
        "show_unknown": cfg["filters"]["show_unknown_fundamentals_on_main"],
        "deep": cfg["pattern"]["deep_fall_pct"], "deep_days": cfg["pattern"]["deep_recent_days"],
        "liq": cfg["filters"]["liquidity_min_crore"], "range": cfg["filters"]["mover_min_avg_range_pct"],
        "news_days": cfg["checks"]["news_days"], "fund": cfg["fundamentals"],
        "state_file": cfg.get("user_state", {}).get("file", "data/state/userstate.enc.json"),
    }


def bucket(sym: str) -> str:
    return hashlib.md5(sym.encode("utf-8")).hexdigest()[:1]  # 16 buckets


def _round(o):
    """Shorter numbers for a lighter page (prices keep 2 decimals, ratios 4)."""
    if isinstance(o, float):
        return round(o, 2) if abs(o) >= 100 else round(o, 4)
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_round(v) for v in o]
    return o


def _split(data: dict) -> dict[str, dict]:
    """Move heavy fields out of data['stocks'] (and the VCP add-on) into buckets loaded on demand."""
    buckets: dict[str, dict] = {}
    for s, a in data.get("stocks", {}).items():
        det = {k: a.pop(k) for k in HEAVY + ("bounce_lows", "fundamentals", "shareholding") if k in a}
        if a.get("profile", {}).get("depths_pct") is not None:
            det["depths_pct"] = a["profile"].pop("depths_pct")
        bt = a.get("backtest") or {}
        det["backtest"] = {k: bt.pop(k) for k in HEAVY_BT if k in bt}
        if "news" in a and a["news"]:
            n = a["news"]
            det["news"] = n
            a["news"] = {"negative": n.get("negative", []), "sources_ok": n.get("sources_ok", []),
                         "count": len(n.get("headlines", [])) + len(n.get("announcements", []))}
        if "deals" in a:
            det["deals"] = a.pop("deals")
        a["_b"] = bucket(s)
        buckets.setdefault(a["_b"], {})[s] = det
    for s, v in ((data.get("extras") or {}).get("vcp") or {}).items():
        vd = {"conditions": v.pop("conditions", None)}
        h = v.get("history") or {}
        vd["history_trades"] = h.pop("trades", None)
        v["_b"] = bucket(s)
        buckets.setdefault(v["_b"], {}).setdefault(s, {})["vcp"] = vd
    return buckets


def _blob(obj, pw: str | None) -> str:
    payload = {"locked": True, "env": encrypt_json(obj, pw, compress=True)} if pw else {"locked": False, "data": obj}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build(res: dict, cfg: dict, password: str | None = None, positions: dict | None = None,
          out_dir: Path | None = None, use_saved_password: bool = True, extras: dict | None = None) -> Path:
    data = json.loads(json.dumps(res))  # deep copy; never touch the caller's results
    data["cfg"] = dashboard_cfg(cfg)
    data["positions"] = positions
    if extras is None:
        ep = OUTPUT_DIR / "extras.json"
        extras = json.loads(ep.read_text(encoding="utf-8")) if ep.exists() and not data.get("demo") else None
    data["extras"] = json.loads(json.dumps(extras)) if extras else None
    data = _round(data)
    data["repo"] = {"name": os.environ.get("GITHUB_REPOSITORY"), "branch": os.environ.get("GITHUB_REF_NAME") or "main"}
    pw = find_password(password) if use_saved_password else password
    d = out_dir or SITE_DIR
    d.mkdir(parents=True, exist_ok=True)
    if len(data.get("stocks", {})) > SPLIT_ABOVE:
        buckets = _split(data)
        data["details_split"] = True
        (d / "details").mkdir(exist_ok=True)
        for old in (d / "details").glob("*.json"):
            old.unlink()
        for k, v in buckets.items():
            (d / "details" / f"{k}.json").write_text(_blob(v, pw), encoding="utf-8")
    # "</" must not appear raw inside the <script> tag
    blob = _blob(data, pw).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__PAYLOAD__", blob)
    out = d / "index.html"
    out.write_text(html, encoding="utf-8")
    # brand files (logo light/dark, icons): regenerate with make_brand_assets.py to change the logo
    for f in ASSETS.glob("*"):
        shutil.copyfile(f, d / f.name)
    # lets the phone "Add to Home screen" open it like an app
    (d / "manifest.json").write_text(json.dumps({
        "name": "3.25 by SHRA1", "short_name": "3.25", "start_url": ".", "display": "standalone",
        "background_color": "#06111d", "theme_color": "#0a2540",
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"}]}), encoding="utf-8")
    return out
