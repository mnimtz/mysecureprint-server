# iOS Onboarding — Admin-initiated Configuration Push

## Goal

After the Azure App Service is deployed and the admin has registered
end-users, the admin wants to *send the iOS configuration* (server
URL + initial pairing token + optionally pre-filled username) to each
iPhone so the end user does not have to manually type the URL into
SetupView.

The current baseline is already:
- a per-tenant **QR code** at `/my/mobile-app/qr.png` carrying
  `{"v":1,"server":"…"}` — but it is **self-service** (employee
  visits the portal first), not admin-pushed.
- the iOS app has a working QR scanner that already understands this
  JSON payload (`QRScannerView.swift:110-115`) and a Setup screen with
  a "QR scannen" button (`SetupView.swift:26-33`).

So pattern infrastructure exists; what's missing is the **admin-side
trigger** and an optional **pre-pairing token** that means the user
doesn't even have to type their MS email twice.

---

## Patterns compared

| # | Pattern                          | UX (end user)                          | Security                                | Server dev | iOS dev | Ops setup                          |
|---|----------------------------------|----------------------------------------|-----------------------------------------|------------|---------|------------------------------------|
| 1 | **Email deep-link invitation**   | Tap link in email → app opens, configured | Time-bound JWT + single-use, but email is plaintext channel | medium     | low     | needs SMTP (already required for invites) |
| 2 | **QR code (printed/displayed)**  | Open app, tap QR scan, point camera    | Local channel only; no replay over the air | low (exists) | done    | admin needs to display/print       |
| 3 | **Push to Entra `extensionAttribute1`** | After MS sign-in, app reads attr, configures itself | Very tight (signed by MS auth) but data lives in Entra | high (Graph writes) | medium  | needs Graph `User.ReadWrite.All` consent |
| 4 | **`.well-known/discovery` on email domain** | Type email, app discovers server | Strong if HTTPS + DNSSEC; depends on operator DNS | low        | medium  | operator must own the email domain |
| 5 | **MDM `.mobileconfig`**          | Profile auto-installed; app pre-configured | Enterprise-grade signed payload          | low (output only) | low | MDM (Intune/Jamf) required         |
| 6 | **Per-operator app build**       | Install from App Store, zero config    | App Store signed                        | low        | low     | own App Store presence per customer (no go) |

### Why patterns 3-6 are out of scope for v0.2

- **#3 (Entra attribute)**: writing to user attributes needs admin
  consent for `User.ReadWrite.All`, the data only becomes available
  *after* MS sign-in (so it can't help with the very-first server URL),
  and it doesn't work if the user signs in with username/password
  instead of Entra. Also: this server is multi-tenant, but
  `extensionAttribute1` is a tenant-wide schema slot — collision risk
  with the customer's own attribute usage. **Reject.**
- **#4 (.well-known)**: elegant, but most SMB customers don't own DNS
  for their `@gmail.com` / `@outlook.com` / mixed-domain workforce.
  Worth revisiting once we have enterprise-only customers. **Defer.**
- **#5 (MDM)**: future enterprise feature. Trivial server side (just
  document the keys) but no immediate ROI. **Defer.**
- **#6 (per-customer build)**: would require Marcus to maintain N App
  Store listings. **Reject.**

---

## Recommendation

**Ship patterns #1 (email deep-link) + #2 (QR code from admin
panel) together.** The two cover the realistic onboarding paths:

- **Email deep-link** = remote workforce, "I just hired Bob, send him
  the link" — admin clicks one button, Bob taps the link in Apple Mail
  on his iPhone, app opens fully configured, taps "Sign in with
  Microsoft", done. Zero typing.
- **QR code** = on-site / in-person, "here's your new iPad, scan
  this" — admin opens the user's detail page in the management
  portal, projects the QR on screen / prints it, user scans.

The two reuse 80% of the server logic: a small **`mobile_invites`**
table holds the one-time token, both the email link and the QR carry
it, the iOS app trades it for a bearer token on first launch. PKCE/SSO
keeps working unchanged.

Pattern #2 already half-exists (`/my/mobile-app/qr.png`); we just
extend the payload, surface it under the admin user-edit page, and
add a "QR an Mitarbeiter zeigen" button.

---

## Detailed implementation plan

### Shared building block — `mobile_invites` table + deep-link grammar

**DB** (new table):

```sql
CREATE TABLE mobile_invites (
  invite_id     TEXT PRIMARY KEY,          -- 24 bytes urlsafe, opaque
  user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  invite_token  TEXT NOT NULL UNIQUE,      -- 32 bytes urlsafe; single-use
  server_url    TEXT NOT NULL,             -- baked at create-time, frozen
  created_by    TEXT NOT NULL,             -- admin user_id
  created_at    TEXT NOT NULL,
  expires_at    TEXT NOT NULL,             -- now + 7 days default
  consumed_at   TEXT,                      -- NULL until used
  consumed_from TEXT,                      -- peer IP for audit
  channel       TEXT NOT NULL              -- 'email' | 'qr'
);
CREATE INDEX idx_mobile_invites_user ON mobile_invites (user_id);
```

**Deep-link / Universal-Link grammar** (one URL serves both channels):

```
https://<server>/m/setup#i=<invite_token>
```

`#` fragment so the token never reaches the web server's logs as a
query string. Falls back to a custom-scheme variant
`mysecureprint://setup?i=<invite_token>&s=<server_b64>` for the deep
link until Universal Links are wired (see ENTRA_REVIEW #11).

**QR payload** (extends existing JSON shape, fully backward-compatible):

```json
{"v":2, "server":"https://acme.mysprint.azurewebsites.net", "invite":"…"}
```

The existing v1 payload still works (no `invite` key → user-driven
login as today).

---

### Server-side changes

**Files touched**:

- `src/db.py` — new helpers `create_mobile_invite(...)`,
  `consume_mobile_invite(token, peer_ip)`, `cleanup_expired_invites()`.
- `src/web/app.py` — admin-side routes under the user edit page:
  - `POST /admin/users/{user_id}/invite/mobile` — create invite, send
    email via existing `invite_mail` module with new template
    `invite_mobile.html`/`.txt`.
  - `GET /admin/users/{user_id}/invite/mobile/qr.png` — render the
    same invite as a QR.
  - `GET /admin/users/{user_id}/invite/mobile` — modal that shows
    "Send email | Show QR | Copy link" + a list of pending invites
    with their expiry/revoke action.
  - `POST /admin/users/{user_id}/invite/mobile/{invite_id}/revoke`.
- `src/web/desktop_routes.py` — new endpoint:
  - `POST /desktop/auth/invite/redeem` — body
    `{invite_token, device_name}` → on success returns the same
    `{token, user}` shape as `/desktop/auth/entra/authcode/exchange`.
    Atomic: row's `consumed_at` is set inside the same transaction
    that issues the bearer token.
  - `GET /m/setup` — a thin HTML page that detects the iOS app via
    Universal-Link / scheme handover; if the app is not installed,
    show "Install from TestFlight" with the existing TestFlight QR.
- `src/web/templates/employee/my_mobile_app.html` — already exists;
  reuse for the admin-side rendering with `is_admin_view=True`.
- `src/web/templates/admin_user_edit.html` — add the "Mobile App
  einrichten" button + pending-invite list.
- `src/web/templates/admin_user_invite.html` — already exists for
  email account invites; add a checkbox "auch Mobile-Setup-Link
  mitsenden" so account-creation + mobile invite become one click.
- `src/invite_mail.py` — new template `invite_mobile.{html,txt}`:
  short, links to `/m/setup#i=<invite>`, mentions TestFlight, has
  the QR inlined as `<img src="cid:qr">` for desktop clients.

**Security plumbing**:

- `invite_token` = `secrets.token_urlsafe(32)` (~256 bits).
- Single-use: `UPDATE … SET consumed_at=? WHERE invite_token=? AND
  consumed_at IS NULL` and check `rowcount==1`. Race-free.
- Default expiry 7 days, configurable per call.
- `cleanup_expired_invites()` called from the existing startup
  background sweeper.
- Rate limit `POST /desktop/auth/invite/redeem` to 5 attempts /
  minute / IP.
- Audit log: `audit(admin_id, "mobile_invite_create", user_id)` and
  `audit(user_id, "mobile_invite_redeem", peer_ip)`.

**Effort**: ~6 hours. DB helpers (1 h), admin routes (1.5 h), redeem
endpoint (1 h), email template (1 h), admin UI (1.5 h).

---

### iOS-side changes

**Files touched**:

- `MySecurePrint-Info.plist` — extend `CFBundleURLTypes` to also
  catch `mysecureprint://setup`. Add `com.apple.developer.
  associated-domains` entitlement for the future Universal Link.
- `MySecurePrintApp.swift` — add `.onOpenURL { url in … }` that
  dispatches `mysecureprint://setup?i=…&s=…` to a new
  `SetupCoordinator.handle(url:)`.
- `SetupView.swift` — when a pending invite is in memory (set by
  `.onOpenURL`), skip the URL TextField and jump directly to a
  "Einladung gefunden — jetzt einrichten" view with one big button.
  On tap, call the new redeem endpoint, store the bearer token in
  Keychain via the existing `SettingsStore`, then push the main
  ContentView.
- `QRScannerView.swift` — already parses JSON; extend to read
  optional `"invite"` key and route into the same redeem flow as the
  deep link.
- `ApiClient` (in `PrintixSendCore`) — add `redeemInvite(token:
  String, deviceName: String) async throws -> LoginResponse`.

**UX**:

```
[Email/QR with deep-link / QR-with-invite]
        │
        ▼
SetupView (alternate mode "Einladung")
   ┌─────────────────────────────────┐
   │ ✓ Server: acme.mysprint…        │
   │ ✓ Einladung von: admin@acme.de  │
   │ ✓ Konto: bob@acme.de             │
   │                                  │
   │ [ Einrichten ]                   │
   └─────────────────────────────────┘
        │ tap
        ▼
POST /desktop/auth/invite/redeem
        │
        ▼ {token, user}
ContentView (main app)
```

After redeem, the user is **already signed in**. They never see the
LoginView. The next time they need Entra (e.g. token expires) they
get the full PKCE flow as today.

**Effort**: ~4 hours. URL routing + view (1.5 h), API client method
(0.5 h), redeem-success flow (1 h), QR-with-invite branch (0.5 h),
testing on TestFlight (0.5 h).

---

### Security considerations (summary)

- **Token entropy**: 256 bits — equivalent to the existing desktop
  bearer.
- **Single-use**: enforced atomically in SQL.
- **TTL**: 7 days default; admin can shorten in UI.
- **Replay-safe**: `consumed_at` flag; second redeem fails closed.
- **Transport**: only over HTTPS (server has `secure_cookies` middleware).
- **Audit**: both create and redeem logged with admin ID and peer IP.
- **Revocation**: admin can revoke any pending invite from the user
  edit page.
- **Threat: email interception** — mitigated by short TTL +
  single-use; an attacker who reads the email *before* the user and
  redeems first will be visible in the audit log (different IP / UA).
  This is the same trust assumption as the existing account-invite
  flow, which uses the same SMTP channel.
- **Threat: QR over shoulder** — only an issue for the few seconds
  between admin showing the QR and user scanning. The QR can be
  marked one-time-view in the admin UI (regenerate after each
  display) for paranoid setups.
- **No bypass of Entra**: an invite issues a **local bearer token**
  for the specific user account. If that account has Entra binding,
  Entra is still the authentication path for re-auth / token refresh
  later. The invite is just an account-creation shortcut — not a
  parallel authentication mechanism.

---

### UX flow diagram (admin-side)

```
Admin → /admin/users/{id}/edit
   │
   ▼
  [ Mobile App einrichten ▾ ]
   │
   ├── E-Mail-Einladung senden ──► generates invite, sends email
   │                                  with deep-link + TestFlight info
   │
   ├── QR-Code anzeigen ──────────► modal with QR (regenerated each
   │                                  open, 7-day TTL)
   │
   ├── Link kopieren ─────────────► clipboard, paste into Teams/Slack
   │
   └── Offene Einladungen … ──────► list with [revoke] buttons,
                                       expiry timestamps
```

---

## Open questions for user before implementation

1. **Universal Links vs custom scheme**: do we want to register an
   `apple-app-site-association` file on the Azure App Service now (so
   the deep-link is `https://…/m/setup#i=…`) or stay with
   `mysecureprint://setup` for v0.2 and migrate later? The former is
   ~4 h extra and requires a stable HTTPS domain (Azure App Service
   default domain works but isn't pretty).
2. **Invite TTL**: 7 days okay, or shorter (24 h) by default with an
   admin override?
3. **One-time-view QR**: should opening the QR modal regenerate the
   token (max paranoia, but a small UX cost if the admin closes the
   modal accidentally), or keep one stable token until the user
   redeems it?
4. **Combine with account-create**: should the existing
   `/admin/users/invite` route automatically also create a mobile
   invite (checkbox default ON), or keep mobile-invite as a separate
   action on existing users?
5. **Email channel**: SMTP is configured per tenant today; should
   mobile invites fall back to "copy link to clipboard" when SMTP is
   unset, or hard-require SMTP?
6. **Self-service vs admin-only**: keep the existing
   `/my/mobile-app/qr.png` (employee scans their own QR from the
   web portal) alongside the new admin-pushed flow, or deprecate it?
   My recommendation: keep both — self-service for users who already
   logged into the web portal, admin push for the cold-start case.
7. **TestFlight install flow**: the `/m/setup` HTML page sees the
   user came from the link but the app is not yet installed. Should
   it auto-redirect to TestFlight, or show an explainer page first?
8. **Pre-fill username vs full passwordless**: the invite is bound
   to a `user_id`, so we *could* fully skip the LoginView. Or we
   could pre-fill the email field and still require MS sign-in for
   the first session (stronger audit trail). Which posture do you
   want for v0.2?

---

## Effort summary

| Component                                  | Effort  |
|--------------------------------------------|---------|
| DB + helpers + GC                          | 1.0 h   |
| Admin routes (create / qr / redeem-list)   | 1.5 h   |
| Redeem endpoint + rate-limit + audit       | 1.0 h   |
| Email template (HTML + txt + i18n)         | 1.0 h   |
| Admin UI on user-edit page                 | 1.5 h   |
| iOS deep-link handler + invite SetupView   | 2.0 h   |
| iOS API client method                      | 0.5 h   |
| iOS QR-with-invite branch                  | 0.5 h   |
| End-to-end testing (sim + TestFlight)      | 1.0 h   |
| Total                                      | **10 h**|

Optional follow-ups (not in v0.2):

| Optional                                   | Effort  |
|--------------------------------------------|---------|
| Universal Links (associated-domains)       | 4 h     |
| MDM `.mobileconfig` exporter               | 2 h     |
| `.well-known/mysecureprint-config.json`    | 1 h     |
| Per-invite single-use QR regeneration      | 1 h     |
