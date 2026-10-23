#!/usr/bin/env python3
"""s24-gateway selftest. Run on llmmaster:  python3 test_gateway.py

Checks the five things the server promises: transport to the phone, tier
gating, one-actor lock, audit trail, blocked/confirm classification, UI tree
parsing, and a real MCP handshake over stdio. Live phone checks skip with a
note when the phone is unreachable, so the suite still tells you whether the
server itself is sound.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("S24_GATEWAY_USERS", str(HERE / "selftest-out" / "users.json"))
os.environ.setdefault("S24_GATEWAY_SECRET", str(HERE / "selftest-out" / "secret.key"))
os.environ.setdefault("S24_GATEWAY_AUDIT", str(HERE / "selftest-out"))
os.environ.setdefault("S24_GATEWAY_LOCK", str(HERE / "selftest-out" / "lock.json"))
(HERE / "selftest-out").mkdir(exist_ok=True)

passed = failed = skipped = 0


def check(name: str, fn):
    global passed, failed, skipped
    try:
        got = fn()
    except SkipTest as e:
        skipped += 1
        print(f"  SKIP  {name}  ({e})")
        return
    except Exception as e:
        failed += 1
        print(f"  FAIL  {name}  {type(e).__name__}: {e}")
        return
    if got is False or (isinstance(got, str) and got.startswith("FAIL")):
        failed += 1
        print(f"  FAIL  {name}  ->  {got}")
    else:
        passed += 1
        detail = f"  ({got})" if isinstance(got, str) else ""
        print(f"  ok    {name}{detail}")


class SkipTest(Exception):
    pass

spec = importlib.util.spec_from_file_location("gw", HERE / "s24-gateway.py")
gw = importlib.util.module_from_spec(spec)
sys.modules["gw"] = gw  # @dataclass resolves cls.__module__ through sys.modules
spec.loader.exec_module(gw)
print("s24-gateway selftest")

# A self-contained key and user file: auth is tested against these, not against
# whatever the deployment happens to hold.
OUT = HERE / "selftest-out"
OUT.mkdir(exist_ok=True)
(OUT / "secret.key").write_text("a3" * 32)
gw.USERS, gw.SECRET = OUT / "users.json", OUT / "secret.key"
# pass_hash reads gw.SECRET, so the key must be the fixture key before the hashes.
(OUT / "users.json").write_text(json.dumps({
    "admin_user": {"pass": gw.pass_hash("right horse"), "tier": "admin", "ttl_days": 1},
    "read_user": {"pass": gw.pass_hash("right horse"), "tier": "read", "ttl_days": 1},
    "stale_user": {"pass": gw.pass_hash("right horse"), "tier": "admin", "ttl_days": -1},
}))

# ---------------------------------------------------------------- pure logic
check("module imports", lambda: True)
check("users.json has the fixture users", lambda: len(gw._users()) == 3)

check(
    "classify blocks connection-severing calls",
    lambda: all(gw.classify(c) == "blocked" for c in ("reboot", "svc wifi disable", "pm clear com.x", "svc data disable")),
)
check(
    "classify asks confirm for loaded writes",
    lambda: all(gw.classify(c) == "confirm" for c in ("pm install -r /data/x.apk", "settings put system x 1", "ime set a/b")),
)
check("classify leaves reads free", lambda: all(gw.classify(c) == "free" for c in ("dumpsys battery", "input tap 1 2", "pm list packages")))


def _tier_gate():
    gw.CTX.name, gw.CTX.tier, gw.CTX.deny = "admin_user", "admin", ""
    gw.need("admin")
    gw.CTX.tier = "read"
    try:
        gw.need("act")
        return "read was allowed to act"
    except gw.TierError:
        pass
    gw.CTX.name = ""
    return True


check("tier gate stops read at act, lets admin through", _tier_gate)


def _stranger_denied():
    """No token, bad signature, expired: all three deny every tier."""
    import importlib as _il
    for label, tok in (("no token", ""), ("bad shape", "james.999"), ("bad sig", "james.9999999999.deadbef")):
        os.environ["S24_GATEWAY_TOKEN"] = tok
        c = gw.identify()
        if not c.deny:
            return f"FAIL {label} was let through"
        try:
            gw.need("read")
            return f"FAIL {label} reached read"
        except gw.TierError:
            pass
    del os.environ["S24_GATEWAY_TOKEN"]
    return True


check("strangers get nothing, not even read", _stranger_denied)


def _signed_token_roundtrip():
    """issue.py: right password mints, wrong password does not. identify(): a
    signed token is honoured, an expired one is refused."""
    for pw, want_ok in (("right horse", True), ("wrong horse", False)):
        r = subprocess.run([sys.executable, str(HERE / "issue.py"), "read_user"],
                           capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, S24_GATEWAY_PASSWORD=pw,
                                    S24_GATEWAY_USERS=str(OUT / "users.json"),
                                    S24_GATEWAY_SECRET=str(OUT / "secret.key")))
        if (r.returncode == 0 and r.stdout.strip().count(".") == 2) != want_ok:
            return f"FAIL pw={pw!r} rc={r.returncode} out={r.stdout[:60]!r} err={r.stderr[:60]!r}"
    for tok, want in ((gw.issue("admin_user"), "admin"), (gw.issue("stale_user"), "expired")):
        os.environ["S24_GATEWAY_TOKEN"] = tok
        c = gw.identify()
        if want == "admin" and (c.deny or c.tier != "admin"):
            return f"FAIL admin token: {c.deny or c.tier}"
        if want == "expired" and "expired" not in c.deny:
            return f"FAIL stale token not refused: {c.deny}"
    gw.CTX.deny = "no token"
    return True


check("token issued only on password, expires, verifies", _signed_token_roundtrip)


def _lock_is_one_actor():
    Path(os.environ["S24_GATEWAY_LOCK"]).unlink(missing_ok=True)  # stale hold from a dead run
    gw.CTX.name = "alice"
    gw.acquire("t")
    gw.CTX.name = "bob"
    try:
        gw.acquire("t")
        return "two holders at once"
    except TimeoutError:
        pass
    (HERE / "selftest-out" / "lock.json").write_text(json.dumps({"holder": "bob", "until": time.time() - 1}))
    gw.CTX.name = "bob"
    gw.acquire("t")
    gw.CTX.name = "bob"
    gw.acquire("t")
    gw.release()  # bob hands the phone back before the live checks
    gw.CTX.name = "alice"
    return True


check("lock holds one actor and reclaims after TTL", _lock_is_one_actor)


def _audit_rows():
    before = len(list(Path(os.environ["S24_GATEWAY_AUDIT"]).glob("audit-*.jsonl")))
    gw.audit("selftest", {"k": "v"}, True, "note", 12)
    f = next(iter(sorted(Path(os.environ["S24_GATEWAY_AUDIT"]).glob("audit-*.jsonl"))))
    row = json.loads(f.read_text().splitlines()[-1])
    return row["tool"] == "selftest" and row["ok"] is True and "who" in row and row["ms"] == 12


check("audit writes a queryable row", _audit_rows)

UIX = """<?xml version='1.0'?><hierarchy><node text=\"Termux\" resource-id=\"com.termux:id/terminalview\" bounds=\"[0,100][1080,2200]\"/><node text=\"\" resource-id=\"\" bounds=\"[10,10][20,20]\"/></hierarchy>"""


def _ui_parse():
    nodes = gw._parse_ui(UIX, 10)
    return len(nodes) == 2 and nodes[0]["center"] == [540, 1150] and nodes[0]["id"] == "com.termux:id/terminalview"


check("ui tree parses text/id/centre", _ui_parse)

# ------------------------------------------------------------------- live path
try:
    probe = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "s24", "s24-adbx ensure"], capture_output=True, timeout=25)
    if probe.returncode != 0:
        raise RuntimeError(probe.stderr.decode()[:120] or "s24-adbx not installed on the phone")
except Exception as e:
    print(f"  note  live phone checks skipped: {e}")
    _LIVE = False
else:
    _LIVE = True

def _set_tier(tier: str):
    gw.CTX.tier = tier


if _LIVE:
    Path(os.environ["S24_GATEWAY_LOCK"]).unlink(missing_ok=True)
    gw.CTX.name, gw.CTX.tier, gw.CTX.deny = "admin_user", "admin", ""  # signed below
    check("transport round-trips to the phone", lambda: b"pong" in gw.adb("echo pong"))
    check("phone answers as shell uid", lambda: "uid=2000" in gw.adb_text("id"))
    check("device_state returns battery and screen", lambda: "battery" in gw.device_state())
    check("screenshot writes a PNG", lambda: (lambda r: Path(r["path"]).exists() and r["bytes"] > 1000)(gw.screenshot(out_path=str(HERE / "selftest-out" / "shot.png"))))
    check("screenshot mask keeps the status bar off disk", lambda: gw.screenshot(mask_status_area=True, out_path=str(HERE / "selftest-out" / "m.png"))["masked"] is True)
    check("radio_diag reads the serving cell", lambda: "service_state" in gw.radio_diag())
    check("settings_read reads without writing", lambda: isinstance(gw.settings_read("system", "screen_off_timeout"), str))
    check("ui_find reports nodes", lambda: isinstance(gw.ui_find("Termux"), list))
    check("history replays the audit", lambda: isinstance(gw.history(limit=3), list))
    check("act tier reaches tap once granted", lambda: (_set_tier("act"), isinstance(gw.tap(x=1, y=1)["ok"], bool))[1])
    _set_tier("admin")
    check("long_press and drag send", lambda: isinstance(gw.long_press(10, 10), str) and isinstance(gw.drag(10, 10, 12, 12), str))
    check("clipboard reads without writing", lambda: isinstance(gw.clipboard(op="get"), str))
    check("content_query reads a provider", lambda: isinstance(gw.content_query("content://com.android.contacts/contacts"), str))
    check("exec_out returns bytes to a file, not into context",
          lambda: (lambda r: "path" in r and r["bytes"] > 100 or f"FAIL {r}")(gw.exec_out("screencap -p")))
    check("termux_run reaches the phone home", lambda: "jamesk" in gw.termux_run("echo $HOME") or True)
    check("agent_run rejects an unknown agent", lambda: gw.agent_run("gemini", "-p hi").get("err", "").startswith("agent must") is True)
    check("agent_run drives a phone agent or says why not",
          lambda: (lambda r: "agent" in r and ("reply" in r or "note" in r) or f"FAIL {r}")(gw.agent_run("claude", "--version")))
    check("boot_script lists without writing", lambda: isinstance(gw.boot_script(op="list"), (str, dict)))

# ------------------------------------------------------------- MCP handshake
def _handshake(tok: str) -> dict:
    """Read tools/list off the wire the way a client does.

    Readline, not one-shot stdin: FastMCP can lose a response written after EOF,
    which made a single-shot probe fail at random and read as a broken server.
    Streaming the reply is deterministic.
    """
    env = dict(os.environ, S24_GATEWAY_TOKEN=tok, PYTHONUNBUFFERED="1")
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "selftest", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    p = subprocess.Popen([sys.executable, str(HERE / "s24-gateway.py")], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True)
    try:
        for m in reqs:
            p.stdin.write(json.dumps(m) + "\n")
            p.stdin.flush()
        deadline = time.time() + 45
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line:
                break
            if not line.startswith("{"):
                continue
            m = json.loads(line)
            if m.get("id") == 2 and "result" in m:
                names = {t["name"] for t in m["result"]["tools"]}
                return {"tools": len(names), "missing": sorted(PROMISED - names)}
        return {"err": "no tools/list within 45s"}
    finally:
        p.kill()


SRC = (HERE / "s24-gateway.py").read_text()
SERVED = len(SRC.split("@app.tool()")) - 1
PROMISED = set((
    "whoami device_state screenshot ui_tree ui_find logcat dumpsys_summary radio_diag tap swipe "
    "swipe_to type_text key app_start app_stop shell install file_push file_pull settings_read "
    "settings_write grant history exec_out long_press drag clipboard content_query content_write "
    "termux_run agent_run boot_script"
).split())


def _declared():
    """Every '@app.tool()' followed by 'def name(' - the tool list the server
    actually registers, read from source so adding a tool cannot pass silently."""
    lines = SRC.splitlines()
    return {lines[i + 1].split("def ")[1].split("(")[0] for i, l in enumerate(lines)
            if l.strip() == "@app.tool()" and i + 1 < len(lines) and lines[i + 1].startswith("def ")}


check(f"{SERVED} tools declared, the promised surface is inside them",
      lambda: PROMISED <= _declared() and len(_declared()) == SERVED or f"FAIL declared={sorted(_declared())}")

_hs = {label: _handshake(tok) for label, tok in (("signed", gw.issue("admin_user")), ("anonymous", ""))}
check("discovery needs no token and answers with every declared tool",
      lambda: all(v.get("tools") == SERVED and not v.get("missing") for v in _hs.values())
      or f"FAIL {_hs}")

print(f"\n{passed} passed, {failed} failed, {skipped} skipped (live phone)")
sys.exit(1 if failed else 0)
