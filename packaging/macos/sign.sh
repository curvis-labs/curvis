#!/usr/bin/env bash
# Sign Curvis.app with a Developer ID Application certificate and the hardened runtime.
#
#     packaging/macos/sign.sh dist/Curvis.app
#
# Needs these environment variables (GitHub secrets in installers.yml):
#   MACOS_CERTIFICATE_P12       base64 of the Developer ID Application certificate (.p12)
#   MACOS_CERTIFICATE_PASSWORD  the .p12 password
#
# The certificate goes into a temporary keychain that is deleted on exit. Every Mach-O
# file is signed from the inside out (libraries first, then the app), because
# `codesign --deep` does not sign PyInstaller's nested libraries reliably.
set -euo pipefail

app="$1"
entitlements="$(cd "$(dirname "$0")" && pwd)/entitlements.plist"
: "${MACOS_CERTIFICATE_P12:?not set}" "${MACOS_CERTIFICATE_PASSWORD:?not set}"

work="$(mktemp -d)"
keychain="$work/signing.keychain-db"
keychain_password="$(uuidgen)"
cleanup() {
  security delete-keychain "$keychain" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

echo "$MACOS_CERTIFICATE_P12" | base64 --decode > "$work/cert.p12"
security create-keychain -p "$keychain_password" "$keychain"
security set-keychain-settings -lut 21600 "$keychain"
security unlock-keychain -p "$keychain_password" "$keychain"
security import "$work/cert.p12" -k "$keychain" -P "$MACOS_CERTIFICATE_PASSWORD" \
  -T /usr/bin/codesign
security set-key-partition-list -S apple-tool:,apple: -s -k "$keychain_password" "$keychain" >/dev/null
security list-keychains -d user -s "$keychain" $(security list-keychains -d user | tr -d '"')

identity="$(security find-identity -v -p codesigning "$keychain" \
  | awk -F'"' '/Developer ID Application/ {print $2; exit}')"
if [[ -z "$identity" ]]; then
  echo "No Developer ID Application identity in the certificate" >&2
  exit 1
fi
echo "Signing with: $identity"

sign() {
  codesign --force --timestamp --options runtime --entitlements "$entitlements" \
    --keychain "$keychain" --sign "$identity" "$@"
}

# Libraries and executables inside the bundle, deepest first.
find "$app/Contents" -type f -print0 \
  | while IFS= read -r -d '' file; do
      if file -b "$file" | grep -q "Mach-O"; then
        printf '%s\t%s\n' "$(awk -F/ '{print NF}' <<<"$file")" "$file"
      fi
    done \
  | sort -rn | cut -f2- \
  | while IFS= read -r file; do sign "$file"; done

sign "$app"
codesign --verify --deep --strict --verbose=2 "$app"
