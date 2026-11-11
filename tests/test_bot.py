"""Telegram commands (prompt section 11): parsing, bad input, multiple lots, partial sells, unknown chats."""
from nsepattern.bot import Bot, process_updates
from nsepattern.state import empty_bot_state, empty_state
from tests.helpers import cfg

C = cfg()
KNOWN = {"TCS", "INFY", "RELIANCE", "M&M"}


def bot(prices=None):
    return Bot(C, empty_state(), empty_bot_state(), {}, KNOWN, lambda s: prices or {})


def test_buy_valid_and_case_insensitive():
    b = bot()
    r = b.handle("/buy tcs.ns 3500 10")
    assert "Bought" in r and "#1 TCS" in r and "₹3,613.75" in r
    t = b.us["trades"][0]
    assert t["symbol"] == "TCS" and t["entry"] == 3500 and t["qty"] == 10 and t["status"] == "open"
    assert b.changed and any(w["symbol"] == "TCS" for w in b.us["watchlist"])
    r = b.handle("/buy M&M 2900 5 2026-11-03 sl=2820 tgt=3050")
    assert "#2 M&M" in r and b.us["trades"][1]["stop"] == 2820 and b.us["trades"][1]["target"] == 3050


def test_buy_bad_input():
    b = bot()
    assert "Format" in b.handle("/buy TCS 3500")
    assert "not in the NSE equity list" in b.handle("/buy TCSX 3500 10")
    assert "not a valid price" in b.handle("/buy TCS abc 10")
    assert "not a valid quantity" in b.handle("/buy TCS 3500 2.5")
    assert "must look like" in b.handle("/buy TCS 3500 10 10-11-2026")
    assert "future" in b.handle("/buy TCS 3500 10 2099-01-01")
    assert "sl= must be" in b.handle("/buy TCS 3500 10 sl=3600")
    assert b.us["trades"] == []


def test_sell_full_partial_and_which_lot():
    b = bot()
    b.handle("/buy TCS 3500 10")
    b.handle("/buy TCS 3400 4")
    r = b.handle("/sell TCS 3600")
    assert "Which one?" in r and "#1" in r and "#2" in r          # several lots → asks
    r = b.handle("/sell TCS 3600 4 #1")                               # partial sale of #1
    assert "Sold" in r and "6 shares of #1 still open" in r
    lot1 = next(t for t in b.us["trades"] if t["no"] == 1)
    assert lot1["qty"] == 6 and lot1["status"] == "open"
    part = [t for t in b.us["trades"] if t["status"] == "closed"]
    assert len(part) == 1 and part[0]["qty"] == 4 and part[0]["exit_price"] == 3600
    r = b.handle("/sell TCS 3450 #2")
    assert "#2" in r and next(t for t in b.us["trades"] if t["no"] == 2)["status"] == "closed"
    assert "only 6 shares" in b.handle("/sell TCS 3600 9 #1")
    assert "no open trade" in b.handle("/sell INFY 1500")


def test_remove_edit_positions():
    b = bot({"TCS": 3620.0})
    b.handle("/buy TCS 3500 10")
    r = b.handle("/positions")
    assert "#1" in r and "+3.43%" in r and "next ↑ +3.25%" in r and "next ↓ -2.00%" in r
    assert "Updated #1" in b.handle("/edit 1 3510 12")
    assert b.us["trades"][0]["entry"] == 3510 and b.us["trades"][0]["qty"] == 12
    assert b.us["trades"][0]["target"] == round(3510 * 1.0325, 2)       # default target follows the price
    assert "no trade #9" in b.handle("/remove 9")
    assert "Removed #1" in b.handle("/remove 1") and b.us["trades"] == []


def test_help_unknown_and_no_password():
    b = bot()
    assert "/buy SYMBOL PRICE QTY" in b.handle("/help")
    assert "don't know" in b.handle("/hello")
    assert "/help" in b.handle("hello there")
    nb = Bot(C, None, empty_bot_state(), {}, KNOWN)
    assert "APP_PASSWORD" in nb.handle("/buy TCS 3500 10")


def test_only_my_chat_is_answered():
    b = bot()
    replies = []
    updates = [{"update_id": 10, "message": {"chat": {"id": 111}, "text": "/buy TCS 3500 10"}},
               {"update_id": 11, "message": {"chat": {"id": 999}, "text": "/buy INFY 1500 10"}},
               {"update_id": 12, "message": {"chat": {"id": 111}, "text": "/help"}}]
    nxt = process_updates(b, updates, "111", replies.append)
    assert nxt == 13
    assert len(replies) == 2                                   # stranger got no reply
    assert [t["symbol"] for t in b.us["trades"]] == ["TCS"]   # stranger's command ignored


def test_command_with_bot_suffix():
    assert "commands" in bot().handle("/help@MyDeskBot")


def test_prune_forgets_removed_trades():
    from nsepattern.jobs import prune_bot_state
    us = {"trades": [{"id": "keep"}]}
    bs = {"fired": ["U:keep:3.25", "D:gone:2", "SL:gone"], "peaks": {"keep": 1, "gone": 2}}
    prune_bot_state(bs, us)
    assert bs["fired"] == ["U:keep:3.25"] and bs["peaks"] == {"keep": 1}
