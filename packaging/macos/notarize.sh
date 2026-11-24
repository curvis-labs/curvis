#!/usr/bin/env bash
# Sign, notarize and staple the .dmg (whose Curvis.app sign.sh has signed).
#
#     packaging/macos/notarize.sh dist/Curvis-1.0.0-macos-arm64.dmg
#
# Needs, besides the two certificate variables of sign.sh:
#   APPLE_ID            the Apple ID of the developer account
#   APPLE_TEAM_ID       the 10-character team ID
#   APPLE_APP_PASSWORD  an app-specific password for that Apple ID (appleid.apple.com)
set -euo pipefail

dmg="$1"
: "${MACOS_CERTIFICATE_P12:?not set}" "${MACOS_CERTIFICATE_PASSWORD:?not set}"
: "${APPLE_ID:?not set}" "${APPLE_TEAM_ID:?not set}" "${APPLE_APP_PASSWORD:?not set}"

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
security unlock-keychain -p "$keychain_password" "$keychain"
security import "$work/cert.p12" -k "$keychain" -P "$MACOS_CERTIFICATE_PASSWORD" \
  -T /usr/bin/codesign
security set-key-partition-list -S apple-tool:,apple: -s -k "$keychain_password" "$keychain" >/dev/null
identity="$(security find-identity -v -p codesigning "$keychain" \
  | awk -F'"' '/Developer ID Application/ {print $2; exit}')"

codesign --force --timestamp --keychain "$keychain" --sign "$identity" "$dmg"

# --wait returns when Apple has finished; on a rejection print Apple's log.
if ! xcrun notarytool submit "$dmg" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
  --password "$APPLE_APP_PASSWORD" --wait --timeout 30m --output-format json > "$work/result.json"; then
  cat "$work/result.json" >&2
fi
cat "$work/result.json"
status="$(plutil -extract status raw "$work/result.json" 2>/dev/null || echo unknown)"
if [[ "$status" != "Accepted" ]]; then
  id="$(plutil -extract id raw "$work/result.json" 2>/dev/null || true)"
  if [[ -n "$id" ]]; then
    xcrun notarytool log "$id" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
      --password "$APPLE_APP_PASSWORD" >&2 || true
  fi
  echo "Notarization failed: $status" >&2
  exit 1
fi

xcrun stapler staple "$dmg"
xcrun stapler validate "$dmg"
spctl --assess --type open --context context:primary-signature --verbose=2 "$dmg"
