"""Secrets: read from environment variables first (GitHub Actions secrets), then from the
local, never-uploaded files `.secrets.env` (KEY=VALUE lines) and `.app_password`.

Nothing here is ever printed or logged.
"""
from __future__ import annotations

import os

from nsepattern.config import ROOT

SECRETS_FILE = ROOT / ".secrets.env"


def _file_values() -> dict[str, str]:
    out: dict[str, str] = {}
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def get_secret(name: str) -> str | None:
    v = os.environ.get(name)
    if v:
        return v
    v = _file_values().get(name)
    if v:
        return v
    if name == "APP_PASSWORD":
        p = ROOT / ".app_password"
        if p.exists():
            return p.read_text(encoding="utf-8").strip() or None
    return None


def save_secret(name: str, value: str) -> None:
    """Add or replace one KEY=VALUE line in .secrets.env (used by telegram-setup for the chat ID)."""
    vals = _file_values()
    vals[name] = value
    SECRETS_FILE.write_text("".join(f"{k}={v}\n" for k, v in vals.items()), encoding="utf-8")
