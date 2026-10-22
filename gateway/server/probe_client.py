"""Probe the gateway the way a client does: stdio MCP, real token from disk."""
import json
import os
import subprocess
import sys

tokens = json.load(open(os.path.expanduser("~/s24-gateway/tokens.json")))
admin = next(k for k, v in tokens.items() if v.get("tier") == "admin")
reader = next(k for k, v in tokens.items() if v.get("tier") == "read")

CALLS = [
    ("admin shell read", admin, "shell", {"command": "dumpsys battery | head -5"}),
    ("admin write needs confirm", admin, "shell", {"command": "settings put system accelerator_rotation 1"}),
    ("admin write confirmed", admin, "shell", {"command": "settings put system accelerator_rotation 1", "confirm": True}),
    ("admin reboot is blocked", admin, "shell", {"command": "reboot"}),
    ("read token reads state", reader, "device_state", {}),
    ("read token cannot tap", reader, "tap", {"x": 10, "y": 10}),
]


def ask(token: str, name: str, args: dict) -> str:
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "probe", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}},
    ]
    req = "".join(json.dumps(m) + "\n" for m in msgs)
    env = dict(os.environ, S24_GATEWAY_TOKEN=token, PYTHONUNBUFFERED="1")
    p = subprocess.run([sys.executable, os.path.expanduser("~/s24-gateway/s24-gateway.py")],
                       input=req.encode(), capture_output=True, timeout=240, env=env)
    for line in p.stdout.decode().splitlines():
        if line.startswith("{"):
            m = json.loads(line)
            if m.get("id") == 2:
                r = m.get("result") or m.get("error") or {}
                c = (r.get("content") or [{}])[0].get("text", "") if r.get("content") else json.dumps(r)
                return f"{'ERROR ' if r.get('isError') else ''}{str(c)[:420]}"
    return f"no reply rc={p.returncode} err={p.stderr.decode()[:200]}"


for label, token, tool, args in CALLS:
    print(f"== {label}\n   {ask(token, tool, args)}")
