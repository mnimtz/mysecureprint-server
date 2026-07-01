# App-Store-Listing — MySecurePrint

Vorbereitete Texte fuer App Store Connect. Pflege/Update vor jeder
Submission. Versionsbezug: 1.0.0 (Build 1).

---

## App-Metadaten (App-Store-Limits in Klammern)

- **App Name** (30): `MySecurePrint`
- **Subtitle** (30): `Secure print via printix-mcp`
- **Keywords** (100): `printix,printix-mcp,secure print,mobile print,nfc,mcp,claude,chatgpt,self-hosted,airprint`
- **Promotional Text** (170):
  > Free companion app for the open-source printix-mcp Docker server.
  > Sign in with Microsoft, enrol NFC cards, share files to print —
  > all via your own MCP.
- **Primary Category**: Business
- **Secondary Category**: Productivity
- **Age Rating**: 4+

## Support / Marketing URLs

- Support URL: `https://github.com/marcus-nimtz/printix-mcp/issues`
- Marketing URL: `https://github.com/marcus-nimtz/printix-mcp`
- Privacy Policy URL: `https://printix-sp.azurewebsites.net/privacy` *(siehe `APP_PRIVACY_POLICY.md`)*

---

## Beschreibung (Deutsch)

> MySecurePrint ist eine unabhaengige, nicht-kommerzielle Drittanbieter-App
> und steht in keiner Verbindung zur Tungsten Automation Corp., HP, Konica
> Minolta, Brother, Lexmark, PaperCut oder einem anderen Druckerhersteller.

Sichere mobile Druck-Begleit-App fuer den Open-Source-Server
**printix-mcp**, den du selbst auf einem Linux-Host, in Docker oder
als Home-Assistant-Add-on betreibst. Melde dich mit deinem
Microsoft-Konto an (Entra OAuth + PKCE), waehle eine SecurePrint-Queue,
sende PDFs oder Fotos direkt aus dem iOS-Share-Sheet — auch im
Hintergrund nach Schliessen der App. Optional liest die App die UID
deiner NFC-Firmenkarte (ISO 14443 / ISO 15693) zur Zuordnung am
Drucker.

Token-Speicherung im iOS-Keychain (Access-Group, geteilt mit der
Share-Extension), kein externes Backend, keine Tracker, kein
Analytics. Quelloffen unter `github.com/marcus-nimtz/printix-mcp`.

## Description (English)

> MySecurePrint is an independent, non-commercial third-party app and
> is NOT affiliated with, endorsed by, or sponsored by Tungsten
> Automation Corp., HP, Konica Minolta, Brother, Lexmark, PaperCut or
> any other printer / print-management vendor.

Secure mobile print companion for the open-source **printix-mcp**
server you run yourself on Linux, in Docker, or as a Home Assistant
add-on. Sign in with your Microsoft account (Entra OAuth + PKCE),
pick a SecurePrint queue, send PDFs or photos straight from the iOS
share sheet — uploads finish even after you close the app. Optional
NFC reader (ISO 14443 / ISO 15693) for enrolling your company access
card.

Bearer tokens are stored in the iOS Keychain (shared access group
with the share extension), no third-party backend, no trackers, no
analytics. Open source at `github.com/marcus-nimtz/printix-mcp`.

---

## What's New in 1.0.0

- Rebrand to MySecurePrint (was: Printix MobilePrint)
- Bearer-Token in Keychain statt UserDefaults (geteilt mit
  Share-Extension)
- Apple-Privacy-Manifest hinterlegt
- iOS-Deployment-Target auf 17.0 abgesenkt
- ATS auf `NSAllowsLocalNetworking` umgestellt
- NFC-UID nur noch in Debug-Builds geloggt
- Share-Extension nutzt jetzt eine Background-URLSession (Upload
  finishes ueber das App-Lifecycle hinweg)
- Timer-Leak in der Upload-View behoben
- NFC-Session-Handler-Leak behoben

---

## App Review Notes (an Apple)

> This app is a thin companion client for the user's own
> self-hosted server (`printix-mcp`, MIT-licensed, on GitHub). The
> user enters their server URL on first launch; there is no
> developer-controlled backend.
>
> - **NFC**: optional, used solely to read the UID of the user's own
>   workplace access card so it can be registered on their print
>   server. We do not write to cards.
> - **Camera**: only for scanning the QR-code that the
>   `printix-mcp` employee portal displays for initial server
>   provisioning.
> - **App Transport Security**: we use `NSAllowsLocalNetworking`
>   because most users run `printix-mcp` on a LAN host without a
>   public TLS certificate. Any non-LAN host must use HTTPS.
> - **Microsoft sign-in** is via `ASWebAuthenticationSession` +
>   PKCE; `code_verifier` is generated server-side, never on the
>   client.
> - **Trademark note**: "Printix" is a registered trademark of
>   Tungsten Automation Corp. We use it only nominatively to
>   describe compatibility with the `printix-mcp` server. App
>   description and screenshots include a clear "not affiliated"
>   disclaimer.

**Demo credentials**: The app cannot be reviewed without a working
`printix-mcp` server. We can provide a temporary demo server URL +
test login on request via the contact email.

---

## Screenshots (geplant, 6.7"-iPhone)

1. Setup-Screen: Server-URL eingeben / QR-Code scannen.
2. Login-Screen: Microsoft-Login-Button.
3. Targets-Screen: SecurePrint, Delegate, Capture.
4. Upload-Screen: PDF + Copies/Color/Duplex-Optionen.
5. Cards-Screen: NFC-Karte registrieren.
6. Share-Sheet aus Safari mit MySecurePrint-Icon.
