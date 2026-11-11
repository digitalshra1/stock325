"""Loads config.yaml. Every threshold used by the tool lives there."""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"
CACHE_DIR = ROOT / "data" / "cache"
STATE_DIR = ROOT / "data" / "state"
OUTPUT_DIR = ROOT / "output"

log = logging.getLogger("nsepattern")


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_CONFIG
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # yfinance is noisy; keep its own errors but not its chatter
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    logging.getLogger("peewee").setLevel(logging.WARNING)
