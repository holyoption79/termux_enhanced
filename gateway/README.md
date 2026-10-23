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
| `server/s24-gateway.py` | `~/s24-gateway/` on llmmaster | The MCP server: signed-token auth, tiers, lock, audit, tools |
| `server/issue.py` | `~/s24-gateway/` | Mint a token after a password check; add, revoke, rotate users |
| `server/test_gateway.py` | `~/s24-gateway/` | Selftest: auth paths, tier gate, live phone, MCP wire |
| `server/probe_client.py` | `~/s24-gateway/` | Talks to the running server the way a real MCP client does |
| `deploy.sh` | run on llmmaster | Installs the above, creates `secret.key`, selftests |

`users.json` and `secret.key` are runtime state on llmmaster, not in the repo.
`secret.key` is chmod 600: rotate it and every live token dies at once.

## Auth

A caller presents a signed token: `user.expiry.signature`, HMAC-SHA256 over
`user.expiry` with `secret.key`. No token, wrong signature, expired, or an
unknown user: denied outright, every tier. There is no read-only path for
strangers, because screen text, notifications and logcat are the owner's data
and a gateway that hands them to whoever connects cannot be left running.

Mint tokens on llmmaster, password required, password never sent to clients:

```bash
python3 ~/s24-gateway/issue.py add <name> <read|act|admin> [ttl_days]
python3 ~/s24-gateway/issue.py <name>     # verify password, print a token
python3 ~/s24-gateway/issue.py revoke <name>
```

## Tiers

A token carries one tier and the tier decides the tool list, not the mood of
the caller. `read` sees state, `act` adds input and apps, `admin` adds
installs, file moves and settings writes.

| Tier | Tools |
|---|---|
| `read` | `whoami` `device_state` `screenshot` `ui_tree` `ui_find` `logcat` `dumpsys_summary` `radio_diag` `settings_read` `content_query` `clipboard` `boot_script` `history`, plus read-only `shell` and `exec_out` |
| `act` | the above + `tap` `long_press` `drag` `swipe` `swipe_to` `type_text` `key` `app_start` `app_stop` `content_write` `termux_run` `agent_run` |
| `admin` | the above + `install` `file_push` `file_pull` `settings_write` `grant`, and `boot_script` writes |

32 tools, but two of them are unbounded: `shell` is any `adb shell` command
(uid=2000) and `termux_run` is any Termux command. Between them the surface is
every action a developer at the console can take — 100% of what adb reaches.
What adb itself cannot reach without root stays out of reach: other apps'
private data, the system partition, modem internals, secure windows, and the
lockscreen PIN. That is a property of an unrooted S24, not of this gateway.

Three devices, none of which reduce what adb can do:

- **One actor at a time.** `act` and above take a lock with a TTL; a second
  agent gets `phone_busy` with the remaining seconds instead of fighting over
  the screen.
- **Audit everything.** One JSON line per call in `audit-YYYY-MM.jsonl`: who,
  tier, tool, args, result, duration. `history(limit=, who=)` reads it back.
- **Confirm before unrolling.** Installs, grants, `settings put`, IME changes
  run only with `confirm=true`.

## The phone's own agents

`agent_run` drives claude, codex and opencode running *on the phone*
(`s24-ai-run`, proot Debian, Termux home bound to `/root` so `~/.claude`,
`~/.codex` and `~/.config/opencode` are the credentials already there). It
spends the owner's API quota and reads their agent history, so it is act tier,
locked, and audited like everything else. Pass each CLI's own print flags:
`agent_run("claude", '-p "summarise ~/notes"')`.

`boot_script` reads and writes `~/.termux/boot/*` — the bring-up hooks Termux:Boot
runs on every boot. That is where adb reconnection belongs, but see the reboot
note under Not built: a script there cannot start adbd, only talk to one that is
already listening.

Some calls are never executed, because they cut the path the gateway travels
on: `reboot`, `svc wifi disable`, `svc data disable`, `pm clear`, `pm uninstall`,
changing a default app or role. They come back as `blocked (...)` with the
command, so the caller can report it to a human.

## Use

```bash
scp -r gateway llmmaster:~/s24-gateway-tmp && ssh llmmaster 'cd ~/s24-gateway-tmp && bash deploy.sh'
```

`deploy.sh` installs the server, creates `secret.key` once (chmod 600, never
printed, rotating it invalidates every token), runs the selftest, and skips the
wrapper install if the phone is asleep. It does not mint tokens — you do that,
so passwords never pass through a deployment log:

```bash
ssh llmmaster 'python3 ~/s24-gateway/issue.py add <name> admin 90'
ssh llmmaster 'python3 ~/s24-gateway/issue.py <name>'      # prints the token
```

Put that in the MCP config of whatever node should hold the phone:

```jsonc
"s24": {
  "command": "ssh",
  "args": ["llmmaster", "S24_GATEWAY_TOKEN=<name.expiry.signature> python3 /home/you/s24-gateway/s24-gateway.py"],
  "timeout": 60000
}
```

The token has to ride as a remote assignment: `ssh` forwards no environment, so
`env: { S24_GATEWAY_TOKEN: ... }` never reaches the server and the client is
denied every call. Give the client a timeout — a cold SSH plus a phone round
trip is seconds, and an untimed MCP client will sit there.

SSH-stdio means no new listening port: the node needs only SSH to llmmaster,
which every fleet node already has. A client that cannot spawn a process needs
`mcp proxy`-style HTTP in front of the same server; that is not built here.

The usual loop is `ui_tree` or `ui_find` to read the screen, `tap text=...` to
hit something by label rather than by coordinate, `screenshot` when the text is
not enough. `screenshot` masks the status bar by default, so card and
notification text never leaves the phone unless asked.

## Not built

- **HTTP transport.** stdio over SSH only, which is what every node can reach.
- **Reboot recovery for adb — genuinely unsolved.** A reboot puts adbd back in
  USB-only mode; `service.adb.tcp.port` is volatile and a shell cannot restart
  adbd, so from Termux alone there is nothing to reconnect to and every tool
  call errors until someone plugs USB (`adb tcpip 5555`) or turns on Wireless
  Debugging, which does survive reboots. `s24-adbx ensure` only helps while
  adbd is already listening — it is not a boot fix. Writing the boot hook is
  `boot_script`; pairing Wireless Debugging to a stable port for Termux's adb
  client is the missing piece and has never been tested here.
- **Multi-device.** One phone, one serial.
- `test_gateway.py` runs 31 checks: signed token honoured, strangers denied
  (no token, bad shape, bad signature, expired), password mints and wrong
  password mints nothing, tier gate, lock, audit, UI parsing, screenshot
  masking, blocked/confirm paths, all 32 tools declared and served, discovery
  without a token — plus the live phone path, the phone's own agent runner, and
  Termux home. It does not cover a reboot, LTE-only operation, or another agent
  stealing the lock mid-call.
