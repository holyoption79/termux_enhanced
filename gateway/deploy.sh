#!/usr/bin/env bash
# Deploy s24-gateway. Run on llmmaster, from inside a copy of gateway/.
#
#   scp -r gateway llmmaster:~/s24-gateway-tmp && ssh llmmaster 'cd ~/s24-gateway-tmp && bash deploy.sh'
#
# Puts the phone wrapper in Termux's bin, the server in ~/s24-gateway, prints
# one client config block, and smoke-tests the path end to end. Idempotent.
set -euo pipefail

SRC=$(cd "$(dirname "$0")" && pwd)
DEST="$HOME/s24-gateway"
SSH=${S24_GATEWAY_SSH:-s24}
PREFIX=${TERMUX_PREFIX:-/data/data/com.termux/files/usr}

mkdir -p "$DEST"
install -m 0755 "$SRC/server/s24-gateway.py" "$DEST/s24-gateway.py"
install -m 0644 "$SRC/server/test_gateway.py"  "$DEST/test_gateway.py"
install -m 0644 "$SRC/server/probe_client.py"  "$DEST/probe_client.py"
[ -f "$DEST/tokens.json" ] || install -m 0644 "$SRC/server/tokens.json" "$DEST/tokens.json"

echo "== phone wrapper -> $SSH:$PREFIX/bin/s24-adbx"
if scp -q -o BatchMode=yes -o ConnectTimeout=10 "$SRC/phone/s24-adbx" "$SSH:$PREFIX/bin/s24-adbx" 2>/dev/null \
   && ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH" "chmod 0755 $PREFIX/bin/s24-adbx && s24-adbx ensure" 2>/dev/null; then
  echo "   wrapper installed, adb ensure ok"
else
  echo "   phone not reachable — wrapper install skipped, re-run when phone wakes"
fi

echo "== tokens (edit tiers per client)"
python3 - "$DEST/tokens.json" <<'PY'
import json, secrets, sys
p = sys.argv[1]
d = json.load(open(p))
live = {k: v for k, v in d.items() if "replace_me" not in k}
for name, tier in (("operator", "admin"), ("observer", "read")):
    if not any(v.get("name") == name for v in live.values()):
        live[secrets.token_hex(16)] = {"name": name, "tier": tier}
json.dump(live, open(p, "w"), indent=2)
for k, v in live.items():
    print(f"  {v['tier']:5s} {v['name']:10s} S24_GATEWAY_TOKEN={k}")
PY

echo "== selftest"
cd "$DEST" && python3 test_gateway.py

cat <<EOF

== client config (any node on the tailnet)
  "s24": {
    "command": "ssh",
    "args": ["llmmaster", "python3", "$DEST/s24-gateway.py"],
    "env": { "S24_GATEWAY_TOKEN": "<token above>" }
  }
EOF
