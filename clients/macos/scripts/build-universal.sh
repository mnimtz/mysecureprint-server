#!/usr/bin/env bash
# ============================================================
# build-universal.sh — Universal-Binary-Build für Printix Send
# ============================================================
# Baut CLI und App als Release + Universal-Binary (arm64 + x86_64).
# Output landet unter .build/apple/Products/Release/.
# ------------------------------------------------------------

set -euo pipefail

cd "$(dirname "$0")/.."

echo "▶ swift build --arch arm64 -c release"
swift build --arch arm64  -c release

echo "▶ swift build --arch x86_64 -c release"
swift build --arch x86_64 -c release

# SPM's "--arch arm64 --arch x86_64" Combo-Mode macht auf CI-Runnern
# manchmal Ärger (git-describe exit 128). Wir bauen deshalb pro Arch
# einzeln und lipo'en am Ende manuell zur Universal-Binary zusammen.
echo "▶ lipo zu Universal-Binary"
OUT=".build/apple/Products/Release"
mkdir -p "$OUT"
for bin in PrintixSendApp printix-send-cli; do
    lipo -create \
         ".build/arm64-apple-macosx/release/$bin" \
         ".build/x86_64-apple-macosx/release/$bin" \
         -output "$OUT/$bin"
    chmod +x "$OUT/$bin"
done

BIN_DIR=".build/apple/Products/Release"
if [[ ! -f "$BIN_DIR/PrintixSendApp" || ! -f "$BIN_DIR/printix-send-cli" ]]; then
    echo "✖ Binaries nicht gefunden unter $BIN_DIR" >&2
    exit 1
fi

echo "✓ Build fertig — Binaries:"
ls -lh "$BIN_DIR/PrintixSendApp" "$BIN_DIR/printix-send-cli"
echo
echo "  Architekturen:"
lipo -info "$BIN_DIR/PrintixSendApp"
lipo -info "$BIN_DIR/printix-send-cli"
