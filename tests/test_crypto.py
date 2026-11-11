"""Python <-> browser encryption compatibility (uses Node's WebCrypto, same API as browsers)."""
import json
import shutil
import subprocess

import pytest

from nsepattern.crypto import decrypt_json, encrypt_json
from nsepattern.dashboard.build import TEMPLATE

OBJ = {"hello": "₹ नमस्ते", "n": [1, 2.5, None], "nested": {"ok": True}}
NODE = shutil.which("node")


def test_python_roundtrip():
    env = encrypt_json(OBJ, "s3cret pass")
    assert decrypt_json(env, "s3cret pass") == OBJ
    with pytest.raises(Exception):
        decrypt_json(env, "wrong")


def _template_crypto() -> str:
    """The exact crypto code the dashboard uses (between the CRYPTO-START / CRYPTO-END markers)."""
    src = TEMPLATE.read_text(encoding="utf-8")
    return src.split("/* CRYPTO-START */")[1].split("/* CRYPTO-END */")[0]


def _node(js: str) -> list[str]:
    r = subprocess.run([NODE, "-e", _template_crypto() + js], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_python_encrypt_js_decrypt():
    env = encrypt_json(OBJ, "s3cret pass")
    lines = _node(f"""
const env = {json.dumps(env)};
decrypt(env, "s3cret pass").then(o => {{ console.log(JSON.stringify(o));
  return decrypt(env, "wrong").then(() => console.log("WRONG-OK"), () => console.log("WRONG-REJECTED")); }})
  .catch(e => {{ console.log("ERR " + e); process.exit(1); }});
""")
    assert json.loads(lines[0]) == OBJ
    assert lines[1] == "WRONG-REJECTED"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_js_encrypt_python_decrypt():
    """The dashboard saves your watchlist/trades encrypted in the browser; Python must read them."""
    lines = _node(f"""
encrypt({json.dumps(OBJ)}, "s3cret pass").then(env => console.log(JSON.stringify(env)))
  .catch(e => {{ console.log("ERR " + e); process.exit(1); }});
""")
    env = json.loads(lines[0])
    assert env["iter"] >= 200_000
    assert decrypt_json(env, "s3cret pass") == OBJ


def test_compressed_roundtrip_python():
    big = {"rows": [{"sym": f"S{i}", "v": i * 1.5} for i in range(2000)]}
    env = encrypt_json(big, "pw", compress=True)
    assert env["z"] == "gzip"
    assert len(env["ct"]) < len(json.dumps(big)) / 3      # really smaller
    assert decrypt_json(env, "pw") == big


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_python_compressed_encrypt_js_decrypt():
    env = encrypt_json(OBJ, "s3cret pass", compress=True)
    lines = _node(f"""
decrypt({json.dumps(env)}, "s3cret pass").then(o => console.log(JSON.stringify(o)))
  .catch(e => {{ console.log("ERR " + e); process.exit(1); }});
""")
    assert json.loads(lines[0]) == OBJ
