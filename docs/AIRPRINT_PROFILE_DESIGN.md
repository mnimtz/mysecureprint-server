# iOS AirPrint Profiles — Design

**Status:** Stage 1 shipped in v0.8.0 · **Last updated:** 2026-07-09

---

## 1. The idea

A user installs one **iOS configuration profile** on their Apple device.
After that, "MySecurePrint" appears in **every** app's Print dialog as
a real printer.

No Bonjour, no VPN, no local network — works over cellular, guest
Wi-Fi, or on the office LAN.

The print job lands on our server, is handled identically to a normal
app upload, and is forwarded via the Printix Cloud Print API to the
right SecurePrint queue. **Always personalised** — never a shared pool.

**Killer advantage over Printix' own app:** print from any iOS/iPadOS/
macOS app without Bonjour discovery — works anywhere the server is
reachable over HTTPS.

---

## 2. What we start with

Reused code from `printix-mcp-linux` (production-tested):

- `src/cloudprint/ipp_server.py` (~660 LOC) — IPP/IPPS FastAPI handler
- `src/cloudprint/ipp_parser.py` (~460 LOC) — IPP protocol parser
  (attributes, groups, job metadata)
- The Printix client integration is API-compatible with the other
  repo — `PrintixClient` has the same contract in both places.

**The hard IPP-protocol work is already done.** We port and wrap it.

---

## 3. Stage 1 scope (v0.8.0)

**Opt-in feature.** Admin must enable it, otherwise nothing changes.

### 3.1 What ships

| Component | Purpose |
|---|---|
| IPP server | Receives IPP print jobs (ported) |
| Token system + DB schema | Personalised profile per user × queue |
| `.mobileconfig` generator | Signed iOS configuration profile |
| Admin config UI | Feature flag + default-queue picker |
| Extended onboarding email | Attaches profile on invite when feature is on |
| iOS app tab | Users can create additional profiles |

### 3.2 What does *not* ship in v0.8.0

- No self-service web portal → v0.9.0
- No MDM variable substitution for bulk rollout → v0.9.x
- No group- or site-scoped default queues → v0.9.x
- No bulk refresh/revoke UI → v0.9.0

---

## 4a. User identification in the print stream

**Central design decision:** we identify the user **exclusively** via
the profile token in the URL — **never** via IPP attributes in the
payload.

### What iOS actually gives us (measured against live `ipp_server.py`)

| IPP attribute | Typical iOS value |
|---|---|
| `requesting-user-name` | `"iPhone von Marcus"` (device name, user-editable) |
| `job-originating-user-name` | Fallback, often empty or same as device name |
| `job-originating-host-name` | `"iPhone-Marcus"` (device hostname) |
| `job-name` | Document title — e.g. `"Rechnung_Mai.pdf"` |
| `document-format` | `application/pdf` (almost always) |

**iOS offers no reliable user identification.** The device field can be
freely changed in iOS Settings by the user.

### How we solve it

```
1. iOS Print → POST /airprint/{profile_token}
                    ↓ token = "3f4a...xyz24chars"
2. Server:    SELECT user_id, printer_id, queue_id, is_revoked
              FROM cloudprint_airprint_profiles
              WHERE profile_token = ?
                ↓
3. Auth check: is_revoked = 0? User exists? Still has permission
              on the queue (in case rights changed since creation)?
                ↓
4. Parse IPP payload → PDF bytes + job-name
                ↓
5. printix_client.submit_job(
      queue_id = <from token>,
      owner_email = <from user row>,
      pdf = <from IPP payload>,
      title = <from IPP job-name>,
   )
                ↓
6. Job arrives at Printix with ownerId = user@company.com
   → At the printer: only that user's card can release it
```

### IPP attributes we still read (metadata only)

- `job-name` → shown as job title in the app history
- `document-format` → sanity check (must be `application/pdf`)
- `job-originating-host-name` → audit log ("printed from iPad-XYZ")

The values are **never** used for auth decisions. Even if iOS sent
`requesting-user-name = "hacker@evil.com"` we would ignore it
completely.

### What the user sees in the app's job history

```
📄  Rechnung_Mai.pdf
   Sent from iPad von Marcus • via iOS AirPrint
   Queue: SecurePrint DE • Status: forwarded to Printix
```

- Filename = `job-name` (from IPP)
- "Sent from X" = `job-originating-host-name` (IPP metadata)
- "via iOS AirPrint" = our own marker (server sets `source='airprint'`)
- Queue = from the token
- Status = as usual

---

## 4b. Auth: personalised token

One profile = **one user × one queue**. Always.

```
URL:      /airprint/{profile_token}
Token:    base32(sha256(user_id + queue_id + created_at + server_secret))[:24]
Lifetime: unlimited, revocable (is_revoked = 1 in DB)
```

- A user can hold multiple profiles (e.g. one for SecurePrint + one
  for an HR queue).
- Every job arrives at Printix with the real user as owner.
- Card release at the printer: only the user themselves can release.

No Basic Auth, no OAuth popup — the token in the URL path is the only
authentication material. HTTPS only. The token is as long as a 128-bit
password.

---

## 5. Rollout paths

### Path A — Onboarding email (zero-touch for new users)

When an admin invites a user (`/admin/users/invite`) AND the feature
is on AND the user has permission on the default queue:

1. Server creates a profile row + token in the DB.
2. Server generates the `.mobileconfig` on the fly, signs it if a
   cert is configured.
3. The invitation email carries the attachment `MySecurePrint
   .mobileconfig` (or `.zip` — see the ZIP toggle) plus a short
   "how to install" block.
4. User opens the attachment on their iPhone → install dialog → done.
5. **The user can print from any iOS app immediately**, even without
   the MySecurePrint app installed.

**If the user has no permission on the default queue:** silent skip.
The invitation goes out normally, without the profile attachment. The
admin sees the state in the invite preview.

### Path B — Create in the app (existing users + extra queues)

When the user is already signed in to the app:

1. Settings → iOS Printers → list of their profiles.
2. "Add new printer" → queue dropdown (only queues they have
   permission on).
3. Optional: display name ("iPhone Marcus", "iPad HR").
4. "Create and install" → the app fetches the `.mobileconfig` over
   HTTPS → hands it to the iOS system via
   `UIDocumentInteractionController`.
5. iOS install dialog → done.

No QR code, no web portal — a pure in-app experience.

---

## 6. Database schema

```sql
CREATE TABLE cloudprint_airprint_profiles (
    id                 TEXT PRIMARY KEY,          -- UUID
    user_id            TEXT NOT NULL,             -- our internal user
    profile_token      TEXT NOT NULL UNIQUE,      -- URL segment
    printer_id         TEXT NOT NULL,             -- Printix printer UUID
    queue_id           TEXT NOT NULL,             -- Printix queue UUID
    queue_display_name TEXT,                      -- e.g. "SecurePrint DE"
    display_name       TEXT,                      -- e.g. "iPhone Marcus"
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

Auto-migration runs on server startup via `db.py`.

---

## 7. Admin config UI (`Configuration → iOS Mobile`)

New section under the existing **Configuration** sidebar entry. The
menu label is **"iOS Mobile"** (intentionally more neutral than
"AirPrint" — leaves room for future mobile features like widgets,
push notifications, MDM management).

```
Configuration
  ├── General
  ├── Printix API
  ├── Entra / OAuth
  ├── Email
  ├── 🆕 iOS Mobile
  ├── 🆕 iOS Mobile — Users
  ├── Security
  └── ...
```

Layout of the iOS Mobile section:

- **AirPrint profiles enable** — feature flag (default off).
- **Default queue for new users** — dropdown reads live from the
  Printix API, Anywhere queues 🌐 first.
- **Attach mobileconfig when inviting users** — controls whether new
  invites carry the profile.
- **Send as ZIP** — wraps the profile in a ZIP archive as fallback for
  strict corporate mail filters.
- **Organization shown in profile** — appears in the profile metadata
  on device ("Installed by: Acme Corp").
- **Signing certificate upload** — upload cert + private key in PEM
  format, sanity-checked via `cryptography.x509`. Optional; without
  it profiles are delivered unsigned (iOS shows "Not Verified"
  warning but install works).
- **Compatibility info box** — reminds the admin that the profile
  installs on iPhone, iPad, and Mac.

The new settings keys use the `ios_mobile_*` namespace so future mobile
features can slot under the same section:

- `ios_mobile_airprint_enabled`
- `ios_mobile_airprint_default_queue_id`
- `ios_mobile_airprint_default_printer_id`
- `ios_mobile_airprint_default_queue_name`
- `ios_mobile_email_attach_default`
- `ios_mobile_email_send_as_zip`
- `airprint_organization`
- `airprint_signing_cert_pem`
- `airprint_signing_key_pem`

When `ios_mobile_airprint_enabled = 0` the feature is completely off:
`/airprint/{token}` returns 404, invitation email is unchanged.

---

## 8. Internationalisation (i18n) — from day one

All strings introduced by this feature must be covered in every
supported language, at build time. No hardcoded German. No
"translate later" backfill.

### Server languages (14) — `src/web/i18n.py`

Core: `de`, `en`, `fr`, `it`, `es`, `nl`, `no`, `sv`
Fun-mode dialects: `bar`, `hessisch`, `oesterreichisch`,
`schwiizerdütsch`, `cockney`, `us_south`

The 8 core languages get real translations. The 6 fun dialects use
DE- or EN-fallbacks with a handful of character-word overrides
("Grod so speichan", "Bung it in a ZIP, guv").

### iOS languages (9) — `Localizable.xcstrings`

`de`, `en`, `es`, `fr`, `it`, `nb`, `nl`, `pt-BR`, `sv`

All 9 get real translations.

### Delivered strings

Server: ~41 keys × 14 languages ≈ 470 translations
iOS: ~33 keys × 9 languages = 297 translations

---

## 9. Display name in the iOS Print dialog

Format in the `.mobileconfig`:

```
DisplayName: MySecurePrint — {queue_display_name}
```

Example: `"MySecurePrint — SecurePrint DE"`

If the user sets their own display name ("iPhone Marcus"), we store
it in the app UI but do **not** write it into the profile — otherwise
iOS would show inconsistent names in the system Print dialogs across
apps.

---

## 10. Certificate for profile signing

**Server TLS cert** (Azure Managed / Let's Encrypt) handles HTTPS
termination — we already have that.

**Profile signing** (`.mobileconfig` itself):
- Priority 1: if an Apple Enterprise / Developer certificate is
  configured (via *Configuration → iOS Mobile → Upload*), sign with
  it — iOS shows "Verified".
- Priority 2: if no cert configured, deliver unsigned — iOS shows
  "Not Verified" (red warning) but the install still works.

For most operators, **unsigned is fine**. Enterprise customers who
want the green checkmark can upload an Apple Developer Enterprise
Certificate.

The upload form (`/admin/settings/airprint-signing/upload`)
validates:
- Both files must be PEM-formatted (`-----BEGIN` header).
- The private key must not be password-encrypted.
- `cryptography.hazmat.primitives.serialization.load_pem_private_key`
  and `cryptography.x509.load_pem_x509_certificate` must both parse
  successfully.

Certs live in the `settings` table (`airprint_signing_cert_pem` +
`airprint_signing_key_pem`). Removal is a single POST to
`/admin/settings/airprint-signing/clear`.

---

## 11. Extending the invitation email

Existing flow (`/admin/users/invite`):

```python
# New pre-email step:
if settings.get("ios_mobile_airprint_enabled") == "1" \
   and settings.get("ios_mobile_email_attach_default") == "1" \
   and _user_has_queue_permission(user, default_queue_id):
    profile = create_airprint_profile(
        user_id = user.id,
        queue_id = default_queue_id,
        created_via = "onboarding_email",
    )
    mobileconfig_bytes = generate_mobileconfig(profile)
    if settings.get("ios_mobile_email_send_as_zip") == "1":
        attachment = zip_wrap(mobileconfig_bytes, README_TXT)
        email.attach("MySecurePrint.zip", attachment, "application/zip")
    else:
        email.attach("MySecurePrint.mobileconfig",
                     mobileconfig_bytes,
                     "application/x-apple-aspen-config")
    email.body += render("airprint_onboarding_block.txt", ...)
```

Email block (i18n de/en/…):

```
📱 PRINT INSTANTLY FROM YOUR IPHONE

We have set up a native iOS printer for you. Open the attachment
MySecurePrint.mobileconfig on your iPhone and confirm the install in
iOS Settings. After that you can print from Safari, Mail, Photos or
any other app directly to our SecurePrint.

For job history, NFC card enrolment and delegation:
▸ MySecurePrint app in the App Store: {app_store_link}
```

---

## 12. iOS app changes

For non-admin users (`hasManagementAccess == false`), the AirPrint
view is shown as a **top-level tab** in the tab bar — after Upload/
Targets/Jobs, before Account. This makes the feature discoverable
for the target user segment.

For admins the tab bar is already crowded (Management is there), so
AirPrint appears under **More → iOS Printers**.

Detail screen `iOS Printers`:

```
┌─────────────────────────────────────┐
│  iOS Printers                       │
├─────────────────────────────────────┤
│                                     │
│  Native printer profiles for iPhone,│
│  iPad and Mac. Install a profile    │
│  once and print from every app to   │
│  your SecurePrint queue.            │
│                                     │
│  🖨️  MySecurePrint —                │
│      SecurePrint DE                 │
│      Last used: yesterday           │
│                                     │
│  ➕  Add new printer                 │
│                                     │
└─────────────────────────────────────┘
```

"Add new printer" wizard:

1. Queue dropdown (only queues from the user's `/me/queues`).
2. Optional display name.
3. Button "Create and install".
4. App fetches `.mobileconfig` over HTTPS.
5. `UIDocumentInteractionController` presents the iOS install dialog.
6. User confirms → profile installed.

**Detecting whether a profile is installed on-device**: iOS doesn't
let apps see this (privacy). We show **all profiles the server has
issued for this user** — the user has to check on-device if they
still have them.

---

## 13. Server endpoints (new)

```
POST /desktop/me/airprint/create
     Body: {queue_id, printer_id, display_name?}
     → {profile_id, download_url}

GET  /desktop/me/airprint/{profile_id}/download
     → .mobileconfig (application/x-apple-aspen-config)

GET  /desktop/me/airprint
     → [{id, queue_display_name, created_at, last_used_at, ...}]

DELETE /desktop/me/airprint/{profile_id}
     → {revoked: true}

POST /airprint/{profile_token}
     Content-Type: application/ipp
     → IPP handler (wraps the ported ipp_server.py)

GET  /airprint/{profile_token}
     → text/plain info response (health check)

POST /admin/settings?section=ios_mobile
     → save section (feature flag + default queue + email flags)

POST /admin/settings/airprint-signing/upload
     → upload cert + key
POST /admin/settings/airprint-signing/clear
     → remove uploaded cert

Admin bulk (v0.7.228+):
GET  /admin/airprint-users            → search + list users with profile counts
GET  /admin/airprint-users/{user_id}  → per-user detail
POST /admin/airprint-users/{user_id}/create  → create profile on behalf of user
GET  /admin/airprint/download/{profile_id}       → .mobileconfig
GET  /admin/airprint/download/{profile_id}.zip   → ZIP + README.txt
POST /admin/airprint/revoke/{profile_id}         → revoke
```

---

## 14. Stage 1 effort estimate (retrospective)

| Task | Actual effort |
|---|---|
| Design doc (this) | ~1 h |
| Port `ipp_server.py` + `ipp_parser.py` | ~3 h |
| DB schema + migration | ~1 h |
| Token system + `/airprint/{token}` handler | ~3 h |
| `.mobileconfig` generator (unsigned first) | ~3 h |
| PKCS7 signing (optional cert upload) | ~2 h |
| Admin config UI + i18n (14 langs) | ~4 h |
| Admin bulk user-management UI + i18n | ~3 h |
| Extended invitation email + ZIP fallback | ~2 h |
| iOS app: `AirPrintProfilesView` + wizard | ~4 h |
| iOS app: install sheet with `UIDocumentInteractionController` | ~1 h |
| iOS app: `Localizable.xcstrings` for 9 languages | ~2 h |
| End-to-end test + docs | ~3 h |
| **Total Stage 1** | **~32 h ≈ 4 working days** |

Roughly matched the initial estimate. The i18n work took slightly
longer than budgeted (real translations for 8 core languages, not
just DE/EN with fallbacks).

---

## 15. Rollout

**v0.8.0 (Stage 1 shipped):**
- Feature flag default OFF (opt-in).
- Existing installations unaffected.
- Operators can enable via `Configuration → iOS Mobile`.

**v0.9.0 (planned — Stage 2):**
- Self-service web portal for users without the iOS app installed.
- Bulk revoke, statistics per queue.
- iOS app: push notification on job completion.
- User-facing "how to uninstall" info sheet after revoke (iOS
  limitation: server can't force-remove profiles without MDM).

**v0.9.x (planned — Enterprise):**
- MDM variable substitution for mass rollout via Intune / Jamf.
- Group- / site-scoped default queues.
- Apple Developer Cert auto-renewal.

---

## 16. Risks + mitigation

| Risk | Mitigation |
|---|---|
| iOS shows "Not Verified" warning | Documented as OK for the default install; enterprise customers upload their own cert. |
| Token leaks into server access logs | Access-log filter masks `/airprint/{TOKEN_MASKED}`. |
| Server reachable but PDF doesn't land at Printix | The ported IPP server already has retry logic + audit log entries. |
| User loses iPhone with installed profile | Admin UI: search-by-user + bulk revoke. |
| Feature confuses existing app users | Opt-in default OFF, clear docs, app menu hidden until feature is on. |
| Custom port on the server (not 443) | Profile now reads the port from `public_url` — supports 8443 etc. and http:// for dev setups (v0.7.230). |

---

## 17. Post-mortem notes from Stage 1

Written after the retrospective on 2026-07-09.

**Went well:**
- IPP protocol port from the sister repo saved ~15 h of work. The
  hard part was solved.
- Token-only auth is very clean. No IPP-attribute-based auth logic
  means no path where user identity could be spoofed.
- The `PayloadScope: User` + no `TargetDeviceType` combo makes the
  profile universal (iOS + iPadOS + macOS) without any per-platform
  branching in the generator.

**Traps we walked into:**
- First iteration hardcoded port 443. Broke for a dev setup using
  `https://host:8443`. Fixed in v0.7.230 by reading port from
  `urlparse(server_url)`.
- Initial queue dropdown loaded from `group_queue_defaults` (the
  local table). That table is empty in fresh installs → dropdown
  said "no queues available" even when Printix had many. Fixed by
  reusing `_load_printix_queues_for_admin` which reads live from the
  Printix API.
- Signing section was originally information-only ("upload your cert
  in a future release"). Users legitimately complained. v0.7.230
  ships a working upload form.

**Unresolved:**
- iOS gives us no clean way to signal "profile has been uninstalled
  by the user". If the user removes the profile from iOS Settings
  the server doesn't know until the next print attempt (which will
  simply not happen). We accept this — the DB row shows `last_used_at`
  drifting, admins can filter for that.
