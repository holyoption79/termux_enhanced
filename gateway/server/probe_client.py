"""Probe the gateway the way a client does: stdio MCP, real tokens from the live store."""
import json
import os
import subprocess
import sys

D = os.path.expanduser("~/s24-gateway")
env_base = dict(os.environ, PYTHONUNBUFFERED="1")


def token_for(user: str, password: str) -> str:
    """Ask the running server to mint a token the same way a client would."""
    r = subprocess.run([sys.executable, f"{D}/issue.py", user],
                       capture_output=True, text=True, timeout=30,
                       env=dict(env_base, S24_GATEWAY_PASSWORD=password, S24_GATEWAY_USERS=f"{D}/users.json", S24_GATEWAY_SECRET=f"{D}/secret.key"))
    if r.returncode != 0:
        raise RuntimeError(f"issue.py {user}: {r.stderr.strip()[:80]}")
    return r.stdout.strip()


CALLS = [
    ("admin shell read", "admin", "shell", {"command": "dumpsys battery | head -5"}),
    ("admin write needs confirm", "admin", "shell", {"command": "settings put system accelerator_rotation 1"}),
    ("admin write confirmed", "admin", "shell", {"command": "settings put system accelerator_rotation 1", "confirm": True}),
    ("admin reboot is blocked", "admin", "shell", {"command": "reboot"}),
    ("read token reads state", "read", "device_state", {}),
    ("read token cannot tap", "read", "tap", {"x": 10, "y": 10}),
]


def ask(tok: str, name: str, args: dict) -> str:
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "probe", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}},
    ]
    req = "".join(json.dumps(m) + "\n" for m in msgs)
    env = dict(os.environ, S24_GATEWAY_TOKEN=tok, PYTHONUNBUFFERED="1",
               S24_GATEWAY_USERS=f"{D}/users.json", S24_GATEWAY_SECRET=f"{D}/secret.key")
    p = subprocess.run([sys.executable, f"{D}/s24-gateway.py"],
                       input=req.encode(), capture_output=True, timeout=240, env=env)
    for line in p.stdout.decode().splitlines():
        if line.startswith("{"):
            m = json.loads(line)
            if m.get("id") == 2:
                r = m.get("result") or m.get("error") or {}
                c = (r.get("content") or [{}])[0].get("text", "") if r.get("content") else json.dumps(r)
                return f"{'ERROR ' if r.get('isError') else ''}{str(c)[:420]}"
    return f"no reply rc={p.returncode} err={p.stderr.decode()[:200]}"


for label, tier, tool, args in CALLS:
    import getpass
    users = json.load(open(f"{D}/users.json"))
    user = next((u for u, e in sorted(users.items()) if e.get("tier") == tier), None)
    if not user:
        print(f"== {label}\n   no {tier} user in users.json; run deploy.sh first")
        continue
    pw = os.environ.get(f"S24_PROBE_{tier.upper()}_PASS") or getpass.getpass(f"{tier} password for {user}: ")
    try:
        print(f"== {label}\n   {ask(token_for(user, pw), tool, args)}")
    except RuntimeError as e:
        print(f"== {label}\n   {e}")
