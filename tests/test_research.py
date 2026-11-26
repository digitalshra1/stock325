import numpy as np
import pandas as pd

from nsepattern import livelog
from nsepattern.engine import research
from tests.helpers import cfg

C = cfg()


def _df(closes, vol=None):
    idx = pd.bdate_range("2026-01-01", periods=len(closes))
    c = np.array(closes, float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.002, "Low": np.minimum(o, c) * 0.998,
                         "Close": c, "Volume": vol if vol is not None else np.full(len(c), 1e5)}, index=idx)


def test_history_rows_and_today_flags():
    closes = [100.0] * 80 + [98, 96, 94, 93] + [94, 95, 97, 98, 99] + [99.5] * 40
    df = _df(closes)
    a = {"backtest": {"best": {"level_pct": -5.0}, "yours": {"level_pct": -6.5}},
         "status": {"best": {"buy_price": 95.0, "zone_touched_today": False}}}
    out = research.stock_research(df, a, C, None)
    assert len(out["hist"]) == 1
    r = out["hist"][0]
    assert r["hit30"] and r["known30"] and "range" in r["fl"] and "shallow" not in r["fl"]
    assert out["today"]["n"] == len(out["today"]["flags"])


def test_summary():
    rows = [{"flags": 0, "fl": [], "hit30": True, "known30": True, "res30": 0.0325},
            {"flags": 3, "fl": ["vol", "gap", "range"], "hit30": False, "known30": True, "res30": -0.1}]
    s = research.summarise(rows, 3.25)
    assert s["by_count"][0]["loss_rate"] == 0 and s["by_count"][3]["loss_rate"] == 100
    assert s["exits"] == []


def test_exit_rules():
    h = np.array([0.0, 0.01, 0.04]); lo = np.array([-0.05, -0.12, 0.0]); c = np.array([-0.03, -0.1, 0.03])
    assert research._exit(h, lo, c, 0.0325, None, None) == (0.0325, 3)       # hold: hit on day 3
    assert research._exit(h, lo, c, 0.0325, 0.10, None) == (-0.10, 2)        # stop -10% hit first on day 2
    assert research._exit(h, lo, c, 0.0325, None, 2) == (-0.1, 2)            # time exit after 2 days at close


def test_live_journal_records_once_per_fall_and_measures_outcome(tmp_path):
    closes = [100.0] * 80 + [98, 96, 94, 93] + [94, 95, 97, 98, 99]
    df = _df(closes)
    d1, d2 = df.index[83], df.index[84]                       # two READY days in the same fall
    st = {"status": "READY", "p0_date": str(df.index[79].date()), "p0": 100.0,
          "best": {"buy_price": 95.0, "zone_touched_today": True}}
    a = {"status": st, "backtest": {"best": {"level_pct": -5.0}, "yours": {"level_pct": -8.0}},
         "grade": {"grade": "GOOD"}, "industry": "IT", "excluded": []}
    path = tmp_path / "log.enc.json"
    c2 = dict(C, research={"live_since": str(d1.date())})
    for d in (d1, d2):
        a["last_date"] = str(d.date())
        livelog.update({"AAA": df[df.index <= d]}, {"AAA": a}, c2, None, None,
                       pd.DatetimeIndex([x for x in df.index if x <= d and x >= d1]), "pw", path=path)
    v = livelog.view(livelog._load(path, "pw", livelog.empty_log("x")), {"AAA": df}, c2)
    assert len(v["rows"]) == 1 and v["rows"][0]["ready_count"] == 2     # once per fall, READY on 2 days
    b = v["rows"][0]["best"]
    assert b["bought"] and b["hit_day"] is not None                    # touched 95, then +3.25%
    assert v["rows"][0]["your"]["bought"] is False                     # 92 never reached (lowest low about 92.8)


def test_live_journal_needs_password(tmp_path):
    assert livelog.update({}, {}, C, None, None, pd.DatetimeIndex([]), None, path=tmp_path / "x") is None


def test_one_entry_per_fall_even_after_a_break():
    lg = livelog.empty_log("2026-01-01")
    e = lambda d, p0d="2026-01-01": {"symbol": "AAA", "p0_date": p0d, "p0": 100.0, "first_ready": d, "last_ready": d, "ready_count": 1}
    livelog._record(lg, "2026-01-05", {"AAA": e("2026-01-05")})
    livelog._record(lg, "2026-01-06", {"AAA": e("2026-01-06")})     # still READY
    livelog._record(lg, "2026-01-06", {"AAA": e("2026-01-06")})     # same day scanned twice: no change
    livelog._record(lg, "2026-01-07", {})                            # out of READY
    livelog._record(lg, "2026-01-08", {"AAA": e("2026-01-08")})     # back in the same fall: same trade
    livelog._record(lg, "2026-02-02", {"AAA": e("2026-02-02", "2026-01-28")})   # a new fall: new entry
    ents = sorted(lg["entries"].values(), key=lambda x: x["first_ready"])
    assert [(x["first_ready"], x["ready_count"]) for x in ents] == [("2026-01-05", 3), ("2026-02-02", 1)]


def test_stop_loss_test_uses_lowest_point_before_target():
    closes = [100.0] * 80 + [98, 96, 94, 93] + [94, 95, 97, 98, 99]
    df = _df(closes)
    e = {"symbol": "AAA", "p0_date": str(df.index[79].date()), "p0": 100.0, "first_ready": str(df.index[82].date())}
    o = livelog._outcome(df, e, -5.0, 0.0325, C)
    assert o["bought"] and o["hit_day"] is not None
    # bought at 95 on the 94 day (opened 96), lowest next day 93*0.998 = 92.81 -> about -2.3% below the buy
    assert -2.5 < o["worst_pct"] < -2.0
    assert o["stops"]["2"]["r"] == "stop" and o["stops"]["3"]["r"] == "hit" and o["stops"]["5"]["r"] == "hit"
    assert o["wait_days"] == 0 and o["low_date"] == str(df.index[83].date()) and o["best_pct"] >= 3.25
