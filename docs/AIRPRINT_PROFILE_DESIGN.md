# iOS AirPrint-Profile für MySecurePrint — Design

**Status:** Draft • **Autor:** Marcus + Claude • **Datum:** 2026-07-09
**Version-Ziel:** v0.8.0

---

## 1. Die Idee

Der User installiert **einmal** ein iOS-Konfigurationsprofil auf seinem
iPhone/iPad. Danach erscheint "MySecurePrint" in **jeder** iOS-App im
"Drucken"-Dialog als echter Drucker.

Kein Bonjour, kein VPN, kein Umweg — funktioniert über Mobilfunk,
öffentliches WLAN, und im Firmennetz.

Der Print-Job landet auf unserem Server, wird identisch zu einem
App-Upload behandelt und via Printix-Cloud-API an die richtige
Secure-Print-Queue durchgereicht.

**Killer-Vorteil gegenüber Printix' App:** Aus jeder App drucken ohne
Bonjour-Discovery — funktioniert überall wo der Server per HTTPS
erreichbar ist.

---

## 2. Was wir schon haben

Aus `printix-mcp-linux` sind **fertig portierbar**:

- `src/cloudprint/ipp_server.py` (~660 Zeilen)
  IPP/IPPS-FastAPI-Handler, empfängt POST auf `/ipp/{tenant_id}`,
  parst Job-Attribute, speichert PDF/PCL/PS auf Disk, asynchroner
  Forwarder an Printix. Vollständig produktionserprobt.
- `src/cloudprint/ipp_parser.py`
  IPP-Protokoll-Parser (Binary-Frames, Attribute, Groups, Job-Metadata).
- Existierende Printix-Client-Integration (`printix_client.PrintixClient`)
  ist identisch mit dem was der IPP-Server drüben aufruft.

Die **harte Arbeit ist erledigt.** Was fehlt ist der Profil-Generator,
das Auth-Konzept, die Web-UI und die Integration in unser DB-Schema.

---

## 3. Was neu gebaut werden muss

### 3.1 `.mobileconfig`-Generator

iOS Configuration Profile ist ein **Property-List-XML** mit signierter
CMS-Signatur (Apple erwartet PKCS#7 mit unserem Server-Zertifikat).

Payload-Typen die wir setzen:

```xml
<key>PayloadType</key><string>com.apple.airprint</string>
<key>PayloadContent</key>
<array>
  <dict>
    <key>ForceTLS</key><true/>
    <key>Port</key><integer>443</integer>
    <key>ResourcePath</key><string>/airprint/{profile_token}</string>
    <key>IPAddress</key><string>printix-sp.azurewebsites.net</string>
  </dict>
</array>
```

Datei-Name: `MySecurePrint-{User-Email}-{Queue-Name}.mobileconfig`.
Signiert mit unserem Server-TLS-Zertifikat (Let's Encrypt / Azure).
iOS zeigt beim Install "Verified" wenn Signatur ok.

**Größe der Aufgabe:** ~200 Zeilen Python (Template + Signing).
Existierende `cryptography` lib reicht.

### 3.2 Auth-Konzept: Profile-Tokens

Wir nutzen **URL-eingebettete Tokens** — keine Basic-Auth-Popups.

```
Struktur: /airprint/{profile_token}
profile_token = base32(sha256(user_id + queue_id + created_at + secret))[:24]
```

- Ein Token = **ein Profil** = **ein User + eine Queue**.
- Widerruf möglich via DB-Flag (`is_revoked`), Server prüft bei jedem
  Job.
- Token in URL statt Basic Auth → keine iOS-Popup-Fragen im
  Print-Dialog.
- **Sicherheit:** Token ist so lang wie ein Password (128 bit).
  HTTPS-only, kein Log-Leak weil im Path (nicht in Query).

Kein Token-Rotation nötig — bei Kompromittierung: revoken + neues
Profil.

### 3.3 DB-Schema-Erweiterung

Neue Tabelle:

```sql
CREATE TABLE cloudprint_airprint_profiles (
    id                 TEXT PRIMARY KEY,          -- UUID
    user_id            TEXT NOT NULL,             -- our internal user
    profile_token      TEXT NOT NULL UNIQUE,      -- URL-Segment
    printer_id         TEXT NOT NULL,             -- Printix printer UUID
    queue_id           TEXT NOT NULL,             -- Printix queue UUID
    queue_display_name TEXT,                      -- z.B. "SecurePrint DE"
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at       TIMESTAMP,                 -- für "aktive/inaktive" Anzeige
    job_count          INTEGER DEFAULT 0,         -- Nutzungs-Statistik
    is_revoked         INTEGER DEFAULT 0,
    revoke_reason      TEXT
);
CREATE INDEX idx_airprint_token ON cloudprint_airprint_profiles(profile_token);
CREATE INDEX idx_airprint_user  ON cloudprint_airprint_profiles(user_id);
```

### 3.4 Route-Registrierung

```python
# src/web/app.py
from cloudprint.ipp_server import register_ipp_routes
from cloudprint.airprint import register_airprint_routes

register_ipp_routes(app)          # /ipp/{tenant_id}   — bestehend
register_airprint_routes(app)     # /airprint/{token}  — neu
```

Der **AirPrint-Handler** (`/airprint/{profile_token}`) ist im Grunde
ein **Wrapper um `ipp_server.py`**:

1. Token nachschlagen → User + Queue laden
2. IPP-Request parsen (bestehender Code)
3. PDF extrahieren
4. `printix_client.submit_job(queue_id, user_email, pdf_bytes)` —
   identisch zum App-Upload-Path
5. IPP-Response zurück

Kein neuer IPP-Code nötig — nur Adapter.

### 3.5 Web-UI: Neuer Menüpunkt "iOS-Profile"

Neuer Punkt in der User-Sidebar (nicht Admin):

```
/my/airprint/                   → Liste eigener Profile
/my/airprint/new                → Neues Profil (Queue-Wahl + Download)
/my/airprint/{id}/download      → .mobileconfig-Datei ausliefern
/my/airprint/{id}/revoke        → Token widerrufen (POST)
```

**Wizard-Flow beim Erstellen:**

1. User klickt "Neues iOS-Profil"
2. Dropdown: Verfügbare Queues (die, für die User Berechtigung hat)
3. Optionaler Anzeige-Name ("iPhone Marcus", "iPad Familie")
4. Klick "Erstellen und laden"
5. Server generiert Token + Profil + speichert in DB
6. Browser lädt `.mobileconfig` herunter
7. User öffnet die Datei am iPhone → iOS zeigt Install-Dialog

**Admin-Sicht** unter `/admin/airprint`:
- Alle Profile aller User (mit Filter)
- Bulk-Revoke möglich
- Stats: aktive Profile, letzte Nutzung, Job-Count

### 3.6 iOS-App-Integration

Die App bekommt einen neuen Menüpunkt:

**"iOS-Drucker installieren"** — zeigt QR-Code der auf die
`/my/airprint/new`-Seite zeigt. User scannt am iPhone → wird direkt
zur Profil-Erstellung geleitet → Download.

---

## 4. Auth-Flow im Detail

```
┌─────────────┐    HTTPS/IPP    ┌───────────────┐   Printix Cloud API
│  iOS Device │ ───────────────▶│ MySecurePrint │ ───────────────────▶
│  (any app)  │  application/ipp│    Server     │
└─────────────┘   POST /airprint└───────┬───────┘
                    /{token}            │
                                        │ 1. Token → user + queue
                                        │ 2. Parse IPP + PDF
                                        │ 3. Log job (cloudprint_jobs)
                                        │ 4. Forward to Printix
                                        │ 5. Return IPP 200 OK
                                        ▼
                                  DB: last_used_at++,
                                      job_count++
```

**Was passiert wenn Token revoked:**
- Server gibt IPP-Status `1030` (client-error-not-authorized) zurück
- iOS zeigt dem User "Drucken fehlgeschlagen" — der muss neues Profil
  installieren

**Was passiert wenn Queue-Berechtigung entzogen:**
- Server gibt IPP-Status `1030` mit Message zurück
- Genauso wie oben — User bekommt Fehler, muss neues Profil ziehen

---

## 5. TLS + Zertifikat

Apple ist wählerisch:
- Selbstsigniertes Zertifikat funktioniert **nicht** ohne manuelles
  "Trust" durch Admin (bei jedem User)
- **Let's Encrypt** oder **Azure Managed Cert** ist Pflicht
- Unser Server läuft schon HTTPS über `printix-sp.azurewebsites.net`
  → das reicht

Falls ein Kunde selbst-hostet:
- Docs müssen erklären dass sie Let's Encrypt via Caddy/Traefik/nginx
  brauchen — oder ein gültiges Enterprise-Zertifikat
- Fallback: Enterprise-Kunden können ihr eigenes Trust-Root pushen via
  MDM (schon in bestehender Firmen-Infrastruktur)

**Profil-Signing** (`.mobileconfig` selbst):
- Optional aber empfohlen (iOS zeigt sonst "Nicht signiert" — funktioniert
  trotzdem)
- Nutzt dasselbe TLS-Zertifikat via `cryptography.pkcs7.PKCS7SignatureBuilder`

---

## 6. UX-Ecken

### 6.1 Wie erklärt man dem User den Install-Prozess?

Die Seite `/my/airprint/new` hat einen Wizard mit **Schritt-für-Schritt-
Anleitung + Screenshots**:

1. "Klicke unten auf Download"
2. "iOS zeigt einen Dialog — bestätige"
3. "Öffne Einstellungen → Profil geladen → Installieren"
4. "Fertig — drucke aus Safari/Mail und wähle 'MySecurePrint'"

Deutscher + englischer Text (bestehende i18n-Infrastruktur nutzen).

### 6.2 Was wenn User mehrere Queues braucht?

Ein Profil = eine Queue. Sinnvoll weil:
- iOS-Print-Dialog wird sonst überladen mit vielen Druckern
- User weiß immer wohin er druckt
- Delegierten kann er nachträglich in der App wählen (bestehende
  Delegate-UI)

Für "Multi-Queue-User" (selten): mehrere Profile installieren.

### 6.3 Was passiert bei Firmenwechsel / User-Löschung?

- User-Delete kaskadiert → alle Profile revoked
- Admin-UI hat Massen-Revoke-Button
- Optional: MDM-Integration (Enterprise) — Profile per MDM zentral
  pushen und zurückziehen

---

## 7. Aufwand-Schätzung

| Komponente | Aufwand | Umfang |
|---|---|---|
| `ipp_server.py` + `ipp_parser.py` portieren + anpassen | 🟢 4 h | 900 LOC copy+adapt |
| `.mobileconfig`-Generator + Signing | 🟡 6 h | ~250 LOC neu |
| DB-Schema + Migration | 🟢 1 h | ~20 LOC SQL |
| Auth-Layer + Token-Verwaltung | 🟢 3 h | ~150 LOC |
| Web-UI `/my/airprint` (List + New + Download) | 🟡 6 h | 3 Templates + Routes |
| Web-UI `/admin/airprint` (Bulk-Verwaltung) | 🟢 3 h | 1 Template + Routes |
| iOS-App: QR-Code-Menüpunkt + Onboarding | 🟢 2 h | 1 View |
| Docs / i18n (de/en) | 🟢 2 h | Strings + Screenshots |
| End-to-End Test: echtes iPhone + Print aus Safari | 🟡 2 h | manuell |
| **Gesamt** | | **~29 h ≈ 4 Arbeitstage** |

Ambitious aber realistisch. Der IPP-Server-Code nimmt uns die
komplexeste Aufgabe ab.

---

## 8. Rollout-Plan

**Phase 1 — MVP (Interner Test):**
- IPP-Server portiert und `/airprint/{token}` reagiert auf iOS
- Profil-Generator + `/my/airprint/new` funktional
- Test auf 1 iPhone von 1 User → Print aus Safari klappt

**Phase 2 — Beta (Ausgewählte Kunden):**
- Admin-UI, Bulk-Revoke, Stats
- iOS-App Integration mit QR-Code-Onboarding
- Docs + Screenshots vollständig

**Phase 3 — Release (v0.8.0):**
- Änderungslog, Marketing-Text
- README erweitert
- App Store Screenshot: iOS-Print-Dialog mit "MySecurePrint"

---

## 9. Risiken

| Risiko | Mitigation |
|---|---|
| Apple ändert IPP-Handshake in iOS 27+ | Wir nutzen IPP 1.1 — Kernstandard, sehr stabil. `ipp_server.py` ist schon iOS 15–19 kompatibel |
| PDF-Konvertierung schlägt fehl bei bestimmten Dokumenten | iOS liefert immer PDF an AirPrint — Standard. Falls Fehler: Fallback auf raw PS/PCL wie bestehend |
| Server-Load skaliert nicht bei vielen Print-Jobs | Aktuell max 50 MB pro Job. Bei Bedarf: Cloudflare als Reverse Proxy vor Azure |
| Cloud-Server erreichbar aber Firewall blockiert Port 443 IPP | Sollte nicht passieren — 443/HTTPS ist überall offen. Falls doch: MDM-Konfig erweitern |
| Nutzer merkt nicht dass er in "MySecurePrint" gedruckt hat und sucht in Printix | Klare Naming-Konvention: iOS zeigt exakt den Queue-Namen den User beim Profil-Erstellen wählt |

---

## 10. Nicht-Ziele (v0.8.0)

- **Push-Benachrichtigungen bei Job-Status-Update** — später, braucht
  APNs-Relay (bereits im Backlog)
- **Preview-Rendering im Print-Dialog** — iOS macht das selbst
- **Custom Papiergrößen im Profil** — iOS wählt Default (A4/Letter),
  reicht für 99 %
- **Farb-/SW-Auswahl im Profil festgeschrieben** — iOS-Dialog fragt
  das eh; unser Server nimmt was iOS schickt

---

## 11. Nächster Schritt

Wenn du das Design ok findest, kann ich morgen früh anfangen:

1. Copy `ipp_server.py` + `ipp_parser.py` aus printix-mcp-linux
2. DB-Migration einbauen
3. `/airprint/{token}`-Wrapper-Handler bauen
4. `.mobileconfig`-Generator + minimales `/my/airprint/new`

Nach ~1 Tag hätten wir einen ersten Live-Test auf deinem iPhone.
Wenn der klappt, geht's an UI-Ausbau.
