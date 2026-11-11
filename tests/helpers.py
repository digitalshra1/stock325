import pandas as pd

from nsepattern.config import load_config


def cfg():
    return load_config()


def frame_from_closes(closes, opens=None, highs=None, lows=None):
    """Build an OHLC frame. By default Open=High=Low=Close (no intraday range)."""
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    o = opens or closes
    h = highs or [max(a, b) for a, b in zip(o, closes)]
    lo = lows or [min(a, b) for a, b in zip(o, closes)]
    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": closes, "Volume": [1e6] * len(closes)},
                        index=idx)
