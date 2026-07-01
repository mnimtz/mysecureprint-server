#!/usr/bin/env bash
# ============================================================
# make-dmg.sh — DMG-Image aus dist/PrintixSend.app
# ============================================================
# Ergebnis: dist/PrintixSend-<VERSION>.dmg mit
#   - PrintixSend.app
#   - Applications-Symlink (Drag-to-Install)
#   - README.txt (Kurzanleitung)
# ------------------------------------------------------------

set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="${VERSION:-0.1.0}"
APP="dist/PrintixSend.app"
DMG="dist/PrintixSend-$VERSION.dmg"
STAGE="dist/dmg-stage"

[[ -d "$APP" ]] || { echo "✖ $APP fehlt — erst make-app-bundle.sh ausführen." >&2; exit 1; }

rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

cat > "$STAGE/README.txt" <<'EOF'
Printix Send — Installation
============================

1. PrintixSend.app in den Ordner "Applications" ziehen
2. App aus dem Applications-Ordner starten
3. Beim ersten Start Rechtsklick → Öffnen (wegen Gatekeeper,
   falls unsigned)
4. Unter Printix Send → Konfiguration… die Server-URL eintragen
5. Anmelden und loslegen

Rechtsklick auf eine Datei → Quick Actions → "Printix Send — <Ziel>"

Logs: ~/Library/Logs/PrintixSend/
Config: ~/Library/Application Support/PrintixSend/config.json
EOF

echo "▶ hdiutil create $DMG"
hdiutil create -volname "Printix Send $VERSION" \
               -srcfolder "$STAGE" \
               -ov -format UDZO \
               "$DMG"

rm -rf "$STAGE"
echo "✓ DMG fertig: $DMG"
du -sh "$DMG"
