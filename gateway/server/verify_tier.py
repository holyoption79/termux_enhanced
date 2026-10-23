"""Verify token transport on Linux. Run on llmmaster: python3 /tmp/verify_tier.py"""
import json, os, subprocess, sys

D = "/home/jamesk/s24-gateway"
toks = json.load(open(f"{D}/tokens.json"))
admin = next(k for k, v in toks.items() if v["tier"] == "admin")
read = next(k for k, v in toks.items() if v["tier"] == "read")

LINES = "".join(json.dumps(m) + "\n" for m in [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "v", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "tap", "arguments": {"x": 10, "y": 10}}},
])

def run(label: str, cmd: list[str]) -> None:
    p = subprocess.run(cmd, input=LINES.encode(), capture_output=True, timeout=90,
                       env=dict(os.environ, PYTHONUNBUFFERED="1"))
    for raw in p.stdout.decode().splitlines():
        if not raw.startswith("{"): continue
        m = json.loads(raw)
        if m.get("id") not in (2, 3): continue
        c = ((m.get("result") or {}).get("content") or [{}])[0].get("text", "")
        print(f"  {label} id{m['id']}: {c[:140]}")

# remote assignment, the way the fixed client config sends it
print("== admin token via ssh-style env ==")
run("admin", ["sh", "-c", f"S24_GATEWAY_TOKEN={admin} python3 {D}/s24-gateway.py"])
print("== read token ==")
run("read", ["sh", "-c", f"S24_GATEWAY_TOKEN={read} python3 {D}/s24-gateway.py"])
print("== no token (fail closed to read) ==")
run("none", ["python3", f"{D}/s24-gateway.py"])
