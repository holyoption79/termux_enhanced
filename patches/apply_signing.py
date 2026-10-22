"""Make a Termux app module sign its debug build with the private s24 key.

Usage: python3 apply_signing.py path/to/app/build.gradle
The upstream public test key stays as the fallback, so build.sh must always
supply the s24 properties (it checks the signer of every APK it produces).
"""
import re
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
if "s24StoreFile" in text:
    sys.exit(0)  # already applied

rules = [
    (r"storeFile file\('(testkey_untrusted\.jks)'\)", r"storeFile file(project.findProperty('s24StoreFile') ?: '\1')"),
    (r"keyAlias '([^']*)'", r"keyAlias project.findProperty('s24KeyAlias') ?: '\1'"),
    (r"storePassword '([^']*)'", r"storePassword project.findProperty('s24StorePassword') ?: '\1'"),
    (r"keyPassword '([^']*)'", r"keyPassword project.findProperty('s24KeyPassword') ?: '\1'"),
]
for pattern, repl in rules:
    text, n = re.subn(pattern, repl, text, count=1)
    if n != 1:
        sys.exit(f"{path}: no match for {pattern}")
open(path, "w", encoding="utf-8").write(text)
print(f"{path}: signing patched")
