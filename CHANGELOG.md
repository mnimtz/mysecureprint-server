# Changelog — MySecurePrint Server

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
