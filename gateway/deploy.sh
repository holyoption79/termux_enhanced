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
install -m 0700 "$SRC/server/issue.py"         "$DEST/issue.py"

# One key per install. Rotating it invalidates every live token immediately.
if [ ! -f "$DEST/secret.key" ]; then
  (umask 077 && openssl rand -hex 32 > "$DEST/secret.key")
  echo "   generated secret.key (chmod 600) — rotate to invalidate every token"
fi
[ -f "$DEST/users.json" ] || echo '{}' > "$DEST/users.json"

echo "== phone wrapper -> $SSH:$PREFIX/bin/s24-adbx"
if scp -q -o BatchMode=yes -o ConnectTimeout=10 "$SRC/phone/s24-adbx" "$SSH:$PREFIX/bin/s24-adbx" 2>/dev/null \
   && ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH" "chmod 0755 $PREFIX/bin/s24-adbx && s24-adbx ensure" 2>/dev/null; then
  echo "   wrapper installed, adb ensure ok"
else
  echo "   phone not reachable — wrapper install skipped, re-run when phone wakes"
fi

echo "== users (add one per client: issue.py add <name> <tier> [ttl_days]) =="
if [ "$(python3 -c "import json;print(len(json.load(open('$DEST/users.json'))))")" = "0" ]; then
  echo "   no users yet. Run: cd $DEST && python3 issue.py add <you> admin 90"
  echo "   Then: python3 issue.py list   /   python3 issue.py <you>"
fi
python3 "$DEST/issue.py" list 2>/dev/null || true

echo "== selftest"
cd "$DEST" && python3 test_gateway.py

cat <<EOF

== client config (any node on the tailnet)
  ssh forwards no environment: the signed token rides on the remote command line.
  Mint one with: python3 $DEST/issue.py <your-user>
  "s24": {
    "command": "ssh",
    "args": ["llmmaster", "S24_GATEWAY_TOKEN=<user.expiry.signature> python3 $DEST/s24-gateway.py"],
    "timeout": 60000
  }
EOF
