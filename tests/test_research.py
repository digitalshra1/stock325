import numpy as np
import pandas as pd

from nsepattern.engine import research
from tests.helpers import cfg

C = cfg()


def _df(closes, vol=None):
    idx = pd.bdate_range("2026-01-01", periods=len(closes))
    c = np.array(closes, float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.002, "Low": np.minimum(o, c) * 0.998,
                         "Close": c, "Volume": vol if vol is not None else np.full(len(c), 1e5)}, index=idx)


def test_recent_signal_hit_and_flags():
    closes = [100.0] * 80 + [98, 96, 94, 93] + [94, 95, 97, 98, 99] + [99.5] * 5
    df = _df(closes)
    a = {"backtest": {"best": {"level_pct": -5.0}, "yours": {"level_pct": -6.5}}, "status": {}}
    out = research.stock_research(df, a, C, None, None, recent_days=60)
    assert len(out["recent"]) == 1
    sig = out["recent"][0]
    b = sig["best"]
    assert b["fill"] <= 95.0 and b["hit_day"] is not None          # bought near 95, then +3.25% reached
    assert b["worst_pct"] <= 0
    assert "range" in b["flags"]                                    # flat stock: usual range under 3%
    assert "shallow" not in b["flags"]                              # -5% is not shallower than -5%
    assert sig["your"]["fill"] <= 93.5                              # your method level is deeper
    assert sig["ready_date"] is not None


def test_open_trade_and_summary():
    closes = [100.0] * 80 + [98, 96, 94, 93, 92, 92.5, 92.2, 92.4]
    df = _df(closes)
    a = {"backtest": {"best": {"level_pct": -4.0}, "yours": None}, "status": {}}
    out = research.stock_research(df, a, C, None, None, recent_days=60)
    b = out["recent"][0]["best"]
    assert b["hit_day"] is None and b["now_pct"] < 0 and "shallow" in b["flags"]
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
