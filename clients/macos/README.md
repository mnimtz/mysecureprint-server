# Printix Send für macOS

Native Menu-Bar-App + Finder-**Quick Actions** als macOS-Pendant zum Windows-Client. Rechtsklick auf eine Datei → **Quick Actions** → *Printix Send — &lt;Ziel&gt;* sendet das Dokument direkt an Printix Secure Print (oder eine Capture-Profil-Inbox).

## Was ist drin?

| Komponente | Zweck |
|---|---|
| **PrintixSendApp** | Menu-Bar-App (🖨 oben rechts) — Login, Ziel-Liste, Konfiguration, Quick-Action-Sync |
| **printix-send-cli** | Headless-Worker, der von den Quick Actions aufgerufen wird |
| **~/Library/Services/** | Pro Target ein `.workflow`-Bundle — erscheint im Finder-Rechtsklick |

## Ablauf

1. **Installieren** — DMG öffnen, `PrintixSend.app` nach *Applications* ziehen
2. **Konfigurieren** — Menu-Bar-Icon → *Konfiguration…* → Server-URL eintragen (z. B. `https://printix.firma.de`)
3. **Anmelden** — Benutzer/Passwort *oder* **Mit Microsoft (Entra)** via Device-Code-Flow
4. **Senden** — Rechtsklick auf PDF/DOCX/… → *Quick Actions* → *Printix Send — &lt;Ziel&gt;*
5. **Bestätigung** — Native Notification (Notification Center) „gesendet" oder „angenommen (läuft im Hintergrund)"

Nach jedem Login synchronisiert die App automatisch die Targets und legt/aktualisiert die Quick Actions. Manuelle Neusynchronisation jederzeit per Menu-Eintrag.

## Architektur

```
┌─────────────────────────────┐
│  PrintixSend.app (Menu Bar) │   SwiftUI + AppKit
│  ─ Login / Entra / Config   │
│  ─ Targets abrufen          │──┐
│  ─ Quick Actions schreiben  │  │
└─────────────────────────────┘  │
                                 ▼
              ~/Library/Services/Printix Send — <Ziel>.workflow
                                 │  ← Finder-Rechtsklick
                                 ▼
┌─────────────────────────────┐
│  printix-send-cli           │   Swift CLI
│  --target <id> <files>      │
│  ─ Keychain-Token lesen     │──→  POST /desktop/send (multipart)
│  ─ Notification anzeigen    │
└─────────────────────────────┘
```

| Plattform-Detail | macOS | Windows |
|---|---|---|
| Rechtsklick-Integration | Quick Actions (`~/Library/Services/*.workflow`) | SendTo-Menü (`shell:sendto\*.lnk`) |
| Token-Speicher | Keychain (`de.printix.send` / `bearer-token`) | DPAPI (`%LocalAppData%\PrintixSend\token.bin`) |
| Config | `~/Library/Application Support/PrintixSend/config.json` | `%LocalAppData%\PrintixSend\config.json` |
| Log | `~/Library/Logs/PrintixSend/printix-send-YYYYMMDD.log` | `%LocalAppData%\PrintixSend\logs\` |
| Dock-Icon | keins (`LSUIElement=true`) | keins (nur Tray) |
| Notification | `UNUserNotificationCenter` | WPF-Toast |

## Entwicklung

```bash
cd macos-client
swift build -c release              # ein-Arch-Build für Entwicklung
bash scripts/build-universal.sh     # arm64 + x86_64 Universal-Binary
VERSION=0.1.0 bash scripts/make-app-bundle.sh
VERSION=0.1.0 bash scripts/make-dmg.sh
open dist/PrintixSend-0.1.0.dmg
```

**Dev-Run ohne Bundle** (zum schnellen Iterieren):
```bash
swift run PrintixSendApp           # Menu-Bar-App
swift run printix-send-cli --help  # CLI
```

Beim `swift run`-Development findet `AppState.resolveCliPath()` die CLI nicht über das Bundle — die Quick Actions rufen dann `/usr/local/bin/printix-send-cli` auf. Für Integrations-Tests also einmal `sudo cp .build/release/printix-send-cli /usr/local/bin/` oder gleich die `.app` bauen.

## Release via GitHub

Tag nach dem Schema `macos-client-v<major>.<minor>.<patch>` pushen → CI baut Universal-DMG und legt ein Release an:

```bash
git tag -a macos-client-v0.1.0 -m "macOS-Client 0.1.0 — erste Veröffentlichung"
git push origin macos-client-v0.1.0
```

Workflow: `.github/workflows/build-macos-client.yml` (läuft auf `macos-14`).

## Signing & Notarization (optional)

Das CI-Ergebnis ist **unsigniert** — beim ersten Start blockt Gatekeeper; der User muss einmal Rechtsklick → *Öffnen* machen. Für produktive Roll-outs:

```bash
export CODESIGN_ID="Developer ID Application: Firma (TEAMID)"
bash scripts/make-app-bundle.sh       # signiert automatisch
bash scripts/make-dmg.sh
xcrun notarytool submit dist/PrintixSend-*.dmg \
      --apple-id <apple-id> --team-id <team-id> --password <app-pw> --wait
xcrun stapler staple dist/PrintixSend-*.dmg
```

## Exit-Codes (CLI)

| Code | Bedeutung |
|---|---|
| 0 | alles gesendet |
| 1 | ungültige Argumente |
| 2 | Config fehlt (App noch nie gestartet) |
| 3 | Keine Token im Keychain (nicht angemeldet) |
| 4 | ungültige Server-URL |
| 5 | mindestens eine Datei fehlgeschlagen |

## Status

**v0.1.0 — erste Veröffentlichung.** Feature-äquivalent zum Windows-Client v6.7.50, bis auf:
- kein permanentes "Home-Fenster" (Menu-Bar ersetzt den Tray)
- kein Auto-Launch bei Login (kommt in v0.2 via `SMAppService`)
- kein Auto-Update-Check (kommt in v0.2 via `latestVersion()`-Endpoint)
