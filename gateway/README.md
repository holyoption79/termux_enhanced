# s24-gateway

An MCP server that gives any MCP client (opencode, claude, codex, anything
speaking stdio MCP) the adb access this project already uses by hand. The phone
is reached through SSH into Termux, so it works on Wi-Fi, LTE or 5G — anywhere
the tailnet reaches.

```
opencode / claude / codex  (any node)
    |  stdio MCP, launched as: ssh llmmaster python3 ~/s24-gateway/s24-gateway.py
    v
s24-gateway            tier gate -> one-actor lock -> audit log
    |  ssh s24 s24-adbx {shell,exec-out} BASE64
    v
Termux adb -> the phone's own adbd -> uid=2000(shell)
```

## Layout

| File | Installs to | Purpose |
|---|---|---|
| `phone/s24-adbx` | `$PREFIX/bin/` on the phone | Runs adb against `127.0.0.1:5555`; takes commands base64-encoded so they survive SSH plus two shells |
| `server/s24-gateway.py` | `~/s24-gateway/` on llmmaster | The MCP server: tiers, lock, audit, tools |
| `server/tokens.json` | `~/s24-gateway/` | token -> `{name, tier}`, edited by hand |
| `server/test_gateway.py` | `~/s24-gateway/` | Selftest: logic, live phone path, MCP handshake |
| `server/probe_client.py` | `~/s24-gateway/` | Talks to the running server the way a real MCP client does: real tokens, tier checks, confirm/blocked paths |
| `deploy.sh` | run on llmmaster | Installs all of the above, mints tokens, selftests |

## Tiers

Every token gets one tier, and the tier decides the tool list, not the mood of
the caller. `read` sees state, `act` adds input and apps, `admin` adds
installs, file moves and settings writes.

| Tier | Tools |
|---|---|
| `read` | `whoami` `device_state` `screenshot` `ui_tree` `ui_find` `logcat` `dumpsys_summary` `radio_diag` `settings_read` `history`, plus read-only `shell` |
| `act` | the above + `tap` `swipe` `swipe_to` `type_text` `key` `app_start` `app_stop` |
| `admin` | the above + `install` `file_push` `file_pull` `settings_write` `grant`, and whole `shell` |

Three devices, none of which reduce what adb can do:

- **One actor at a time.** `act` and above take a lock with a TTL; a second
  agent gets `phone_busy` with the remaining seconds instead of fighting over
  the screen.
- **Audit everything.** One JSON line per call in `audit-YYYY-MM.jsonl`: who,
  tier, tool, args, result, duration. `history(limit=, who=)` reads it back.
- **Confirm before unrolling.** Installs, grants, `settings put`, IME changes
  run only with `confirm=true`.

Some calls are never executed, because they cut the path the gateway travels
on: `reboot`, `svc wifi disable`, `svc data disable`, `pm clear`, `pm uninstall`,
changing a default app or role. They come back as `blocked (...)` with the
command, so the caller can report it to a human.

## Use

```bash
scp -r gateway llmmaster:~/s24-gateway-tmp && ssh llmmaster 'cd ~/s24-gateway-tmp && bash deploy.sh'
```

`deploy.sh` ends by printing a client block. Paste it into the MCP config of
whatever node should hold the phone:

```jsonc
"s24": {
  "command": "ssh",
  "args": ["llmmaster", "S24_GATEWAY_TOKEN=<the token deploy printed> python3 /home/you/s24-gateway/s24-gateway.py"],
  "timeout": 60000
}
```

The token has to ride as a remote assignment: `ssh` forwards no environment, so
`env: { S24_GATEWAY_TOKEN: ... }` never reaches the server and every client
silently lands in `read` (it fails closed, `whoami` tells you the truth). Give
the client a timeout — a cold SSH plus a phone round trip is seconds, and an
untimed MCP client will sit there.

SSH-stdio means no new listening port: the node needs only SSH to llmmaster,
which every fleet node already has. A client that cannot spawn a process needs
`mcp proxy`-style HTTP in front of the same server; that is not built here.

The usual loop is `ui_tree` or `ui_find` to read the screen, `tap text=...` to
hit something by label rather than by coordinate, `screenshot` when the text is
not enough. `screenshot` masks the status bar by default, so card and
notification text never leaves the phone unless asked.

## Not built

- **HTTP transport.** stdio over SSH only, which is what every node can reach.
- **Reboot recovery for adb.** `adb tcpip 5555` dies with a reboot; reconnect
  is `s24-adbx ensure`. Boot-time re-arm through Termux:Boot is a separate
  change and has not been tested against a reboot.
- **Multi-device.** One phone, one serial.
- `test_gateway.py` covers gating, lock, audit, parsing, handshake, and — when
  the phone is reachable — the live path. It does not cover a reboot, LTE-only
  operation, or another agent stealing the lock mid-call.
