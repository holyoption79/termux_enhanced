#!/system/bin/sh
# Restore a fresh S24 Termux (custom build) from a backup made with:
#   tar -czf - . -C $PREFIX/etc ssh -C $PREFIX/bin sk s24-ai-run   (run in ~)
# Run as the Termux user, e.g. via:  adb shell run-as com.termux sh restore-phone.sh
# Expects the backup at /data/local/tmp/s24-restore.tgz.
set -e

export PREFIX=/data/data/com.termux/files/usr
export HOME=/data/data/com.termux/files/home
export PATH=$PREFIX/bin TMPDIR=$PREFIX/tmp LANG=en_US.UTF-8
for lib in libtermux-exec-ld-preload.so libtermux-exec.so; do
	[ -f "$PREFIX/lib/$lib" ] && { export LD_PRELOAD=$PREFIX/lib/$lib; break; }
done
export DEBIAN_FRONTEND=noninteractive
cd "$HOME"

echo "== packages"
apt-get update -q >/dev/null
apt-get install -y -q -o Dpkg::Options::=--force-confold openssh proot-distro dialog git >/dev/null
echo "   openssh $(dpkg-query -W -f='${Version}' openssh), proot-distro $(dpkg-query -W -f='${Version}' proot-distro)"

echo "== unpack backup"
stage=$TMPDIR/s24-restore
rm -rf "$stage" && mkdir -p "$stage"
tar -xzf /data/local/tmp/s24-restore.tgz -C "$stage"

echo "== home"
(cd "$stage" && tar -cf - --exclude=./ssh --exclude=./sk --exclude=./s24-ai-run .) | tar -xf - -C "$HOME"
chmod 700 "$HOME/.ssh"
chmod 600 "$HOME"/.ssh/* 2>/dev/null || true
chmod 644 "$HOME/.ssh/id_ed25519.pub" 2>/dev/null || true

echo "== sshd host keys and config"
cp -p "$stage"/ssh/ssh_host_* "$stage/ssh/sshd_config" "$PREFIX/etc/ssh/"
chmod 600 "$PREFIX"/etc/ssh/ssh_host_*_key

echo "== scripts"
cp -p "$stage/sk" "$stage/s24-ai-run" "$PREFIX/bin/"
chmod 755 "$PREFIX/bin/sk" "$PREFIX/bin/s24-ai-run"
for t in claude codex opencode; do ln -sfn s24-ai-run "$PREFIX/bin/$t"; done

echo "== Termux:Boot"
mkdir -p "$HOME/.termux/boot"
cat > "$HOME/.termux/boot/10-sshd" <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
# Keep the SSH server up from boot: hold a wakelock, then start sshd.
termux-wake-lock
sshd
EOF
chmod 700 "$HOME/.termux/boot/10-sshd"

rm -rf "$stage"
echo "restore done"
