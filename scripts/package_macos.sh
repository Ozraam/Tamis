#!/usr/bin/env bash
# Wrap dist/Tamis.app in dist/Tamis-<version>.dmg (and a .zip), for a release.
# Run scripts/build_app.sh first.
#
# Without TAMIS_SIGN_IDENTITY, the app keeps the ad hoc signature build_app.sh gave it:
# it isn't notarized, so Gatekeeper blocks it the first time it is opened (see "Releases"
# in the README for how to open it anyway). With an Apple Developer account:
#
#   TAMIS_SIGN_IDENTITY   "Developer ID Application: Name (TEAMID)", in the keychain
#   TAMIS_NOTARY_PROFILE  a keychain profile made with `xcrun notarytool store-credentials`, or else
#   TAMIS_NOTARY_KEY, TAMIS_NOTARY_KEY_ID, TAMIS_NOTARY_ISSUER  an App Store Connect API key (.p8 path, ids)
#
# the app is signed with that Developer ID, and the DMG notarized and stapled if notary credentials are set.
set -euo pipefail
cd "$(dirname "$0")/.."

app=dist/Tamis.app
if [[ ! -d "$app" ]]; then
    echo "$app not found: run scripts/build_app.sh first" >&2
    exit 1
fi
version=$(plutil -extract CFBundleShortVersionString raw "$app/Contents/Info.plist")
dmg="dist/Tamis-$version.dmg"
zip="dist/Tamis-$version.zip"
identity="${TAMIS_SIGN_IDENTITY:-}"

if [[ -n "$identity" ]]; then
    # Hardened runtime, required for notarization. Every binary inside is signed, then the bundle.
    sign() {
        codesign --force --timestamp --options runtime --entitlements scripts/entitlements.plist \
            --sign "$identity" "$@"
    }
    find "$app/Contents" -type f \( -name "*.dylib" -o -name "*.so" -o -perm -u+x \) -print0 |
        while IFS= read -r -d '' file; do
            if file "$file" | grep -q "Mach-O"; then
                sign "$file"
            fi
        done
    sign "$app"
fi
codesign --verify --deep --strict "$app"

staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
cp -R "$app" "$staging/"
ln -s /Applications "$staging/Applications"
rm -f "$dmg" "$zip"
hdiutil create -volname "Tamis $version" -srcfolder "$staging" -ov -format UDZO "$dmg"
ditto -c -k --keepParent "$app" "$zip"

if [[ -z "$identity" ]]; then
    echo "Packaged $dmg and $zip, signed ad hoc (not notarized)"
    exit 0
fi
codesign --force --timestamp --sign "$identity" "$dmg"
if [[ -n "${TAMIS_NOTARY_PROFILE:-}" ]]; then
    xcrun notarytool submit "$dmg" --keychain-profile "$TAMIS_NOTARY_PROFILE" --wait
elif [[ -n "${TAMIS_NOTARY_KEY:-}" ]]; then
    xcrun notarytool submit "$dmg" --key "$TAMIS_NOTARY_KEY" --key-id "$TAMIS_NOTARY_KEY_ID" \
        --issuer "$TAMIS_NOTARY_ISSUER" --wait
else
    echo "No notary credentials: $dmg is signed but not notarized" >&2
    exit 0
fi
xcrun stapler staple "$dmg"
spctl --assess --type open --context context:primary-signature --verbose "$dmg"
echo "Notarized $dmg"
