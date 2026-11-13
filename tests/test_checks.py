from datetime import date

import pandas as pd

from nsepattern.data.fundamentals import grade
from nsepattern.data.news import keyword_regex, match_keywords, short_name
from nsepattern.engine import checks as ck
from tests.helpers import cfg

C = cfg()


def test_grade_good_weak_unknown():
    good = {"eps": 10, "profit_margin": 12, "debt_to_equity": 0.4, "revenue_growth": 8, "earnings_growth": 5, "roe": 18}
    assert grade(good, {"pledge": 0.0}, "IT", C)["grade"] == "GOOD"
    assert grade({"eps": -1, "roe": 20, "revenue_growth": 5}, None, "IT", C)["grade"] == "WEAK"
    assert grade({"roe": 20, "revenue_growth": 5}, None, "IT", C)["grade"] == "UNKNOWN"
    g = grade(dict(good, debt_to_equity=3.0), None, "IT", C)
    assert g["grade"] == "WEAK" and "High debt" in g["red_flags"][0]
    # debt rule skipped for banks / financial services
    assert grade(dict(good, debt_to_equity=8.0), None, "Financial Services", C)["grade"] == "GOOD"
    assert grade(good, {"pledge": 25.0}, "IT", C)["grade"] == "WEAK"
    assert grade(good, {"pledge": 5.0, "pledge_chg": 1.0}, "IT", C)["grade"] == "WEAK"
    assert grade(dict(good, roe=3), None, "IT", C)["grade"] == "WEAK"
    assert grade(dict(good, revenue_growth=-15), None, "IT", C)["grade"] == "WEAK"
    assert grade(dict(good, earnings_growth=-40), None, "IT", C)["grade"] == "WEAK"


def test_keywords_whole_words():
    rx = keyword_regex(C["checks"]["negative_keywords"])
    assert match_keywords("SEBI issues show-cause notice", rx) == ["sebi"]
    assert match_keywords("ED raids promoter premises", rx) == ["ed"]
    assert match_keywords("Company reported record profit", rx) == []  # 'reported' must not match 'ed'
    assert match_keywords("Faces GST  notice of Rs 20 cr", rx) == ["gst  notice"]
    assert match_keywords("Fined by regulator", rx) == []  # whole word only: 'fine' != 'fined'


def test_short_name():
    assert short_name("Infosys Limited") == "Infosys"
    assert short_name("Tata Steel Ltd.") == "Tata Steel"


def test_why_falling():
    assert ck.check_why_falling(-5, -4, -3, C)["result"] == "PASS"
    assert ck.check_why_falling(-9, -4, -3, C)["result"] == "WARN"
    assert ck.check_why_falling(-15, -2, -1, C)["result"] == "FAIL"
    assert ck.check_why_falling(-5, None, None, C)["result"] == "NA"


def test_delivery_volume_deals_pledge():
    assert ck.check_delivery(40, 45, C)["result"] == "PASS"
    assert ck.check_delivery(70, 45, C)["result"] == "WARN"
    assert ck.check_delivery(None, 45, C)["result"] == "NA"
    assert ck.check_volume(3e6, 1e6, C)["result"] == "WARN"
    assert ck.check_volume(1.5e6, 1e6, C)["result"] == "PASS"
    empty = pd.DataFrame(columns=["side", "qty"])
    assert ck.check_deals(empty, 1e8, C)["result"] == "PASS"
    big = pd.DataFrame({"side": ["SELL"], "qty": [1e6]})
    assert ck.check_deals(big, 1e8, C)["result"] == "WARN"      # 1% of shares sold
    assert ck.check_deals(big, 1e10, C)["result"] == "PASS"     # 0.01%
    assert ck.check_deals(None, 1e8, C)["result"] == "NA"
    assert ck.check_pledge({"pledge": 0.0}, C)["result"] == "PASS"
    assert ck.check_pledge({"pledge": 5.0, "pledge_chg": 2.0}, C)["result"] == "WARN"
    assert ck.check_pledge({"pledge": 30.0}, C)["result"] == "FAIL"


def test_support_and_results_and_surveillance():
    assert ck.check_support(100, [("2026-01-01", 101.5)], 80, C)["result"] == "PASS"
    assert ck.check_support(100, [("2026-01-01", 110)], 98, C)["result"] == "PASS"   # near 52w low
    assert ck.check_support(100, [("2026-01-01", 110)], 80, C)["result"] == "WARN"
    t = date(2026, 11, 10)
    assert ck.check_results([date(2026, 11, 13)], [], C, today=t)["result"] == "WARN"
    assert ck.check_results([date(2026, 12, 13)], [], C, today=t)["result"] == "PASS"
    ann = [{"date": "09-Nov-2026", "title": "Financial Result Updates", "text": ""}]
    assert ck.check_results([], ann, C, today=t)["result"] == "WARN"
    assert ck.check_results(None, None, C, today=t)["result"] == "NA"
    assert ck.check_surveillance("ABC", {"ABC": "ST-ASM Stage I"}, {}, C)["result"] == "FAIL"
    assert ck.check_surveillance("ABC", {}, {"ABC": "5"}, C)["result"] == "FAIL"
    assert ck.check_surveillance("ABC", {}, {"ABC": "20"}, C)["result"] == "PASS"


def test_summary_and_rank():
    cs = [{"result": "PASS"}] * 7 + [{"result": "WARN"}, {"result": "NA"}, {"result": "FAIL"}]
    s = ck.summarise(cs)
    assert s["line"] == "7/10 checks passed (1 N/A)"
    perfect = ck.ranking_score({"hit_rate": 1.0, "trades": 30, "median_days": 1, "p10_mae": 0.0}, 100, "Uptrend", 20, C)
    assert perfect["score"] == 100.0
    weak = ck.ranking_score({"hit_rate": 0.0, "trades": 0, "median_days": None, "p10_mae": -0.2}, 0, "Downtrend", -30, C)
    assert weak["score"] == 0.0


def test_fundamentals_never_downgraded_and_capped(tmp_path, monkeypatch):
    import json
    from datetime import date, timedelta
    from nsepattern.data import fundamentals as F
    monkeypatch.setattr(F, "FUND_DIR", tmp_path)
    old_day = (date.today() - timedelta(days=60)).isoformat()
    good = {"symbol": "AAA", "fetched": old_day, "eps": 5, "roe": 12, "profit_margin": 9, "revenue_growth": 4}
    (tmp_path / "AAA.json").write_text(json.dumps(good))
    calls = []

    def poor(sym):                      # what Yahoo gives cloud servers: almost nothing
        calls.append(sym)
        return {"symbol": sym, "fetched": date.today().isoformat(), "eps": None}
    monkeypatch.setattr(F, "fetch_one", poor)
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    out = F.get_fundamentals(["AAA", "BBB", "CCC"], max_age_days=30, max_refresh=2)
    assert out["AAA"]["roe"] == 12                     # good saved data kept
    assert "BBB" not in out and not (tmp_path / "BBB.json").exists()   # failed new download not saved
    assert calls == ["AAA", "BBB"]                     # at most 2 refreshes this run
    calls.clear()
    F.get_fundamentals(["AAA"], max_age_days=30)
    assert calls == []                                 # AAA was just tried: not retried for a week


def test_shareholding_keeps_saved_copy_when_nse_blocks(tmp_path, monkeypatch):
    import json
    from datetime import date, timedelta
    from nsepattern.data import nse
    monkeypatch.setattr(nse, "SHP_DIR", tmp_path)
    saved = {"promoter": 55.0, "pledge": 0.0, "fii": 20.0, "dii": 15.0,
             "fetched": (date.today() - timedelta(days=90)).isoformat()}
    (tmp_path / "ABC.json").write_text(json.dumps(saved))
    monkeypatch.setattr(nse, "_get_json", lambda *a, **k: None)      # NSE refuses (cloud)
    assert nse.shareholding_age("ABC") == 90
    out = nse.shareholding(None, "ABC", max_age_days=30)            # stale -> tries, fails
    assert out["promoter"] == 55.0 and out["fii"] == 20.0           # last good copy returned
    assert json.loads((tmp_path / "ABC.json").read_text())["fetched"] == saved["fetched"]  # not overwritten
    assert nse.shareholding_age("NEW") >= 1e9
