#!/usr/bin/env python3
"""s24-gateway - an MCP server that lets any MCP client drive one S24 Ultra.

Runs on llmmaster. Talks to the phone through SSH into Termux, where the
s24-adbx wrapper runs adb against the phone's own adbd (127.0.0.1:5555). The
phone does not need to be on Wi-Fi: SSH rides Tailscale, so LTE and 5G work.

    MCP client (opencode / claude / codex / any node on the tailnet)
        |  stdio, launched as: ssh llmmaster python3 ~/s24-gateway/s24-gateway.py
        v
    this server  -- tier gate, device lock, audit log
        |  ssh s24 s24-adbx {shell,exec-out} BASE64
        v
    Termux adb -> the phone's adbd -> uid=2000(shell)

Tiers are per client token (tokens.json). A token sees read, or read+act, or
read+act+admin. Destructive calls run only with confirm=true. A small set of
calls that would cut this very connection (reboot, radio off, adbd down) is
blocked outright and reported instead of executed.

State lives next to the script:
  tokens.json         token -> {name, tier}            (edit by hand)
  lock.json           {holder, until}                 (written by the server)
  audit-YYYY-MM.jsonl one JSON line per call          (append only)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
USERS = Path(os.environ.get("S24_GATEWAY_USERS", HERE / "users.json"))
SECRET = Path(os.environ.get("S24_GATEWAY_SECRET", HERE / "secret.key"))
LOCK = Path(os.environ.get("S24_GATEWAY_LOCK", HERE / "lock.json"))
AUDIT_DIR = Path(os.environ.get("S24_GATEWAY_AUDIT", HERE))
LOCK_TTL = float(os.environ.get("S24_GATEWAY_LOCK_TTL", "120"))
SSH_HOST = os.environ.get("S24_GATEWAY_SSH", "s24")
SERIAL = "127.0.0.1:5555"
TIMEOUT = 45

TIERS = {"read": 0, "act": 1, "admin": 2}

# Never executed; reported instead. Each of these would drop the path the
# gateway itself travels on, or is not undoable from a shell.
BLOCKED = (
    "reboot",
    "recovery",
    "fastboot",
    "svc wifi disable",
    "svc data disable",
    "pm clear ",
    "pm uninstall ",
    "cmd appset",
    "settings put secure default_",
)

# Reversible-but-loaded: fine, once confirmed.
NEEDS_CONFIRM = (
    "pm install",
    "ime set",
    "cmd notification",
    "settings put",
    "input keyevent 26",
)


class TierError(RuntimeError):
    pass


@dataclass
class Ctx:
    name: str = ""
    tier: str = ""
    deny: str = "no token"


CTX = Ctx()


def _secret() -> bytes:
    return SECRET.read_bytes().strip()


def _sig(body: str) -> str:
    return hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]


def pass_hash(password: str) -> str:
    """Stored form of a password: keyed by the server secret, so a leaked
    users.json leaks nothing."""
    return hashlib.sha256(_secret() + password.encode()).hexdigest()


def _users() -> dict:
    try:
        return json.loads(USERS.read_text())
    except Exception:
        return {}


def issue(user: str) -> str:
    """Sign a token for an existing user. issue.py calls this after checking
    the password. The token is self-describing and self-expiring: no store,
    no rotation dance, just secret.key changing to kill every live token."""
    entry = _users().get(user)
    if not entry:
        raise KeyError(user)
    exp = int(time.time()) + int(entry.get("ttl_days", 90)) * 86400
    return f"{user}.{exp}.{_sig(f'{user}.{exp}')}"


def identify() -> Ctx:
    """Bind this process to a signed, unexpired token naming a known user.

    Everything else is denied. There is no read-only fallback for strangers:
    screen text, notifications and logcat are the owner's own data, and a
    gateway that hands them out to whoever connects is not a gateway the owner
    can leave running.

    ssh forwards no environment, so the token has to ride as a remote command
    line assignment: S24_GATEWAY_TOKEN=user.exp.sig python3 s24-gateway.py
    """
    tok = os.environ.get("S24_GATEWAY_TOKEN", "").strip()
    if not tok:
        CTX.deny = "no token: run 'python3 issue.py <user>' in ~/s24-gateway on llmmaster"
        return CTX
    parts = tok.split(".")
    if len(parts) != 3:
        CTX.deny = "bad token: want <user>.<expiry>.<signature>"
        return CTX
    user, exp_s, mac = parts
    try:
        exp = int(exp_s)
    except ValueError:
        CTX.deny = "bad token: expiry is not a number"
        return CTX
    if not hmac.compare_digest(mac, _sig(f"{user}.{exp}")):
        CTX.deny = f"bad signature for '{user}': not signed by this server"
        return CTX
    if exp < int(time.time()):
        CTX.deny = f"'{user}' token expired: mint a new one"
        return CTX
    entry = _users().get(user)
    if not entry:
        CTX.deny = f"'{user}' is not in users.json"
        return CTX
    tier = entry.get("tier", "read")
    CTX.name, CTX.tier, CTX.deny = user, (tier if tier in TIERS else "read"), ""
    return CTX


def need(min_tier: str) -> None:
    if CTX.deny:
        raise TierError(CTX.deny)
    if TIERS[CTX.tier] < TIERS[min_tier]:
        raise TierError(f"'{CTX.name}' is {CTX.tier}; {min_tier} tier required")


def classify(cmd: str) -> str:
    """blocked | confirm | free"""
    probe = " " + " ".join(cmd.split()) + " "
    for pat in BLOCKED:
        if pat in probe:
            return "blocked"
    for pat in NEEDS_CONFIRM:
        if pat in probe:
            return "confirm"
    return "free"


# ---------------------------------------------------------------- transport
def adb(cmd: str, binary: bool = False, timeout: float = TIMEOUT) -> bytes:
    """Run one adb shell command on the phone, return raw stdout."""
    b64 = base64.b64encode(cmd.encode()).decode()
    verb = "exec-out" if binary else "shell"
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", SSH_HOST, f"s24-adbx {verb} {b64}"]
    p = subprocess.run(argv, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(err or f"s24-adbx exit {p.returncode}")
    return p.stdout if binary else p.stdout.replace(b"\r\n", b"\n")


def adb_text(cmd: str, timeout: float = TIMEOUT) -> str:
    return adb(cmd, timeout=timeout).decode("utf-8", "replace").strip()


# -------------------------------------------------------------------- lock
def acquire(tool: str) -> str:
    """One actor at a time. Returns the holder label; raises if busy."""
    now = time.time()
    doc = {"holder": CTX.name, "until": now + LOCK_TTL, "tool": tool}
    try:
        cur = json.loads(LOCK.read_text())
    except Exception:
        cur = {}
    if cur and cur.get("holder") != CTX.name and float(cur.get("until", 0)) > now:
        raise TimeoutError(f"phone held by '{cur.get('holder')}' for {int(cur['until'] - now)}s more")
    LOCK.write_text(json.dumps(doc))
    return CTX.name


def release() -> None:
    """Drop this token's hold. Called from audit(): a call is over once it has
    been audited, so the lock covers one call, not one session. LOCK_TTL only
    matters for a process that died mid-call."""
    try:
        cur = json.loads(LOCK.read_text())
    except Exception:
        return
    if cur.get("holder") == CTX.name:
        LOCK.unlink(missing_ok=True)


def audit(tool: str, args: dict, ok: bool, detail: str, ms: int) -> None:
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "who": CTX.name,
        "tier": CTX.tier,
        "tool": tool,
        "args": {k: (v if not isinstance(v, str) or len(v) < 400 else v[:400] + "…") for k, v in args.items()},
        "ok": ok,
        "ms": ms,
        "out": detail[:600] + ("…" if len(detail) > 600 else ""),
    }
    path = AUDIT_DIR / f"audit-{time.strftime('%Y-%m')}.jsonl"
    with open(path, "a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    release()


def guarded(tool: str, cmd: str, confirm: bool, args: dict, run):
    """Tier gate -> lock -> run, with audit on both paths."""
    t0 = time.time()
    verdict = classify(cmd)
    try:
        if verdict == "blocked":
            out = f"blocked (would sever the gateway path): {cmd}"
        elif verdict == "confirm" and not confirm:
            out = f"needs confirm=true: {cmd}"
        else:
            acquire(tool)
            out = run()
        audit(tool, args, True, str(out), int((time.time() - t0) * 1000))
        return out
    except Exception as e:
        audit(tool, args, False, f"{type(e).__name__}: {e}", int((time.time() - t0) * 1000))
        raise


# -------------------------------------------------------------------- tools
app = FastMCP("s24-gateway")


@app.tool()
def whoami() -> dict:
    """Who this client is, what tier, and who holds the phone. Strangers see the
    denial reason and nothing else — holder identity is not public."""
    if CTX.deny:
        return {"deny": CTX.deny}
    try:
        holder = json.loads(LOCK.read_text())
    except Exception:
        holder = None
    busy = bool(holder and holder.get("holder") != CTX.name and float(holder.get("until", 0)) > time.time())
    return {"who": CTX.name, "tier": CTX.tier, "phone_busy": busy, "holder": (holder or {}).get("holder")}


@app.tool()
def device_state() -> dict:
    """Screen size, rotation, battery, serving cell and network. Read tier.

    'rotation' is settings system user_rotation: 0=upright, 1=90 left, 2=inverted,
    3=90 right. Every field is read on its own so a key this device does not
    have shows up as 'err ...' on that one field instead of failing the read.
    """
    need("read")
    t0 = time.time()
    out: dict = {}
    acquire("device_state")

    def field(label: str, cmd: str):
        """Only commands that always exit 0 on the S24 Ultra belong here: a bare
        'grep' with no match returns 1 and adb propagates it as a failure."""
        try:
            out[label] = adb_text(cmd)
        except Exception as e:
            out[label] = f"err {type(e).__name__}: {e}"

    field("windows", "dumpsys window displays | grep -m1 -E 'cur=' || true")
    field("battery", "dumpsys battery | grep -E 'powered|level:|status:|voltage:|temperature:|technology:' | head -8 || true")
    # wm size and settings get always answer; 'wm orientation' and
    # 'SurfaceOrientation' do not exist on this device and sank the whole read.
    field("screen", "wm size")
    field("rotation", "settings get system user_rotation")
    field("radio", "dumpsys telephony.registry | grep -m2 -E 'mServiceState|mDataConnectionState' || true")
    field("net", "ip route | head -3 || true")
    field("wifi", "dumpsys wifi | grep -m1 -E 'mWifiInfo' || true")

    got = sum(1 for v in out.values() if not str(v).startswith("err"))
    audit("device_state", {}, True, f"{got}/{len(out)} fields", int((time.time() - t0) * 1000))
    return out


def _call(fn, tool: str, args: dict, t0: float):
    acquire(tool)
    try:
        r = fn()
        audit(tool, args, True, str(r), int((time.time() - t0) * 1000))
        return r
    except Exception as e:
        audit(tool, args, False, f"{type(e).__name__}: {e}", int((time.time() - t0) * 1000))
        raise


@app.tool()
def screenshot(mask_status_area: bool = True, out_path: str = "/tmp/s24.png") -> dict:
    """PNG of the screen. mask_status_area drops the status bar so card and
    notification text stays on the phone. Saved on llmmaster at out_path."""
    need("read")
    t0 = time.time()
    acquire("screenshot")
    png = adb("screencap -p", binary=True)
    audit("screenshot", {"mask": mask_status_area, "out": out_path}, True, f"{len(png)} bytes", int((time.time() - t0) * 1000))
    p = Path(out_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not mask_status_area:
        p.write_bytes(png)
        return {"path": str(p), "bytes": len(png), "masked": False}
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(png))
    w, h = img.size
    top = int(h * 0.045) + 24  # status bar band, generous
    img.crop((0, top, w, h)).save(p, "PNG")
    return {"path": str(p), "bytes": p.stat().st_size, "masked": True, "kept": f"{w}x{h - top}"}


@app.tool()
def ui_tree(interactive_only: bool = True, limit: int = 120) -> dict:
    """The screen as text: every node's text, resource-id, class and centre.
    This is how you find a tap target without guessing coordinates. A terminal
    in the foreground never idles, so uiautomator can refuse; the answer then
    says so and carries the view hierarchy instead of inventing nodes."""
    need("read")
    t0 = time.time()
    acquire("ui_tree")
    raw = adb_text("{ uiautomator dump /data/local/tmp/win.xml; cat /data/local/tmp/win.xml; } 2>&1 || true", timeout=60)
    nodes = _parse_ui(raw, limit)
    if interactive_only:
        nodes = [n for n in nodes if n["text"] or n["id"]]
    if not nodes:
        why = "uiautomator did not settle" if "ERROR" in raw else "no readable nodes"
        out = {"count": 0, "nodes": [], "note": f"{why}: {(raw.splitlines() or ['empty'])[0][:160]}", "screen": _hint()}
        try:
            out["view_dump"] = adb_text("dumpsys activity top | sed -n '1,140p'", timeout=30)
        except Exception as e:
            out["view_dump"] = f"err {e}"
        audit("ui_tree", {"fallback": True}, True, out["note"], int((time.time() - t0) * 1000))
        return out
    audit("ui_tree", {"limit": limit}, True, f"{len(nodes)} nodes", int((time.time() - t0) * 1000))
    return {"count": len(nodes), "nodes": nodes}


def _parse_ui(xml_text: str, limit: int) -> list[dict]:
    start = xml_text.find("<?xml")
    if start < 0:
        return []
    body = xml_text[start:]
    out: list[dict] = []
    try:
        root = ET.fromstring(body[: body.rindex(">") + 1])
    except Exception:
        return out
    for el in root.iter("node"):
        if len(out) >= limit:
            break
        b = el.get("bounds", "") or ""
        cx = cy = None
        nums = [int(v) for v in re.findall(r"-?\d+", b)]
        if len(nums) == 4:
            cx, cy = (nums[0] + nums[2]) // 2, (nums[1] + nums[3]) // 2
        out.append(
            {
                "text": el.get("text", "") or "",
                "id": el.get("resource-id", "") or "",
                "cls": el.get("class", "") or "",
                "clickable": el.get("clickable") == "true",
                "center": [cx, cy],
                "bounds": b,
            }
        )
    return out


@app.tool()
def ui_find(query: str, limit: int = 12) -> list[dict]:
    """Nodes whose text or resource-id contains query, with tap coordinates."""
    need("read")
    hits = [n for n in ui_tree(interactive_only=False, limit=400)["nodes"] if query.lower() in (n["text"] + " " + n["id"]).lower()]
    return hits[:limit]


@app.tool()
def logcat(tags: str = "", grep: str = "", lines: int = 80) -> str:
    """Recent logcat, -d so it never blocks. tags like 'ActivityManager:I *:S'."""
    need("read")
    t0 = time.time()
    spec = f"logcat -d -t {int(lines)}"
    if tags:
        spec = f"logcat -d -t {int(lines)} {tags}"
    cmd = spec + (f" | grep -E {shlex.quote(grep)} || true" if grep else "")
    r = _call(lambda: adb_text(cmd, timeout=30), "logcat", {"tags": tags, "grep": grep}, t0)
    return r


@app.tool()
def dumpsys_summary(service: str = "window", grep: str = "") -> str:
    """One service's dumpsys, trimmed. service: window battery wifi telephony
    activity input surface wm notification."""
    need("read")
    t0 = time.time()
    svc = "".join(c for c in service if c.isalnum() or c in "._-") or "window"
    cmd = f"dumpsys {svc} | head -200" + (f" | grep -E {shlex.quote(grep)}" if grep else "")
    return _call(lambda: adb_text(cmd, timeout=30), "dumpsys", {"service": svc, "grep": grep}, t0)


@app.tool()
def radio_diag() -> dict:
    """Serving cell, RSRP/RSRQ/SINR, band, tx power, registration. The reading
    you take when the phone is out of Wi-Fi range and you are on LTE/5G."""
    need("read")
    t0 = time.time()
    acquire("radio_diag")
    out: dict = {}
    for key, cmd in (
        ("service_state", "dumpsys telephony.registry | grep -m2 -E 'mServiceState|mDataConnectionState'"),
        ("signal", "dumpsys telephony.registry | grep -m1 -A12 'mCallState'"),
        ("network", "dumpsys wifi | grep -m1 -E 'mWifiInfo|RSSI'"),
        ("radio", "dumpsys -l | grep -cE 'phone|telephony'; svc -v 2>/dev/null | head -3"),
    ):
        try:
            out[key] = adb_text(cmd, timeout=25)
        except Exception as e:
            out[key] = f"err {type(e).__name__}: {e}"
    audit("radio_diag", {}, True, "ok", int((time.time() - t0) * 1000))
    return out


@app.tool()
def tap(text: str = "", res_id: str = "", x: int = -1, y: int = -1, index: int = 0) -> dict:
    """Tap. Give text or res_id and the server resolves the coordinate from the
    live UI tree; x/y are the fallback when you already know them."""
    need("act")
    t0 = time.time()
    acquire("tap")
    if text or res_id:
        q = text or res_id
        hits = [n for n in ui_tree(interactive_only=False, limit=400)["nodes"] if q.lower() in (n["text"] + " " + n["id"]).lower()]
        if not hits:
            return {"ok": False, "why": f"no node matches {q!r}; screen moved or label differs", "screen": _hint()}
        pick = hits[min(index, len(hits) - 1)]
        x, y = pick["center"]
        if x is None:
            return {"ok": False, "why": f"{q!r} has no bounds", "node": pick}
        label = pick["text"] or pick["id"]
    else:
        label = f"coord {x},{y}"
    r = adb_text(f"input tap {int(x)} {int(y)}")
    audit("tap", {"label": label, "x": x, "y": y}, True, r or "sent", int((time.time() - t0) * 1000))
    return {"ok": True, "at": [x, y], "target": label, "raw": r}


def _hint() -> str:
    try:
        return "front app: " + adb_text("dumpsys window | grep -m1 mFocusedApp")
    except Exception:
        return "screen unreadable"


@app.tool()
def swipe(direction: str = "up", x: int = 540, y: int = 1200, distance: int = 600, duration_ms: int = 260) -> str:
    """up/down/left/right from (x,y), or two points via swipe_to."""
    need("act")
    t0 = time.time()
    vec = {"up": (0, -distance), "down": (0, distance), "left": (-distance, 0), "right": (distance, 0)}
    if direction not in vec:
        raise ValueError(f"direction must be one of {sorted(vec)}")
    dx, dy = vec[direction]
    r = adb_text(f"input swipe {int(x)} {int(y)} {int(x + dx)} {int(y + dy)} {int(duration_ms)}")
    audit("swipe", {"d": direction}, True, r or "sent", int((time.time() - t0) * 1000))
    return r or "sent"


@app.tool()
def swipe_to(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 260) -> str:
    need("act")
    t0 = time.time()
    r = adb_text(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}")
    audit("swipe_to", {"a": [x1, y1], "b": [x2, y2]}, True, r or "sent", int((time.time() - t0) * 1000))
    return r or "sent"


@app.tool()
def type_text(text: str, tap_first: str = "") -> str:
    """Type into the focused field. tap_first names a field to focus first."""
    need("act")
    t0 = time.time()
    acquire("type_text")
    if tap_first:
        tap(text=tap_first)
    safe = " ".join(text.split())
    r = adb_text(f"input text {shlex.quote(safe)}")
    audit("type_text", {"len": len(text)}, True, r or "sent", int((time.time() - t0) * 1000))
    return r or "sent"


@app.tool()
def key(event: str = "BACK") -> str:
    """KEYCODE_* by name: BACK HOME APP_SWITCH ENTER MENU SEARCH DELETE POWER
    VOLUME_UP VOLUME_DOWN, or a numeric code."""
    need("act")
    t0 = time.time()
    code = event if event.isdigit() else f"KEYCODE_{event.upper()}"
    r = adb_text(f"input keyevent {code}")
    audit("key", {"code": code}, True, r or "sent", int((time.time() - t0) * 1000))
    return r or "sent"


@app.tool()
def app_start(target: str, wait: bool = True) -> dict:
    """Launch by package (monkey) or explicit 'pkg/.Activity'. Reports the
    focused window after, which is how you know it landed."""
    need("act")
    t0 = time.time()
    acquire("app_start")
    if "/" in target:
        cmd = f"am start -n {shlex.quote(target)}"
    else:
        cmd = f"monkey -p {shlex.quote(target)} -c android.intent.category.LAUNCHER 1"
    r = adb_text(cmd)
    landed = ""
    if wait:
        time.sleep(1.2)
        try:
            landed = adb_text("dumpsys window | grep -m1 mFocusedApp")
        except Exception:
            landed = "unreadable"
    audit("app_start", {"t": target}, True, landed, int((time.time() - t0) * 1000))
    return {"sent": r, "focused": landed}


@app.tool()
def app_stop(package: str) -> str:
    need("act")
    t0 = time.time()
    r = adb_text(f"am force-stop {shlex.quote(package)}")
    audit("app_stop", {"p": package}, True, r or "ok", int((time.time() - t0) * 1000))
    return r or "ok"


@app.tool()
def shell(command: str, confirm: bool = False) -> str:
    """adb shell as uid=2000. read/act tokens get it read-only: any command
    that writes is filtered by the same tiers and confirm rules as everything
    else. admin tokens get it whole."""
    need("read")
    cmd = " ".join(command.split())
    if CTX.tier == "read":
        verdict = classify(cmd)
        if verdict == "blocked":
            return f"blocked: {cmd}"
        if verdict != "free" and not confirm:
            return f"needs confirm=true: {cmd}"
        writes = ("input ", "am ", "pm ", "svc ", "settings put", "ime ", "cmd ")
        if cmd.startswith(writes) and CTX.tier == "read":
            return f"read tier cannot run '{cmd}'; ask for act"
    return guarded("shell", cmd, confirm, {"cmd": cmd}, lambda: adb_text(cmd))


@app.tool()
def install(apk_path: str, confirm: bool = False, reinstall: bool = True) -> str:
    """Install an APK already on the phone (Termux or /sdcard path). admin tier."""
    need("admin")
    cmd = "install" + (" -r" if reinstall else "") + f" {shlex.quote(apk_path)}"
    return guarded("install", cmd, confirm, {"apk": apk_path}, lambda: adb_text(f"cmd package {cmd}", timeout=120))


@app.tool()
def file_push(src: str, dest: str, confirm: bool = False) -> str:
    """Copy from Termux home to a device path. To move a file from llmmaster,
    scp it to the phone first, then push. admin tier."""
    need("admin")
    return guarded("file_push", f"push {src} {dest}", confirm, {"src": src, "dest": dest}, lambda: adb_text(f"adb -s {SERIAL} push {shlex.quote(src)} {shlex.quote(dest)}", timeout=180))


@app.tool()
def file_pull(src: str, dest: str = "/data/data/com.termux/files/home/pulled", confirm: bool = True) -> str:
    """Copy a device path into Termux home. admin tier."""
    need("admin")
    return guarded(
        "file_pull",
        f"pull {src} {dest}",
        confirm,
        {"src": src, "dest": dest},
        lambda: adb_text(f"adb -s {SERIAL} pull {shlex.quote(src)} {shlex.quote(dest)}", timeout=180),
    )


@app.tool()
def settings_read(uri: str = "system", key: str = "") -> str:
    need("read")
    scope = uri if uri in ("system", "secure", "global", "dbinfo") else "system"
    cmd = f"settings get {scope} {shlex.quote(key)}" if key else f"settings list {scope} | head -120"
    return _call(lambda: adb_text(cmd), "settings_read", {"s": scope, "k": key}, time.time())


@app.tool()
def settings_write(uri: str, key: str, value: str, confirm: bool = False) -> str:
    """settings put. Anything that changes a default app or role is blocked."""
    need("admin")
    scope = uri if uri in ("system", "secure", "global") else "system"
    cmd = f"settings put {scope} {key} {value}"
    return guarded("settings_write", cmd, confirm, {"s": scope, "k": key, "v": value}, lambda: adb_text(cmd))


@app.tool()
def grant(package: str, permission: str, confirm: bool = True) -> str:
    """pm grant, e.g. Termux WRITE_SECURE_SETTINGS so boot scripts can reach
    settings. admin tier."""
    need("admin")
    cmd = f"pm grant {package} {permission}"
    return guarded("grant", cmd, confirm, {"p": package, "perm": permission}, lambda: adb_text(cmd))


@app.tool()
def history(limit: int = 20, who: str = "") -> list[dict]:
    """Recent audit rows, newest first. What happened to the phone and by whom."""
    need("read")
    files = sorted(AUDIT_DIR.glob("audit-*.jsonl"))
    rows: list[dict] = []
    for f in reversed(files):
        for line in reversed(f.read_text().splitlines()):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if who and r.get("who") != who:
                continue
            rows.append(r)
            if len(rows) >= limit:
                return rows
    return rows


identify()

if __name__ == "__main__":
    app.run()
