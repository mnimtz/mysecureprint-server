# Changelog — MySecurePrint Server

## 0.7.29 — 2026-06-30 — Security-Härtung aus 3-fach-Audit

**Critical**

- Session-Fixation behoben: Beim Login (lokal + Entra) wird die Pre-Auth-Session jetzt verworfen bevor `user_id` gesetzt wird. Vorher konnte ein vom Angreifer gepflanzter Session-Cookie nach erfolgreichem Login mitlaufen.
- `get_delegations_for_delegate(user_id)` lieferte für Printix-Only-Delegationen (`delegate_user_id=''`) und Email-basierte Delegate-Einträge keine Ergebnisse — der Delegate sah seine Owner gar nicht. Match jetzt auch über `delegate_printix_user_id` und `delegate_email`.
- Timing-Side-Channels weg: Bearer-Token-Fallback in `db.py` und OAuth-Client-Secret-Prüfung in `oauth.py` nutzen jetzt `hmac.compare_digest`.
- User-Enumeration-Defense: `authenticate_user` läuft jetzt auch bei nicht-existierendem User durch `verify_password` mit einem Dummy-Hash → kein Timing-Channel mehr zwischen "User existiert nicht" und "Passwort falsch".

**High**

- Session-Cookie ist jetzt `https_only=True` (Override via `SESSION_COOKIE_INSECURE=1` für lokale HTTP-Tests).
- Admin-Toggles `/admin/api-trace/toggle` und `/admin/perf-logs/toggle` akzeptieren keine GETs mehr (waren via `<img src=…>` von extern triggerbar bei eingeloggtem Admin).
- BearerAuthMiddleware unterscheidet jetzt zwischen DB-Down (`503`) und ungültigem Token (`401`) — vorher landete jeder DB-Fehler als "Invalid bearer token" und versteckte Outages.
- Open-Redirect-Schutz bei `/my/employees/delegation/*/{approve,reject}` — Referer wird gegen Same-Host whitelisted, sonst fallback auf `/my/employees`.
- MCP-Proxy hatte `timeout=None` → hängender Sub-Prozess blockierte Worker-Slots dauerhaft. Jetzt `connect=5s/write=30s/pool=5s`, read=None nur für SSE.
- MCP-Proxy-Fehlertext nicht mehr roh an Client (versteckte lokale Pfade/Versionen) — nur generische Message + Server-Log.
- Background-Tasks (4 Scheduler) werden jetzt in einem Modul-Set gehalten, damit der Event-Loop sie nicht via Weak-Ref GC-killen kann.
- `printix_client.upload_file_to_url` schloss die `requests.Session` nicht — pro Upload leakte ein Connection-Pool. Jetzt `with` statement.

**iOS-Client**

- `SettingsStore.bearerToken.didSet`: löscht den UserDefaults-Spiegel nur noch wenn der Keychain-Write erfolgreich war — sonst standen User ohne Token da bei provisionierungs-Fehlern.
- `SetupView`: strenge Server-URL-Validierung (Scheme http/https, nicht-leerer Host mit Punkt oder `localhost`) — vorher reichte jeder zufällige String.
- `SetupView.redeemMobileInvite`: nicht mehr alles als `URLError(.userAuthenticationRequired)` verklausuliert; Server-Error-Body wird geparsed und als NSError mit echter Message hochgereicht.

## 0.7.28 — 2026-06-30 — Guest-Print / Email-to-Print + Mail.Send Consent + Email-Template-Fixes

**Neu — Guest-Print / Email-to-Print Gateway**

- Modul `src/guestprint/` mit DB-Layer (`store.py`), Graph-Mail-Poller (`poller.py`)
  und async Runner (`runner.py`) für die `guestprint_mailbox`/`guestprint_guest`/`guestprint_job`-Tabellen.
- Admin-UI unter `/admin/guestprint`: Mailbox-CRUD, Gast-Whitelist mit Ablauf-Tagen,
  Job-Log, manueller Poll-Trigger, globaler An/Aus-Schalter.
- Sicherheits-relevante Härtung:
  - `validate_email_address`: blockiert `\r\n\t\x00` und Whitespace.
  - `_safe_filename`: Whitelist `[A-Za-z0-9._\- ]`, kein Path-Traversal.
  - MIME/Erweiterung-Whitelist (PDF/PNG/JPEG nur), Hard-Cap (Default 25 MiB,
    Server-Cap 100 MiB), Re-Check nach Download gegen lügende Graph-Size.
  - Multi-Worker-sicher: `try_acquire_poll_lock` macht atomares SQL-UPDATE
    auf `last_poll_at` — kein Doppelpoll wenn mehrere uvicorn-Worker laufen.
  - Tenant-Isolation: alle Admin-Routes prüfen `mb.tenant_id == active`.
  - Idempotenz via UNIQUE `(mailbox_id, message_id, attachment_name)`.

**Neu — Mail.Send / Mail.Read Auto-Consent im Entra-Setup**

- `auto_register_app(include_mail_send=True)` erteilt jetzt direkt nach
  App-Registration den Tenant-weiten Admin-Consent für die `Mail.Send`
  (und optional `Mail.Read`) App-Role via `/servicePrincipals/{sp}/appRoleAssignments`.
  Vorher musste der Admin den Consent manuell im Azure Portal klicken.
- Neuer Public-Helper `grant_mail_app_role_consent()` für nachträgliche
  Erteilung wenn der initiale Grant fehlgeschlagen ist (z.B. weil der
  Device-Code-Admin keinen Global-Admin-Status hatte).
- Device-Code-Scopes erweitert um `AppRoleAssignment.ReadWrite.All`.

**Fix — Email-Templates Lokalisierung + Platzhalter**

- Neuer Platzhalter `{qr_code}`: Admin kann den QR-Block frei platzieren.
  Wenn nicht im Template enthalten, wird er wie bisher am Ende angefügt.
- Neuer Platzhalter `{app_store_url}`: Verweist auf MySecurePrint im App Store
  (per `ios_app_store_url`-Setting überschreibbar).
- Neue Sprach-Wahl im Template-Editor (`mobile_invite_email_lang`-Setting).
  Vorher: Admin schrieb englischen Body, aber Preview zeigte deutschen
  QR-Label/Footer weil i18n-Bausteine die Admin-UI-Sprache zogen → unstimmig.
  Jetzt: Body + Bausteine in der gleichen, explizit gewählten Template-Sprache.
- Preview und echter Versand verwenden konsistent dieselbe Template-Sprache.

**Public Helper**

- `mail_client.get_graph_token()` liest Tenant/Client/Secret aus den Settings
  und liefert ein App-Only Graph-Token — dient als Basis für den Email-Poller.

## 0.7.27 — 2026-06-30 — Entra-Auto-Setup: App-Name + Auto-Tenant-Consent

**Geändert:** Beim Entra-Auto-Setup via Device-Code-Flow heißt die im Azure-Tenant
angelegte App jetzt **„MySecurePrint"** (vorher „Printix Management Console").
Betrifft nur Neu-Einrichtungen — bestehende Registrierungen muss man in Azure
Portal → App registrations → Properties → Display name manuell umbenennen.

**Neu:** Auto-Setup erteilt jetzt automatisch **Tenant-weiten Admin-Consent**
für `openid`/`profile`/`email`/`User.Read` via `oauth2PermissionGrants` mit
`consentType=AllPrincipals`. Folge: Normale User sehen beim ersten Microsoft-
Login **keinen Permissions-Screen** mehr, sondern landen direkt in der App.

Voraussetzung: Device-Code-Flow holt zusätzlich `DelegatedPermissionGrant.ReadWrite.All`
— der Admin sieht beim Einrichten also eine etwas längere Permission-Liste.

Wenn der Grant fehlschlägt (z.B. Tenant verlangt strikt manuellen Consent),
liefert `auto_register_app` `admin_consent="grant_failed"` zurück und der Admin
muss in Azure Portal manuell „Grant admin consent" klicken.

## 0.7.26 — 2026-06-30 — Delegation-Druck als Admin-Toggle (Server-Side)

User-Wunsch: Statt dass jeder iOS-User selber den Delegate-Toggle in
den App-Settings aktivieren kann, soll der Admin das vom Server aus
zentral steuern.

Neuer Setting `delegation_print_allowed` (default OFF):
- `/admin/settings?section=queue` — neuer Checkbox „Delegation-Druck
  erlauben" unterhalb des Queue-Override-Toggle.
- Persistierung via existierender queue-defaults/save Route.

Server-Enforcement:
- `/desktop/targets` liefert nur Delegate-Targets wenn aktiv +
  Response-Feld `delegation_allowed: bool` fuer den iOS-Client.
- `/desktop/send` blockt `print:delegate:*` und `print:user:*`
  Direct-API-Calls wenn deaktiviert (defense-in-depth — auch wenn
  iOS-Targets nicht gerendert werden, gehen sonst Curl-Calls durch).

iOS-Seite (follow-up Patch): Local-Toggle nur sichtbar wenn
delegation_allowed=true; sonst Hinweis „Vom Admin deaktiviert".

## 0.7.25 — 2026-06-30 — Live-Lookup Response unwrappen ({"user": {...}})

v0.7.24 Live-Fallback failed weil Printix's get_user() den User in
{"user": {...}, "success": true, "message": "OK"} wrappt. Wir griffen
auf live.get('email') (Top-Level) zu — leer → wieder „not found".

Fix: erst `live.get('user', live)` unwrappen, dann email/name aus dem
inner dict ziehen.

Damit funktionieren Delegation-Picks an User die nicht im Cache sind
(z.B. Guest-User die erst gerade hinzugefuegt wurden) sauber.

## 0.7.24 — 2026-06-30 — Delegation: Live-Printix-Lookup als Cache-Miss-Fallback

User-Report: print:user:<id> failed mit „delegation user not found or
has no email" obwohl der User per Picker sichtbar war.

Root-Cause: Picker holt User LIVE von /desktop/management/users (per
Printix-API). Submit-Lookup geht aber gegen `cached_printix_users`-
Tabelle. Wenn der Cache leer/stale ist (Printix-User-Sync nicht
gelaufen) → Mismatch → target_not_found.

Fix: bei Cache-Miss wird der User jetzt LIVE per
`client.get_user(printix_user_id)` aufgeloest. Email + Name werden
genauso lowercase normalisiert wie der gecachte Pfad.

Empfehlung: trotzdem regelmaessig User-Sync laufen lassen
(Performance + Offline-Tauglichkeit).

## 0.7.23 — 2026-06-30 — Delegate-Pfade auch lowercase (consistent mit v0.7.22)

v0.7.22 hat owner_email (print:self) auf lowercase gestellt, aber die
zwei Delegate-Pfade (print:delegate:<id> und print:user:<id>) nahmen
den email-Wert weiter raw aus DB. Wenn der Delegate-Eintrag mit
mixed-case Email (z.B. „Maik.Schneider@kofax.email") angelegt wurde,
schlaegt change_job_owner spaeter mit USER_NOT_FOUND fehl.

Fix: beide Delegate-Pfade lowercased die email vor Verwendung als
submit_user_email.

## 0.7.22 — 2026-06-30 — DEFINITIVER FIX: Email lowercase (Printix case-sensitive verifiziert)

Direkter Test gegen Printix Cloud Print API bestaetigt zwei Wahrheiten:

**Test 1: Anywhere-Queues sind broken im Tenant** (egal welche Email,
egal welcher release-Mode) — 44s timeout, 500 UNKNOWN_ERROR.
  → Printix-Support kontaktieren mit den ErrorIDs.

**Test 2: change_job_owner + submit_print_job sind CASE-SENSITIVE**
auf Emails. Mit derselben Brother-MFC-Queue:

  userEmail=Marcus@nimtz.email → 404 USER_NOT_FOUND
  userEmail=marcus@nimtz.email → 200 OK ✅

Mein v0.7.8 Lowercase-Fix war richtig. Mein v0.7.10 Revert war
falsch — basierte auf dem User-Listen-Display in der Web-UI, das
NICHT die canonical email zeigt. Die canonical email IST lowercase.

Fix: `owner_email` wieder konsequent `.strip().lower()` an allen
Quellen (users.email, cached_printix_users.email, find_printix_user_
by_identity).

## 0.7.21 — 2026-06-30 — Self-Test-Endpoint /admin/printix-submit-selftest

Neuer Admin-Endpoint der auf dem Server selbst 6 verschiedene Submit-
Varianten gegen den echten Printix-Endpoint testet und die Antworten
side-by-side als JSON zurueckgibt.

  GET /admin/printix-submit-selftest
  GET /admin/printix-submit-selftest?queue=<uuid>&email=<your-email>

Varianten:
  v1 full_body + release_false (= unser aktueller Default)
  v2 full_body + release_true
  v3 minimal + release_false (kein color/duplex/copies)
  v4 minimal + release_true
  v5 no user, no pdl, release_false
  v6 no user, full body, release_false

Pro Variante: HTTP-Status + Printix-ErrorID + Response-Body. Damit
sehen wir empirisch welche Kombi durchgeht. Wenn ALLE 6 failen, ist
das Bug ausserhalb des Bodies (Auth/Tenant/Lizenz).

## 0.7.20 — 2026-06-30 — Submit-Retry mit minimalem Body bei 500

Wenn der Submit mit vollem Body ({color, duplex, copies}) 500 zurueckgibt,
wird automatisch ein zweiter Submit mit MINIMALEM Body ({}) versucht.
Damit testen wir empirisch ob ein Body-Feld die UNKNOWN_ERROR-Ursache ist.

Log-Verhalten:
- 'submit OK (full body)' -> alles gut
- 'submit 500 mit full body … Retry mit minimal body' -> erstes Submit
  failed, Retry laeuft
- 'submit OK (minimal body)' -> Body-Feld war der Schuldige; muss
  identifiziert werden
- 'submit 500 auch mit minimal body' -> Bug ist nicht im Body, sondern
  in Auth/Tenant-Config/Premium-Lizenz

## 0.7.19 — 2026-06-30 — /health/perf Diagnose-Endpoint + Slow-Log immer aktiv

User-Report: jede Admin-Seite + iOS-Send braucht ~2 Min, trotz v0.7.15
Performance-Pass. Heisst die Bremse ist tiefer.

Neuer unauth Diagnose-Endpoint:

  GET /health/perf

Misst pro DB-Operation die Latenz in Millisekunden:
- t_conn_open_ms — Connection-Open
- t_select1_ms — trivialer SELECT 1
- t_count_audit_ms — COUNT(*) audit_log + Anzahl
- t_pragmas_ms — PRAGMA-Reads + Werte (journal/synchronous/cache_size)
- t_total_ms — Summe

Erwartung: alles <50ms bei lokalem Disk. >500ms = SMB-Mount-Latenz
(Azure Files). Ueber 2000ms = ernstes Problem.

Plus: admin_audit Slow-Log nicht mehr gated auf perf_logs_enabled —
laeuft IMMER wenn dt_total >500ms. Damit sehen wir bei naechstem
2-Min-Hang sofort die Breakdown im Container-Log.

## 0.7.18 — 2026-06-30 — Sorry-Revert: release_immediately=False + change_job_owner Duplikat raus

Sehr peinliche Erkenntnis nach 1:1-Vergleich mit dem nachweislich
funktionierenden `printix-mcp-linux/src/web/employee_routes.py:752` und
`forwarder.py:153`:

  Beide nutzen `release_immediately=False`.

Mein v0.7.15-Fix („release_immediately=True ist das Default des alten
Codes") basierte auf der MCP-Tool-Signature in `printix-mcp/server.py`,
NICHT auf dem produktiven Print-Flow. Der produktive Code in
printix-mcp-linux nutzt False. Damit reverte ich v0.7.15 in diesem
Punkt.

Bonus-Fix: `printix_client.py` hatte ZWEI `change_job_owner`-Definitionen
(Line 432 + Line 507). Python verwendet last-definition-wins, daher
ueberschrieb die form-urlencoded-Variante (`data={userEmail}`) die
korrekte JSON-mit-Query-Param-Variante. Die zweite Definition entfernt.

Damit ist der Submit-Aufruf jetzt BIT-IDENTISCH zu der Variante die in
printix-mcp-linux nachweislich funktioniert. Wenn jetzt immer noch 500
zurueckkommt, liegt's mit hoher Sicherheit an den Printix-OAuth-
Credentials (falscher Scope) bzw. der Premium-Lizenz, nicht am Code.

## 0.7.17 — 2026-06-30 — Diagnose-Marker fuer /desktop/send Upload-Latenz

User-Report: 300 KB JPG braucht Minuten beim Senden aus iOS. Zwei
Diagnose-Marker (immer aktiv, nicht gated):

  Desktop-Send INGRESS — target=… peer=…
  Desktop-Send BODY-RECEIVED — target=… size=… dt_read=Xms

INGRESS = der HTTP-Request erreicht den Handler. BODY-RECEIVED = der
Multipart-Body ist vollstaendig gelesen. Differenz INGRESS→BODY-
RECEIVED ist die Upload-Wartezeit (vom Server-Standpunkt).

Damit unterscheidbar: ist der Upload netzwerkseitig langsam
(Body kommt langsam an) oder hat Azure App Service Probleme
(Body schnell da, aber Handler wartet).

iOS-Seite: URLSession-Timeouts gesenkt
- timeoutIntervalForRequest: 900 → 60 s (= 60s zwischen Datenpaketen)
- timeoutIntervalForResource: 1800 → 180 s (= 3 min Total-Upload)

Beim Fail-Fall sieht der User nun innerhalb 1-3 Minuten einen klaren
Fehler statt ewiges Spinner-Hängen.

## 0.7.16 — 2026-06-30 — 1-Klick-Toggle fuer perf_logs_enabled

Neue Admin-Route `/admin/perf-logs/toggle` (GET + POST), analog zur
api-trace-Toggle:

  https://printix-sp.azurewebsites.net/admin/perf-logs/toggle?enabled=1
  → schaltet Perf-Logs AN
  https://printix-sp.azurewebsites.net/admin/perf-logs/toggle?enabled=0
  → schaltet AUS

Response ist Plain-Text — bequem aus Browser-Adressleiste oder als
Bookmark. Bestaetigt 'jetzt AN / read_back=1' + zeigt den Toggle-Link
fuer den Gegenstatus.

## 0.7.15 — 2026-06-30 — CRITICAL Print-Fix (release_immediately=True) + Perf-Pass

### Print-Fix — der echte Bug

User-Insight: im alten printix-mcp werden Secure-Print-Jobs IMMER mit
`release_immediately=True` gesubmittet. Unser Code hatte das auf False
umgestellt — DAS war der Grund fuer die Printix-500-Errors (TS70RB,
PwuzH9, 3OM337, SQFSJK), nicht der user/userMapping-Parameter.

Korrekter Flow (aus altem printix-mcp uebernommen):
  1. submit_print_job(user=email, release_immediately=True)
     → Job ist sofort im Cloud-Pool, bereit fuer Karten-Pull
  2. upload_file_to_url(...)
  3. complete_upload(job_id)
  4. **change_job_owner(job_id, real_user_email)** — setzt Owner
     fuer Secure-Print-Berechtigung am Drucker

release_immediately=False triggerte einen anderen Code-Pfad in Printix
('Print Later') der mit unseren Anfragen 500 wirft. Mein v0.7.14
userMapping-Fix war auf falscher Faehrte.

Fuer Delegate (print:delegate:<id>): gleicher Pattern — submit mit
absender-email, dann change_job_owner zum delegate.

### Perf-Pass (Agent-Arbeit parallel)

- `db.py` `_conn()`: journal_mode=MEMORY, synchronous=NORMAL,
  cache_size=-64000 (64 MB), temp_store=MEMORY. Azure-Files SMB hat
  sehr langsame fsync — diese Pragmas reduzieren das massiv.
  Override via Env DB_JOURNAL_MODE / DB_SYNCHRONOUS.
- Neuer Index `idx_audit_log_action(action, created_at DESC)`.
- `admin_audit`: COUNT(*) auf 1001 gecappt, LEFT JOIN bei COUNT
  ohne User-Filter geskippt, distinct_sources hardcoded statt
  json_extract Full-Scan.
- `admin_groups_page`: list_groups + list_printers parallel via
  asyncio.gather(asyncio.to_thread(...)) statt sequentiell.
- `/desktop/send`: Tenant-Lookup + create_cloudprint_job aus der
  sync Pre-202-Phase in BG-Task verschoben. iOS sieht 202 nach <50ms.

### Was zu tun nach Deploy

1. `INSERT INTO settings (key, value, updated_at) VALUES
   ('perf_logs_enabled','1',datetime('now'))` fuer 5-10 Min an —
   dann zeigen die `dt_*`-Marker wo's noch klemmt.
2. Boot-Log sollte
   `SQLite tuning aktiv: journal=memory synchronous=normal …` zeigen.
3. Wenn Memory-Journal Sorgen macht (Crash → letzte sec weg):
   `DB_JOURNAL_MODE=TRUNCATE` setzen.

## 0.7.14 — 2026-06-30 — CRITICAL: userMapping statt user-Query-Param fuer Secure Print

User-Report: Wiederholte Printix-500-Errors (ErrorIDs TS70RB, PwuzH9,
3OM337, SQFSJK) beim iOS-Druck. Aus Printix-Docs (printix.github.io):

> „You cannot specify both `userMapping` and the `user` query parameter
> simultaneously."
> „For Secure Print workflows with `releaseImmediately=false`, use
> `userMapping` instead of the `user` parameter."

Root-Cause: wir uebergaben den User immer als `?user=Email`. Fuer
Cloud-Print mit `releaseImmediately=false` (Secure Print Anywhere)
verlangt Printix aber `userMapping` im JSON-Body — mit einem
explizitem Identity-Key. Akzeptierte Keys:
  AzureObjectId / AzureUPN / SAMAccountName /
  OnPremImmutableId / OnPremUpn / **Email**

Fix:
- `printix_client.submit_print_job()` bekommt zwei neue Parameter
  `user_mapping_key` + `user_mapping_value`. Wenn gesetzt, wird das
  als `body.userMapping={key,value}` mitgegeben.
- `printix_client` schreibt entweder `?user=...` ODER
  `body.userMapping={...}` — niemals beides.
- `desktop_routes._process_desktop_send_bg`: nutzt jetzt
  `userMapping(key='Email', value=submit_user_email)`.
- Fallback-Retry: bei 400/422/500 OHNE userMapping (Job landet in
  tenant-globaler Queue, User holt via Karte am Drucker).

Damit sollte der Submit fuer `releaseImmediately=false` (= Secure
Print) endlich durchlaufen.

### 0.7.14 — Performance-Pass: SQLite-Tuning fuer Azure-Files, /admin/audit Fix, iOS-Upload schneller

User-Report: `/admin/audit` lud ~2 Minuten, andere Admin-Seiten ebenfalls
sehr langsam, iOS-Upload eines 300-KB-JPGs zeigte 2-3 Minuten Spinner.

Haupt-Bottleneck: Das `/data`-Volume liegt auf einem Azure-Files-SMB-Mount.
Auf SMB ist jeder fsync teuer, WAL-Journal unzuverlaessig, und SQLite-
Roundtrips sind ein Vielfaches langsamer als auf lokaler Disk.

Fixes:

- **`src/db.py`** — `_conn()` setzt jetzt `journal_mode=MEMORY`,
  `synchronous=NORMAL`, `cache_size=-64000` (64 MB), `temp_store=MEMORY`.
  Trade-off: bei OS-Crash mitten in einer Transaktion ist die DB im
  Worst-Case korrupt — taegliche `blob_backup`-Snapshots decken das ab.
  Override via `DB_JOURNAL_MODE` / `DB_SYNCHRONOUS` Env-Vars.
  Neuer Index `idx_audit_log_action (action, created_at DESC)`.
  Helper `perf_logs_enabled()` liest Setting `perf_logs_enabled`.

- **`src/web/app.py` `admin_audit`** — COUNT(\*) ist auf 1000 capped
  (Subquery `SELECT 1 ... LIMIT 1001`); Template kann `≥1000` zeigen.
  LEFT JOIN users wird beim COUNT nur noch gebaut wenn User-Filter
  aktiv. Das teure `SELECT DISTINCT json_extract(details,'$.source')`
  ist durch eine hartkodierte Liste (`ios_app`, `web`, `email`,
  `desktop`, `mcp`) ersetzt. `dt_total/dt_db` Log hinter
  `perf_logs_enabled`.

- **`src/web/app.py` `admin_groups`** — `list_groups` + `list_printers`
  laufen jetzt parallel via `asyncio.gather(asyncio.to_thread(...))`
  statt sequenziell zu blockieren.

- **`src/web/desktop_routes.py` `/desktop/send`** — `create_cloudprint_job`
  + tenant-Lookup waren synchron vor dem 202 (auf SMB jeweils 200-600 ms).
  Bei einem 300-KB-JPG ergab das den 2-3-s-Spinner. Jetzt: 202 sofort,
  Tracking-Insert via `asyncio.create_task(asyncio.to_thread(...))`.
  Stage-Log gated.

Was NICHT geaendert:
- Azure-Files-Mount selbst — Infra-Arbeit ausserhalb dieses Releases.
- Persistenter Printix-Printers/Groups-Cache (nur `cached_printix_users`
  existiert heute) — naechste Stufe.
- Heavy-Imports auf Modulebene — defensive `try`-Imports in Handlern
  bleiben; Importzeit nach erstem Hit sub-ms.

Empfehlung: `perf_logs_enabled=1` einmalig setzen, Logs ein paar Minuten
beobachten um restliche Hotspots zu sehen (`dt_db`).

## 0.7.13 — 2026-06-30 — API-Trace 'Aktiv'-Status auch ohne Listing-Daten

User-Report: Setting im Audit-Log korrekt gespeichert (`api_trace_toggle
enabled=1`), aber das Dashboard zeigt weiter „Inaktiv".

Root-Cause: `is_enabled()` lag im selben try/except-Block wie
`list_trace_entries` / `list_distinct_components`. Wenn EINE der
beiden Listing-Calls eine Exception warf (z.B. leere Tabelle nach
Schema-Init-Race), wurde `trace_active = False` im except-Block
gesetzt — obwohl das Setting in der DB sauber auf "1" stand.

Fix: `is_enabled()` separat berechnen. Wenn der Listing-Code crasht,
bleibt der Aktiv-Status trotzdem korrekt sichtbar.

## 0.7.12 — 2026-06-30 — API-Trace-Toggle: visuelles Feedback + GET-Fallback

User-Report: 'Einschalten'-Button im /admin/api-trace bewirkte nichts
Sichtbares. Der Toggle hat geschrieben, die Seite hat aber kein
Feedback gezeigt — User wusste nicht ob's geklappt hat.

Drei Aenderungen:

1. **Sichtbares Feedback**: nach Toggle Redirect mit `?ok=trace_on` /
   `?ok=trace_off` / `?err=save_failed` / `?err=not_admin` — das Template
   zeigt entsprechende Banner.
2. **303 statt 302**: explizites POST→GET fuer Redirect, Browser
   konvertiert garantiert auf GET (vorher implizit).
3. **GET-Fallback**: `/admin/api-trace/toggle?enabled=1` funktioniert
   auch — fuer Debugging und Bookmark.
4. **Diagnostik-Logs**: `api_trace_toggle OK: user=X set=1 read_back=1`
   damit beim naechsten Bug klar ist ob's gespeichert wurde.

## 0.7.11 — 2026-06-30 — API-Trace fuer Outbound-Call-Debugging

Admin-Pain: bei Printix-API-Errors steht im Server-Log oft nur
"Printix API Error 500" ohne Request- oder Response-Details — die
echte Ursache (welcher Body wurde gesendet, was kam zurueck) war
nicht einsehbar.

Neu: Admin-Dashboard **/admin/api-trace** das jeden Outbound-Call
(Printix-Cloud-API, OAuth, Cloud-Upload) mit Request + Response,
Headern, Body, Dauer und Status sichtbar macht. Klick auf eine
Zeile oeffnet ein Detail-Panel mit pretty-printed JSON.

Implementierung:

1. Neue Tabelle `api_trace_log` (Schema in `src/api_trace.py`,
   Init aus `src/db.py`). Auto-Prune ab 5000 Eintraegen.

2. Neuer Wrapper `_session_request()` in `PrintixClient` — alle
   API-Calls (`_get/_post/_put/_patch/_delete`, `submit_print_job`,
   `change_job_owner`, `upload_file_to_url`, OAuth-Token-Request)
   gehen jetzt durch `api_trace.trace_request()`.

3. Admin-UI `/admin/api-trace`: Tabelle mit Filter (Komponente,
   Methode, Status-Klasse, Volltext), Detail-Panel als Slide-In,
   Auto-Refresh-Toggle (5s), Toggle zum An/Aus-Schalten des
   Trace, "Log leeren". Sidebar-Eintrag unter Datenschutz.

**Sicherheit**: Authorization-Header werden maskiert
(`Bearer abcd...wxyz`), `client_secret=...` und `password=...`
in URLs/Bodies werden redacted, Bodies werden auf 4 KB gekuerzt.
Default ist API-Trace AUS — gezielt fuers Debugging einschalten
und nach der Session wieder aus. Auf Produktion sollte der
Trace nicht dauerhaft laufen.

## 0.7.10 — 2026-06-30 — Lowercase-Revert + Printix-Submit-Retry ohne user-Param

User-Insight: in der Printix-User-Liste steht der eigene Account
case-preserving als `Marcus@nimtz.email` (grosses M). Die Lowercase-
Normalisierung aus v0.7.8 war also kontraproduktiv — wir machten den
Match noch schlechter. ZUSAETZLICH: laut Printix-API-Docs ist der
`user`-Parameter beim /submit-Endpoint OPTIONAL und primaer fuer
Redirector / USB-Print / Third-Party-Pull. Bei Cloud-Print zur
SecurePrint-Anywhere-Queue kann er ggf. weggelassen werden.

Zwei Aenderungen:

1. **Lowercase-Revert**: `owner_email` behaelt die original-Case wie
   in `users.email` / `cached_printix_users.email` — Printix matched
   wahrscheinlich case-preserving.

2. **Submit-Retry ohne user-Param**: wenn Printix mit user='...' einen
   500 zurueckgibt, versuchen wir den Submit ein zweites Mal mit
   `user=None`. Printix legt den Job dann in die tenant-globale
   Cloud-Queue — der User kann ihn ueber seine Karte am Drucker
   trotzdem abholen.

3. **Volle Response-Body-Logs**: Printix-API-Errors loggen jetzt
   die ersten 1000 Bytes des Response-Body. Vorher war oft nur
   "An error occourred" sichtbar; die echte Ursache stand manchmal
   nur im raw-body.

## 0.7.9 — 2026-06-30 — Audit-Zeitstempel in lokaler Zeitzone

User-Report: Audit-Log zeigt 07:16 UTC obwohl Server-TZ Europe/Berlin
gesetzt ist und die User-OS-Settings auf CEST stehen — eigentlich
soll 09:16 angezeigt werden.

Der `_localtime_filter` (Jinja, konvertiert UTC-ISO auf konfigurierte
Display-TZ via `_resolve_display_tz`) war schon registriert, das
Audit-Template nutzte ihn aber nicht — gab nur `e.created_at[:19]`
raw aus.

Fix: `admin_audit.html` Z. 231: `{{ e.created_at | localtime }}`.
Format: 'YYYY-MM-DD HH:MM:SS TZ'.

## 0.7.8 — 2026-06-30 — Printix-Submit-500 wegen Email-Case (Marcus@ vs marcus@)

User-Report: iOS-Druck failed mit Printix API 500 (ErrorID TS70RB,
PwuzH9). Im Log:
  `submit_to='Marcus@nimtz.email'`  (Großschreibung von Entra)
  → POST .../submit?user=Marcus%40nimtz.email → 500

Printix matched Emails case-sensitive — `Marcus@…` ≠ `marcus@…`.
Entra-Login stored `users.email` mit dem Casing wie's in MS Graph
zurueckkam (oft Vorname-Casing).

Fix: `owner_email` im /desktop/send wird konsequent ge-lowercased,
egal aus welcher Quelle (users.email, cached_printix_users, Printix-
Identity-Lookup). Plus Backfill: bei Entra-Login wird die DB-
gespeicherte users.email auch lowercase gestellt.

Zwischen den Loglines: nach Deploy + einmal Entra-Logout/Login wird
deine users.email von 'Marcus@nimtz.email' auf 'marcus@nimtz.email'
permanent umgeschrieben. Ab dann printet print:self sauber durch.

## 0.7.7 — 2026-06-30 — _user_descr() ueberall in desktop_routes Logs

User-Wunsch: 'user='Marcus'' in Logs ist Display-Name, hilft nicht
beim Diagnostizieren. Sollten echte Identifier (username, email,
printix_user_id) sein.

Alle Log-Marker in `src/web/desktop_routes.py` von `user.get("username")`
auf `_user_descr(user)` umgestellt (Outer-Exception eingeschlossen).
Stages [1/5] bis [5/5] + START + QUEUED + Delegate + Capture + Error-
Pfade — total 22 Stellen.

Beispiel-Output ab jetzt:
  `user=marcus.nimtz (Marcus Nimtz, marcus@nimtz.email) [px:abc12345]`

Statt vorher: `user='Marcus'`. Eindeutig diagnostizierbar.

Eine Stelle (Zeile 88: lokale SQL-Query-Variable) bleibt
`user.get("username")` — dort wird ja gezielt der Username-Wert
gebraucht, nicht ein Display-String.

## 0.7.6 — 2026-06-30 — Username/full_name aus Printix beim Entra-Login

User-Report: 'Marcus' als users.username ist verwirrend in Logs/UI,
sollte mit dem Printix-Username konsistent sein.

Zwei Schritte beim Entra-Login:

1. **Neu-Linking (erweitert v0.6.6)**: wenn der Entra-User per Email
   in cached_printix_users matched, werden nicht nur printix_user_id,
   sondern auch users.username + users.full_name auf die Printix-
   Werte angeglichen (mit Kollisionscheck via username_exists).
2. **Backfill bei bereits gelinkten Usern**: bei jedem Login wird
   geprueft ob users.username/full_name mit cached_printix_users
   uebereinstimmt. Falls nicht: einmaliges Update + Audit-Log-Eintrag
   entra_printix_backfill. Idempotent — laeuft jeden Login, macht
   aber nur was wenn nötig.

Ergebnis: dein User wird beim naechsten Login von 'Marcus' auf
deinen Printix-Username umgestellt (z.B. 'marcus.nimtz'). Logs +
UI zeigen dann ueberall den selben Identifier wie das Printix-Portal.

## 0.7.5 — 2026-06-30 — Diagnose + Watchdog + lesbare User-Logs

iOS-Druck blieb in 'queued' haengen, kein klarer Fehler zu sehen.
Plus User-Wunsch: Log zeigt nur 'Marcus' statt vollem Namen.

- **Lesbare User-Logs**: neuer `_user_descr(user)` Helper rendert
  `Marcus Nimtz (Marcus, marcus@nimtz.email) [px:abc12345]`. Erst-
  verwendung im no-queue-Diagnostic.
- **Detaillierte no_queue-Diagnose**: wenn 3-Tier-Resolver nichts
  findet, loggt der Server exakt was jeder Tier zurueckgab
  (override_allowed/user_q/group_ids/group_qs/global_q/global_lbl).
  Spart Rate-und-Versuch beim naechsten Bug.
- **5-Min-Watchdog**: Background-Task laenger als 300s → cancelled
  + Status auf `error: bg_task_timeout`. Vorher haengte Job ewig auf
  `queued`.
- **Outer-Exception schreibt Status**: wenn der ganze BG-Task-Body
  crashed (z.B. ImportError), wird der Job auf
  `error: bg_task_crashed: <reason>` gesetzt statt stumm zu sterben.

## 0.7.4 — 2026-06-30 — Audit-Log UI: Severity, Source-Filter, klickbare Spalten, voller Benutzername

User-Beschwerden zur `/admin/audit`-Seite:
1. Nur Vorname „Marcus" sichtbar statt vollem Namen.
2. „Filter zuruecksetzen"-Button lief rechts aus dem Rahmen.
3. Filter-Form sah unsauber/unprofessionell aus.
4. Keine Klick-zum-Filtern-Interaktion auf Action-/User-Zellen.
5. Severity nicht erkennbar — `*_failed` sah aus wie normaler Eintrag.

Fixes (alle in 0.7.4):
1. **Voller Name**: SELECT zieht jetzt `u.full_name`; Anzeige bevorzugt
   `full_name` → `username` → `email` → `user_id[:8]`. E-Mail erscheint
   als grauer Untertitel wenn unterschiedlich.
2. **Severity-Spalte + Badge** (info/warning/error). Ableitung aus Action-
   Name (`_failed`, `denied`, `revoked`, `oid_mismatch` → error;
   `_warning`, `_expired`, `disabled`, `removed` → warning; sonst info).
   Zusaetzlicher Severity-Filter im Header.
3. **Source-Filter & -Spalte**: neuer `source`-Query-Param, extrahiert
   `details.source` aus JSON (z.B. `ios_app`, `web`, `email`, `desktop`,
   `mcp`). SQL nutzt `json_extract` mit LIKE-Fallback. Distinct-Quellen
   aus DB. Lokalisierte Labels: „iOS-App", „Web-UI", „E-Mail", …
4. **Klickbare Zellen**: Klick auf User-Zelle filtert nach Username,
   Klick auf Action-Badge filtert nach Action, Klick auf Source-Badge
   filtert nach Quelle.
5. **Filter-Bar Redesign**: responsives Grid (1 Spalte mobil → 2 Tablet
   → 4 Desktop), Buttons in eigener Action-Zeile rechts-aligned ueber
   gestrichelten Trenner, nichts laeuft mehr aus dem Rahmen.
6. **Pagination**: zeigt jetzt „Seite X von Y (Z Eintraege gesamt)",
   Buttons mit „Zurueck/Weiter"-Beschriftung.
7. **Empty-State**: zentriertes Icon + Hint + Reset-Link statt leere
   Tabelle.
8. CSS nutzt `var(--primary/--surface/--border/...)` aus base.html.
9. i18n: neue DE+EN-Keys unter `_V074_AUDIT_UI` (audit_sev_*,
   audit_col_*, audit_source_*, audit_page_indicator, etc.). Andere
   Locales fallen via Standard-Pattern auf EN zurueck.

## 0.7.3 — 2026-06-30 — Fix: iOS Jobs-Tab war IMMER leer (tenant_id-Mismatch)

User-Report: „Jobs in der iOS-App ging noch nie".

Root-Cause: bei jedem iOS-Send legt /desktop/send eine Tracking-Row in
`cloudprint_jobs` an, ABER mit `tenant_id=""` (hardcoded). Der GET
/desktop/me/jobs-Endpoint filtert aber strikt nach
`WHERE tenant_id = <user-tenant-uuid>` → Leerstring matched die Tenant-
UUID nie → 0 Treffer → leerer Tab. Egal wie viele Jobs der User schickte.

Fix:
1. `create_cloudprint_job()` Aufruf in /desktop/send legt jetzt den
   tatsaechlichen tenant_id mit ein (Lookup via get_parent_user_id +
   get_tenant_full_by_user_id mit Fallback).
2. /desktop/me/jobs Query auf `(tenant_id=? OR tenant_id='')` gelockert
   damit auch historische Rows (vor dem Fix) sichtbar werden.

## 0.7.2 — 2026-06-30 — Fix: /desktop/send respektiert 3-Tier-Queue-Resolver

User-Report: iOS-Druck schlaegt mit `no_queue: no secure print queue
configured` fehl obwohl die Default-Queue korrekt im Admin gesetzt
ist und die iOS-App sie sauber anzeigt.

Root-Cause: Inkonsistenz zwischen /desktop/targets und /desktop/send:
- `/desktop/targets` nutzt `resolve_user_queue()` (3-Tier: User-Override
  → Group → Global) und liefert deshalb die korrekte Queue an die App.
- `/desktop/send` checkte aber NUR die Legacy-Spalte
  `tenants.lpr_target_queue` + Single-Tenant-Fallback. Der globale
  Default (`default_lpr_target_queue`) wurde ignoriert.

Fix: in `_process_desktop_send_bg` zusaetzlicher Step VOR dem
no_queue-Fail — `resolve_user_queue()` konsultieren und die Queue in
`config["lpr_target_queue"]` injizieren. tenant-Lookup nochmal probieren
falls noch leer. Logging erweitert mit „3-tier resolver hit"-Marker.

## 0.7.1 — 2026-06-30 — QR-Code in /welcome + /account scanbar (war 20x20px)

User-Report: weder iPhone-Kamera noch unsere App erkennt den Setup-QR.
Root-Cause: segno-SVG-Output ignoriert `scale` und liefert
`<svg width="20" height="20">` — 20 Pixel ist zu klein zum Scannen.

Fix: `_make_welcome_qr_svg()` rendert jetzt PNG (scale=10) als base64
data-URI in `<img>`-Tag mit CSS `width:260px`. Gleiche Pipeline wie
employee-routes /m/setup-qr.png — die PNG-Path respektiert scale
zuverlaessig.

## 0.7.0 — 2026-06-30 — Microsoft Graph als Mail-Provider (Resend optional)

Wer EntraID via Auto-Setup eingerichtet hat, kann jetzt System-Mails
(Mobile-Invite, Welcome, GDPR-Export, Reports, …) ueber den eigenen
O365-Tenant per **Microsoft Graph** `Mail.Send` verschicken — keine
Resend-Subscription mehr noetig, eigene Domain als Absender, Audit-
Trail in Exchange Online. Resend bleibt Default und Fallback.

Bausteine:
1. `mail_client.send_via_graph()` — App-Only-Token via Client-
   Credentials, POST `/users/{from}/sendMail`. Cached Token bis 10min
   vor Ablauf. Klare Fehler-Hints fuer 401/403/404.
2. `mail_client.send_mail()` — Provider-agnostischer Wrapper. Bei
   Provider=`graph` + Fehler + konfigurierten Resend-Credentials wird
   automatisch auf Resend zurueckgefallen (mit Warn-Log).
3. `entra.auto_register_app()` — neue Parameter
   `include_mail_send: bool` (Mail.Send Application-Role) und
   `include_mail_read: bool` (Mail.Read — Vorbereitung fuer den
   Email-to-Print-Gateway in v0.8.0; Permission wird jetzt schon
   registriert, damit der Admin den Consent nur einmal durchklickt).
4. Entra-Auto-Setup-UI: zwei optionale Checkboxen vor „Auto-Setup
   starten" — Admin entscheidet pro Tenant welche Mail-Permissions
   die neue App bekommen soll. Wahl wird in der Session gemerkt und
   im Poll-Endpoint an `auto_register_app` durchgereicht; danach
   in DB-Settings `entra_mail_send_enabled` / `entra_mail_read_enabled`
   persistiert.
5. Admin-Settings → „Globales Mail-Fallback": Provider-Dropdown
   (Resend / Microsoft Graph) + Mailbox-Feld fuer den Graph-Sender.
   Graph-Option ist disabled bis Mail.Send-Permission registriert ist.
   `_send_mobile_invite_email()` dispatcht jetzt via Provider.
6. **Email-to-Print** (v0.8.0): nur Permission-Vorbereitung. Toggle im
   Admin-UI ist disabled, Setting `email_to_print_enabled` default `0`.

**Wichtig fuer den Admin nach Auto-Setup** (steht auch im UI):
- Im Azure-Portal &rarr; API-Berechtigungen &rarr; **Grant admin consent**.
- Eine **Application Access Policy** in Exchange Online setzen, damit
  die App-Identitaet nur die eine Service-Mailbox bedienen kann:
  ```powershell
  New-ApplicationAccessPolicy -AppId <client_id> `
      -PolicyScopeGroupId noreply@firma.de `
      -AccessRight RestrictAccess `
      -Description "MySecurePrint Mail.Send only on noreply mailbox"
  ```

Bestehende Resend-Pfade aendern sich nicht — Default-Provider bleibt
Resend, alle alten Tenants funktionieren ohne Aktion weiter.

Doku: `docs/mail-via-graph.md`.

## 0.6.9 — 2026-06-30 — Entra-Auto-Register: Mobile-Redirect-URI fuer iOS

User-Report: iOS-App-Anmeldung via Microsoft scheitert mit
AADSTS50011 'redirect URI mysecureprint://oauth/callback does not
match the redirect URIs configured for the application'. Web-Login am
Server geht — die per Entra-Auto-Setup registrierte App hatte aber
nur den Server-Callback (web.redirectUris), nicht den iOS-Custom-URL-
Scheme-Redirect (publicClient.redirectUris). Folge: jede Erst-
Einrichtung muss manuell im Azure-Portal nachgepatched werden.

Fix: `auto_register_app()` legt jetzt zusaetzlich die Mobile-Platform
mit `mysecureprint://oauth/callback` an und markiert die App via
`isFallbackPublicClient: true` als Public-Client (Voraussetzung fuer
PKCE ohne Client-Secret).

**Workaround fuer bestehende Tenants** (deren App vor v0.6.9 registriert
wurde): einmalig im Azure-Portal nachpatchen:

  Azure-Portal → App-Registrierung → Authentifizierung →
  Plattform hinzufuegen → Mobile and desktop applications →
  Custom redirect URI: `mysecureprint://oauth/callback`

(Ein „Repair"-Button im Admin-UI, der das via Graph-API selbst macht,
ist fuer v0.7 vorgesehen.)

## 0.6.8 — 2026-06-30 — CRITICAL: Root-Cause /desktop/* 404 gefunden + gefixt

Aus den Azure-StartupLogs der v0.6.7-Instanz:

  Desktop-Init: starting…
  Desktop-Init: imported desktop_auth
  Desktop-Init: FAILED with exception:
  Traceback (most recent call last):
    File "/app/web/app.py", line 6341, in create_app
      init_desktop_schema()
    File "/app/desktop_auth.py", line 34, in init_desktop_schema
      with _conn() as conn:
    ...
  sqlite3.OperationalError: unable to open database file

Root Cause: Azure-Files-Mount `/data/` ist beim Boot manchmal noch nicht
bereit wenn `create_app()` laeuft. `init_desktop_schema()` kann die
SQLite-DB nicht oeffnen → der gemeinsame try/except brach ab BEVOR
`register_desktop_routes()` aufgerufen wurde → alle /desktop/* gaben
404 zurueck, bis zum naechsten erfolgreichen Restart.

Fix (zwei Stellen):
1. `web/app.py`: Schema-Init und Routen-Registrierung ENTKOPPELT.
   Routen werden IMMER registriert, auch wenn Schema-Init fehlschlaegt.
2. `desktop_auth.py`: neuer `_ensure_schema()`-Lazy-Guard, der bei
   jedem ersten `create_token()`/`validate_token()`-Aufruf das Schema
   nachzieht falls beim Boot fehlgeschlagen.

Damit ist /desktop/* nach einem Boot, bei dem der Azure-Files-Mount
zu spaet kommt, trotzdem voll funktional.

## 0.6.7 — 2026-06-29 — /desktop/targets: user_can_choose Flag

User-Report: iOS-App zeigt nur „SecurePrint" als fixes Ziel, obwohl im
Admin „User darf Queue waehlen" aktiviert ist.

Aenderung in `/desktop/targets`-Response: neues Feld `user_can_choose:
bool`. Quelle: `is_user_queue_override_allowed()` aus 3-Tier-Hierarchie
(Global → Group → User-Override).

iOS-Seite (App v0.6.4) liest das Flag und zeigt einen Queue-Picker
zusaetzlich zur Default-Queue an. `/desktop/queues` existierte bereits.

Audit-Log-Coverage fuer iOS-Sends verifiziert (kein Code-Fix noetig):
- `cloudprint_jobs` Row wird VOR Background-Task angelegt (queued/
  forwarded/error)
- `audit_log` enthaelt `print_job_submitted` mit `source: ios_app`
- `/desktop/me/jobs` matched via username/email/printix_user_id —
  iOS-Sends erscheinen im Jobs-Tab

## 0.6.6 — 2026-06-29 — User-Landing, Entra→Printix Auto-Link, Perf-Index

Drei kleinere UX/Performance-Fixes:

### 1. Nicht-Admin-User landen einheitlich auf `/account`

Bisher: `role_type=employee` → `/my` (Mitarbeiter-Portal), `role_type=user`
→ `/account` (Info-Seite). Das war fuer User verwirrend, die nach dem
Login die Info-Seite mit QR-Code, MCP-Credentials, OAuth-Daten und
GDPR-Export erwarteten. Jetzt: alle Nicht-Admins (employee + user) landen
nach dem Login auf `/account`. Das Mitarbeiter-Portal `/my` bleibt
unveraendert ueber Sidebar/Navigation erreichbar — nur das
Default-Landing-Target hat sich geaendert. Die Invitation-Activation-
Guard-Middleware erlaubt Employees jetzt zusaetzlich zu `/my/*` auch
`/account/*` (Fallback-Redirect ebenfalls auf `/account` statt `/my`).

### 2. Entra-Login: Auto-Link zu printix_user_id ueber Email

User-Feedback: "wenn man via Entra sich anmeldet, ist doch gleicher
User/Email wie in Printix — wieso kein Abgleich bzw. user-id Import
dabei?". Stimmt — der `users`-Row hatte zwar eine `printix_user_id`-
Spalte, aber sie wurde beim ersten Entra-Login nie befuellt. Folge:
Admins mussten haendisch den Printix-User zuordnen, bevor MCP-Anfragen
funktionierten.

Jetzt: direkt nach erfolgreichem Entra-Login (`/auth/entra/callback`)
und Audit-Log-Eintrag laeuft ein Auto-Link-Schritt:
- nur wenn `users.printix_user_id` noch leer ist
- Lookup via `find_printix_user_by_identity(email)` (case-insensitive,
  matcht username, full email, local-part)
- bei eindeutigem Match: `update_user(id, printix_user_id=...)` +
  Audit-Log `entra_printix_linked`
- bei mehrdeutigem Match (mehrere Tenants): kein Linking, Warning im Log
- Fehler werden geschluckt — Login-Flow bleibt robust

### 3. Performance: fehlender `audit_log(user_id)`-Index

GDPR-Export (`gdpr_export.py`) und `server.py` filtern audit_log nach
`user_id` — bisher gab es nur Indexe auf `created_at` und `(tenant_id,
created_at DESC)`. Bei groesseren Logs fuehrte das zu Full-Table-Scans.
Neu: `CREATE INDEX idx_audit_log_user ON audit_log (user_id,
created_at DESC)`.

Weitere Indexe wurden geprueft — `desktop_tokens(user_id)`,
`cloudprint_jobs(tenant_id, created_at DESC)`,
`cloudprint_jobs(username, created_at DESC)` existieren bereits.

### Performance-Hinweise fuer 0.6.7

Beim Audit fielen weitere potentielle Bottlenecks auf, die mehr Aufwand
brauchen:
- Mehrere Admin-Handler rufen `_make_printix_client(...).list_users()`
  o.ae. live waehrend des Requests auf (siehe app.py:4519, 4161, 5886) —
  Umstellung auf `cached_printix_users` mit "kann veraltet sein"-Hinweis
  wuerde ~500ms-2s pro Request sparen.
- `get_audit_log()` (DB) macht `SELECT a.*` + JOIN ohne LIMIT-Pushdown —
  bei grossen Logs koennte ein `created_at`-Cutoff helfen.
- `cache.schedule_prefetch` laeuft synchron im Login-Pfad — bei kalten
  Tenants spuerbar.

## 0.6.5 — 2026-06-29 — Mobile-Invite redeem ohne entra_oid

iOS-Audit hat aufgedeckt, dass der Mobile-Invite-Flow End-to-End
unbenutzbar war: die iOS-App kennt den Entra-OID nicht (PKCE-Flow tauscht
serverseitig), `/api/v1/mobile-invite/redeem` verlangte ihn aber als
Pflichtfeld und gab sonst 400 `missing_oid` zurueck. Folge: der QR-Code
in der Admin-Mobile-Invite-Email konnte nie zu einem fertigen Login
fuehren; iOS fiel immer auf manuelle Username/Passwort-Eingabe zurueck.

Aenderung: `entra_oid` ist jetzt optional. Wenn der Client keinen oid
mitliefert, wird der Bearer-Token rein gegen den Invite-Token getauscht.
Bei vorhandenem oid greifen weiterhin Mismatch-Check + Erst-Linking.

Security-Tradeoff: der Invite-Token ist one-shot, admin-issued, mit
Expiry — ausreichend als Auth-Proof. Die zusaetzliche oid-Verifikation
war defense-in-depth, blockierte aber den Flow vollstaendig.

## 0.6.4 — 2026-06-29 — Audit-Cleanups (S-3 … S-7): Crash-Logging, Boot-Härtung, Token-IDs

Code-Audit-Cleanups, alle nicht-funktional — reine Härtung von
Fehlerpfaden + Vorbereitung auf saubere FastAPI-Migration.

- **S-3 (HIGH) — `create_app()` Crash-Logging** (`src/web/run.py`):
  Aufruf in try/except gewrapped. Bei Boot-Fehler landet jetzt der
  volle Traceback via `logger.exception()` im stdout-Log; danach
  `sys.exit(1)`. Vorher: Uvicorn hat den halb-gestarteten Container
  weiterlaufen lassen, der eigentliche Crash-Grund war im Log nicht
  sichtbar.
- **S-4 (HIGH) — `sitecustomize.py` Top-Level-Imports**
  (`src/sitecustomize.py`): `from printix_client import …` aus dem
  Modul-Top in eine Installer-Funktion verschoben + try/except um den
  Monkey-Patch-Install. `sitecustomize` wird bei JEDEM Python-Start
  geladen — ein ImportError dort hätte sonst auch Healthcheck-,
  CLI- und Sub-Tooling-Prozesse blockiert. Eine Umbenennung in
  `card_transform.py` (S-11) wäre die saubere Lösung, ist aber
  riskant (Such-/Diff-Aufwand) und im Kommentar als TODO vermerkt.
- **S-5 (HIGH) — `list_tokens_for_user` ohne ID** (`src/desktop_auth.py`):
  SELECT auf `rowid AS id` erweitert; Output-Dict trägt jetzt eine
  stabile `id`, mit der das Settings-/Admin-UI gezielt einzelne
  Tokens revoken kann, ohne den vollständigen Token-Wert im DOM zu
  exponieren. Aufrufer (`web/app.py` zählt nur `len(...)`,
  `desktop_routes.py` importiert nur das Symbol) sind unverändert
  kompatibel.
- **S-6 (HIGH) — `@app.on_event("startup")` Deprecation** (`src/web/app.py`):
  Alle 5 Startup-Handler hängen an Closure-Variablen aus `create_app()`
  (u.a. `_run_printix_user_sync_once`); eine saubere Lifespan-Migration
  hätte den ganzen Builder restrukturiert. Konservativ: Decorators
  belassen + TODO-Kommentare gesetzt mit Hinweis auf die Migration
  via `app.router.lifespan_context`.
- **S-7 (MEDIUM) — `sys.path.insert` Spam** (`src/web/app.py`): die 5
  unkonditionalen `sys.path.insert(0, "/app")` (bzw. den
  `_src_dir`-Insert in `_make_printix_client`) auf
  `if "/app" not in sys.path` umgestellt. Reduziert wiederholtes
  Voranstellen bei jedem Handler-Call. Die bereits konditionalen
  Inserts in `web/desktop_routes.py` blieben unverändert.

Kein User-sichtbares Feature, keine API-Änderung. Nach Deploy
verifizieren: `/desktop/auth/login` + Settings-Seite + Auto-TLS-Routen
müssen unverändert funktionieren.

## 0.6.3 — 2026-06-29 — CRITICAL: cloudprint.printix_cache_db wiederhergestellt

Server-Audit hat einen toten Import-Pfad gefunden: der slim-Commit
(f95afe2) hat `src/cloudprint/printix_cache_db.py` mitgeloescht,
obwohl 5+ Aufrufstellen (desktop_routes._process_desktop_send_bg,
cloudprint/db_extensions, etc.) `find_printix_user_by_identity`
importieren. Folge: jeder /desktop/send-Upload und jede LPR-Job-
Identity-Resolution waere mit ImportError im Background-Task
abgestuerzt — Client haette HTTP 202 gesehen und kein Druck.

Fix: schlankes Modul mit nur der Lookup-Funktion (sync-Logik gehoert
inzwischen woanders hin) wiederhergestellt.

## 0.6.2 — 2026-06-29 — Fix /desktop/me/jobs 500 (Spalten-Mismatch)

User-Report: Jobs-Tab in iOS zeigt „server antwortet 500". Root-Cause:
SQL-Query selectierte `filename`, Spalte heisst aber `job_name`. SQLite
warf OperationalError, der via 500 zurueckkam.

Fix: `SELECT job_id, job_name AS filename, ...` — App-seitig erwartet
die Property `filename` (JobsView.PrintJob), bleibt unveraendert.

## 0.6.1 — 2026-06-29 — CRITICAL: Diagnostik /desktop/* 404 + QR-Email + Erweiterte-Fix

User-Report: iOS-App-Anmeldung geht nicht mehr (/desktop/auth/login →
404). ALLE /desktop/*-Endpoints 404 obwohl im Code definiert.
Container-Log zeigt keinen Registrierungs-Fehler.

### Diagnostik
- 6 neue logger.info-Marker um den register_desktop_routes-Block —
  Init, imports, init_desktop_schema, register-call, COMPLETED.
- Bei Exception: logger.exception(...) mit vollem Traceback statt
  1-Zeiler. Beim nächsten Restart sehen wir wo's haengt.

### QR-Code in Mobile-Invite-Email
- PNG inline als base64 data:URI im HTML
- Im Default-Template + konfigurierbaren Body
- Scanbar vom Bildschirm wenn User Email auf PC liest und Phone scannen will

### „Erweiterte Einstellungen" Section-Fix
- Sidebar-Link zeigt jetzt nur Server-URL + Mail + Backups
  (?section=general) statt aller Sektionen

## 0.6.0 — 2026-06-29 — Share-Extension zurück + Jobs-Tab + Queue-Browser + Anywhere-Detection

Größerer Release: vier zusammenhängende User-Wünsche.

### Share-Extension restauriert
War der Original-USP. Im v0.4-Slim-Down faelschlich rausgeworfen. Jetzt
wieder im Build mit Root-Cause-Fix:
- Hybrid aus älterer (179 LOC, war funktional) + neuerer (PrintixSendCore-
  Upload-Helper) Version
- Diagnose-Logs via `os_log` (Subsystem `de.nimtz.mysecureprint.share`)
- 6 Bug-Fix-Kandidaten preventiv:
  1. App-Group-Mismatch behoben (Share-Ext las nach falscher Group-ID)
  2. Token-Migration: Keychain-First mit UserDefaults-Fallback
  3. Target-ID-Fallback (selectedTargetIds → lastTargetId → print:self)
  4. `ProcessInfo.performExpiringActivity` (App-Extension-safe Background-Task)
  5. Sichtbare Status-Meldungen statt silent-fail
  6. „Kein Login gefunden" / „App nicht eingerichtet" UX-Hinweise
- Fehler werden zusätzlich als JSON in App-Group-UserDefaults
  (`lastShareError`) abgelegt — Haupt-App kann's lesen + anzeigen

### iOS Jobs-Tab
Neuer „Jobs" Tab in der Haupt-App (zwischen Ziele und Konto). Zeigt die
letzten 30 Print-Jobs des Users mit:
- Status-Badge (grün=gesendet, orange=läuft, rot=Fehler)
- Queue + Zeitstempel
- Fehlermeldung bei Failure
- Pull-to-Refresh

### Server: Queue-Browser-Endpoint
- `GET /desktop/queues` — alle Tenant-Queues für iOS-Picker (Anywhere
  oben sortiert, Vendor + Model im Payload)
- `GET /desktop/me/jobs` — Job-History für den aktuellen User (Filter
  auf username/email/printix_user_id, max 200)
- `POST /desktop/send` versteht `print:queue:<queue_id>` — User kann
  jetzt eine **beliebige** Queue als Ziel wählen, nicht nur die
  resolved Default-Queue

### Anywhere-Detection: Multi-Signal statt nur Name
User-Report: „Filter ‚nur Anywhere' bleibt leer obwohl Anywhere-Queues
existieren — User-Vorschlag: hersteller=Printix".

Neue `_is_anywhere_queue()`-Helper-Logik. Wird in **3 Stellen** genutzt
(`/admin/settings#queue`, `/admin/groups`, `/desktop/queues`):
- vendor / manufacturer / brand == „Printix" (User-Tipp)
- printerType / type / queueType enthält „anywhere" oder „virtual"
- model enthält „anywhere"
- isAnywhere Boolean-Field
- name-Fallback (legacy)
Response liefert jetzt vendor + model mit zurück.

### Version-Badge in Top-Bar
Auf JEDER Seite oben links sichtbar (dunkles Monospace-Badge) damit man
auf einen Blick weiß welche Version läuft. Plus `GET /health` returnt
die Version als JSON.

## 0.5.7 — 2026-06-29 — Mail-Versand-Fix + /admin/mcp-permissions + Account-Page

### Mobile-Invite Email-Versand-Bug
User-Report: „E-Mail-Versand fehlgeschlagen — URL kann manuell kopiert
werden". Ursache: `_send_mobile_invite_email` importierte das im
Slim-Down geloeschte `reporting.mail_client`-Modul → ImportError →
try/except returnte stillschweigend False → User sah Fallback-Hinweis.

Fix: neues schlankes `src/mail_client.py` mit HTTP-Resend-Client
(POST api.resend.com/emails, kein SMTP — laeuft auf Azure App Service).
Credentials-Fallback-Kette:
1. tenant.mail_api_key + tenant.mail_from (per Tenant)
2. global_mail_api_key + global_mail_from (DB-Settings unter
   /admin/settings?section=general)
3. ENV-Variablen RESEND_API_KEY + RESEND_FROM (Deployment)

### MCP-Berechtigungen (Agent v0.5.6)
- `/admin/mcp-permissions` Seite + 5 Routen aus printix-mcp-linux
  portiert
- 2 Master-Toggles: `rbac_enabled` + `group_peer_reports_enabled`
- 5 MCP-Rollen: end_user / helpdesk / admin / auditor (DPO) / service_account
- Auditor + Service-Account nur per-User zuweisbar (nie via Gruppe) —
  Art. 37-39 GDPR / Art. 28 GDPR Mapping
- Orphan-Group-Cleanup-UI
- RBAC ist FULLY WIRED: server.py `_check_tool_permission` greift bei
  jedem MCP-Tool-Call, Denials landen im Audit-Log, live-toggleable
  ohne Container-Restart
- 54 neue i18n-Keys (mp_*, nav_rbac)
- Sidebar-Link „MCP-Berechtigungen" unter 🛡️ Datenschutz

### Aus v0.5.6 ueberschrieben
- /account Seite (war v0.5.6, jetzt im selben Push)
- ChatGPT-MCP-DCR verifiziert via curl-Test

## 0.5.6 — 2026-06-29 — User-Account-Seite + ChatGPT-MCP-DCR verifiziert

User-Feedback: „wenn ich mit als user anmelde, kommt eine komplett
falsche seite" — reguläre User (role_type=user) landeten via Fallback
auf /admin.

### Neue /account Seite
- `_user_home_target()` routet reguläre User auf `/account` statt
  Admin-Fallback.
- Eigene Info-Seite: 👋 Begrüßung, 📱 iOS-App-Setup mit QR + 3-Step-
  Anleitung, 🤖 MCP-Zugang (claude.ai-URL + ChatGPT-URL + OAuth-Client-
  ID/Secret mit Show/Hide-Toggle wenn MCP aktiv), 🛡️ DSGVO-Block (Email,
  Name, Rolle, aktive Tokens + Privacy-Link).
- ~30 neue i18n-Keys (account_*) in DE+EN.

### ChatGPT MCP-DCR verifiziert
/.well-known/oauth-authorization-server liefert
`registration_endpoint = .../oauth/register`. Test-POST mit JSON-Body
returnt korrekt einen client_id — DCR funktioniert.

## 0.5.5 — 2026-06-29 — Entra-DC DB-persistiert + Queue-Filter + slim Users-Search

Drei offene Punkte aus User-Feedback.

### Entra Device-Code: DB-Persistenz statt Session
User-Symptom: „kurz Code angezeigt, dann no device". Diagnose-Logs
(v0.5.2) wiesen auf Session-Cookie-Verlust hin — auf Azure App Service
passiert das laut diversen Berichten gelegentlich (Cookie-Verlust ueber
Reverse-Proxy, SameSite-Quirks, etc.).

Fix: device_code wird jetzt in der DB (settings-table, Key
`entra_dc_pending_<user_id>`) als JSON-Payload persistiert. Poll-
Endpoint liest **zuerst** aus DB, **dann** Session als Fallback.
Erfolg/Expire/Error löscht den DB-Eintrag (kein Stale-State).

### Queue-Picker: Anywhere-Filter + Suchfeld
In /admin/settings?section=queue: zwei neue Controls oben am Picker:
- 🌐 „Nur Anywhere-Queues" Checkbox
- Suchfeld (filtert nach Queue-Name + Drucker-Name)
JS hide/show auf den Select-Options, kein Round-Trip. Picker als
`<select size="8">` damit mehrere Optionen direkt sichtbar.

### Neuer Endpoint /desktop/users/search
Bisher konnten Employees keinen Printix-User auf dem iOS-Delegation-
Picker suchen (`/desktop/management/users` ist admin-only).

Neuer Endpoint: open fuer alle eingeloggten Token, liest aus
`cached_printix_users`-Cache (kein Live-Printix-API-Call → schnell +
keine Tenant-Credentials-Pruefung). Liefert nur Minimal-Felder
(`id, full_name, email, role`) und zwingt einen Tenant-Scope ueber
`get_parent_user_id` → `get_tenant_for_user`. Limit 50 Treffer pro
Suche.

iOS-App nutzt aktuell noch `managementUsers()` — wird in einem
Follow-up auf `users/search` umgestellt damit Employees den Picker
auch verwenden koennen.

## 0.5.4 — 2026-06-29 — Server-Handler für iOS-Delegation-Druck + iOS-Queue-Label-Fix

iOS-Picker erstellt seit v0.5.2 Targets mit ID `print:user:<printix_id>`
fuer Delegation-Druck an beliebige Printix-User. Server hat das Format
bisher nicht verstanden → Job wurde mit `target_unsupported` abgelehnt.

### Server: print:user:<id>-Handler in /desktop/send
- Neue Verzweigung in `_process_desktop_send_bg`: bei target_id
  beginnend mit `print:user:` lookuped die `cached_printix_users`-Tabelle
  nach printix_user_id, holt Email + full_name.
- Setzt `submit_user_email` = Email des Ziel-Users → Job landet in
  dessen SecurePrint-Queue (Printix attribuiert via `submitUserEmail`).
- Audit-Event `print_job_delegated` mit Sender/Empfaenger/Job-ID.
- Returnt `target_not_found` wenn User nicht im Cache (sollte nie
  passieren wenn iOS-Picker erfolgreich lookups, aber defensive).

### iOS: Queue-Label-Fix
- TargetsView ueberschrieb das vom Server gelieferte Queue-Label
  (z.B. „Anywhere - Marketing") mit hardcodiertem „Mein Secure Print".
- Jetzt: Server-Label wird 1:1 verwendet wenn vorhanden; Fallback nur
  fuer alte Server-Versionen.

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
