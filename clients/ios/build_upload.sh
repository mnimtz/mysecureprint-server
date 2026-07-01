#!/bin/bash
# MySecurePrint — Phase 1 (Binary): Build + Sign + Upload zu App Store Connect.
#
# Naming-Clarity: das hier ist NICHT TestFlight-only — der Upload landet als
# Build in ASC und ist sowohl fuer TestFlight als auch fuer die App-Store-
# Version verwendbar. Phase 2 (asc_submit.py) attached den Build dann an
# die App-Store-Version 1.0.0 und submitted ihn DIREKT zum App-Store-Review.
# TestFlight wird in unserem Flow uebersprungen.
#
# Setup (einmalig):
#   export ASC_KEY_ID=Q77537L95P
#   export ASC_ISSUER_ID=221f1947-cc3d-45a2-84d0-bb01d66de2b3
#   .p8 liegt unter ~/.appstoreconnect/private_keys/AuthKey_Q77537L95P.p8
#
# Voraussetzungen (User-Setup im Web-Portal):
#   1. Bundle-IDs in Apple Developer Portal registriert
#      (de.nimtz.mysecureprint + de.nimtz.mysecureprint.share)
#   2. App-Record in App Store Connect angelegt
#      Apps → "+" → New App: Name MySecurePrint, Bundle de.nimtz.mysecureprint,
#      Primary Language Deutsch, SKU mysecureprint-ios-001
#
# Dann:  ./build_upload.sh
set -euo pipefail
cd "$(dirname "$0")"

SCHEME=MySecurePrint
PROJECT=MySecurePrint.xcodeproj
ARCHIVE=build/MySecurePrint.xcarchive
EXPORT=build/export

# Build-Nummer automatisch hochsetzen, damit jeder Upload unique ist.
BUILD=$(date +%Y%m%d%H%M)
echo "▶︎ Build number: $BUILD"

xcodebuild -project "$PROJECT" -scheme "$SCHEME" \
  -configuration Release -destination 'generic/platform=iOS' \
  -archivePath "$ARCHIVE" \
  -allowProvisioningUpdates \
  -authenticationKeyID "${ASC_KEY_ID:-Q77537L95P}" \
  -authenticationKeyIssuerID "${ASC_ISSUER_ID:-221f1947-cc3d-45a2-84d0-bb01d66de2b3}" \
  -authenticationKeyPath "$HOME/.appstoreconnect/private_keys/AuthKey_${ASC_KEY_ID:-Q77537L95P}.p8" \
  CURRENT_PROJECT_VERSION="$BUILD" \
  clean archive

xcodebuild -exportArchive \
  -archivePath "$ARCHIVE" \
  -exportOptionsPlist ExportOptions.plist \
  -exportPath "$EXPORT" \
  -allowProvisioningUpdates \
  -authenticationKeyID "${ASC_KEY_ID:-Q77537L95P}" \
  -authenticationKeyIssuerID "${ASC_ISSUER_ID:-221f1947-cc3d-45a2-84d0-bb01d66de2b3}" \
  -authenticationKeyPath "$HOME/.appstoreconnect/private_keys/AuthKey_${ASC_KEY_ID:-Q77537L95P}.p8"

IPA=$(ls "$EXPORT"/*.ipa | head -1)
echo "▶︎ Gebaut: $IPA"

if [[ -n "${ASC_KEY_ID:-}" && -n "${ASC_ISSUER_ID:-}" ]]; then
  echo "▶︎ Upload an App Store Connect…"
  xcrun altool --upload-app -f "$IPA" -t ios \
    --apiKey "$ASC_KEY_ID" --apiIssuer "$ASC_ISSUER_ID"
  echo "✓ Upload OK. Build ist nach Apple-Processing in ASC verfuegbar (~5-15 min)."
  echo "  Naechster Schritt: python3 asc_submit.py --version 1.0.0 --wait-build"
else
  echo "ℹ︎ IPA exportiert aber NICHT hochgeladen."
  echo "  Setze ASC_KEY_ID + ASC_ISSUER_ID — oder drag $IPA in Transporter.app."
fi
