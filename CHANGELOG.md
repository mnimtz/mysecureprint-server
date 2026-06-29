# Changelog — MySecurePrint Server

## 0.5.3 — 2026-06-29 — Mobile-Invite Bulk + Email-Template + Auto-User-Sync von Printix

User wollte: Email beim Printix-Import vorausfüllen, Bulk-Einladungen,
konfigurierbare Email-Vorlage, Auto-Sync alle X Min mit optionaler
Auto-Mobile-Invite.

### Mobile-Invite
- **Email-Prefill aus Printix**: Beim User-Import aus Printix wird die
  Email-Adresse jetzt automatisch in den lokalen User-Record übernommen
  (bisher leer falls Form-Field nicht ausgefüllt).
- **Bulk-Mobile-Invite**: Checkbox-Spalte in `/admin/users` + Bulk-
  Aktions-Button → `POST /admin/users/bulk-mobile-invite` erzeugt + sendet
  pro selektiertem User einen Invite in einem Schritt.
- **Email-Template-Editor**: neue Seite `/admin/email-templates` mit
  Subject + Body-Editor, Live-Vorschau, Placeholder-Liste (`{full_name}`,
  `{server_url}`, `{invite_url}`, `{expires_at}`, `{admin_name}`). Wird
  via `str.format_map(defaultdict(str, …))` substituiert — fehlende
  Placeholder werfen keinen Exception.

### Auto-User-Sync von Printix
- **Neue Settings**:
  - `printix_user_sync_enabled` (default 0)
  - `printix_user_sync_interval_minutes` (default 60, range 5..1440)
  - `printix_user_sync_auto_invite` (default 0)
  - `printix_user_sync_last_run_at` / `_last_result` (Status)
- **Admin-Seite `/admin/printix-sync`**: Toggle, Intervall-Picker, Auto-
  Invite-Toggle, „Jetzt synchronisieren"-Button, Last-Run-Status.
- **Background-Scheduler**: Startup-Event-Loop, fragt alle 5 Min ob
  enabled. Wenn ja + Intervall fällig → `_run_printix_user_sync_once`
  via `asyncio.to_thread`. Diff gegen lokale `users`-Tabelle, neue
  User werden mit role=employee + status=approved angelegt; Auto-Invite
  triggert pro neuem User einen 7-Tage Mobile-Invite mit Email-Versand.
- **Audit-Events**: `printix_sync_run`, `printix_sync_user_imported`,
  `printix_sync_settings_saved`, `mobile_invite_email_template_saved`.

### Sidebar
Unter „👥 Benutzer" zwei neue Einträge:
- „Printix-Sync" → `/admin/printix-sync`
- „E-Mail-Vorlagen" → `/admin/email-templates`

## 0.5.2 — 2026-06-29 — Section-Filter Entra-Split + MCP-DCR /oauth/register + Audit-iOS-Jobs + iOS Multi-Target

User-Feedback:
- „MS Entra Konfiguration in der Navi-Leiste, öffnet immer noch alle Optionen" → Entra-Sektion ist immer noch im großen General-Card gemixt
- „MCP für ChatGPT gibt 422 zurück: Dynamic client registration failed" → /register-Pfad-Konflikt
- „Im Audit-log sollten die Druck-Jobs via iOS ersichtlich sein. Filter Möglichkeiten" → fehlt
- iOS: Delegate-Toggle bewirkt nichts, Multi-User-Select fehlt, Share-Extension geht nicht
- „Normale Mitarbeiter sehen Benutzer + Workstations" → Role-Gate fehlt

### Section-Filter Entra-Split
Die große /admin/settings-Sektion „Erweiterte Einstellungen" enthielt
Server-URL + Mail + Entra in einem einzigen Card. `?section=entra`
zeigte das ganze Card → User sah Server-URL + Mail trotzdem.

Fix: jede Sub-Sektion bekommt eigenen `{% if section == ... %}` Gate.
`?section=entra` → nur Entra-Block. `?section=general` → Server-URL +
Mail + Backups. Ohne Section-Param → alles (Voll-Modus).

### MCP Dynamic Client Registration → /oauth/register
ChatGPT-Connector schickte den DCR-POST auf `/register`. Das ist in
FastAPI mit dem Admin-Registrierungs-Endpoint (Form-Body) kollidiert
→ 422 Unprocessable Entity wegen fehlender Form-Fields.

Fix: `/.well-known/oauth-authorization-server` veröffentlicht jetzt
`registration_endpoint = base/oauth/register`. Der OAuth-Middleware-
Pfad-Matcher in `oauth.py` akzeptiert beides (Rückwärts-Kompat).
ChatGPT sollte beim nächsten Connect funktionieren.

### Audit-Log iOS-Druckjobs + Filter (parallel-Agent)
- `_process_desktop_send_bg` schreibt jetzt `print_job_submitted` und
  `print_job_failed` ins Audit-Log inkl. target_id, source=ios_app,
  job_filename, error_code.
- Neuer Filter-Bar in `/admin/audit`: User-Suchfeld, Action-Dropdown
  (dynamisch aus DISTINCT), from/to Datum + Uhrzeit, Reset-Link.
- Query-Param-validated mit `?`-placeholders.

### iOS-Fixes (parallel-Agent)
- **Share-Extension entfernt** (Target + Sources + pbxproj-Eintraege).
- **Role-Gate ManagementView**: Benutzer + Workstations-Sektionen jetzt
  nur fuer Admin/User sichtbar, nicht fuer Employees.
- **Delegate-Toggle** umbenannt: „Delegation-Druck erlauben" / „Allow
  Delegation Print". Bug-fix: Multi-User-Picker auf Ziele-Tab.
- **Delegation-User-Picker**: wenn Toggle on, Suchfeld + Tap-to-add
  fuer beliebige Printix-User (id `print:user:<printix_user_id>`).
  Multi-Select, Job geht an alle gewaehlten gleichzeitig.
- **Build SUCCEEDED**.

### Entra Device-Code Diagnostik
User berichtet „kurz Code, dann no device". Polling-Endpoint verliert
das device_code aus der Session. Logging hinzugefuegt — bei naechstem
Auto-Setup-Versuch landet die Cookie/Session-Spur im Container-Log.

### /admin/groups Defensive 500-Schutz
`list_group_queue_defaults`-Call gewrappt in try/except, falls die
Migration aus irgend einem Grund nicht durchgelaufen ist.

### Server-side TODO
iOS-Picker setzt `print:user:<id>` als target_id, aber `/desktop/send`
versteht das noch nicht — Server-Implementation fuer „Job an anderen
Printix-User senden" (mit Auth-Policy + Audit) folgt in v0.5.3.

## 0.5.1 — 2026-06-29 — Sektion-Filter + Sidebar-Cleanup + Brand-Refresh + GDPR voll + Entra-LoginView-Fix

User-Feedback adressiert:
- „bei jedem Punkt links erscheinen rechts alle Punkte" → Sidebar-Links
  fuehrten zur grossen /admin/settings-Seite mit allen Sektionen
- „Sicherheit-Bereich braucht's nicht fuer Azure" → SSL/TLS/Tunnel-
  Seiten sind HomeAssistant-Relikte

### Eingebaut
- **Section-Filter** in /admin/settings: Sidebar verlinkt mit
  `?section=queue|printix|entra|legal`; Template zeigt nur die
  angeforderte Sektion. „Alle Einstellungen anzeigen →"-Link oben.
- **🔐 Sicherheit-Kategorie weg** aus der Sidebar (Routen bleiben
  erreichbar via direkter URL).
- **Brand-Refresh** (parallel Agent): Inter-Font via Bunny Fonts
  (DSGVO), modernes Token-System; Legacy --ta-* Variablen aliased.
- **GDPR-Export voll** (parallel Agent): neues `src/gdpr_export.py`
  mit komplettem User-Data-Sammler (audit_log, mobile_invites,
  cloudprint_jobs, delegations, cards etc.). Sensitive Felder
  redacted, Listen-Truncation, Smoke-Test bestanden.
- **iOS Entra-LoginView-Fix** (parallel Agent): MS-Fehlermeldungen
  aus dem Callback-URL werden jetzt im UI gezeigt statt generisches
  „Login fehlgeschlagen".
- **iOS App Store-Audit** (parallel Agent): App ist build-ready;
  Privacy-Manifest, Icons, Info.plist, Team-ID alles korrekt. Nur
  User-Side Tasks offen (App Store Connect Listing, Screenshots).

## 0.5.0 — 2026-06-29 — Queue-Hierarchie + 11 fehlende Employee-Templates + Audit-Fixes

Combined release: drei zusammenhängende Themen aus User-Feedback.

### 🔴 Audit-Fund: 11 Employee-Templates fehlten komplett
Jeder `/my/*` Click eines Employees führte zu `TemplateNotFound` → 500
Internal Server Error. Bedeutet: der gesamte Employee-Portal-Pfad war
unbenutzbar.

Neu geschrieben unter `src/web/templates/employee/`:
- `my_dashboard.html`, `my_jobs.html`, `my_delegation.html`,
  `my_cloud_print.html`, `my_send_to.html`, `my_mobile_app.html`,
  `my_reports.html`, `employees_list.html`, `employees_new.html`,
  `employees_detail.html`, `feature_locked.html` (663 LoC)
- Alle slim-konform: keine Capture/Reports/Guest-Print-Refs.
- `my_reports.html`: redirect-Stub auf `/admin/mcp-access` (Reports
  laufen jetzt via MCP-Tools).

### 🔴 Entra Device-Code zeigt nur „device_code_failed"
Microsoft's `error_description` wurde im `entra.py:start_device_code_flow`
verworfen → Admin sah keinen Hinweis warum Auto-Setup nicht ging.

Jetzt: `start_device_code_flow` propagiert die MS-Fehlermeldung als
`{"error": "..."}` Dict, der Web-Handler reicht das + ein Hinweis-Text
zu den 3 häufigsten Ursachen (Tenant-Policy / Netzwerk / MS down) ans
UI weiter.

### 🟡 6 dead Nav-Links repariert
`/admin/users/import-printix`, `/admin/mcp-reports-cookbook`,
`/settings`, `/dashboard` — alle entfernt oder auf existierende Routen
umgeleitet.

### 🆕 v0.5.0 Feature: 3-Tier Queue-Hierarchie
**Globale Default-Queue** + **Gruppen-Default** + **User-Override**.

Backend (`src/cloudprint/db_extensions.py`):
- Neue Tabelle `group_queue_defaults` (per-Sync-Gruppe Default-Queue)
- Migration: `cached_printix_users.groups_json` Spalte
- Helper-Funktionen: `get_global_default_queue`, `set_global_default_queue`,
  `is_user_queue_override_allowed`, `set_user_queue_override_allowed`,
  `list_group_queue_defaults`, `get_group_queue_default`,
  `set_group_queue_default`, `delete_group_queue_default`,
  `get_user_printix_group_ids`, `resolve_user_queue`
- Settings: `default_lpr_target_queue`, `default_lpr_target_queue_label`,
  `allow_user_queue_override`

`/desktop/targets` (`web/desktop_routes.py`):
- Ersetzt die alte Fallback-Kette durch `resolve_user_queue()` —
  Auflösungs-Reihenfolge User-Override → Group → Global → leer
- Response-Description zeigt jetzt die Quelle („Vom Admin festgelegt"
  / „Über Sync-Gruppe XYZ" / „Eigene Queue-Auswahl") damit iOS-User
  weiß wieso er DIESES Ziel sieht.

Admin-UI:
- Neue Sektion in `/admin/settings#queue` — globale Default-Queue
  Picker (Anywhere-Queues 🌐 oben sortiert) + Override-Toggle
- Neue Seite `/admin/groups` — pro Printix-Sync-Gruppe Default-Queue
  setzen, mit Live-Liste der Tenant-Gruppen
- Sidebar: zwei neue Einträge unter „Konfiguration" — „Standard-Druck-
  Queue" + „Gruppen-Defaults"

Audit-Events: `queue_defaults_saved`, `group_queue_set`,
`group_queue_cleared`

### i18n
~50 neue Keys (queue_*, groups_*, nav_cfg_queue, nav_cfg_groups)
in DE+EN mit Fallback.

## 0.4.7 — 2026-06-29 — Top-Bar mit User-Menü + Logout

User: „es gibt kein logout-button auf dem server". Der Logout war zwar
unten in der Sidebar (`<a href="/logout">`), wurde aber leicht
übersehen — Standard-Pattern ist oben-rechts.

Neue Top-Bar oberhalb der Breadcrumb:
- 👤 User-Name (+ „Admin"-Badge wenn applicable)
- 🚪 Logout-Button (rot, klar als „verlassen"-Aktion erkennbar)
- Sichtbar auf jeder Seite wenn eingeloggt

Der Sidebar-Bottom-Logout bleibt zusätzlich drin als Fallback (Mobile-
Hamburger-Pfad).

## 0.4.6 — 2026-06-29 — Nav-Restrukturierung + GDPR-Seite

User-Feedback: keine erkennbare Menüstruktur (Admin-Kategorie war
collapsed-by-default → User sah nur „Dashboard") plus GDPR-Settings
nirgends auffindbar.

### Neue Sidebar-Struktur (alle Kategorien open-by-default)
```
🏠 Dashboard
👥 Benutzer (Übersicht / Einladen / User anlegen / Bulk-Import)
⚙️ Konfiguration (Setup-Status / Printix / Entra / Legal / Erweitert)
🔐 Sicherheit (SSL / TLS / Auto-TLS / Tunnel)
🛡️ Datenschutz (Datenschutz-Settings / Audit / Privacy-Preview)
☁️ Cloud (Backup / MCP-Zugang)
```

### Neue Admin-Seite /admin/gdpr
- **Daten-Retention**: Audit-Log (default 365 Tage), Mobile-Invites
  (30 Tage), Session-Max-Age (168h), opt-in Auto-Löschung disabled
  User nach X Tagen (90).
- **DSAR-Export** (Art. 15 DSGVO): Form mit Email/Username → JSON-
  Download aller Subject-Daten — Datenauskunft-Anfragen direkt admin-
  bedienbar.
- **Right-to-be-forgotten** (Art. 17): Pointer auf /admin/users +
  Erklärung der Anonymisierung.
- **Privacy-Preview**: Links zu öffentlicher /privacy + /imprint +
  Edit-Button für die Settings.

### Audit-Events: gdpr_settings_saved, gdpr_export_user

### i18n: 35 neue Keys (nav_cat_*, nav_cfg_*, nav_gdpr_*, gdpr_*) in
DE+EN, EN-Fallback für die anderen 12 Sprachen.

## 0.4.5 — 2026-06-29 — Printix-Zugangsdaten editierbar + Anchor-Sprung

User stellte fest dass die Welcome-Status-Links für „Printix-
Zugangsdaten" und „Microsoft Entra ID" beide auf dieselbe Seite
(`/admin/settings`) führten — ohne dort eine Printix-Sektion vorzufinden.
Tenant-Credentials waren seit v0.1.0 NUR über den Register-Wizard
setzbar, nicht editierbar im laufenden Betrieb.

Eingebaut:
- Neue **Printix-Sektion** in `admin_settings.html` (anchor `#printix`)
  mit allen 5 API-Client-Pairs (Print/Card/Workstation/UserMgmt/Shared)
  + Tenant-ID + Tenant-Name. Felder zeigen aktuelle Client-IDs
  vorausgefüllt; Secrets bleiben leer (= unverändert). Verschlüsselte
  Speicherung via Fernet.
- Neuer POST-Endpoint `/admin/settings/printix` ruft
  `db.update_tenant_credentials()` — bestehende Tenant-Update-Logik aus
  der DB-Schicht wiederverwendet.
- Anchor `#entra` in der Entra-Sektion ergänzt — Sprung von Welcome.
- Welcome-Status-Links zeigen jetzt:
  - Printix → `/admin/settings#printix`
  - Entra → `/admin/settings#entra`
  - Legal → `/admin/settings#legal` (war schon richtig)
- Audit-Event `printix_credentials_updated`.
- Neue i18n-Keys (printix_creds_*) in DE+EN, Fallback Rest.

## 0.4.4 — 2026-06-29 — Breadcrumb "← Zurück zum Dashboard"

User landete auf `/admin/settings` / `/admin/mcp-access` etc. ohne
sichtbaren Zurueck-Pfad — Sidebar war zwar da, aber auf Mobile hinter
dem Hamburger und allgemein nicht so eindeutig wie ein expliziter
Zurueck-Link.

Eingebaut in `base.html`: Sticky Breadcrumb-Bar oben auf jeder
Unterseite (User eingeloggt + `active_page != welcome/my_portal`).
Verweist auf `/welcome` fuer Admins bzw. `/my` fuer Employees.

Plus die `_page_map` in app.py um die fehlenden Routen ergaenzt
(`/admin/blob-backup`, `/admin/mcp-access`, `/my/cloud-print`,
`/my/mobile-app`) — die hatten vorher kein `active_page` gesetzt und
wuerden ohne den Map-Eintrag keinen Breadcrumb zeigen.

Neue i18n-Keys: `breadcrumb_dashboard` (DE/EN).

## 0.4.3 — 2026-06-29 — Sidebar-Großputz + MCP im Setup-Status

User klickte „Benutzer aus Printix importieren" → 404 und vermisste den
MCP-Eintrag im Setup-Status-Dashboard. Auf der Sidebar gab's noch eine
ganze Reihe 404-Links aus dem Slim-Down die niemand entfernt hatte.

### 404-Links aus Sidebar entfernt
- 🏠 Dashboard (`/dashboard`) — gab keine Dashboard-Route mehr; ersetzt
  durch `/welcome` für Admins und `/my` für Employees (gleiches Icon,
  funktioniert jetzt).
- **„Printix Management"** komplette Kategorie (9 Links: `/tenant`,
  `/tenant/users`, `/tenant/printers`, `/tenant/queues`,
  `/tenant/workstations`, `/tenant/sites`, `/tenant/networks`,
  `/tenant/snmp`, `/tenant/demo`) — alle 404 seit Slim-Down. Equivalent
  jetzt via MCP-Tools erreichbar.
- **„Karten & Codes"** Kategorie (`/cards`) — 404.
- **„Fleet Management"** Kategorie (`/fleet`, `/fleet/package-builder`)
  — 404.
- Im Admin-Abschnitt: „Aus Printix importieren"
  (`/admin/users/import-printix`) — 404.
- Bottom-Sidebar: 🔑 Passwort ändern (`/settings/password`) — 404.

### Neue Status-Zeile im /welcome (Setup-Status)
- **MCP-Zugang (Claude/ChatGPT)** — zeigt grün/gelb je nach Aktivierung,
  Link „Configure →" geht direkt nach `/admin/mcp-access`.
- Default-Indikator: gelb (warn) + Text „deaktiviert (optional)" — damit
  klar ist dass MCP ein optionales Feature ist, nicht ein Pflicht-Setup.

### Neue i18n-Keys
- `welcome_status_mcp`, `welcome_status_mcp_on`, `welcome_status_mcp_off`
  in DE + EN.

## 0.4.2 — 2026-06-29 — Admin-Settings: tote Module-Sektionen weg

User entdeckte dass `/admin/settings` immer noch Eingabefelder für
Capture-Webhook + IPPS Cloud-Print + die Pro-Feature-Lizenz-Box zeigte
— alles Module die seit v0.1.0 nicht mehr existieren.

Entfernt aus `admin_settings.html`:
- **Pro-Feature-Lizenz-Box** (Lines 6–78) — `license.py` ist seit v0.2.2
  ein Stub, die Aktivierungs-Felder hatten keine Backend-Funktion mehr.
- **Capture-Webhook-URL**-Sektion + Beispiel-URL — Capture-Modul ist
  weg.
- **Cloud Print / IPPS**-Sektion mit ipps_public_url + ipps_port — IPPS-
  Listener wurde im v0.1.0 Slim-Down rausgeworfen.

Auch in `base.html`: das letzte „Pro-Features"-Kommentar war noch im
Employee-Sidebar-Bereich. Vereinfacht auf direkt zugängliches
„Employee-Portal" — der `pro_print_job_mgmt_enabled`-Gate war seit dem
license.py-Stub eh immer True, also redundant.

## 0.4.1 — 2026-06-29 — Fix Welcome-QR-Code (silent TypeError)

Welcome-Page + Mobile-Invite zeigten "QR unavailable" statt einen QR-
Code. `segno.save(stream, kind="svg", ...)` schreibt Bytes, nicht Text
— mit `io.StringIO()` als Stream wirft segno einen `TypeError: string
argument expected, got 'bytes'`, der von dem try/except geschluckt wird
→ leerer Return.

Fix: beide `_make_*_qr_svg`-Helper nutzen jetzt `io.BytesIO()` und
decodieren am Ende mit `.decode("utf-8")`. Die PNG-Variante in
`/admin/users/{id}/mobile-invite/{invite_id}/qr.png` war schon korrekt
(nutzte schon BytesIO).

## 0.4.0 — 2026-06-29 — MCP server zurück (opt-in)

Der MCP-Server für claude.ai / ChatGPT ist zurück — als optionales Feature
ohne die Reports/Capture/Demo-Lasten des Originals.

### Was dazukommt
- `src/server.py` (5000 Zeilen, **86 MCP-Tools**) — frisch aus dem
  printix-mcp-linux-Quellbaum geslimmt: 133 → 86 Tools. Behalten wurden
  alle Tools rund um User, Workstations, Cards, Printers, Queues,
  Networks, Sites, SNMP, Audit-Log, Tenant-Browsing und GDPR-Export.
  Gestrichen wurden Reports, Scheduler, Capture, Demo, Roadmap und alle
  Tools die intern auf `reporting`/`capture`/`guestprint`/
  `package_builder` zugriffen.
- `src/oauth.py` (707 Zeilen) — Multi-Tenant OAuth 2.0 Authorization
  Code Server für claude.ai/ChatGPT-Konnektoren (1:1 aus dem Original).
- `src/auth.py` (181 Zeilen) — Bearer-Auth-Middleware (Token → Tenant
  Lookup pro Request).
- Proxy-Routen in `web/app.py`: `/mcp`, `/sse`, `/messages`,
  `/oauth/*`, `/.well-known/*` — leiten an den internen MCP-Server
  (`127.0.0.1:8765`) durch, Streaming-by-default. Gated durch das
  `mcp_enabled` Setting: aus → 503, an → durchgereicht.
- `entrypoint.sh` startet jetzt **zwei** Prozesse: den MCP-Server im
  Hintergrund (intern), dann die Web-UI als Vordergrund-Prozess.
  SIGTERM räumt beide sauber ab.
- ARM/Bicep: neue Env-Variablen `MCP_PORT=8765` + `MCP_HOST=127.0.0.1`
  (intern only — Azure App Service exposed weiterhin nur Port 8080).

### Neue Admin-Seite
- `/admin/mcp-access` — Status-Übersicht, Aktivierungs-Toggle,
  Verbindungs-URLs für claude.ai / ChatGPT, Bearer-Token-Display
  (für Make.com / curl), OAuth-Client-ID/Secret-Display + Rotate-
  Buttons, kurze Anleitung pro Client.
- Sidebar-Nav: „MCP-Zugang" unter „System".
- Audit-Log-Events: `mcp_enabled_changed`, `mcp_bearer_rotated`,
  `mcp_oauth_rotated`.

### Sicherheit
- **Default-aus**: ein frisches Deployment hat den MCP nicht weltweit
  offen. Admin muss explizit den Schalter umlegen.
- Der MCP-Sub-Prozess bindet nur auf `127.0.0.1` — selbst wenn jemand
  den Toggle vergisst, gibt's keine direkte Außenwelt-Anbindung.
  Nur über den Proxy mit Setting-Check erreichbar.
- Bearer + OAuth-Credentials rotierbar mit einem Klick.

### Was NICHT wieder eingebaut wurde
- Reports + Scheduler (die ganzen `query_*`, `top_*`, `cost_*`-Tools)
- Capture (`send_to_capture`, `capture_status` etc.)
- Demo (`demo_generate`, `demo_rollback` etc.)
- Roadmap (`list_feature_requests` etc.)
- Guest-Print
- IPP/Cloud-Print-Listener

Diese Module sind in mysecureprint-server nicht vorhanden — die Tools
wären ins Leere gelaufen.

## 0.3.3 — 2026-06-29 — Fix /admin → 500 (missing template)

After successful admin registration the redirect target /admin tried to
render `admin_dashboard.html` which was dropped in the slim-down →
TemplateNotFound → 500 Internal Server Error.

- /admin now redirects authenticated admins to /welcome (the proper
  admin dashboard with config-status panel). The old handler had
  MCP/SSE/Tunnel-Info logic that's been irrelevant since v0.1.0 — full
  body removed.
- Non-admins hitting /admin go to their role-based home target.

## 0.3.2 — 2026-06-29 — MCP-Leftover-Bereinigung

User reported that the registration-success page still showed Bearer
Token + OAuth Client-ID/Secret + /mcp + /sse URLs — leftovers from the
printix-mcp-docker fork. The MCP server was dropped in v0.1.0 but
several user-visible references survived. Cleaned up:

- `register_success.html`: dropped Bearer-Token + OAuth + /mcp + /sse
  blocks. Replaced with a 5-step onboarding checklist (Printix creds →
  Entra setup → Legal → Cloud-Backup → Invite users) plus deep-links to
  the relevant admin sections.
- `register_step4.html`: summary table now shows `{{ base_url }}`
  instead of `{{ base_url }}/mcp`.
- `admin_settings.html`: removed the MCP/SSE/OAuth URL list under
  "Current URL" info — just shows the base URL now.
- `base.html` sidebar:
  - removed the Reports category entirely (reports/ submodules were
    dropped in v0.1.0 → all 404)
  - removed the Pro Features category (capture + guestprint were
    dropped → 404; /my for employees still reachable via the bottom
    section)
  - removed `/admin/mcp-permissions` (RBAC) — MCP-only feature
  - removed `/admin/mcp-reports-cookbook` (footer + reports nav)
  - removed bottom-sidebar 🔌 Connect, ❓ Help, 💬 Feedback links
    (Connect-Center was the MCP client-config page; Help was an alias
    of Connect-Center)
  - kept `/admin/audit` and moved it under System
- `/my/connect` route now redirects: employees → `/my/mobile-app`,
  admins → `/admin`. Old template file `my_connect.html` deleted.
- `/help` route redirects the same way (alias).
- New i18n keys for the rewritten success page:
  `reg_success_next_steps_intro`, `reg_success_step_printix(_help)`,
  `reg_success_step_entra(_help)`, `reg_success_step_legal(_help)`,
  `reg_success_step_backup(_help)`, `reg_success_step_users(_help)`,
  `reg_pending_explainer` — in DE + EN with EN-fallback for the others.

## 0.3.1 — 2026-06-29 — Restrict landing UX (no public config leakage)

The `/welcome` page used to be public and showed status indicators
revealing which modules (Printix, Entra, Legal, Admin) were configured
or missing. That leaked operational info to any anonymous visitor and
also confused fresh-deploy admins (clicking "Configure →" hit a login
wall with no obvious next step).

### Changes
- `GET /` redirect logic:
  - no users yet → `/register` (first-admin wizard)
  - logged in → `_user_home_target(user)` (`/admin` for admins,
    `/my` for employees)
  - anonymous → `/login` (which already shows the Microsoft SSO button
    when Entra is configured, so end-users sign in with one click)
- `GET /welcome` now requires an authenticated admin. Non-authenticated
  visitors are sent to `/login`; logged-in non-admins go to their
  role-based home (`/my` for employees). The status-indicator dashboard
  stays exactly as designed — it just isn't world-readable anymore.
- Sidebar nav: added "Setup-Status" and "Cloud-Backup" entries under
  the admin "System" sub-group so admins can find the welcome dashboard
  and blob-backup page via the menu.
- New i18n keys: `nav_setup_status`, `nav_blob_backup` (DE/EN, EN
  fallback for the others).

## 0.3.0 — 2026-06-29 — Blob auto-backup + i18n hardening

### Cloud-Backup nach Azure Blob Storage (new)
- New module `src/blob_backup.py` — wraps `backup_manager.create_backup()`
  with an upload to Azure Blob Storage. Survives loss of the `/data`
  Azure-Files mount. Double-encrypted: Fernet at app layer + Azure
  encryption at rest.
- New admin page `/admin/blob-backup` with status panel, configuration
  form, blob list, manual "Run now" button, and one-click restore-from-blob.
- New DB settings: `blob_backup_enabled`, `blob_backup_connection_string`
  (Fernet-encrypted), `blob_backup_container` (default
  `mysecureprint-backups`), `blob_backup_passphrase` (Fernet-encrypted),
  `blob_backup_retention_days` (default 30), `blob_backup_last_run_at`,
  `blob_backup_last_result`.
- New audit-log events: `blob_backup_settings_saved`,
  `blob_backup_run_manual`, `blob_backup_restored`.
- Daily background scheduler in `web/app.py` startup-event — fires once
  per 24h when `blob_backup_enabled=1`, calls `run_once()` on a worker
  thread (offloaded with `asyncio.to_thread`) so it doesn't block the
  event loop.
- Auto-prune of old blobs based on `retention_days`. 0 = keep forever.
- ARM/Bicep templates updated: auto-create blob container
  `mysecureprint-backups` in the same Storage Account, plus
  `AZURE_STORAGE_CONNECTION_STRING` env variable pre-populated so the
  feature works out of the box (admin only has to set the encryption
  passphrase + toggle on).
- New dependency `azure-storage-blob>=12.19.0`.

### i18n hardening
- 6 hardcoded strings extracted from `welcome.html` (Copy / QR / QR
  unavailable / Setup / Server Status / Configure → 4×) and now use
  `{{ _(...) }}`.
- 10 hardcoded German strings in `web/app.py` extracted to translator
  calls — covers user-registration form validation, OAuth callback
  errors, and CSV bulk-import error details that previously showed
  German text even for English/French users.
- Translation gap from prior audit closed: ~10,777 missing entries
  filled across fr/it/es/nl/no/sv plus four DE dialects
  (bar/hessisch/oesterreichisch/schwiizerdütsch) and two EN dialects
  (cockney/us_south). The longer admin help-text strings fall back to
  English in non-DE/EN languages — explicit human translation
  recommended before going public in those locales.
- New `_V030_KEYS` block at the bottom of `i18n.py` defines the new
  v0.3.0 keys (welcome + blob backup) in DE + EN with EN-fallback
  for all other supported languages.
- Defensive stub `src/license.py` so leftover legacy `from license
  import is_feature_enabled` calls in admin routes don't crash —
  `is_feature_enabled()` always returns True (matches the v0.1.0
  "everything always-on" design).

### Removed orphan import
- `from package_builder import PackageBuilderCore` in `create_app()`
  was a Workstation-Agent leftover and crashed every container start
  on Azure App Service. Removed in v0.2.2 (during the deploy
  troubleshooting that motivated the diagnostic logging in
  `entrypoint.sh`); explicitly noted here for the v0.3.0 release notes.

## 0.2.0 — 2026-06-29 — iOS Onboarding: Email-Deeplink + Admin-QR

Admins can now invite users to the MySecurePrint iOS app with a single
click. The user receives an email (or QR code, or both) containing a
one-time redemption URL. The iOS app on iPhone receives a pre-configured
server URL — no manual typing.

### New
- DB: `mobile_invites` table (id, user_id, token, token_hash,
  server_url, ttl_seconds, created_at, expires_at, redeemed_at,
  redeemed_from, created_by, channel, email_sent_at, email_recipient)
  with idempotent migration via `_init_mobile_invites_schema()`.
- Admin routes:
  - `GET /admin/users/{id}/mobile-invite` — manage page
  - `POST /admin/users/{id}/mobile-invite/create` — create invite
  - `POST /admin/users/{id}/mobile-invite/{invite_id}/email` — resend
  - `POST /admin/users/{id}/mobile-invite/{invite_id}/revoke`
  - `GET  /admin/users/{id}/mobile-invite/{invite_id}/qr.png`
- Public route: `GET /m/setup?i=<token>` shows an explainer page on iOS
  (with App-Store link if app not installed) and offers the deep-link
  `mysecureprint://setup?server=...&token=...` directly.
- API: `POST /api/v1/mobile-invite/redeem` — iOS app exchanges the
  token + MS-signed-in identity for a permanent Bearer token. Returns
  410 Gone on already-redeemed/expired (idempotent).
- New templates: `admin_user_mobile_invite.html`, `m_setup.html`.
- Existing `/admin/users/invite` now has a "Mobile Invite" checkbox
  (default ON) — admin creates user + mobile invite in one step.
- New "📱 Mobile invite" action button per row in `admin_users.html`.
- Audit log: 4 new event types — `mobile_invite_created`,
  `mobile_invite_sent_email`, `mobile_invite_redeemed`,
  `mobile_invite_revoked`.
- Token is `secrets.token_urlsafe(32)` (≈256 bits). Only the SHA-256
  hash is persisted after creation; the raw token is shown to the
  admin exactly once.
- Single-use enforcement: redemption is atomic via UPDATE with
  redeemed_at + expires_at predicate; second redeem returns 410.
- GC: `cleanup_expired_pending()` now also sweeps abandoned
  (expired + unredeemed) `mobile_invites` rows.

### Defaults from the 8-question design review
1. Custom URL scheme `mysecureprint://setup` (Universal Links → v0.3)
2. Invite TTL: 7 days default, override per-invite to 24h / 30d
3. QR token stable until redeemed (no per-view regen)
4. Combined account-create + mobile-invite checkbox default ON
5. Copy-link fallback when SMTP isn't configured
6. Self-service /my/mobile-app/qr.png preserved alongside admin push
7. TestFlight: explainer page with App-Store link, no forced redirect
8. MS sign-in still required after redemption (audit-trail value)

### iOS-side follow-up
The MySecurePrint iOS app needs a corresponding update to handle the
`mysecureprint://setup` URL scheme. That commit lives in
`printix-mcp-addon/MobileApp/ios-client/` and ships in iOS app v1.1.0.
Until then, users see the explainer page with a "Copy URL" fallback.

### Effort
~10 hours (matches design estimate)

## 0.1.3 — 2026-06-29 — Entra hardening (continuous evaluation + GC + secret expiry warnings)

Five 🟠 items from ENTRA_REVIEW.md.

### Pending-tables GC sweep (5 min interval)
Both `desktop_entra_pending` and `desktop_entra_authcode_pending`
now have an automatic background cleanup task that runs every 5
minutes. Stops these tables from growing unbounded over time.

### Single-tenant App Registration default
Auto-setup wizard now creates the Entra App Registration with
signInAudience=AzureADMyOrg (single-tenant) by default. Existing
deployments are unaffected — only newly auto-created apps get the
new default. The setting `entra_app_audience` allows opting back into
multi-tenant for advanced cases.

### Continuous evaluation (24h background task, opt-in)
New setting `entra_continuous_eval_enabled` (default off). When on, a
daily background task uses the stored MS refresh_token to verify that
each Entra-signed-in user is still active in their tenant. If MS
returns `invalid_grant` or `interaction_required`, the user's server
Bearer token is revoked, effectively logging them out within 24h.

### Secret-expiry warnings
Auto-setup wizard now records the secret's expiry date. When the
secret has <60 days left, a yellow banner appears on /admin/settings
and the /welcome status indicator for Entra turns yellow. Admin can
trigger "Rotate Entra client secret" to create a fresh one via MS
Graph without redoing the entire Entra setup.

### refresh_token storage (Fernet-encrypted)
The `users.entra_refresh_token` column was added (idempotent
migration). Stored Fernet-encrypted. Used only for continuous-eval
(see above) — not exposed to clients.

## 0.1.2 — 2026-06-29 — Entra ID security hygiene fixes

Three critical fixes identified in `ENTRA_REVIEW.md`.

### #1 — Verify `tid` claim against configured Entra Tenant ID
Previously the server accepted ANY Microsoft account if
`entra_tenant_id` was unconfigured (fell back to `common`). Now the
server refuses to start an Entra flow when `entra_tenant_id` is empty
(`is_enabled()` returns False, `build_authorize_url*` returns None)
and verifies the `tid` claim on every returned token — both in the
web Authorization-Code flow (`exchange_code_for_user`) and the iOS
PKCE flow (`exchange_code_pkce`). Foreign-tenant sign-ins are
rejected with an audit-log line `Entra rejected signin: tid mismatch
(got=X expected=Y)`.

### #2 — Stop linking accounts by email
Email-based account-linking on Entra sign-in was the second half of
the same attack vector. `get_or_create_entra_user` now matches
strictly on `entra_oid`; the email-fallback branch is gone. If the
oid is unknown, the function only auto-creates a new account when
`entra_auto_approve` is enabled. A bootstrap exception kicks in when
the DB is empty: the very first Entra sign-in becomes admin (so the
auto-setup wizard still works). Existing local accounts must be
linked explicitly by an admin before their owner can sign in via Entra.

### #3 — Delete pending row at start of exchange, not at end
A failed Microsoft token exchange used to leave the `state` row
behind for 10 minutes, allowing the same value to be replayed. The
row is now deleted as soon as it's found in `/desktop/auth/entra/
authcode/exchange`, before any downstream Microsoft call. Plus a
constant-time `state` compare (`secrets.compare_digest`) and an
opportunistic sweep of expired rows on each exchange.

## 0.1.1 — 2026-06-29 — Public welcome page with QR

New `/welcome` route (also default at `/` for fresh deployments) shows
the server's URL, an iOS-setup QR code, setup-status indicators
(Printix / Entra / Legal / Admin), and quick-action buttons. Helps
fresh-deploy users find their footing without scrolling through Azure
Portal outputs.

- QR encodes `mysecureprint://setup?server=<url>/` — forward-compatible
  deep-link for the planned v0.2.0 iOS auto-onboarding feature
- Status indicators link directly to the relevant `/admin/settings`
  sub-sections
- Public (no login required) — safe to link from emails / IT docs
- i18n DE+EN, other languages via EN-fallback
- Re-uses the already-bundled `segno` QR library — no new dependency

## 0.1.0 — Initial release

Slim Azure-deployable print backend for the **MySecurePrint** iOS companion app.

Forked from `printix-mcp-docker` v7.9.4 with focus reduced to:

- iOS app endpoints (`/desktop/auth/entra/*`, `/desktop/cards/*`, `/desktop/management/*`)
- Web upload + print conversion (`/my/upload`) — Word/JPG/PDF → PCL XL via LibreOffice + Ghostscript
- End-user management: register, invite, Microsoft Entra SSO, local accounts
- Admin: Printix-API-Credentials, audit log, backup, HTTPS setup (Cloudflare Tunnel / Auto-TLS / manual cert)
- Public legal pages: `/privacy`, `/datenschutz`, `/imprint`, `/impressum`, `/legal`
- 1× Printix tenant per deployment

### Removed compared to printix-mcp-docker

- MCP server entirely (no `/mcp`, `/sse`, OAuth-as-issuer)
- Reports + Scheduler + Report-Mail
- Capture webhook + Guest-Print mailboxes
- IPP/IPPS cloud-print listener (port 631)
- Dashboard + Tenant-Browser
- Pro-Feature license system (everything always-on)
- Roadmap feature

### Azure-Deploy

- `deploy/azure/azuredeploy.json` — ARM template, default B1 App-Service-Plan
- `deploy/azure/main.bicep` — Bicep equivalent
- "Deploy to Azure" button in README — 5-min one-click setup
- Container published to `ghcr.io/mnimtz/mysecureprint-server:latest` (multi-arch amd64/arm64) via GitHub Actions
