"""Local dashboard server (your PC only, http://127.0.0.1:8765).

* Serves output/site (the dashboard).
* /api/state  GET/PUT  the encrypted personal-data file. The page encrypts in the browser,
  so this server only ever stores the encrypted blob (it never sees your password).
* /api/quotes?s=A,B    latest prices from Yahoo 5-minute bars (about 15 minutes delayed),
  cached for 60 seconds, so lists can show a fresher LTP while the market is open.
Bound to 127.0.0.1 only; writes are refused from any other web origin.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import yfinance as yf

from nsepattern.config import OUTPUT_DIR
from nsepattern.state import state_path

log = logging.getLogger("nsepattern.server")
_QCACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()
MAX_STATE = 5_000_000


def fetch_quotes(symbols: list[str]) -> dict:
    now = time.time()
    out, need = {}, []
    with _LOCK:
        for s in symbols:
            hit = _QCACHE.get(s)
            if hit and now - hit[0] < 60:
                out[s] = hit[1]
            else:
                need.append(s)
    if need:
        try:
            raw = yf.download([f"{s}.NS" for s in need], period="2d", interval="5m", group_by="ticker",
                              progress=False, threads=True, auto_adjust=False)
            for s in need:
                try:
                    d = raw[f"{s}.NS"] if len(need) > 1 else raw
                    d = d.dropna(subset=["Close"])
                    if d.empty:
                        continue
                    last = d.iloc[-1]
                    q = {"ltp": float(last["Close"]), "time": d.index[-1].isoformat(),
                         "day_high": float(d["High"][d.index.date == d.index[-1].date()].max()),
                         "day_low": float(d["Low"][d.index.date == d.index[-1].date()].min())}
                    out[s] = q
                    with _LOCK:
                        _QCACHE[s] = (now, q)
                except (KeyError, IndexError):
                    pass
        except Exception as e:  # noqa: BLE001
            log.warning("Live quotes failed: %s", e)
    return out


class Handler(SimpleHTTPRequestHandler):
    cfg: dict = {}
    port: int = 8765

    def log_message(self, fmt, *args):  # quieter console
        if "/api/" in (args[0] if args else ""):
            log.debug(fmt, *args)

    def _json(self, code: int, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self) -> bool:
        o = self.headers.get("Origin")
        return o is None or o in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}")

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/api/ping":
            return self._json(200, {"mode": "local", "has_state": state_path(self.cfg).exists()})
        if u.path == "/api/state":
            p = state_path(self.cfg)
            if not p.exists():
                return self._json(404, {"error": "no state yet"})
            return self._json(200, json.loads(p.read_text(encoding="utf-8")))
        if u.path == "/api/quotes":
            syms = [s.strip().upper() for s in parse_qs(u.query).get("s", [""])[0].split(",") if s.strip()][:150]
            return self._json(200, fetch_quotes(syms))
        return super().do_GET()

    def do_PUT(self):  # noqa: N802
        if urlparse(self.path).path != "/api/state":
            return self._json(404, {"error": "not found"})
        if not self._origin_ok():
            return self._json(403, {"error": "origin not allowed"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_STATE:
            return self._json(400, {"error": "bad size"})
        try:
            env = json.loads(self.rfile.read(n))
            if not all(k in env for k in ("v", "iter", "salt", "iv", "ct")):
                raise ValueError("not an encrypted envelope")
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        p = state_path(self.cfg)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(env), encoding="utf-8")
        tmp.replace(p)
        return self._json(200, {"ok": True})


def serve(cfg: dict, port: int = 8765):
    site = OUTPUT_DIR / "site"
    site.mkdir(parents=True, exist_ok=True)
    Handler.cfg, Handler.port = cfg, port
    httpd = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(site)))
    print(f"Dashboard: http://127.0.0.1:{port}/   (demo: http://127.0.0.1:{port}/demo/)  Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
