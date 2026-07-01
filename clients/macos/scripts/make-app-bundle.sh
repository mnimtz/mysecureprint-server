#!/usr/bin/env bash
# ============================================================
# make-app-bundle.sh — .app-Bundle aus den Swift-Build-Binaries
# ============================================================
# Erzeugt dist/PrintixSend.app mit folgender Struktur:
#
#   PrintixSend.app/
#     Contents/
#       Info.plist
#       MacOS/
#         PrintixSendApp     (Hauptprozess, wird vom Finder gestartet)
#         printix-send-cli   (wird von Quick-Actions aufgerufen)
#       Resources/
#         AppIcon.icns       (optional)
#
# Umgebungsvariablen:
#   VERSION       — Version-String, default 0.1.0
#   CODESIGN_ID   — "Developer ID Application: Firma (TEAMID)"
#                   Wenn gesetzt, wird signiert (Gatekeeper-ready).
# ------------------------------------------------------------

set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="${VERSION:-0.1.0}"
APP_NAME="PrintixSend"
BUNDLE_ID="de.printix.send"
BIN_DIR=".build/apple/Products/Release"
DIST="dist"
APP="$DIST/$APP_NAME.app"

if [[ ! -f "$BIN_DIR/PrintixSendApp" || ! -f "$BIN_DIR/printix-send-cli" ]]; then
    echo "✖ Bitte zuerst scripts/build-universal.sh ausführen." >&2
    exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Binaries einkopieren
cp "$BIN_DIR/PrintixSendApp"     "$APP/Contents/MacOS/PrintixSendApp"
cp "$BIN_DIR/printix-send-cli"   "$APP/Contents/MacOS/printix-send-cli"
chmod +x "$APP/Contents/MacOS/"*

# App-Icon (optional, wenn Resources/AppIcon.icns vorhanden)
if [[ -f "Resources/AppIcon.icns" ]]; then
    cp "Resources/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
fi

# Info.plist — LSUIElement=true → kein Dock-Icon (Menu-Bar-only)
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key><string>de</string>
    <key>CFBundleExecutable</key><string>PrintixSendApp</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleName</key><string>$APP_NAME</string>
    <key>CFBundleDisplayName</key><string>Printix Send</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>LSUIElement</key><true/>
    <key>NSHumanReadableCopyright</key><string>© Printix Send</string>
    <key>NSAppTransportSecurity</key>
    <dict><key>NSAllowsArbitraryLoads</key><true/></dict>
    <key>CFBundleIconFile</key><string>AppIcon</string>
</dict>
</plist>
EOF

# Optional: Code-Signing für Gatekeeper
if [[ -n "${CODESIGN_ID:-}" ]]; then
    echo "▶ Codesign mit: $CODESIGN_ID"
    codesign --force --options runtime --timestamp \
             --sign "$CODESIGN_ID" \
             "$APP/Contents/MacOS/printix-send-cli"
    codesign --force --options runtime --timestamp \
             --sign "$CODESIGN_ID" \
             "$APP/Contents/MacOS/PrintixSendApp"
    codesign --force --options runtime --timestamp \
             --sign "$CODESIGN_ID" \
             "$APP"
    echo "✓ Signiert. (Notarization separat mit scripts/notarize.sh)"
else
    echo "ℹ Kein CODESIGN_ID — Bundle ist unsigniert (Gatekeeper blockt!)"
fi

echo "✓ Bundle: $APP"
du -sh "$APP"
