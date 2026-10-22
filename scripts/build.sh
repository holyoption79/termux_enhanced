#!/usr/bin/env bash
# Build the S24 Termux fork on a Linux host with the Android SDK:
#   termux-app  + a close button per session in the sessions drawer
#               + larger drawer and keyboard-toggle extra keys
#   termux-boot (same signing key, so it can share Termux's user id)
# Both are signed with the private key in keystore/; the build refuses to
# publish an APK whose signer does not match that key.
#
# Layout (this script lives in scripts/):
#   ../termux-app ../termux-boot   upstream checkouts
#   ../patches/                    apply_session_close.py, apply_extra_keys.py, apply_signing.py
#   ../keystore/                   s24-termux.jks, signing.env (S24_STORE_PASSWORD)
#   ../out/                        resulting APKs
set -euo pipefail
cd "$(dirname "$0")/.."

export ANDROID_HOME=${ANDROID_HOME:-$HOME/android-sdk}
export ANDROID_SDK_ROOT=$ANDROID_HOME
# shellcheck source=/dev/null
. keystore/signing.env
export ORG_GRADLE_PROJECT_s24StoreFile=$PWD/keystore/s24-termux.jks
export ORG_GRADLE_PROJECT_s24KeyAlias=s24
export ORG_GRADLE_PROJECT_s24StorePassword=$S24_STORE_PASSWORD
export ORG_GRADLE_PROJECT_s24KeyPassword=$S24_STORE_PASSWORD

# Patch from clean upstream sources every time, so an edited patch script never
# lands on top of its previous version. Local edits in the checkouts are discarded.
for repo in termux-app termux-boot; do
	git -C "$repo" checkout -q -- .
	python3 patches/apply_signing.py "$repo/app/build.gradle"
done
(cd termux-app && python3 ../patches/apply_session_close.py)
(cd termux-app && python3 ../patches/apply_extra_keys.py)

(cd termux-app && ./gradlew --no-daemon --console=plain assembleDebug)
(cd termux-boot && ./gradlew --no-daemon --console=plain assembleDebug)

rm -rf out && mkdir out
cp termux-app/app/build/outputs/apk/debug/termux-app_*arm64-v8a.apk out/
cp termux-boot/app/build/outputs/apk/debug/*.apk out/

apksigner=$(ls -d "$ANDROID_HOME"/build-tools/*/apksigner | sort -V | tail -1)
want=$(keytool -list -v -keystore keystore/s24-termux.jks -storepass "$S24_STORE_PASSWORD" -alias s24 |
	awk '/SHA256:/{print $2; exit}' | tr -d ':' | tr 'A-F' 'a-f')
for apk in out/*.apk; do
	got=$("$apksigner" verify --print-certs "$apk" | awk '/certificate SHA-256 digest/{print $NF; exit}')
	if [ "$got" != "$want" ]; then
		echo "signer mismatch for $apk: $got != $want" >&2
		exit 1
	fi
	echo "ok  $(basename "$apk")  signer sha256 ${got:0:16}…"
done
