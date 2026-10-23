#!/usr/bin/env python3
"""issue.py - mint gateway tokens. Run inside ~/s24-gateway on llmmaster.

    python3 issue.py add <user> <tier> [ttl_days]   create a user, print its token
    python3 issue.py <user>                         verify password, print a token
    python3 issue.py revoke <user>                  drop the user, its tokens die

Tokens are signed with secret.key and carry their own expiry. The password is
never sent to clients: it only unlocks minting. Losing users.json leaks nothing
about passwords; losing secret.key invalidates every live token.
"""
from __future__ import annotations

import getpass
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("gw", HERE / "s24-gateway.py")
gw = importlib.util.module_from_spec(spec)
sys.modules["gw"] = gw
spec.loader.exec_module(gw)


def _pw(prompt: str) -> str:
    """Read a password from the terminal, or from S24_GATEWAY_PASSWORD when
    there is no terminal (deployment run over ssh)."""
    import os

    env = os.environ.get("S24_GATEWAY_PASSWORD")
    return env if env is not None else getpass.getpass(prompt)


def _write(users: dict) -> None:
    gw.USERS.write_text(json.dumps(users, indent=2, sort_keys=True) + "\n")
    gw.USERS.chmod(0o600)


def main(argv: list[str]) -> int:
    if not SECRET_OK():
        print(f"missing {gw.SECRET} - run deploy.sh first", file=sys.stderr)
        return 1
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2

    cmd, users = argv[0], gw._users()

    if cmd == "add":
        if len(argv) < 3:
            print("usage: issue.py add <user> <read|act|admin> [ttl_days]", file=sys.stderr)
            return 2
        user, tier = argv[1], argv[2]
        if tier not in gw.TIERS:
            print(f"tier must be one of {sorted(gw.TIERS)}", file=sys.stderr)
            return 2
        ttl = int(argv[3]) if len(argv) > 3 else 90
        if user in users:
            print(f"'{user}' exists; use 'passwd' to change its password", file=sys.stderr)
            return 1
        users[user] = {"pass": gw.pass_hash(_pw(f"password for {user}: ")), "tier": tier, "ttl_days": ttl}
        _write(users)
        print(gw.issue(user))
        return 0

    if cmd == "passwd":
        if len(argv) < 2 or argv[1] not in users:
            print("usage: issue.py passwd <user>", file=sys.stderr)
            return 2
        user = argv[1]
        if gw.pass_hash(_pw(f"current password for {user}: ")) != users[user]["pass"]:
            print("wrong password", file=sys.stderr)
            return 1
        users[user]["pass"] = gw.pass_hash(_pw(f"new password for {user}: "))
        _write(users)
        print(f"{user}: password changed, existing tokens still valid until they expire", file=sys.stderr)
        return 0

    if cmd == "revoke":
        if len(argv) < 2 or argv[1] not in users:
            print("usage: issue.py revoke <user>", file=sys.stderr)
            return 2
        del users[argv[1]]
        _write(users)
        print(f"{argv[1]}: revoked, token dead on the next call", file=sys.stderr)
        return 0

    if cmd == "list":
        for u, e in sorted(users.items()):
            print(f"  {u:20} {e['tier']:6} ttl={e.get('ttl_days', 90)}d")
        return 0

    user = cmd
    entry = users.get(user)
    if not entry:
        print(f"'{user}' not found", file=sys.stderr)
        return 1
    if gw.pass_hash(_pw(f"password for {user}: ")) != entry["pass"]:
        print("wrong password", file=sys.stderr)
        return 1
    print(gw.issue(user))
    return 0


def SECRET_OK() -> bool:
    return gw.SECRET.exists() and gw.USERS.exists()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
