import pandas as pd

from nsepattern.engine.mood import compute_mood_history, reasons_for
from tests.helpers import cfg


def _s(vals, start="2024-01-01"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)), dtype=float)


def test_normal_when_calm():
    c = cfg()
    closes = {"nifty": _s([100] * 10), "india_vix": _s([12] * 10)}
    f = compute_mood_history(closes, c)
    assert (f["mood"] == "NORMAL").all()


def test_points_and_levels():
    c = cfg()
    nifty = _s([100, 100, 100, 100, 100, 100, 98])        # -2% 1d, -2% 5d
    vix = _s([14, 14, 14, 14, 14, 14, 21])                 # >=20 and +50% in 5d
    f = compute_mood_history({"nifty": nifty, "india_vix": vix}, c)
    last = f.iloc[-1]
    assert last["points"] == 3 and last["mood"] == "CAUTION"
    assert set(reasons_for(last)) == {"NIFTY 1-day fall", "India VIX high", "India VIX jump (5d)"}


def test_stressed_with_global_and_breadth():
    c = cfg()
    nifty = _s([100] * 6 + [95])
    vix = _s([22] * 7)
    # US data is used from the previous day (no look-ahead): put the S&P drop on day 6
    sp = _s([100] * 5 + [97, 97])
    breadth = _s([50] * 6 + [85])
    f = compute_mood_history({"nifty": nifty, "india_vix": vix, "sp500": sp}, c, breadth)
    last = f.iloc[-1]
    # NIFTY 1d (-5%), NIFTY 5d (-5%), VIX high, S&P 1d (-3%, previous day), breadth 85%
    assert last["points"] == 5 and last["mood"] == "STRESSED"


def test_us_data_not_used_same_day():
    c = cfg()
    nifty = _s([100] * 7)
    sp = _s([100] * 6 + [90])  # crash on the last date: not known yet in India that evening
    f = compute_mood_history({"nifty": nifty, "sp500": sp}, c)
    assert f.iloc[-1]["points"] == 0
