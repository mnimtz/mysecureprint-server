# v0.2.0 — iOS Onboarding Server-Side Build Report

Date: 2026-06-29
Tag: v0.2.0 (bumped from v0.1.3)
Scope: server-side only (mysecureprint-server). iOS-app side ships separately.

## 1. New routes + templates + DB columns

### Routes
| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET    | `/admin/users/{user_id}/mobile-invite` | admin | Manage page (list + new form) |
| POST   | `/admin/users/{user_id}/mobile-invite/create` | admin | Create + optionally send email |
| POST   | `/admin/users/{user_id}/mobile-invite/{invite_id}/email` | admin | Resend by email |
| POST   | `/admin/users/{user_id}/mobile-invite/{invite_id}/revoke` | admin | Soft-revoke (expires_at = now) |
| GET    | `/admin/users/{user_id}/mobile-invite/{invite_id}/qr.png` | admin | Render QR as PNG |
| GET    | `/m/setup?i=<token>` | public | iOS explainer page + deep-link |
| POST   | `/api/v1/mobile-invite/redeem` | public+token | Atomic redeem → bearer token |
| POST   | `/admin/users/invite` (existing) | admin | Now also accepts `also_create_mobile_invite` |

### Templates
| File | New / Modified |
|------|----------------|
| `src/web/templates/admin_user_mobile_invite.html` | new |
| `src/web/templates/m_setup.html` | new |
| `src/web/templates/admin_user_invite.html` | modified — checkbox + mobile-invite URL display |
| `src/web/templates/admin_users.html` | modified — `📱 Mobile invite` action per row |

Per-route email templates as separate Jinja files were intentionally
inlined — the slim repo does not ship the `invite_mail` / `reporting`
package, so `_send_mobile_invite_email()` builds the HTML body in
Python and ships it via the existing `send_report()` helper (when
present). When SMTP is not configured, the UI falls through to the
"copy link to clipboard" fallback per design Q5.

### DB schema — `mobile_invites`
| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | UUID4 |
| user_id | TEXT FK users(id) ON DELETE CASCADE | |
| token | TEXT UNIQUE | `secrets.token_urlsafe(32)` |
| token_hash | TEXT | SHA-256 of token; indexed |
| server_url | TEXT | snapshot of `MCP_PUBLIC_URL` |
| ttl_seconds | INTEGER default 604800 | |
| created_at | TEXT | ISO-8601 |
| expires_at | TEXT | indexed |
| redeemed_at | TEXT | '' until redeem |
| redeemed_from | TEXT | peer IP (audit) |
| created_by | TEXT | admin user_id |
| channel | TEXT | 'email' / 'qr' / 'both' |
| email_sent_at | TEXT | '' until first send |
| email_recipient | TEXT | snapshot of users.email |

Indexes: `idx_mobile_invites_user`, `idx_mobile_invites_token_hash`,
`idx_mobile_invites_expires`.

## 2. Lines added/modified

| File | Lines added | Notes |
|------|-------------|-------|
| src/db.py | ~220 | mobile_invites schema + 8 helpers + GC extension |
| src/web/app.py | ~470 | 7 routes + 3 internal helpers + invite-POST patch |
| src/web/i18n.py | ~110 | 50 keys × 2 langs + fallback loop |
| src/web/templates/admin_user_mobile_invite.html | 130 | new |
| src/web/templates/m_setup.html | 46 | new |
| src/web/templates/admin_user_invite.html | +18 | checkbox + URL panel |
| src/web/templates/admin_users.html | +5 | row action button |
| CHANGELOG.md | +55 | 0.2.0 entry |
| VERSION | 1 | 0.1.3 → 0.2.0 |

## 3. Validation

- `python3.12 -c "import ast; ast.parse(open('src/db.py').read()); ast.parse(open('src/web/app.py').read()); ast.parse(open('src/web/i18n.py').read())"` — **PASS** (all 3 OK).
- DB migration smoke (in-memory sqlite, `DB_PATH=/tmp/test_mi.db`):
  - `mobile_invites` table present with all 14 columns.
  - `create_mobile_invite` produces 43-char urlsafe token.
  - `get_mobile_invite_by_token` round-trip ✓.
  - First `redeem_mobile_invite` → True; second → False (single-use enforced atomically).
  - `cleanup_expired_pending` deletes expired unredeemed rows.
  - `revoke_mobile_invite` soft-revokes (expires_at = now).
- Jinja parse check (jinja2 3.x):
  - `admin_user_mobile_invite.html` parse OK
  - `m_setup.html` parse OK
  - `admin_user_invite.html` parse OK
  - `admin_users.html` parse OK
- i18n key presence check: `mobile_invite_title` + `m_setup_title` populated for `de`, `en`, and all fallback languages (`fr`, `schwiizerdütsch`, etc).

## 4. End-to-end flow

```
Admin (browser)
  └─ /admin/users → row → "📱 Mobile invite"
       └─ /admin/users/{uid}/mobile-invite (GET)
            └─ form: TTL (24h/7d/30d), channel (email/qr/both), recipient
                 └─ POST create  → create_mobile_invite() → fresh raw token
                      ├─ audit("mobile_invite_created")
                      ├─ if channel ∈ {email,both} + send_email_now=ON:
                      │      _send_mobile_invite_email() → mark_email_sent
                      │      audit("mobile_invite_sent_email")
                      └─ render page with: invite URL once, QR SVG, "Copy URL", "Email", "QR PNG"

End user (iPhone)
  └─ taps email link → /m/setup?i=<token> (GET, public)
       ├─ UA check → if iOS, show "Open MySecurePrint" button
       ├─ button href = mysecureprint://setup?server=<server>&token=<token>
       └─ also: QR SVG, raw URL fallback, App-Store install link

iOS app (future v1.1.0)
  └─ .onOpenURL(mysecureprint://setup?...)
       ├─ parse server + token
       ├─ run MS Entra PKCE sign-in (default Q8 — still required)
       ├─ POST /api/v1/mobile-invite/redeem
       │      { token, entra_oid, email, display_name, device_name }
       │      ↓
       │      server: look up invite → check expiry → check OID matches users.entra_oid
       │             (or first-time-link if entra_oid empty + OID was just authenticated by MS)
       │      ↓
       │      atomic UPDATE … SET redeemed_at=now, redeemed_from=peer_ip
       │             WHERE token_hash=? AND redeemed_at='' AND expires_at>now
       │      ↓
       │      desktop_auth.create_token(user_id, device_name)
       │      audit("mobile_invite_redeemed")
       │      → 200 { bearer_token, server_url, user }
       │      or 410 Gone (already redeemed / expired / race)
       │      or 403 (entra_oid mismatch)
       └─ store bearer_token in Keychain → log into ContentView
```

## 5. iOS-app follow-up tasks (separate commit in printix-mcp-addon)

NOT touched in this commit. Track these for iOS v1.1.0:

1. **URL scheme handler** — extend `CFBundleURLTypes` in `MySecurePrint-Info.plist`
   to include `mysecureprint://setup`. (Custom scheme; Universal Links → v0.3.)
2. **`.onOpenURL`** in `Printix_MobilePrintApp.swift` (or `LoginView.swift`) —
   dispatch to a `SetupCoordinator.handle(url:)`.
3. **Parse query params** — extract `server` + `token` from the URL.
   Pre-fill `SettingsStore.serverURL = server`; stash token in memory.
4. **Mandatory MS sign-in** — even with a valid invite, run the existing
   Microsoft Entra PKCE flow (per design Q8 — stronger audit trail).
   Pre-fill email if Microsoft userinfo is reachable; otherwise let
   the user choose the account.
5. **Redeem call** — after MS sign-in succeeds, POST `/api/v1/mobile-invite/redeem`
   with `{ token, entra_oid, email, display_name, device_name }`. Expect
   `{ bearer_token, server_url, user }`.
6. **Keychain store** — persist `bearer_token` via existing
   `KeychainTokenStore`; populate the `SettingsStore` with `server_url`
   + the user object; skip LoginView, jump to ContentView.
7. **Error handling**:
   - 410 already_redeemed → show "This invite was already used" + open
     normal login.
   - 410 expired → "Ask your admin for a new invite."
   - 403 oid_mismatch → "This invite belongs to a different account."
8. **Test against a live deployed server** — full TestFlight round-trip:
   admin clicks create → email arrives → tap link on iPhone → app opens
   pre-configured → MS sign-in → bearer issued → can print.

## 6. Git push + tag

After all edits land and validation passes, the next step is:

```bash
git add -A
git commit -m "v0.2.0: iOS onboarding via email deeplink + admin QR
...
"
git tag v0.2.0
git push origin main
git push origin v0.2.0
```

(Pushed in the same task — see git log + remote.)
