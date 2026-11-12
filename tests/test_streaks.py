import pytest

from nsepattern.engine.streaks import find_streaks
from tests.helpers import frame_from_closes


def test_simple_streak_p0_and_depth():
    df = frame_from_closes([100, 99, 97, 95, 96.5 * 1.0 + 3])  # 3 down days then a big green day
    s = find_streaks(df)
    assert len(s) == 1
    st = s[0]
    assert st.p0 == 100 and st.p0_idx == 0 and st.first_idx == 1 and st.end_idx == 3
    assert st.down_days == 3 and not st.ongoing
    assert st.depth_close == pytest.approx(-0.05)


def test_depth_low_uses_intraday_low():
    closes = [100, 99, 97, 96, 100]
    lows = [100, 98, 96, 94, 96]
    df = frame_from_closes(closes, lows=lows)
    st = find_streaks(df)[0]
    assert st.depth_low == pytest.approx(-0.06)
    assert st.depth_close == pytest.approx(-0.04)
    assert st.low_idx == 3


def test_one_small_green_day_is_a_pause():
    # down, down, +0.2% (pause), down, then big green
    df = frame_from_closes([100, 99, 98, 98.196, 96, 99])
    s = find_streaks(df)
    assert len(s) == 1
    assert s[0].down_days == 3 and s[0].pauses == 1 and s[0].end_idx == 4


def test_flat_day_counts_as_pause():
    df = frame_from_closes([100, 99, 98, 98, 97, 100])
    s = find_streaks(df)
    assert len(s) == 1 and s[0].down_days == 3 and s[0].pauses == 1


def test_big_green_day_breaks_streak():
    # +0.5% green is bigger than 0.30% -> two separate streaks
    df = frame_from_closes([100, 99, 98, 98.49, 97, 96, 100])
    s = find_streaks(df)
    assert len(s) == 2
    assert s[0].down_days == 2 and s[1].down_days == 2
    assert s[1].p0 == 98.49


def test_second_small_green_day_ends_streak():
    df = frame_from_closes([100, 99, 99.1, 98, 98.1, 97, 96, 100])
    s = find_streaks(df)
    # first streak: 99 (down), 99.1 pause, 98 down -> end at idx 3; 98.1 is 2nd small green -> ends
    assert s[0].down_days == 2 and s[0].pauses == 1 and s[0].end_idx == 3
    # 97, 96 form a new streak starting from P0 = 98.1
    assert s[1].p0 == 98.1 and s[1].down_days == 2


def test_two_small_greens_in_a_row_end_streak():
    df = frame_from_closes([100, 99, 98, 98.1, 98.2, 97, 96, 100])
    s = find_streaks(df)
    assert s[0].end_idx == 2 and s[0].pauses == 0 and s[0].down_days == 2


def test_pause_then_end_is_not_counted():
    # pause followed by a big green: streak ends on the last down day, pause not counted
    df = frame_from_closes([100, 99, 98, 98.1, 101])
    s = find_streaks(df)
    assert s[0].end_idx == 2 and s[0].pauses == 0


def test_short_streak_ignored():
    df = frame_from_closes([100, 99, 101, 100, 102])
    assert find_streaks(df) == []
    assert len(find_streaks(df, min_down_days=1)) == 2


def test_ongoing_streak():
    df = frame_from_closes([100, 101, 100, 99, 98])
    s = find_streaks(df)
    assert len(s) == 1 and s[0].ongoing and s[0].p0 == 101


def test_ongoing_with_pending_pause():
    df = frame_from_closes([100, 99, 98, 98.1])
    s = find_streaks(df)
    assert s[0].ongoing and s[0].end_idx == 2 and s[0].pauses == 0


def test_usual_days_from_deeper_half():
    from nsepattern.engine.profile import build_profile
    from nsepattern.engine.streaks import Streak
    from tests.helpers import cfg

    def s(days, depth):
        return Streak(p0_idx=0, first_idx=1, end_idx=days, down_days=days, pauses=0, p0=100.0,
                      depth_close=depth, depth_low=depth, low_idx=days, ongoing=False)
    # many short shallow dips, and fewer long deep falls
    streaks = [s(2, -0.025)] * 6 + [s(3, -0.03)] * 2 + [s(4, -0.06)] * 3 + [s(5, -0.08)] * 2
    c = cfg()
    assert build_profile(streaks, c)["usual_days"] == 4.0          # deeper half
    c["pattern"]["usual_days_from"] = "all"
    assert build_profile(streaks, c)["usual_days"] == 3.0          # all falls (old way)
    assert build_profile(streaks, c)["usual_depth_pct"] == pytest.approx(-3.0)  # depth unchanged


def test_getting_close_rule_and_vs_or():
    from nsepattern.engine.profile import _getting_close
    st = {"close_within_pct": 2.5, "close_days_before_usual": 1, "close_rule": "and"}
    assert _getting_close(2.0, 3, 4, st)            # near and mature
    assert not _getting_close(2.0, 2, 4, st)        # near but too early in the fall
    assert not _getting_close(8.7, 3, 4, st)        # mature but far from the buy price
    st["close_rule"] = "or"
    assert _getting_close(8.7, 3, 4, st) and _getting_close(2.0, 2, 4, st)
