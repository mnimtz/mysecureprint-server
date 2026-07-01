# MySecurePrint — App Store Connect Submission Guide

End-to-end Checkliste fuer TestFlight + App Store Review.
Stand: 2026-06-30. Quelle-Texte: `APP_STORE_LISTING.md`, `APP_PRIVACY_POLICY.md`, `README.md`.

---

## 🟢 Was der User noch tun muss (alles uebrige ist erledigt)

Alle nicht-user-spezifischen TODOs sind im Repo bereits angewandt
(Privacy-URL, Copyright, Share-Ext-PrivacyInfo, etc., siehe unten).
Es bleiben nur Schritte, die **echte User-Daten / Apple-Login**
brauchen:

1. **Apple Developer Account login** in Xcode (Team `KQGPPH4S33`).
2. **App-ID + Share-Ext-App-ID** im Developer-Portal erzeugen
   (`de.nimtz.mysecureprint` + `.share`) inkl. App-Group
   `group.de.nimtz.mysecureprint` auf beiden + NFC-Capability auf
   Haupt-App.
3. **Demo-Mailaccount** anlegen (z.B. `apple-review@nimtz.email`) und
   in App Store Connect → Reviewer-Felder eintragen, plus Demo-Server
   konfigurieren (oder den Public-Demo `https://printix-sp.azurewebsites.net`
   nutzen + Demo-Tenant + Account praeparieren).
4. **Reviewer-Kontakt** (Name/Telefon/Email) in App Store Connect.
5. **Apple-ID + App-Specific-Password** generieren (appleid.apple.com)
   — nur fuer CLI-Upload via `altool`. Per Xcode-GUI nicht noetig.
6. **Screenshots** mit Geraet/Simulator produzieren (siehe §5).
   Universal-Build: iPhone 6.9" **und** iPad 13" Pflicht.
7. **Marketing-Version** entscheiden (0.6.x weiter / Sprung auf 1.0.0
   fuer Public-Release) und vor Archive bumpen.
8. **App-Privacy-Section in App Store Connect** ausfuellen (Werte siehe
   §6 — Email, Name, Device ID als "Linked to user", "Not used for
   tracking", "App Functionality").

Bestehende Dateien:
- `APP_STORE_LISTING.md` — Listing-Texte (DE/EN)
- `APP_PRIVACY_POLICY.md` — Privacy-Policy-Template
- `MySecurePrint/PrivacyInfo.xcprivacy` — Apple-Privacy-Manifest
- `ExportOptions.plist` — App-Store-Connect-Export, Team `KQGPPH4S33`

---

## 1. App-Identitaet

| Feld | Wert |
|---|---|
| Display-Name | **MySecurePrint** |
| Haupt-Bundle-ID | `de.nimtz.mysecureprint` |
| Share-Extension-Bundle-ID | `de.nimtz.mysecureprint.share` |
| App-Group | `group.de.nimtz.mysecureprint` |
| Keychain-Service | `de.nimtz.mysecureprint` |
| Keychain-Access-Group | `$(AppIdentifierPrefix)group.de.nimtz.mysecureprint` |
| Custom-URL-Scheme | `mysecureprint://oauth/callback` |
| Development-Team | `KQGPPH4S33` |
| iOS-Deployment-Target | 17.0 |
| Primaere Sprache | Englisch (Listing DE+EN beide vorbereitet) |
| Aktuelle Marketing-Version | **0.6.3** (vor Submission auf 0.6.4 / 1.0.0 bumpen) |
| Aktueller Build (CURRENT_PROJECT_VERSION) | **2** (vor jedem Upload um +1 erhoehen) |

### Wo zu bumpen (pbxproj)

Datei: `MySecurePrint.xcodeproj/project.pbxproj`

Vier Stellen je `MARKETING_VERSION` und `CURRENT_PROJECT_VERSION`:
- Z. 265-277 — Share-Ext Debug
- Z. 294-306 — Share-Ext Release
- Z. 445-461 — App Debug
- Z. 484-500 — App Release

**Alle vier Werte MUESSEN synchron sein**, sonst lehnt App Store Connect den Build ab.

Quick-Bump (z.B. auf 0.6.4 / Build 3):
```bash
cd MobileApp/ios-client
sed -i '' 's/MARKETING_VERSION = 0\.6\.3;/MARKETING_VERSION = 0.6.4;/g' MySecurePrint.xcodeproj/project.pbxproj
sed -i '' 's/CURRENT_PROJECT_VERSION = 2;/CURRENT_PROJECT_VERSION = 3;/g' MySecurePrint.xcodeproj/project.pbxproj
```

---

## 2. Entitlements + Capabilities

### Haupt-App (`MySecurePrint.entitlements`)

| Capability | Wert |
|---|---|
| App Groups | `group.de.nimtz.mysecureprint` |
| Keychain Sharing | `$(AppIdentifierPrefix)group.de.nimtz.mysecureprint` |
| NFC Tag Reader | `com.apple.developer.nfc.readersession.formats = [TAG]` |
| URL Schemes (Info.plist) | `mysecureprint` (CFBundleURLName `de.nimtz.mysecureprint.oauth`) |

### Share-Extension (`MySecurePrintShare.entitlements`)

| Capability | Wert |
|---|---|
| App Groups | `group.de.nimtz.mysecureprint` |
| Keychain Sharing | `$(AppIdentifierPrefix)group.de.nimtz.mysecureprint` |
| Activation | bis zu 10 Bilder oder 10 Files (Info.plist `NSExtensionActivationRule`) |

### Background-Modes

**Status (verifiziert):** Share-Ext nutzt `ProcessInfo.performExpiringActivity` (`ShareViewController.swift:80`), **NICHT** `URLSession` mit `backgroundSessionConfiguration`. Damit ist **kein** `UIBackgroundModes`-Eintrag in der Share-Ext-Info.plist noetig — Apple lehnt sogar `UIBackgroundModes` ohne dazugehoerigen Code teils ab. Falls spaeter auf echte Background-URLSession umgestellt wird, dann `UIBackgroundModes = [fetch]` in `MySecurePrintShare/Info.plist` ergaenzen.

### Universal Links

Aktuell **NICHT** konfiguriert (kein `applicationLinks` / `com.apple.developer.associated-domains`). Falls Marketing-Webseite (siehe TODO) Universal Links bekommen soll, spaeter nachruesten.

### Permission-Strings (Info.plist) — Begruendungen fuer Apple-Review

| Key | Wert | Review-Hinweis |
|---|---|---|
| `NSCameraUsageDescription` | "MySecurePrint nutzt die Kamera, um den QR-Code aus dem Mitarbeiter-Portal zu scannen und den Server automatisch einzurichten." | QR-Scan, nur Setup-Flow. |
| `NFCReaderUsageDescription` | "MySecurePrint liest die UID deiner RFID-Karte (z.B. Firmenausweis), damit du sie zum Druck-Release zuordnen kannst." | NFC nur Read, keine Write-Operation. |

`NSPhotoLibraryUsageDescription` ist aktuell **NICHT** gesetzt — die App nutzt `UIDocumentPickerViewController`, keine PhotoKit-Zugriffe. Falls in Zukunft Photos-Picker → Permission-String ergaenzen.

### App Transport Security

`NSAllowsLocalNetworking = true` (LAN-Self-Hosted). `NSAllowsArbitraryLoadsInWebContent = false`.
**Review-Hinweis MUSS** das erklaeren (siehe Review-Notes unten).

### Export Compliance

`ITSAppUsesNonExemptEncryption = false` — befreit von jaehrlicher Erklaerung.

---

## 3. App Store Connect Listing

### Basis (aus `APP_STORE_LISTING.md`)

| Feld | Wert | Limit |
|---|---|---|
| App Name | `MySecurePrint` | 30 |
| Subtitle | `Secure print via printix-mcp` | 30 |
| Primary Category | Business | — |
| Secondary Category | Productivity | — |
| Age Rating | 4+ | — |
| Promotional Text | "Free companion app for the open-source printix-mcp Docker server. Sign in with Microsoft, enrol NFC cards, share files to print — all via your own MCP." | 170 |
| Keywords | `printix,printix-mcp,secure print,mobile print,nfc,mcp,claude,chatgpt,self-hosted,airprint` | 100 |
| Support URL | `https://github.com/marcus-nimtz/printix-mcp/issues` | — |
| Marketing URL | `https://github.com/marcus-nimtz/printix-mcp` | — |
| Privacy Policy URL | `https://printix-sp.azurewebsites.net/privacy` (Server-Route `/privacy` rendert App-Privacy-Policy inkl. iOS-Abschnitt §11; DE-Alias `/datenschutz`) | — |
| Copyright | `Copyright © 2026 Marcus Nimtz. All rights reserved.` (auch in beiden `Info.plist` als `NSHumanReadableCopyright` gesetzt) | — |

### Description (DE)

> MySecurePrint ist eine unabhaengige, nicht-kommerzielle Drittanbieter-App und steht in keiner Verbindung zur Tungsten Automation Corp., HP, Konica Minolta, Brother, Lexmark, PaperCut oder einem anderen Druckerhersteller.

Sichere mobile Druck-Begleit-App fuer den Open-Source-Server **printix-mcp**, den du selbst auf einem Linux-Host, in Docker oder als Home-Assistant-Add-on betreibst. Melde dich mit deinem Microsoft-Konto an (Entra OAuth + PKCE), waehle eine SecurePrint-Queue, sende PDFs oder Fotos direkt aus dem iOS-Share-Sheet — auch im Hintergrund nach Schliessen der App. Optional liest die App die UID deiner NFC-Firmenkarte (ISO 14443 / ISO 15693) zur Zuordnung am Drucker.

Token-Speicherung im iOS-Keychain (Access-Group, geteilt mit der Share-Extension), kein externes Backend, keine Tracker, kein Analytics. Quelloffen unter `github.com/marcus-nimtz/printix-mcp`.

### Description (EN)

> MySecurePrint is an independent, non-commercial third-party app and is NOT affiliated with, endorsed by, or sponsored by Tungsten Automation Corp., HP, Konica Minolta, Brother, Lexmark, PaperCut or any other printer / print-management vendor.

Secure mobile print companion for the open-source **printix-mcp** server you run yourself on Linux, in Docker, or as a Home Assistant add-on. Sign in with your Microsoft account (Entra OAuth + PKCE), pick a SecurePrint queue, send PDFs or photos straight from the iOS share sheet — uploads finish even after you close the app. Optional NFC reader (ISO 14443 / ISO 15693) for enrolling your company access card.

Bearer tokens are stored in the iOS Keychain (shared access group with the share extension), no third-party backend, no trackers, no analytics. Open source at `github.com/marcus-nimtz/printix-mcp`.

### What's New (Release-Notes-Vorlage)

```
- Bugfixes und Stabilitaet
- <<TODO: konkrete Aenderungen seit letztem Release nachtragen>>
```

---

## 4. App-Review-Information

### Demo-Account / Reviewer-Setup

Apple verlangt funktionierende Demo-Credentials, sonst Reject. Da die App ohne `printix-mcp`-Server NICHT funktioniert, MUSS man Apple einen Zugang zu einem temporaeren Testserver geben.

**Reviewer-Felder in App Store Connect:**

| Feld | Wert |
|---|---|
| Sign-In Required | YES |
| Username | `<<TODO: Test-Mailaccount z.B. apple-review@nimtz.email>>` |
| Password | `<<TODO: Passwort fuer obigen Account>>` |
| Demo-Server-URL | `<<TODO: z.B. https://demo.printix-mcp.nimtz.email>>` |
| Contact First Name | `<<TODO: Marcus>>` |
| Contact Last Name | `<<TODO: Nimtz>>` |
| Contact Phone | `<<TODO: +49 …>>` |
| Contact Email | `<<TODO: marcus@nimtz.email>>` |

### Notes for Reviewer (im Notes-Feld einfuegen)

```
This app is a thin companion client for the user's own self-hosted server
(`printix-mcp`, MIT-licensed, on GitHub). The user enters their server URL on
first launch; there is no developer-controlled backend.

HOW TO REVIEW
1. Launch the app — you will see a Setup screen asking for a server URL.
2. Enter the demo server URL provided above (or scan the QR code from the
   demo employee portal — the camera permission is for THIS QR scan).
3. Sign in with the demo Microsoft account credentials above. Sign-in uses
   ASWebAuthenticationSession + PKCE.
4. After sign-in, you can browse Targets, send a test PDF via Upload, and
   (on NFC-capable devices) enrol an NFC card.

PERMISSIONS — WHY
- NFC: optional, used solely to read the UID of the user's own workplace
  access card so it can be registered on their print server. We do not
  write to cards.
- Camera: only for scanning the QR-code that the printix-mcp employee
  portal displays for initial server provisioning.
- App Transport Security: we use NSAllowsLocalNetworking because most
  users run printix-mcp on a LAN host without a public TLS certificate.
  Any non-LAN host must use HTTPS.

TRADEMARK
"Printix" is a registered trademark of Tungsten Automation Corp. We use it
only nominatively (fair use) to describe compatibility with the
`printix-mcp` server. App description and screenshots include a clear
"not affiliated" disclaimer.

PRIVACY
No third-party backend, no analytics, no tracking. All data is sent only
to the server URL the user configured. Bearer tokens live in the iOS
Keychain. Full policy: https://printix-sp.azurewebsites.net/privacy
```

---

## 5. Screenshots

### Required Sizes (Stand iOS 17+ / App-Store-Connect 2026)

Apple akzeptiert seit 2024 das Hochladen einer einzigen Aufloesung pro Geraeteklasse — die ueblichste ist:

| Display-Klasse | Geraet | Aufloesung |
|---|---|---|
| iPhone 6.9" (verpflichtend) | iPhone 16 Pro Max | 1290 x 2796 px (Portrait) |
| iPhone 6.5" (verpflichtend, Fallback) | iPhone 11 Pro Max | 1242 x 2688 px (Portrait) |
| iPad 13" (nur falls Universal) | iPad Pro M4 13" | 2064 x 2752 px |

**Status:** `TARGETED_DEVICE_FAMILY = "1,2"` (Universal, iPhone + iPad) — verifiziert in pbxproj (alle 4 Build-Configs). Apple verlangt damit **auch iPad-Screenshots** (13" Pro M4, 2064 x 2752 px).

> Empfehlung fuer den ersten Submission: TARGETED_DEVICE_FAMILY auf `"1"` reduzieren (iPhone-only) — weniger Apple-Review-Risiko, kein iPad-Screenshot-Aufwand. Aenderung in pbxproj an allen 4 Stellen (Z. 283, 312, 471, 510). iPad-Support kann jederzeit per Update nachgeliefert werden.

### Empfohlene 6 Shots (laut `APP_STORE_LISTING.md`)

1. **Setup**: Server-URL eingeben / QR-Code scannen
2. **Login**: Microsoft-Login-Button
3. **Targets**: SecurePrint, Delegate, Capture
4. **Upload**: PDF + Copies/Color/Duplex-Optionen
5. **Cards**: NFC-Karte registrieren
6. **Share-Sheet** aus Safari mit MySecurePrint-Icon

Optional dazu:
7. **Jobs/History** — laufende und vergangene Druck-Jobs
8. **Management/Settings** — Logout, Konto wechseln

Existierende Screenshots: **keine im Repo gefunden**. Vorschlag: `appstore/screenshots/iphone-6.9/01-setup.png` etc. anlegen.

---

## 6. Privacy-Manifest (`PrivacyInfo.xcprivacy`)

Datei: `MySecurePrint/PrivacyInfo.xcprivacy`.

### Tracking
- `NSPrivacyTracking = false`
- `NSPrivacyTrackingDomains = []`

### Collected Data Types (alle Linked-to-User, kein Tracking, Zweck = App Functionality)
| Datentyp | Zweck |
|---|---|
| Email Address | App Functionality |
| Name | App Functionality |
| Device ID | App Functionality |

### Required-Reason-API-Disclosures
| API | Reason-Code | Bedeutung |
|---|---|---|
| `NSPrivacyAccessedAPICategoryUserDefaults` | `CA92.1` | App-Group-UserDefaults shared mit Share-Ext |
| `NSPrivacyAccessedAPICategoryFileTimestamp` | `C617.1` | Timestamp temporaerer Upload-Files |

> **Audit-Ergebnis (verifiziert):** Code-Suche in `MySecurePrint/` + `MySecurePrintShare/` nach `mach_absolute_time`, `boot_time`, `dlsym`, `systemUptime`, `FileAttributeKey`, `attributesOfItem`, `creationDate`, `modificationDate` → **keine Treffer**. Die deklarierten Reason-Codes `CA92.1` (UserDefaults) und `C617.1` (FileTimestamp) sind ausreichend. `FileTimestamp` bleibt drin als Vorsorge fuer Foundation-Frameworks, die das intern triggern koennten.

**Share-Extension PrivacyInfo:** ERLEDIGT — `MySecurePrintShare/PrivacyInfo.xcprivacy` als Kopie der Haupt-App-Datei angelegt. Wird automatisch ins Share-Ext-Target aufgenommen, weil das Projekt einen `PBXFileSystemSynchronizedRootGroup` fuer `MySecurePrintShare/` nutzt (Xcode 16+) und die Datei nicht in den `membershipExceptions` (nur `Info.plist`, `MySecurePrintShare.entitlements`) steht. Kein pbxproj-Edit noetig.

---

## 7. TestFlight Build-Upload

### Vorbereitung
- Apple Developer-Account aktiv, Team `KQGPPH4S33`
- App-ID + Share-Ext-App-ID in App Store Connect angelegt (Bundle-IDs siehe oben)
- App-Group `group.de.nimtz.mysecureprint` auf beide App-IDs gemappt
- Keychain-Group + NFC-Capability auf Haupt-App aktiv
- Automatic Signing aktiv (ExportOptions.plist: `signingStyle = automatic`)

### Variante A: via Xcode-GUI (empfohlen fuer ersten Upload)
1. `MobileApp/ios-client/MySecurePrint.xcodeproj` oeffnen
2. Scheme: `MySecurePrint`, Destination: `Any iOS Device (arm64)`
3. Product → Archive
4. Im Organizer: `Distribute App` → `App Store Connect` → `Upload` → Automatic Signing
5. Warten bis Status "Processing" durch ist (~5-30 min)
6. In App Store Connect → TestFlight → Build erscheint, Export-Compliance auf "no encryption beyond iOS standard" bestaetigen

### Variante B: CLI (fuer spaetere Releases)
```bash
cd MobileApp/ios-client

# Archive
xcodebuild archive \
  -project MySecurePrint.xcodeproj \
  -scheme MySecurePrint \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath build/MySecurePrint.xcarchive \
  CODE_SIGN_STYLE=Automatic \
  DEVELOPMENT_TEAM=KQGPPH4S33

# Export IPA fuer App Store Connect
xcodebuild -exportArchive \
  -archivePath build/MySecurePrint.xcarchive \
  -exportPath build/export \
  -exportOptionsPlist ExportOptions.plist

# Upload (App-Specific-Password aus appleid.apple.com, "altool" tut's noch)
xcrun altool --upload-app \
  --type ios \
  --file build/export/MySecurePrint.ipa \
  --username "<<TODO: deine Apple-ID-Email>>" \
  --password "<<TODO: App-Specific-Password>>"
```

### Provisioning-Profiles (Automatic)
- Xcode legt zwei Profile selbst an:
  - `iOS Team Provisioning Profile: de.nimtz.mysecureprint`
  - `iOS Team Provisioning Profile: de.nimtz.mysecureprint.share`
- Falls Manual Signing gewuenscht → in `ExportOptions.plist` die `provisioningProfiles`-Keys mit den UUID-Namen befuellen.

---

## 8. Bekannte Huerden

| Thema | Status / Hinweis |
|---|---|
| Share-Extension App-Group + Signing | OK — Entitlements + Bundle-ID gesetzt. Beim ersten Upload pruefen, dass das Profile fuer `.share` vom Portal automatisch erzeugt wurde. |
| NFC-Capability | `com.apple.developer.nfc.readersession.formats = [TAG]` gesetzt. Apple benoetigt **keinen** Sonderantrag fuer Tag-Reading (nur "Core NFC"); fuer Background-Tag-Reading oder Apple-Pay-aehnliche Szenarien waere ein Approval noetig, das nutzen wir nicht. |
| Camera (QR) | `NSCameraUsageDescription` gesetzt — OK. |
| App-Transport-Security mit LAN | `NSAllowsLocalNetworking` — MUSS im Review-Notes-Feld erklaert werden (siehe Notes oben). Sonst Reject-Risiko. |
| Trademark "Printix" | Disclaimer in Description + README. Apple-Reviewer manchmal sensibel — Notes erwaehnen "nominative fair use". |
| Open-Source-Bezug | Apple verlangt keine separate Erklaerung, aber Privacy-Policy MUSS unter eigener URL erreichbar sein (nicht nur als Markdown im Repo). |
| Marketing-Version-Sprung von 0.6.3 → 1.0 | Erster Store-Release sollte `1.0.0` sein. Bauen aktuell als 0.6.x → 0.7.x fuer interne TestFlight-Runs, dann `1.0.0` fuer Public-Submission. |
| Share-Ext `PrivacyInfo.xcprivacy` | ✅ Erledigt — Datei unter `MySecurePrintShare/PrivacyInfo.xcprivacy`, wird via SynchronizedRootGroup automatisch ins Target eingebunden. |
| Background-Modes (URLSession) | Nicht noetig — Share-Ext nutzt `performExpiringActivity`, keine Background-URLSession. |

---

## 9. Pre-Submission Checklist

### Vor jedem TestFlight-Upload
- [ ] Marketing-Version in pbxproj gebumpt (alle 4 Stellen)
- [ ] Build-Number erhoeht (alle 4 Stellen)
- [ ] `git status` clean / Aenderungen committet
- [ ] App auf physischem Geraet einmal laufen lassen (Setup → Login → Upload → Share-Sheet)
- [ ] Xcode → Product → Analyze (keine neuen Warnings)
- [ ] Archive baut ohne Fehler
- [ ] Upload an App Store Connect erfolgreich
- [ ] Export-Compliance-Frage im AC beantwortet (`no` → uses iOS standard encryption only)

### Vor erster Public-Submission (1.0.0)
- [ ] Listing-Texte (DE + EN) final reviewt
- [ ] 6+ Screenshots fuer iPhone 6.9" hochgeladen
- [ ] Privacy-Policy unter oeffentlicher URL erreichbar (kein 404, kein 401)
- [ ] Support-URL erreichbar
- [ ] Demo-Account angelegt + getestet, Credentials in Reviewer-Notes
- [ ] Demo-`printix-mcp`-Server laeuft + ist von ausserhalb erreichbar (oder VPN-Anleitung in Notes)
- [ ] Reviewer-Notes (siehe Abschnitt 4) eingefuegt
- [ ] Age-Rating-Fragebogen ausgefuellt → 4+
- [ ] Pricing = Free
- [ ] Verfuegbarkeit = Worldwide oder gewuenschte Laender
- [ ] App Privacy-Section in App Store Connect ausgefuellt:
  - Data Collected: Email, Name, Device ID (alle "Linked to user", "Not used for tracking", Purpose: "App Functionality")
  - Data NOT collected for advertising, analytics, product personalization, etc.
- [ ] Internal TestFlight Group fuer Self-Test bestaetigt einen kompletten Print-Job
- [ ] External-Beta-Group (optional) — braucht Beta-App-Review (~24h)
- [ ] "Submit for Review" druecken

---

## 10. Offene TODOs vom User

Siehe gepinnte Liste ganz oben ("Was der User noch tun muss"). Alles
nicht-user-spezifische ist erledigt:

- ✅ Privacy-Policy-URL — `https://printix-sp.azurewebsites.net/privacy`
- ✅ Copyright — `Copyright © 2026 Marcus Nimtz.` (Info.plist + Listing)
- ✅ Share-Ext `PrivacyInfo.xcprivacy` angelegt
- ✅ `TARGETED_DEVICE_FAMILY` geprueft: Universal `"1,2"` (User-Entscheidung: iPhone-only vor Submission?)
- ✅ Required-Reason-API-Audit: keine zusaetzlichen Reason-Codes noetig
- ✅ Background-Modes-Check: nicht noetig (`performExpiringActivity`)

Offen (user-spezifisch):
- [ ] Apple-ID + App-Specific-Password (nur fuer CLI-Upload noetig)
- [ ] Demo-Server-URL + Test-Account anlegen
- [ ] Reviewer-Kontaktdaten in App Store Connect eintragen
- [ ] Screenshots produzieren (iPhone 6.9" Pflicht, iPad 13" falls Universal bleibt)
- [ ] Marketing-Version-Entscheidung (0.6.x weiter / Sprung auf 1.0.0)
- [ ] Apple-ID-Login + App-IDs im Developer-Portal anlegen
- [ ] App-Privacy-Section im App Store Connect ausfuellen
