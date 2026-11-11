"""Command line: python -m nsepattern <command>"""
from __future__ import annotations

import argparse
import sys

from nsepattern.config import load_config, setup_logging


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nsepattern", description="NSE down-streak pattern tool")
    ap.add_argument("--config", help="path to config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c1 = sub.add_parser("checkpoint1", help="pattern profile + buy levels for a few stocks")
    c1.add_argument("symbols", nargs="*", help="NSE symbols (default: RELIANCE TATASTEEL HDFCBANK INFY + a mid-cap mover)")
    r = sub.add_parser("run", help="full analysis over all NSE stocks (writes output/results.json)")
    r.add_argument("--weekly", action="store_true", help="also refresh stock list, fundamentals, shareholding")
    r.add_argument("--limit", type=int, help="only the first N stocks (for testing)")
    sub.add_parser("checkpoint2", help="report from the last run's output/results.json")
    db = sub.add_parser("dashboard", help="rebuild output/site/index.html from output/results.json")
    db.add_argument("--password", help="app password (default: env APP_PASSWORD or .app_password file)")
    for name, hlp in (("evening", "full scan + dashboard + evening Telegram alerts"),
                      ("monitor", "market-hours price alerts + answer Telegram commands"),
                      ("listen", "answer Telegram commands"),
                      ("morning", "global markets refresh + morning Telegram message")):
        j = sub.add_parser(name, help=hlp)
        j.add_argument("--dry-run", action="store_true", help="print messages instead of sending")
        if name == "monitor":
            j.add_argument("--force", action="store_true", help="check prices even outside market hours")
        if name == "evening":
            j.add_argument("--no-run", action="store_true", help="only send alerts from the last results")
            j.add_argument("--if-missed", action="store_true", help="backup run: skip if today's scan already ran")
            j.add_argument("--weekly", action="store_true", help="also refresh stock list and fundamentals")
        if name == "morning":
            j.add_argument("--no-refresh", action="store_true", help="only send the message")
    c = sub.add_parser("cmd", help='test a Telegram command locally, e.g. cmd "/buy TCS 3500 10"')
    c.add_argument("text")
    c.add_argument("--send", action="store_true", help="really send the reply to Telegram")
    s = sub.add_parser("sim", help="simulate a price bar for one stock and run all price alerts")
    s.add_argument("symbol")
    s.add_argument("--high", type=float, required=True)
    s.add_argument("--low", type=float, required=True)
    s.add_argument("--last", type=float, required=True)
    s.add_argument("--send", action="store_true", help="really send the alerts to Telegram")
    sub.add_parser("telegram-setup", help="find and save your Telegram chat ID")
    sub.add_parser("global", help="morning refresh of global markets + expected NIFTY opening (quick)")
    sub.add_parser("extras", help="add-on layer: VCP, global markets, industry analytics, LTP -> output/extras.json")
    sv = sub.add_parser("serve", help="local dashboard server with saving + live prices (http://127.0.0.1:8765)")
    sv.add_argument("--port", type=int, default=8765)
    dm = sub.add_parser("demo", help="offline demo on synthetic data -> output/demo/index.html")
    dm.add_argument("--password", help="lock the demo too (default: unlocked)")
    a = ap.parse_args(argv)
    if a.cmd == "demo":
        setup_logging(a.verbose)
        from nsepattern.demo import build_demo
        out = build_demo(load_config(a.config), a.password)  # unlocked unless --password is given
        print(f"Demo dashboard written to {out}")
        return 0
    setup_logging(a.verbose)
    cfg = load_config(a.config)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.cmd in ("evening", "monitor", "listen", "morning", "cmd", "sim", "telegram-setup"):
        from nsepattern import jobs
        if a.cmd == "evening":
            jobs.job_evening(cfg, a.dry_run, run=not a.no_run, if_missed=a.if_missed, weekly=a.weekly)
        elif a.cmd == "monitor":
            jobs.job_monitor(cfg, a.dry_run, force=a.force)
        elif a.cmd == "listen":
            jobs.job_listen(cfg, a.dry_run)
        elif a.cmd == "morning":
            jobs.job_morning(cfg, a.dry_run, refresh=not a.no_refresh)
        elif a.cmd == "cmd":
            jobs.job_cmd(cfg, a.text, dry=not a.send)
        elif a.cmd == "sim":
            jobs.job_sim(cfg, a.symbol, a.high, a.low, a.last, dry=not a.send)
        else:
            print(jobs.telegram_setup(cfg))
        return 0
    if a.cmd == "extras":
        from nsepattern.extras import run_extras
        run_extras(cfg)
        return 0
    if a.cmd == "global":
        import json
        from nsepattern.config import OUTPUT_DIR
        from nsepattern.dashboard.build import build
        from nsepattern.extras import refresh_global
        refresh_global(cfg)
        res = json.loads((OUTPUT_DIR / "results.json").read_text(encoding="utf-8"))
        print("Dashboard:", build(res, cfg))
        return 0
    if a.cmd == "serve":
        from nsepattern.server import serve
        serve(cfg, a.port)
        return 0
    if a.cmd in ("run", "checkpoint2"):
        import json
        from nsepattern.config import OUTPUT_DIR
        from nsepattern.reports.checkpoint2 import render
        if a.cmd == "run":
            from nsepattern.pipeline import run_full
            from nsepattern.dashboard.build import build
            res = run_full(cfg, weekly=a.weekly, limit=a.limit)
            try:  # add-on layer; never allowed to break the main run
                from nsepattern.extras import run_extras
                from nsepattern.pipeline import LAST_PRICES
                run_extras(cfg, prices=dict(LAST_PRICES) or None)
            except Exception as e:  # noqa: BLE001
                import logging
                logging.getLogger("nsepattern").warning("Extras layer failed (main results are fine): %s", e)
            print("Dashboard:", build(res, cfg))
        else:
            res = json.loads((OUTPUT_DIR / "results.json").read_text(encoding="utf-8"))
        print(render(res, cfg))
        return 0
    if a.cmd == "dashboard":
        import json
        from nsepattern.config import OUTPUT_DIR
        from nsepattern.dashboard.build import build
        res = json.loads((OUTPUT_DIR / "results.json").read_text(encoding="utf-8"))
        out = build(res, cfg, a.password)
        locked = "password protected" if json.loads(out.read_text(encoding="utf-8").split('id="payload" type="application/json">')[1].split("</script>")[0])["locked"] else "UNLOCKED local preview"
        print(f"Dashboard written to {out} ({locked})")
        return 0
    if a.cmd == "checkpoint1":
        from nsepattern.reports.checkpoint1 import run
        from nsepattern.data.universe import normalise_symbol
        md = run(cfg, [normalise_symbol(s) for s in a.symbols] or None)
        sys.stdout.reconfigure(encoding="utf-8")
        print(md)
    return 0
