"""Alert sequences (prompt section 12): target, every +1 % step, −2 % then every −1 %, re-arm, no duplicates."""
from nsepattern import alerts as al
from tests.helpers import cfg

C = cfg()


def trade(entry=100.0, **kw):
    return dict({"id": "t1", "no": 1, "symbol": "TCS", "entry": entry, "qty": 10, "status": "open",
                 "target": round(entry * 1.0325, 2)}, **kw)


def run(t, high, low, last, fired, peaks, dip=None):
    return al.position_alerts(t, {"high": high, "low": low, "last": last}, fired, peaks, C, dip)


def test_target_then_every_one_percent_step_once():
    t, f, p = trade(), set(), {}
    assert run(t, 102, 99.5, 101, f, p) == []
    m = run(t, 103.3, 101, 103, f, p)
    assert len(m) == 1 and "🎯" in m[0] and "+3.25%" in m[0] and "Start watching to sell" in m[0]
    assert run(t, 103.9, 102.5, 103.5, f, p) == []                       # no duplicate
    m = run(t, 104.3, 103, 104.2, f, p)
    assert len(m) == 1 and "📈" in m[0] and "+4.25%" in m[0] and "peak ₹104.30" in m[0]
    m = run(t, 105.3, 104, 105, f, p)
    assert "+5.25%" in m[0]
    assert run(t, 105.2, 104, 105, f, p) == []


def test_jump_over_several_steps_sends_one_message_for_the_highest():
    t, f, p = trade(), set(), {}
    m = run(t, 106.4, 100, 106, f, p)
    assert len(m) == 1 and "+6.25%" in m[0] and "Target hit" in m[0]
    assert {"U:t1:3.25", "U:t1:4.25", "U:t1:5.25", "U:t1:6.25"} <= f


def test_downside_warning_steps_and_context():
    t, f, p = trade(), set(), {}
    m = run(t, 100, 97.9, 98, f, p, dip=-3.5)
    assert len(m) == 1 and "−2% below your buy" in m[0] and "Not following the pattern" in m[0]
    assert "usual dip after a buy is up to −3.5%" in m[0] and "so this is normal" in m[0]
    assert run(t, 98.5, 97.5, 97.8, f, p, dip=-3.5) == []                 # no duplicate at −2 %
    m = run(t, 98, 96.9, 97, f, p, dip=-3.5)
    assert "−3% below" in m[0] and "normal" in m[0]
    m = run(t, 97, 95.9, 96, f, p, dip=-3.5)
    assert "−4% below" in m[0] and "unusual" in m[0]


def test_rearm_after_recovery_above_minus_two():
    t, f, p = trade(), set(), {}
    run(t, 100, 97.9, 98, f, p)                       # −2 % warning
    run(t, 98, 96.9, 97, f, p)                        # −3 %
    assert run(t, 99, 97, 98.5, f, p) == []           # recovers above −2 % → re-armed (no message)
    m = run(t, 98.5, 97.95, 98.1, f, p)
    assert len(m) == 1 and "−2% below" in m[0]        # sequence starts again from −2 %


def test_own_target_alert_and_closed_trade_ignored():
    t, f, p = trade(target=110.0), set(), {}
    m = run(t, 110.2, 105, 110, f, p)
    assert any("your own target" in x for x in m)
    assert run(trade(status="closed"), 120, 80, 100, set(), {}) == []


def test_next_levels():
    t, f = trade(), set()
    assert al.next_levels(t, f, C) == (3.25, -2.0)
    run(t, 104.3, 97.9, 104, f, {})
    nu, nd = al.next_levels(t, f, C)
    assert nu == 5.25 and nd == -2.0                 # −2 % fired but re-armed because last price recovered


def _cand(sym="ABC", best=95.0, your=96.0, status="READY"):
    return {"symbol": sym, "status": {"status": status, "best": {"buy_price": best}, "your": {"buy_price": your}}}


def test_zone_touched_once_per_day_and_mood_rules():
    sent = set()
    bars = {"ABC": {"high": 99, "low": 95.8, "last": 97}}
    m = al.zone_alerts([_cand()], bars, sent, "NORMAL")
    assert len(m) == 1 and "touched your buy level" in m[0]
    assert al.zone_alerts([_cand()], bars, sent, "NORMAL") == []        # once per day
    assert "Caution" in al.zone_alerts([_cand("XYZ")], {"XYZ": bars["ABC"]}, set(), "CAUTION")[0]
    assert al.zone_alerts([_cand()], bars, set(), "STRESSED") == []      # paused in stressed markets
    assert al.zone_alerts([_cand()], {"ABC": {"high": 99, "low": 96.5, "last": 97}}, set(), "NORMAL") == []


def _res(mood="NORMAL", ready=("AAA",)):
    def stock(s, st):
        return {"symbol": s, "industry": "IT", "grade": {"grade": "GOOD"}, "excluded": [],
                "status": {"status": st, "days_in_streak": 3, "depth_low_pct": -5.0,
                           "best": {"buy_price": 95.0, "gap_pct": 0.3}, "your": {"buy_price": 96.0}},
                "profile": {"usual_days": 3, "usual_depth_pct": -5.0},
                "backtest": {"best": {"hit_rate": 0.8, "confidence": "High",
                                      "by_mood": {"NORMAL": {"hit_rate": 0.8, "trades": 10}, "STRESSED": {"hit_rate": 0.4, "trades": 5}}},
                             "yours": {"hit_rate": 0.7}},
                "rank": {"score": 70}, "check_summary": {"line": "8/10 checks passed"}}
    stocks = {s: stock(s, "READY") for s in ready}
    stocks["CCC"] = stock("CCC", "GETTING CLOSE")
    return {"data_date": "2026-11-11", "mood": {"mood": mood, "reasons": []}, "stocks": stocks}


def test_evening_new_ready_mood_change_and_summary():
    bs = {"prev_ready": [], "last_mood": "CAUTION", "log": []}
    m = al.evening_messages(_res(), bs, C, None, {})
    assert any("Market mood changed" in x and "CAUTION → NORMAL" in x for x in m)
    assert any("New READY stocks (1)" in x and "AAA" in x for x in m)
    assert any("Evening summary" in x for x in m)
    m2 = al.evening_messages(_res(), bs, C, None, {})              # same READY again → not "new"
    assert not any("New READY" in x for x in m2)


def test_evening_stressed_pauses_buy_alerts():
    bs = {"prev_ready": [], "last_mood": "STRESSED", "log": []}
    m = al.evening_messages(_res("STRESSED", ("AAA", "BBB")), bs, C, None, {})
    paused = [x for x in m if "buy signals paused" in x]
    assert paused and "40% hit rate in stressed markets vs 80% normally" in paused[0]
    assert not any("New READY stocks" in x for x in m)


def test_inr_grouping():
    assert al.inr(1234567.891) == "₹12,34,567.89"
    assert al.inr(-950.5, 0) == "-₹950"
