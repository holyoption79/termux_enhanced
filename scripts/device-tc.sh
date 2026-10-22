#!/usr/bin/env bash
# Device tests for the S24 Termux build: drive the real UI over adb and check
# the results over SSH. Run on the PC with the phone attached by USB:
#   scripts/device-tc.sh
# Before running: phone unlocked, Termux in front at a shell prompt in the
# home directory, sessions drawer closed. Don't touch the phone while it runs.
# Env: ADB (default adb), SSH_HOST (default s24).
# Prints PASS/FAIL per check; the exit status is the number of failures.
set -u

ADB=${ADB:-adb}
HOST=${SSH_HOST:-s24}
UI=$(mktemp)
trap 'rm -f "$UI"' EXIT
fails=0

adb_sh() { MSYS_NO_PATHCONV=1 "$ADB" shell "$@"; }
phone() { ssh -n -o ConnectTimeout=8 "$HOST" "$1"; }
check() { if [ "$2" = ok ]; then echo "PASS  $1"; else echo "FAIL  $1${3:+: $3}"; fails=$((fails + 1)); fi; }

# Refresh the UI tree, one <node> per line.
dump() {
	adb_sh uiautomator dump /sdcard/device-tc.xml >/dev/null 2>&1
	MSYS_NO_PATHCONV=1 "$ADB" exec-out cat /sdcard/device-tc.xml | grep -o '<node [^>]*>' > "$UI"
	adb_sh rm -f /sdcard/device-tc.xml
}
count() { grep -cF "$1" "$UI"; }
# bounds MATCH [N] -> "left top right bottom" of the Nth node containing MATCH
bounds() {
	grep -F "$1" "$UI" | sed -n "${2:-1}p" |
		sed -E 's/.*bounds="\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\]".*/\1 \2 \3 \4/'
}
tap() {
	local b; b=$(bounds "$@")
	[ -n "$b" ] || { echo "  no node: $1" >&2; return 1; }
	set -- $b
	adb_sh input tap $((($1 + $3) / 2)) $((($2 + $4) / 2)); sleep 0.5
}
# Swipe up out of an extra key, which fires its popup key.
swipe_up() {
	local b x; b=$(bounds "$@")
	[ -n "$b" ] || { echo "  no node: $1" >&2; return 1; }
	set -- $b; x=$((($1 + $3) / 2))
	adb_sh input swipe $x $((($2 + $4) / 2)) $x $(($2 - 120)) 300; sleep 0.5
}
type_line() { adb_sh input text "'${1// /%s}'"; adb_sh input keyevent 66; sleep 1; }
ime_shown() { adb_sh dumpsys input_method | grep -q 'mInputShown=true'; }
# pids_of ARGV... -> pids of phone processes whose command line is exactly ARGV
pids_of() { phone 'ps -eo pid=,args=' | awk -v want="$*" '{ p = $1; sub(/^ *[0-9]+ +/, ""); if ($0 == want) print p }'; }

adb_sh dumpsys window | grep -q 'mCurrentFocus=.*com\.termux/' || { echo "Termux is not in front" >&2; exit 99; }
dump
if ! ime_shown; then tap 'text="⌨"'; dump; fi

# T1: every extra key and popup sends the expected bytes, captured with cat -A.
phone 'rm -f ~/.device-tc-keys'
type_line 'cat -A >~/.device-tc-keys'
for k in Esc ↹ ← ↓ ↑ →; do tap "text=\"$k\""; done
tap 'text="Ctrl"'; adb_sh input text a; sleep 0.3
tap 'text="Alt"'; adb_sh input text x; sleep 0.3
for k in Esc ↹ ← ↓ ↑ →; do swipe_up "text=\"$k\""; done
adb_sh input keyevent 66; sleep 0.5
swipe_up 'text="^C"'; sleep 1 # popup ^D: EOF on an empty line ends cat
want='^[^I^[[D^[[B^[[A^[[C^A^[x~|^[[H^[[6~^[[5~^[[F$'
got=$(phone 'cat ~/.device-tc-keys; rm -f ~/.device-tc-keys')
[ "$got" = "$want" ] && r=ok || r=bad
check "T1 keys and popups send the right bytes" $r "got '$got', want '$want'"
[ -z "$(pids_of cat -A)" ] && r=ok || r=bad
check "T1 ^D popup ends cat" $r

# T2: ^C interrupts the foreground job.
type_line 'sleep 4242'
[ -n "$(pids_of sleep 4242)" ] || echo "  setup: sleep 4242 did not start" >&2
tap 'text="^C"'; sleep 0.8
[ -z "$(pids_of sleep 4242)" ] && r=ok || r=bad
check "T2 ^C interrupts the foreground job" $r

# T3: the keyboard key hides and shows the soft keyboard (the row moves, so re-read it).
dump; tap 'text="⌨"'; sleep 0.8
ime_shown && r=bad || r=ok
check "T3 keyboard key hides the keyboard" $r
dump; tap 'text="⌨"'; sleep 0.8
ime_shown && r=ok || r=bad
check "T3 keyboard key shows the keyboard" $r

# T4: the drawer's close button ends a stuck session and removes it from the list.
dump; tap 'text="☰"'; sleep 1; dump
before=$(count 'id/session_title')
[ "$before" -ge 1 ] && [ "$(count 'id/session_close')" = "$before" ] && r=ok || r=bad
check "T4 drawer lists every session with a close button" $r "rows $before"
tap 'id/new_session_button'; sleep 2
type_line 'sleep 4343'
[ -n "$(pids_of sleep 4343)" ] || echo "  setup: sleep 4343 did not start" >&2
dump; tap 'text="☰"'; sleep 1; dump
tap 'id/session_close' $((before + 1)); sleep 1; dump
[ "$(count 'Close session')" = 1 ] && r=ok || r=bad
check "T4 close asks for confirmation" $r
tap 'resource-id="android:id/button1"'; sleep 1.5
[ -z "$(pids_of sleep 4343)" ] && r=ok || r=bad
check "T4 stuck process is killed" $r
dump
[ "$(count 'id/session_title')" = "$before" ] && r=ok || r=bad
check "T4 session is removed from the list" $r
# Close the drawer by tapping the scrim at the right edge of the screen.
set -- $(bounds '<node ' 1)
adb_sh input tap $(($3 - 40)) $(($4 / 4)); sleep 0.5

# T5: exactly one SSH listener, and the pid file the .bashrc guard reads is live.
n=$(phone 'ps -eo args=' | grep -cE '^(/data/data/com\.termux/files/usr/bin/)?sshd$')
[ "$n" = 1 ] && r=ok || r=bad
check "T5 one sshd listener" $r "found $n"
[ "$(phone 'kill -0 "$(cat "$PREFIX/var/run/sshd.pid")" && echo yes')" = yes ] && r=ok || r=bad
check "T5 sshd pid file is live" $r

echo "$fails failure(s)"
exit "$fails"
