import pandas as pd
import pytest

from nsepattern.engine.backtest import level_stats, simulate_level
from nsepattern.engine.streaks import find_streaks
from tests.helpers import frame_from_closes

T = 0.0325


def run(df, level, window=30):
    s = find_streaks(df)
    return simulate_level(df, s, level, T, window)


def _pad(closes, n=40, value=None):
    v = value if value is not None else closes[-1]
    return closes + [v] * n


def test_fill_on_low_touch_at_buy_price():
    # P0=100, buy at -5% = 95. Day 2 low touches 94.5 (open 97) -> fill at 95
    closes = [100, 98, 96, 95.5, 101]
    opens = [100, 99, 97, 96, 96]
    lows = [100, 97.5, 94.5, 95, 96]
    highs = [100, 99, 97, 96.5, 101]
    df = frame_from_closes(_pad(closes, value=101), _pad(opens, value=101), _pad(highs, value=101),
                           _pad(lows, value=101))
    tr = run(df, -0.05)
    assert len(tr) == 1
    t = tr.iloc[0]
    assert t["fill"] == pytest.approx(95.0)
    assert t["fill_date"] == df.index[2]


def test_gap_down_fills_at_open():
    closes = [100, 98, 93, 94, 101]
    opens = [100, 99, 93.5, 93, 95]
    lows = [100, 97.8, 92.8, 92.9, 95]
    highs = [100, 99, 94, 94.5, 101]
    df = frame_from_closes(_pad(closes, value=101), _pad(opens, value=101), _pad(highs, value=101),
                           _pad(lows, value=101))
    t = run(df, -0.05).iloc[0]
    assert t["fill"] == pytest.approx(93.5)  # opened below 95


def test_order_not_live_on_first_down_day():
    # Only the FIRST down day touches the level; afterwards it doesn't -> no fill
    closes = [100, 96, 95.8, 102]
    lows = [100, 94, 95.5, 95.5]
    df = frame_from_closes(_pad(closes, value=102), lows=_pad(lows, value=102))
    assert run(df, -0.05).empty


def test_order_live_on_day_after_streak_end():
    # streak 100 -> 98 -> 97 ends; next day (green) low touches 95 -> fills
    closes = [100, 98, 97, 99, 101]
    lows = [100, 98, 97, 94.9, 99]
    df = frame_from_closes(_pad(closes, value=101), lows=_pad(lows, value=101))
    tr = run(df, -0.05)
    assert len(tr) == 1 and tr.iloc[0]["fill_date"] == df.index[3]


def test_target_not_checked_on_fill_day():
    # Fill day high already above target -> must not count; target hits next day
    closes = [100, 98, 96, 99]
    opens = [100, 99, 97, 98]
    lows = [100, 97.5, 94.0, 97]
    highs = [100, 99, 99.0, 98.2]  # fill at 95, target 98.0875; fill-day high 99 ignored
    rest = [100] * 40
    df = frame_from_closes(closes + rest, opens + rest, highs + rest, lows + rest)
    t = run(df, -0.05).iloc[0]
    assert t["hit"] and t["days_to_target"] == 1
    assert t["result"] == pytest.approx(T)


def test_mae_and_miss_result():
    # fill at 95, then it sinks to 90 and closes window at 92 -> miss
    closes = [100, 98, 96, 101]
    lows = [100, 98, 95, 101]
    tail_c = [93] * 10 + [91] * 10 + [92] * 25
    tail_l = [93] * 10 + [90] * 10 + [92] * 25
    df = frame_from_closes([100, 98, 96, 93] + tail_c,
                           lows=[100, 98, 95, 93] + tail_l)
    tr = run(df, -0.05, window=30)
    t = tr.iloc[0]
    assert not t["hit"]
    assert t["mae"] == pytest.approx(90 / 95 - 1)
    fill_i = df.index.get_loc(t["fill_date"])
    assert t["result"] == pytest.approx(df["Close"].iloc[fill_i + 30] / 95 - 1)


def test_skip_unfinished_window_without_hit():
    df = frame_from_closes([100, 98, 96, 95, 95, 95], lows=[100, 98, 94.9, 95, 95, 95])
    assert run(df, -0.05).empty


def test_expectancy_formula():
    tr = pd.DataFrame({"hit": [True, True, False], "days_to_target": [2, 4, None],
                       "mae": [-0.01, -0.02, -0.06], "result": [T, T, -0.04]})
    st = level_stats(tr, T, years=1)
    p = (2 + 2) / (3 + 4)
    assert st["expectancy"] == pytest.approx(p * T + (1 - p) * -0.04)
    assert st["hit_rate"] == pytest.approx(2 / 3)
    assert st["median_days"] == 3
    assert st["p10_mae"] == pytest.approx(pd.Series([-0.01, -0.02, -0.06]).quantile(0.1))


def test_hit_by_days():
    from nsepattern.engine.backtest import hit_by_days
    tr = pd.DataFrame({"hit": [True, True, True, False], "days_to_target": [3, 8, 25, None]})
    h = hit_by_days(tr)
    assert h["5"] == pytest.approx(0.25)
    assert h["10"] == pytest.approx(0.50)
    assert h["20"] == pytest.approx(0.50)
    assert h["30"] == pytest.approx(0.75)   # equals the normal 30-day hit rate
