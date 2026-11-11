"""Down-streak detection (prompt section 7.1).

Rules
-----
* A down day is a close strictly below the previous close.
* A streak is consecutive down days. At most ``max_pauses`` (default one) green
  day smaller than ``pause_max_green_pct`` inside a streak does not break it and is
  not counted as a down day. A flat close (0 %) counts as such a small green day.
  A bigger green day, or one small green day too many, ends the streak.
* A pause only counts as "inside" the streak if another down day follows it. If
  the streak ends right after a pause, the streak ends on the last down day.
* P0 = close of the day before the first down day.
* depth_close = lowest close in the streak / P0 - 1
  depth_low   = lowest low  in the streak / P0 - 1
  (the streak spans first down day .. last down day, including any confirmed pause)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass
class Streak:
    p0_idx: int          # row index of the P0 day (day before first down day)
    first_idx: int       # row index of the first down day
    end_idx: int         # row index of the last down day
    down_days: int       # number of down days (pause days not counted)
    pauses: int          # number of confirmed pause days inside the streak
    p0: float            # P0 close
    depth_close: float   # fraction, e.g. -0.05
    depth_low: float     # fraction
    low_idx: int         # row index of the lowest low in the streak
    ongoing: bool        # True if the streak is still running on the last row
    p0_date: pd.Timestamp | None = None
    end_date: pd.Timestamp | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def find_streaks(df: pd.DataFrame, pause_max_green_pct: float = 0.30, max_pauses: int = 1,
                 min_down_days: int = 2) -> list[Streak]:
    """Find all down streaks in an OHLC frame (columns Open, High, Low, Close; date index)."""
    close = df["Close"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    n = len(close)
    pause_lim = pause_max_green_pct / 100.0
    out: list[Streak] = []

    in_streak = False
    p0_idx = first_idx = end_idx = 0
    down_days = pauses = 0
    pending_pause = False  # a small green day seen after the last down day, not yet confirmed

    def close_streak(ongoing: bool):
        if down_days < min_down_days:
            return
        seg_c = close[first_idx:end_idx + 1]
        seg_l = low[first_idx:end_idx + 1]
        p0 = close[p0_idx]
        li = int(np.nanargmin(seg_l)) + first_idx
        out.append(Streak(
            p0_idx=p0_idx, first_idx=first_idx, end_idx=end_idx, down_days=down_days,
            pauses=pauses, p0=float(p0),
            depth_close=float(np.nanmin(seg_c) / p0 - 1),
            depth_low=float(np.nanmin(seg_l) / p0 - 1),
            low_idx=li, ongoing=ongoing,
            p0_date=df.index[p0_idx], end_date=df.index[end_idx],
        ))

    for i in range(1, n):
        if not (np.isfinite(close[i]) and np.isfinite(close[i - 1])) or close[i - 1] <= 0:
            if in_streak:
                close_streak(False)
            in_streak, pending_pause = False, False
            continue
        ret = close[i] / close[i - 1] - 1
        if ret < 0:  # down day
            if not in_streak:
                in_streak = True
                p0_idx, first_idx, end_idx = i - 1, i, i
                down_days, pauses = 1, 0
            else:
                if pending_pause:
                    pauses += 1
                    pending_pause = False
                down_days += 1
                end_idx = i
        else:  # green or flat day
            if not in_streak:
                continue
            if ret < pause_lim and not pending_pause and pauses < max_pauses:
                pending_pause = True  # may become a confirmed pause if a down day follows
                continue
            close_streak(False)
            in_streak, pending_pause = False, False

    if in_streak:
        close_streak(True)
    return out


def streaks_frame(streaks: list[Streak]) -> pd.DataFrame:
    if not streaks:
        return pd.DataFrame(columns=list(Streak.__dataclass_fields__))
    return pd.DataFrame([s.to_dict() for s in streaks])
