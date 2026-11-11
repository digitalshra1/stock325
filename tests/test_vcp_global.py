import numpy as np
import pandas as pd
import pytest

from nsepattern.engine import global_mkts as gm
from nsepattern.engine import vcp
from tests.helpers import cfg

C = cfg()


def _seg(a, b, n):
    return list(np.linspace(a, b, n + 1)[1:])


def vcp_series(breakout=True):
    closes = list(np.linspace(100, 200, 260))                # long uptrend
    closes += _seg(200, 170, 8) + _seg(170, 198, 10)         # pullback 1: 15 %
    closes += _seg(198, 182, 6) + _seg(182, 196, 8)          # pullback 2: ~8 %
    closes += _seg(196, 189, 4) + _seg(189, 195, 5)          # pullback 3: ~3.6 %
    closes += [195, 194.5, 195.2, 194.8, 195.1, 194.9]       # tight
    n_base = len(closes) - 260
    spread = list(np.full(260, 0.012)) + list(np.linspace(0.02, 0.004, n_base))
    vol = list(np.full(260, 1_000_000.0)) + list(np.linspace(1_200_000, 400_000, n_base))
    if breakout:
        closes.append(204.0)
        spread.append(0.02)
        vol.append(3_000_000.0)
    c = np.array(closes)
    sp = np.array(spread)
    idx = pd.bdate_range("2023-01-02", periods=len(c))
    return pd.DataFrame({"Open": c, "High": c * (1 + sp), "Low": c * (1 - sp), "Close": c, "Volume": vol}, index=idx)


def test_swings_and_contractions():
    h = np.array([1, 2, 5, 2, 1, 0.5, 1, 4, 1, 0.8, 1, 3, 1, 1, 1], float)
    pts = vcp.swing_points(h, h, 2)
    kinds = [p[1] for p in pts]
    assert kinds[:2] == ["H", "L"]
    cons = vcp.contractions(pts)
    assert cons and cons[0]["depth_pct"] == pytest.approx(90.0)


def test_vcp_breakout_detected():
    r = vcp.detect(vcp_series(True), C)
    assert r["conditions"]["ema_stack"]["ok"]
    assert r["conditions"]["shrinking"]["ok"], r["conditions"]["shrinking"]["detail"]
    assert r["conditions"]["breakout_volume"]["ok"]
    assert r["status"] == "VCP BREAKOUT", r


def test_vcp_setup_without_breakout():
    r = vcp.detect(vcp_series(False), C)
    assert r["status"] == "VCP SETUP", {k: v["ok"] for k, v in r["conditions"].items()}
    assert not r["conditions"]["breakout_volume"]["ok"]


def test_vcp_not_in_downtrend():
    df = vcp_series(True)
    df = df.iloc[::-1].copy()
    df.index = pd.bdate_range("2023-01-02", periods=len(df))
    assert vcp.detect(df, C)["status"] is None


def test_short_data_is_safe():
    assert vcp.detect(vcp_series(True).tail(100), C)["status"] is None


def test_relationship_uses_previous_global_session_only():
    idx = pd.bdate_range("2024-01-01", periods=300)
    rng = np.random.default_rng(1)
    g_ret = rng.normal(0, 1, 300)
    g = pd.Series(100 * np.cumprod(1 + g_ret / 100), index=idx)
    # NIFTY's day move on D copies the global move of D-1 (perfect lagged relationship)
    n_ret = np.r_[0, g_ret[:-1]]
    close = 100 * np.cumprod(1 + n_ret / 100)
    opn = np.r_[100, close[:-1]] * (1 + np.r_[0, g_ret[:-1]] / 200)
    nifty = pd.DataFrame({"Open": opn, "Close": close}, index=idx)
    r = gm.relationship(g, nifty, years=3)
    assert r["corr_day"] > 0.99
    assert r["up_then_up"] == pytest.approx(100.0)
    assert r["down_then_down"] == pytest.approx(100.0)
    # same-day relationship would be invisible -> make sure we did not use day D's global move
    nifty_same = pd.DataFrame({"Open": opn, "Close": 100 * np.cumprod(1 + g_ret / 100)}, index=idx)
    assert abs(gm.relationship(g, nifty_same, years=3)["corr_day"]) < 0.2


def test_confidence():
    assert gm.confidence(5, 8) is None
    assert gm.confidence(50, 100) == pytest.approx(50.0)
    assert gm.confidence(70, 100) > 99


def test_session_completion_uses_ist_close_times():
    from datetime import datetime
    idx = pd.bdate_range("2026-09-01", "2026-11-10")
    s = pd.Series(np.linspace(100, 110, len(idx)), index=idx)
    s.iloc[-1] = s.iloc[-2] * 1.02  # last session +2 %
    ist = gm.IST
    # US session dated 10 Nov closes 16:00 New York = 02:30 IST on 11 Nov (winter time)
    assert gm.session_close_ist("^DJI", "2026-11-10").strftime("%d %H:%M") == "11 02:30"
    live = gm.snapshot(s, "^DJI", now=datetime(2026, 11, 11, 1, 0, tzinfo=ist))
    done = gm.snapshot(s, "^DJI", now=datetime(2026, 11, 11, 3, 0, tzinfo=ist))
    assert not live["complete"] and done["complete"]
    assert done["chg_done"] == pytest.approx(2.0)
    assert live["chg_done"] != pytest.approx(2.0)   # unfinished session never feeds the outlook
    assert live["country"] == "United States" and live["flag"] == "us"
    # summer time: 16:00 New York = 01:30 IST
    assert gm.session_close_ist("^DJI", "2026-07-10").strftime("%H:%M") == "01:30"
    assert gm.session_close_ist("^GDAXI", "2026-11-10").strftime("%H:%M") == "22:00"
