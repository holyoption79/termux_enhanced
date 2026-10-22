# termux_enhanced

A Termux build and setup for a Galaxy S24 Ultra (SM-S928N, Android 16, One UI 8.5), made for
research use over Tailscale. Its main goal: **a stuck session can always be closed from
the GUI**, even when the soft keyboard is unresponsive.

## What it changes

**App patches** (applied to upstream `termux/termux-app`, see `patches/`):

- **Close button per session** in the sessions drawer. Tapping ✕ asks for confirmation,
  then SIGKILLs the session's foreground process group and its shell, and removes the
  session from the list. This works when the session is stuck in a full-screen program.
- **Larger ☰ (drawer) and ⌨ (keyboard toggle) extra keys.** These are the way out when
  the keyboard is stuck, so they get 1.5× the column width and a 1.8× symbol.
- **Own signing key**, shared with a rebuilt **Termux:Boot**, so the boot add-on can run
  scripts in Termux's user id and start sshd after a reboot.

**Phone configuration** (`phone/`):

| File | Installs to | Purpose |
|---|---|---|
| `termux.properties` | `~/.termux/` | One row of extra keys (`☰ ⌨ Esc ↹ Ctrl Alt ← ↓ ↑ → ^C`), dark drawer, bar cursor, small margins |
| `colors.properties` | `~/.termux/` | AMOLED black background with the Catppuccin Mocha palette |
| `bashrc.snippet` | append to `~/.bashrc` | Start sshd when a shell opens and it is not running; `trap "exit 0" USR1` for `sk` |
| `boot/10-sshd` | `~/.termux/boot/` | Termux:Boot: take a wakelock and start sshd at boot |
| `bin/sk` | `$PREFIX/bin/` | Close sessions from the command line or a `dialog` menu |
| `bin/s24-ai-run` | `$PREFIX/bin/` | Run `claude`, `codex` and `opencode` inside proot Debian (symlink each name to it) |

Swipe up on an extra key for its popup: `Esc`→`~`, `↹`→`|`, arrows→`HOME`/`PGDN`/`PGUP`/`END`,
`^C`→`^D`.

The font is [D2Coding](https://github.com/naver/d2codingfont) (SIL OFL). Hangul is exactly two
cells wide, so mixed Korean text lines up in TUIs. It is not included here; install it with:

```sh
curl -fsSLo d2.zip https://github.com/naver/d2codingfont/releases/download/VER1.3.2/D2Coding-Ver1.3.2-20180524.zip
unzip -p d2.zip D2Coding/D2Coding-Ver1.3.2-20180524.ttf > ~/.termux/font.ttf
termux-reload-settings
```

`termux.properties` is read as ISO-8859-1, so the symbols in it are written as `\uXXXX` escapes.
Keep the file ASCII-only when you edit it.

## Build

On a Linux host with JDK 21 and the Android SDK (`~/android-sdk`, build-tools 34–36, platforms
34 and 36):

```sh
git clone https://github.com/holyoption79/termux_enhanced && cd termux_enhanced
git clone https://github.com/termux/termux-app && git -C termux-app checkout 8629e632fcb95da272221be327db653fb24befe9
git clone https://github.com/termux/termux-boot && git -C termux-boot checkout a8493bd6ba016bc370af34aa65fcbe065cc00ced
mkdir keystore
keytool -genkeypair -keystore keystore/s24-termux.jks -alias s24 -keyalg RSA -keysize 4096 -validity 36500
echo 'S24_STORE_PASSWORD=...' > keystore/signing.env && chmod 600 keystore/*
scripts/build.sh    # -> out/termux-app_*_arm64-v8a.apk, out/termux-boot-app_*.apk
```

The patch scripts check that each anchor appears exactly once, so a changed upstream fails
loudly instead of being patched in the wrong place. `patches/termux-app.diff` is the resulting
diff, for reference.

**Keep the signing key.** Every later update must be signed with the same key, or Android refuses
it and the only way forward is uninstalling, which deletes the Termux home. `keystore/`, `out/`
and the upstream checkouts are git-ignored.

## Install

Switching from a Play Store or F-Droid Termux means uninstalling it first, because the signature
differs, and that deletes the Termux home. Back it up before you do. Then:

```sh
adb install -r out/termux-app_apt-android-7-debug_arm64-v8a.apk
adb install -r out/termux-boot-app_v0.8.1+debug.apk   # then open Termux:Boot once
```

Later updates install over the existing app and keep its data. Open Termux sessions are closed by
the update. `scripts/restore-phone.sh` restores a home backup into a fresh install (see its header).

## Testing

`scripts/device-tc.sh` drives the real UI over adb and checks the results over SSH. It covers:

- every extra key and popup sends the right bytes (captured with `cat -A`);
- `^C` interrupts a foreground job;
- `⌨` hides and shows the soft keyboard;
- the drawer's ✕ asks for confirmation, kills a stuck session's process and removes the session;
- exactly one sshd is running and the pid file the `.bashrc` guard reads is live.

```sh
ADB=adb SSH_HOST=s24 scripts/device-tc.sh   # phone unlocked, Termux in front, drawer closed
```

Last run on 2026-10-22, SM-S928N: 11/11 checks passed. The landscape layout and the absence of
settings parse errors in logcat were checked by hand. Starting sshd at boot through Termux:Boot
has not yet been tested with a reboot.
