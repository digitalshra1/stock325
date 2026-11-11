"""Local server: state save/load endpoints and the cross-site write guard (uses a temp state file)."""
import json
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from nsepattern.crypto import encrypt_json
from nsepattern.server import Handler
from tests.helpers import cfg


@pytest.fixture()
def server(tmp_path):
    c = cfg()
    c["user_state"]["file"] = str(tmp_path / "state.enc.json")
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "index.html").write_text("hi", encoding="utf-8")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(tmp_path / "site")))
    port = httpd.server_address[1]
    Handler.cfg, Handler.port = c, port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", tmp_path
    httpd.shutdown()


def _req(url, method="GET", body=None, headers=None):
    r = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def test_ping_and_empty_state(server):
    base, _ = server
    assert _req(base + "/api/ping") == (200, {"mode": "local", "has_state": False})
    assert _req(base + "/api/state")[0] == 404


def test_put_get_state(server):
    base, tmp = server
    env = encrypt_json({"watchlist": [{"symbol": "ABC"}]}, "pw")
    code, _ = _req(base + "/api/state", "PUT", json.dumps(env).encode(), {"Content-Type": "application/json"})
    assert code == 200
    assert json.loads((tmp / "state.enc.json").read_text()) == env
    assert _req(base + "/api/state") == (200, env)


def test_rejects_plain_json_and_other_sites(server):
    base, _ = server
    assert _req(base + "/api/state", "PUT", b'{"watchlist": []}')[0] == 400  # must be encrypted
    env = json.dumps(encrypt_json({}, "pw")).encode()
    assert _req(base + "/api/state", "PUT", env, {"Origin": "https://evil.example"})[0] == 403
