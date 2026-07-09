# iOS AirPrint-Profile für MySecurePrint — Design (Final)

**Status:** Ready to build • **Autor:** Marcus + Claude • **Datum:** 2026-07-09
**Version-Ziel:** v0.8.0 (Stufe 1)

---

## 1. Die Idee

Der User installiert **einmal** ein iOS-Konfigurationsprofil auf seinem
iPhone/iPad. Danach erscheint "MySecurePrint" in **jeder** iOS-App im
"Drucken"-Dialog als echter Drucker.

Kein Bonjour, kein VPN, kein Umweg — funktioniert über Mobilfunk,
öffentliches WLAN und im Firmennetz.

Der Print-Job landet auf unserem Server, wird identisch zu einem
App-Upload behandelt und via Printix-Cloud-API an die richtige
SecurePrint-Queue durchgereicht. **Immer personenbezogen** — kein
Shared-Pool.

**Killer-Vorteil gegenüber Printix' App:** Aus jeder iOS-App drucken
ohne Bonjour-Discovery — funktioniert überall wo der Server per HTTPS
erreichbar ist.

---

## 2. Was wir schon haben (aus printix-mcp-linux)

- `src/cloudprint/ipp_server.py` (~660 Zeilen) — produktionserprobter
  IPP/IPPS-FastAPI-Handler
- `src/cloudprint/ipp_parser.py` — IPP-Protokoll-Parser (Attributes,
  Groups, Job-Metadata)
- Die Printix-Client-Integration ist identisch — der `PrintixClient`
  in beiden Repos hat denselben API-Vertrag

**Die harte IPP-Arbeit ist erledigt.** Wir portieren und wrappen.

---

## 3. Stufe 1 Scope (v0.8.0)

**Opt-in Feature** — Admin muss aktivieren, sonst passiert nichts.

### 3.1 Was gebaut wird

| Komponente | Zweck |
|---|---|
| IPP-Server | Empfängt IPP-Print-Jobs (portiert) |
| Token-System + DB-Schema | Personalisierte Profile pro User × Queue |
| `.mobileconfig`-Generator | Signiertes iOS-Konfigurationsprofil |
| Admin-Config UI | Feature-Flag + Default-Queue-Auswahl |
| Onboarding-Email erweitert | Auto-Anhang bei Einladung wenn Feature aktiv |
| iOS-App Menüpunkt | Weitere Profile aus der App erstellen |

### 3.2 Was NICHT gebaut wird (v0.8.0)

- Kein Self-Service-Web-Portal → v0.9.0
- Kein Massen-Rollout via MDM-Variablen → v0.9.x
- Keine Group/Site-spezifischen Default-Queues → v0.9.x
- Keine Bulk-Refresh/Revoke → v0.9.0

---

## 4a. User-Identifikation im Print-Stream

**Zentrale Design-Entscheidung:** Wir identifizieren den User AUSSCHLIEßLICH
über den Profile-Token in der URL — **niemals** über IPP-Attribute im
Datenstrom.

### Was iOS uns liefert (aus Live-Analyse `ipp_server.py`)

| IPP-Attribut | Typischer Wert bei iOS |
|---|---|
| `requesting-user-name` | `"iPhone von Marcus"` (Device-Name, vom User änderbar) |
| `job-originating-user-name` | Fallback, meist leer oder = Device-Name |
| `job-originating-host-name` | `"iPhone-Marcus"` (Device-Hostname) |
| `job-name` | Dokumenttitel — z.B. `"Rechnung_Mai.pdf"` |
| `document-format` | `application/pdf` (fast immer) |

**iOS bietet keine verlässliche User-Identifikation.** Das Device-Feld
kann jeder User in den iOS-Einstellungen frei ändern.

### Wie wir es lösen

```
1. iOS Print → POST /airprint/{profile_token}
                    ↓ token = "3f4a...xyz24chars"
2. Server:    SELECT user_id, printer_id, queue_id, is_revoked
              FROM cloudprint_airprint_profiles
              WHERE profile_token = ?
                ↓
3. Auth-Check: is_revoked=0? User existiert? Queue-Berechtigung
              (falls Rechte sich seit Profil-Erstellung geändert haben)
                ↓
4. IPP-Payload parsen → PDF-Bytes + job-name
                ↓
5. printix_client.submit_job(
      queue_id=<from token>,
      owner_email=<from user-row>,
      pdf=<from ipp payload>,
      title=<from ipp job-name>,
   )
                ↓
6. Job landet bei Printix mit ownerId = user@firma.de
   → Am Drucker: nur diese Kartenauslösung
```

### IPP-Attribute die wir trotzdem lesen (aber nur als Metadaten)

- `job-name` → als Job-Titel in der App-Historie
- `document-format` → Sicherheitsprüfung (muss `application/pdf` sein)
- `job-originating-host-name` → Audit-Log ("gedruckt von iPad-XYZ")

Die Werte werden NIE für Auth-Entscheidungen verwendet. Auch wenn iOS
`requesting-user-name = "hacker@evil.com"` schickt: wir ignorieren es
komplett.

### Was der User im Job-Verlauf der App sieht

```
📄  Rechnung_Mai.pdf
   Gesendet von iPad von Marcus • über iOS AirPrint
   Queue: SecurePrint DE • Status: An Printix gesendet
```

- Dateiname = `job-name` (IPP)
- "Gesendet von X" = `job-originating-host-name` (IPP-Metadaten)
- "über iOS AirPrint" = eigener Marker (Server setzt `source='airprint'`)
- Queue = aus Token
- Status = wie gehabt

---

## 4b. Auth-Konzept: personalisierter Token

Ein Profil = **ein User × eine Queue**. **Immer.**

```
URL:      /airprint/{profile_token}
Token:    base32(sha256(user_id + queue_id + created_at + server_secret))[:24]
Lifetime: unbefristet, widerrufbar (is_revoked=1 in DB)
```

- Ein User kann mehrere Profile haben (z.B. eins für SecurePrint, eins
  für HR-Queue)
- Jeder Job wird bei Printix mit dem echten User als Owner eingereicht
- Kartenlogin am Drucker: nur der User selbst kann seinen Job auslösen

Kein Basic-Auth, kein OAuth-Popup — der Token im URL-Pfad ist die
einzige Authentifizierung. HTTPS-only, Token so lang wie 128-bit-
Password.

---

## 5. Rollout-Pfade

### Pfad A — Onboarding-Email (Zero-Touch für Neu-User)

Wenn Admin einen User einlädt (`/admin/users/invite`) UND das Feature
ist aktiv UND der User hat Berechtigung auf die Default-Queue:

1. Server erstellt Profil-Row + Token in DB
2. Server generiert `.mobileconfig` on-the-fly, signiert
3. Einladungs-Email bekommt Anhang `MySecurePrint.mobileconfig` + Absatz
   mit Anleitung
4. User öffnet Anhang am iPhone → Install-Dialog → fertig
5. **User kann sofort aus jeder iOS-App drucken**, ohne dass die
   MySecurePrint-App installiert sein muss

**Wenn User keine Berechtigung auf Default-Queue hat:** Silent skip.
Einladung geht normal raus, ohne Profil-Anhang. Admin sieht in der
Invite-Preview ob das Profil mitgeht.

### Pfad B — In der App generieren (bestehende User + zusätzliche Queues)

Wenn User schon eingeloggt in der App ist:

1. Einstellungen → iOS-Drucker → Liste der eigenen Profile
2. "Neuer Drucker" → Queue-Dropdown (nur Queues auf die er
   Berechtigung hat)
3. Optional: Anzeigename ("iPhone Marcus", "iPad HR")
4. "Erstellen und installieren" → App holt `.mobileconfig` per HTTPS
   → übergibt an iOS-System via `UIDocumentInteractionController`
5. iOS-Install-Dialog → fertig

Kein QR-Code, kein Web-Portal — reine App-Erfahrung.

---

## 6. DB-Schema

```sql
CREATE TABLE cloudprint_airprint_profiles (
    id                 TEXT PRIMARY KEY,          -- UUID
    user_id            TEXT NOT NULL,             -- our internal user
    profile_token      TEXT NOT NULL UNIQUE,      -- URL-Segment
    printer_id         TEXT NOT NULL,             -- Printix printer UUID
    queue_id           TEXT NOT NULL,             -- Printix queue UUID
    queue_display_name TEXT,                      -- z.B. "SecurePrint DE"
    display_name       TEXT,                      -- z.B. "iPhone Marcus"
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at       TIMESTAMP,
    job_count          INTEGER DEFAULT 0,
    is_revoked         INTEGER DEFAULT 0,
    revoke_reason      TEXT,
    created_via        TEXT  -- 'onboarding_email' | 'app' | 'admin'
);
CREATE INDEX idx_airprint_token ON cloudprint_airprint_profiles(profile_token);
CREATE INDEX idx_airprint_user  ON cloudprint_airprint_profiles(user_id);
```

Auto-Migration in `db.py` beim Server-Start.

---

## 7. Admin-Config UI (`Konfiguration → iOS Mobile`)

Neue Section unter dem bestehenden **Konfiguration**-Menüpunkt der
Admin-Sidebar. Der Section-Name im Menü lautet **"iOS Mobile"** (bewusst
neutraler als "AirPrint" — soll auch für zukünftige mobile Features
Platz haben: Widgets, Push-Notifs, MDM-Verwaltung).

```
Konfiguration                       ← bestehendes Sidebar-Menü
  ├── Allgemein
  ├── Printix-API
  ├── Entra / OAuth
  ├── Email
  ├── 🆕 iOS Mobile                 ← neue Sektion
  ├── Sicherheit
  └── ...

┌─────────────────────────────────────────────────────────┐
│  📱  iOS Mobile                                          │
├─────────────────────────────────────────────────────────┤
│  ☑ AirPrint-Profile aktivieren                          │
│    Aktiviert nativen iOS-Druck. User können aus         │
│    jeder iOS-App auf ihre SecurePrint-Queue drucken.    │
│                                                         │
│  Standard-Queue für Neu-User:                           │
│    ▸ [ SecurePrint Anywhere DE          ⌄ ]             │
│    Die Queue die beim Onboarding automatisch als        │
│    Profil ausgeliefert wird.                            │
│                                                         │
│  ☑ Beim Einladen: mobileconfig automatisch mitsenden    │
│    Neu eingeladene User bekommen das Profil direkt      │
│    per Email-Anhang und können sofort drucken.          │
│                                                         │
│  Zertifikat für Profil-Signing:                         │
│    Status: ✓ Server-Zertifikat gültig bis 2027-04-12    │
│    ► Apple Developer Cert hochladen (optional)          │
│                                                         │
│  [ Speichern ]                                          │
└─────────────────────────────────────────────────────────┘
```

Neue Settings-Keys in `settings`-Tabelle:
- `ios_mobile_airprint_enabled` (0/1)
- `ios_mobile_airprint_default_queue_id` (Printix queue UUID)
- `ios_mobile_airprint_default_printer_id` (Printix printer UUID)
- `ios_mobile_airprint_default_queue_name` (Display-Name für Email/UI)
- `ios_mobile_airprint_email_attach_default` (0/1)

Namespace `ios_mobile_*` damit zukünftige mobile Features (Push, MDM,
etc.) sich unter derselben Config-Sektion einreihen können.

Wenn `airprint_enabled=0`: Feature ist komplett aus. Route
`/airprint/{token}` gibt 404. Einladungs-Email unverändert.

---

## 8. Internationalisierung (i18n) — **von Anfang an**

Die App und der Server unterstützen viele Sprachen. **Jeder neue String
in diesem Feature muss in allen unterstützten Sprachen abgedeckt sein**
— keine hardcoded deutschen Strings, keine Nach-Übersetzung "später".

### Server-Sprachen (14) — `src/web/i18n.py`

Kern: `de`, `en`, `fr`, `it`, `es`, `nl`, `no`, `sv`
Fun-Sprachen: `bar`, `hessisch`, `oesterreichisch`, `schwiizerdütsch`,
`cockney`, `us_south`

### iOS-Sprachen (9) — `Localizable.xcstrings`

`de`, `en`, `es`, `fr`, `it`, `nb`, `nl`, `pt-BR`, `sv`

### Neue i18n-Keys (Server)

Alle Keys mit Namespace `ios_mobile_*` bzw. `airprint_*`:

**Admin-Config-Sektion:**
- `nav_config_ios_mobile` → "iOS Mobile"
- `ios_mobile_section_title` → "iOS Mobile"
- `ios_mobile_airprint_enabled_label` → "AirPrint-Profile aktivieren"
- `ios_mobile_airprint_enabled_help` → "Aktiviert nativen iOS-Druck…"
- `ios_mobile_default_queue_label` → "Standard-Queue für Neu-User"
- `ios_mobile_default_queue_help` → "Die Queue die beim Onboarding…"
- `ios_mobile_email_attach_label` → "Beim Einladen: mobileconfig…"
- `ios_mobile_email_attach_help` → "Neu eingeladene User…"
- `ios_mobile_signing_status_ok` → "Server-Zertifikat gültig bis {date}"
- `ios_mobile_signing_upload_cert` → "Apple Developer Cert hochladen"
- `ios_mobile_save` → "Speichern"
- `ios_mobile_saved` → "Einstellungen gespeichert"

**Onboarding-Email-Block:**
- `email_airprint_headline` → "Sofort aus dem iPhone drucken"
- `email_airprint_intro` → "Wir haben dir gleich einen nativen…"
- `email_airprint_install_steps` → "1. Anhang öffnen 2. iOS-Einstellungen…"
- `email_airprint_app_hint` → "Für Job-Verlauf, NFC-Kartenlogin und Delegation…"

**Fehler-Meldungen (bei /airprint/{token}):**
- `airprint_error_token_invalid` → "Profil nicht gefunden oder widerrufen"
- `airprint_error_queue_no_permission` → "Kein Zugriff auf diese Queue"

### Neue i18n-Keys (iOS App)

**Einstellungen-Menü:**
- `settings_section_ios_printers` → "iOS-Drucker"
- `ios_printers_title` → "iOS-Drucker"
- `ios_printers_empty` → "Noch kein Profil installiert"
- `ios_printers_add_button` → "Neuen Drucker hinzufügen"
- `ios_printers_hint` → "Bereits druckbar aus jeder iOS-App — der Drucker heißt …"

**Wizard "Neuer Drucker":**
- `ios_printers_wizard_title` → "Neuer iOS-Drucker"
- `ios_printers_wizard_queue_label` → "Queue auswählen"
- `ios_printers_wizard_display_name_label` → "Anzeigename (optional)"
- `ios_printers_wizard_display_name_placeholder` → "z. B. iPhone Marcus"
- `ios_printers_wizard_create_button` → "Erstellen und installieren"
- `ios_printers_wizard_install_hint` → "iOS zeigt gleich einen…"

**Profil-Zeile:**
- `ios_printers_last_used` → "Zuletzt genutzt: {date}"
- `ios_printers_never_used` → "Noch nicht genutzt"
- `ios_printers_revoke_action` → "Profil widerrufen"
- `ios_printers_revoke_confirm` → "Profil wirklich widerrufen?"
- `ios_printers_revoke_reason_placeholder` → "z. B. iPhone verloren"

### Umsetzung während Feature-Bau

Für jeden neu geschriebenen String:

1. Server-Template: `{{ _('key_name') }}` — NICHT hardcoded
2. Server-i18n.py: Alle 14 Sprachen befüllen (bei den Fun-Sprachen
   Zeitdruck-tauglich: englische Fallbacks + charakteristische Wörter)
3. iOS Swift: `String(localized: "key_name")` — NICHT hardcoded
4. iOS xcstrings: Alle 9 Sprachen befüllen (via Xcode oder Skript)

Keine Ausnahmen. `grep -rn "hardcoded\|TODO.*translate"` wird beim
E2E-Test-Task verifiziert.

---

## 9. Anzeige-Name im iOS Print-Dialog

Format im `.mobileconfig`:

```
DisplayName: MySecurePrint — {queue_display_name}
```

Beispiel: `"MySecurePrint — SecurePrint Anywhere DE"`

Wenn der User seinen eigenen Display-Namen setzt ("iPhone Marcus"),
wird er in die App-UI übernommen aber NICHT ins Profil — iOS zeigt
sonst inkonsistente Namen im System.

---

## 10. Zertifikat für Profil-Signing

**Server-TLS-Cert** (Azure Managed / Let's Encrypt) für TLS-Termination
— haben wir schon.

**Profil-Signing** (`.mobileconfig` selbst):
- Priorität 1: Wenn Apple Developer ID im Server konfiguriert
  (`/admin/settings → iOS AirPrint → Signing-Cert hochladen`), damit
  signieren — iOS zeigt "Verified"
- Priorität 2: Wenn kein Apple Cert vorhanden, Profil unsigned
  ausliefern — iOS zeigt "Unsigned" (roter Warnhinweis) aber
  Installation funktioniert

Für den Anfang: **unsigned** ist OK. Später Enterprise-Kunden können
Apple Developer Cert hochladen wenn sie den Warnhinweis vermeiden
wollen.

---

## 11. Onboarding-Email Erweiterung

Existierender Flow (`/admin/users/invite`):

```python
# NEU vor dem Email-Send:
if settings.get("airprint_enabled") == "1" \
   and settings.get("airprint_email_attach_default") == "1" \
   and _user_has_queue_permission(user, default_queue_id):
    profile = create_airprint_profile(
        user_id=user.id,
        queue_id=default_queue_id,
        created_via="onboarding_email",
    )
    mobileconfig_bytes = generate_mobileconfig(profile)
    email.attach("MySecurePrint.mobileconfig", mobileconfig_bytes,
                 mime="application/x-apple-aspen-config")
    email.body += render_template("airprint_onboarding_block.txt", ...)
```

Email-Block (i18n de/en):

```
📱 SOFORT AUS DEM iPHONE DRUCKEN

Wir haben dir gleich einen nativen iOS-Drucker eingerichtet. Öffne
den Anhang MySecurePrint.mobileconfig am iPhone und bestätige die
Installation in den iOS-Einstellungen. Danach kannst du aus Safari,
Mail, Fotos oder jeder anderen App direkt an unseren Firmen-
SecurePrint drucken.

Für Job-Verlauf, NFC-Kartenlogin und Delegation:
▸ MySecurePrint App im App Store: {app_store_link}
```

---

## 12. iOS-App Erweiterung

Neuer Menüpunkt in Einstellungen:

```
Einstellungen
  ├── Server / Anmeldung
  ├── Standard-Ziel
  ├── Delegation
  ├── 🆕 iOS-Drucker                    →
  ├── Live-Aktivitäten
  └── Über
```

Detail-Screen `iOS-Drucker`:

```
┌─────────────────────────────────────┐
│  iOS-Drucker                        │
├─────────────────────────────────────┤
│                                     │
│  Auf diesem iPhone installierte     │
│  Drucker-Profile:                   │
│                                     │
│  🖨️  MySecurePrint —                │
│      SecurePrint DE                 │
│      Zuletzt genutzt: gestern       │
│                                     │
│  ➕  Neuen Drucker hinzufügen        │
│                                     │
│  Bereits druckbar aus jeder iOS-    │
│  App — der Drucker heißt            │
│  "MySecurePrint — [Queue]".         │
│                                     │
└─────────────────────────────────────┘
```

Bei "Neuen Drucker hinzufügen":

```
1. Queue-Dropdown (nur eigene Berechtigungen aus /me/queues)
2. Optional: Anzeigename
3. Button "Erstellen und öffnen"
4. App holt .mobileconfig via HTTPS
5. UIDocumentInteractionController zeigt iOS-Install-Dialog
6. User bestätigt → fertig
```

Detection ob Profil installiert ist: iOS erlaubt das nicht direkt
zu prüfen (Privacy). Wir zeigen also alle **auf dem Server registrierten**
Profile — der User muss selbst schauen ob er sie am iPhone hat.

---

## 13. Server-Endpoints (neu)

```
POST /desktop/me/airprint/create
     Body: {queue_id, printer_id, display_name?}
     Response: {profile_id, mobileconfig_url}

GET  /desktop/me/airprint/{profile_id}/download
     Response: .mobileconfig (application/x-apple-aspen-config)

GET  /desktop/me/airprint
     Response: [{id, queue_display_name, created_at, last_used_at, ...}]

DELETE /desktop/me/airprint/{profile_id}
     Response: {revoked: true}

POST /airprint/{profile_token}
     Content-Type: application/ipp
     → IPP-Handler (wrapt bestehenden ipp_server.py)

GET  /airprint/{profile_token}
     Response: text/plain Info-Antwort für Health-Checks

POST /admin/airprint/settings
     Body: {enabled, default_queue_id, email_attach_default}
     Response: {ok: true}
```

---

## 14. Aufwand-Schätzung Stufe 1

| Task | Aufwand |
|---|---|
| Design-Doc (dieses) | ✓ fertig |
| IPP-Server + Parser portieren | 3 h |
| DB-Schema + Migration | 1 h |
| Token-System + `/airprint/{token}`-Handler | 3 h |
| `.mobileconfig`-Generator (unsigned first) | 3 h |
| PKCS7-Signing (optional cert upload) | 2 h |
| Admin-Config UI (settings-Section) | 3 h |
| Einladungs-Email erweitern | 2 h |
| iOS-App: Menüpunkt "iOS-Drucker" | 4 h |
| iOS-App: Profile-Detail + Wizard | 3 h |
| End-to-End-Test auf iPhone | 2 h |
| Docs (Admin-Anleitung, i18n) | 2 h |
| **Gesamt Stufe 1** | **~28 h ≈ 3–4 Tage** |

---

## 15. Rollout

**v0.8.0 (Stufe 1 fertig):**
- Feature-Flag default AUS (Opt-in)
- Existierende Installationen unbeeinflusst
- Kunden können in `/admin/settings` aktivieren
- Test-Kunde bekommt ~1 Woche für Feedback

**v0.9.0 (Stufe 2):**
- Self-Service Web-Portal
- Bulk-Revoke, Statistiken
- iOS-App: Push-Notif bei Job-Abschluss

**v0.9.x+ (Enterprise):**
- MDM-Variablen für Massenrollout
- Group/Site-spezifische Default-Queues
- Apple Developer Cert Auto-Renewal

---

## 16. Risiken + Mitigation

| Risiko | Mitigation |
|---|---|
| iOS zeigt "Unsigned" Warnung | Als Standard OK dokumentieren; Enterprise-Kunden können Cert hochladen |
| Token in URL leakt in Server-Logs | Access-Log-Filter: `/airprint/{TOKEN_MASKED}` |
| Server erreichbar aber PDF landet nicht bei Printix | Bestehender IPP-Server hat schon Retry-Logic + Audit-Log |
| Nutzer verliert iPhone mit installiertem Profil | Admin-UI: "Profile suchen nach User" + Bulk-Revoke |
| Feature verwirrt bestehende App-User | Opt-in default OFF, klare Docs, App-Menüpunkt versteckt bis Feature aktiv |

---

## 17. Nächste Schritte

1. ✓ Design-Doc final (dieses hier)
2. IPP-Server + Parser portieren (Task #63)
3. DB-Schema + Migration (Task #64)
4. Token-Handler (Task #66)
5. `.mobileconfig`-Generator (Task #65)
6. Admin-Config UI (Task #67)
7. Einladungs-Email (Task #68)
8. iOS-App Menüpunkt (Task #69)
9. E2E-Test + Docs (Task #70)

MVP-Deadline: 3–4 Arbeitstage. Live-Test auf iPhone sobald 1–4 fertig
sind (~1,5 Tage).
