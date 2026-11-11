"""Minimal Telegram Bot API client (sendMessage, getUpdates) with a dry-run mode.

Dry run prints messages instead of sending them, so everything can be tested without a bot.
The bot token is never printed or logged.
"""
from __future__ import annotations

import html
import logging
import sys
import time

import requests

log = logging.getLogger("nsepattern.telegram")


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Split on line breaks so HTML tags are never cut in half."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single giant line (rare)
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur.rstrip("\n"))
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur.rstrip("\n"))
    return parts


def esc(s) -> str:
    return html.escape(str(s), quote=False)


class Telegram:
    def __init__(self, token: str | None, chat_id: str | None, dry_run: bool = False, max_len: int = 4000):
        self.token, self.chat_id = token, str(chat_id) if chat_id else None
        self.dry_run = dry_run or not token or not chat_id
        self.max_len = max_len
        self.sent: list[str] = []  # everything "sent" in this run (also used by tests)

    @property
    def _base(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def send(self, text: str, chat_id: str | None = None) -> bool:
        ok = True
        for part in split_message(text, self.max_len):
            self.sent.append(part)
            if self.dry_run:
                out = sys.stdout
                try:
                    out.reconfigure(encoding="utf-8")
                except AttributeError:
                    pass
                print("\n──── Telegram (dry run) ────\n" + part + "\n────────────────────────────", file=out)
                continue
            for attempt in range(3):
                try:
                    r = requests.post(f"{self._base}/sendMessage", timeout=20, data={
                        "chat_id": chat_id or self.chat_id, "text": part, "parse_mode": "HTML",
                        "disable_web_page_preview": "true"})
                    if r.status_code == 200:
                        break
                    log.warning("Telegram send failed: HTTP %s %s", r.status_code, r.text[:200])
                    if r.status_code == 429:
                        time.sleep(int(r.json().get("parameters", {}).get("retry_after", 3)))
                    elif r.status_code == 400:  # bad HTML: retry as plain text
                        requests.post(f"{self._base}/sendMessage", timeout=20,
                                      data={"chat_id": chat_id or self.chat_id, "text": part})
                        break
                except requests.RequestException as e:
                    log.warning("Telegram send error: %s", e.__class__.__name__)
                    time.sleep(2 * (attempt + 1))
            else:
                ok = False
        return ok

    def get_updates(self, offset: int | None) -> list[dict]:
        if not self.token:
            return []
        try:
            r = requests.get(f"{self._base}/getUpdates", timeout=30,
                             params={"offset": offset or 0, "timeout": 0, "allowed_updates": '["message"]'})
            if r.status_code == 200:
                return r.json().get("result", [])
            log.warning("Telegram getUpdates failed: HTTP %s", r.status_code)
        except requests.RequestException as e:
            log.warning("Telegram getUpdates error: %s", e.__class__.__name__)
        return []
