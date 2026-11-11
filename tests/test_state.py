import json

from nsepattern.state import check_price_alerts, check_stop_losses, empty_state, load_state, save_state
from tests.helpers import cfg


def _state():
    s = empty_state()
    s["trades"] = [{"id": "t1", "symbol": "ABC", "entry": 100.0, "qty": 10, "target": 103.25, "stop": 95.0,
                    "status": "open"}]
    s["alerts"] = [{"id": "a1", "symbol": "ABC", "kind": "below", "price": 97.0, "active": True},
                   {"id": "a2", "symbol": "XYZ", "kind": "above", "price": 50.0, "active": True}]
    return s


def test_price_alerts_fire_once():
    s = _state()
    msgs = check_price_alerts(s, {"ABC": {"high": 99, "low": 96.5, "last": 96.8}}, "now")
    assert len(msgs) == 1 and "fell below" in msgs[0]
    assert s["alerts"][0]["active"] is False
    assert check_price_alerts(s, {"ABC": {"high": 99, "low": 90, "last": 91}}, "now") == []
    assert len(check_price_alerts(s, {"XYZ": {"high": 51, "low": 49, "last": 50.5}}, "now")) == 1


def test_stop_loss_once_and_rearm():
    s, f = _state(), set()
    assert check_stop_losses(s, {"ABC": {"high": 99, "low": 96, "last": 97}}, f) == []
    m = check_stop_losses(s, {"ABC": {"high": 97, "low": 94.9, "last": 95.5}}, f)
    assert len(m) == 1 and "stop loss" in m[0]
    assert check_stop_losses(s, {"ABC": {"high": 96, "low": 94, "last": 94.5}}, f) == []   # no duplicate
    assert check_stop_losses(s, {"ABC": {"high": 97, "low": 95.5, "last": 96}}, f) == []   # recovered -> re-armed
    assert len(check_stop_losses(s, {"ABC": {"high": 96, "low": 94.8, "last": 95}}, f)) == 1


def test_closed_trade_no_alert():
    s = _state()
    s["trades"][0]["status"] = "closed"
    assert check_stop_losses(s, {"ABC": {"high": 90, "low": 80, "last": 85}}, set()) == []


def test_save_load_roundtrip(tmp_path, monkeypatch):
    c = cfg()
    c["user_state"]["file"] = str(tmp_path / "s.enc.json")
    s = _state()
    save_state(c, s, "pw")
    raw = json.loads((tmp_path / "s.enc.json").read_text())
    assert "ct" in raw and "ABC" not in json.dumps(raw)   # really encrypted
    back = load_state(c, "pw")
    assert back["trades"][0]["symbol"] == "ABC"
    assert back["trades"][0]["no"] == 1          # short number for Telegram


def test_http_circuit_breaker(monkeypatch):
    import requests
    from nsepattern.data import http as h
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise requests.ConnectionError("down")
    s = h.PoliteSession(sleep_sec=0, retries=1)
    monkeypatch.setattr(s.s, "get", boom)
    monkeypatch.setattr(h.time, "sleep", lambda x: None)
    for i in range(h.PoliteSession.BREAK_AFTER):
        assert s.get(f"https://example.com/{i}") is None
    n = len(calls)
    assert s.get("https://example.com/x") is None
    assert len(calls) == n  # paused: no new request was made
