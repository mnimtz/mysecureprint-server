# Microsoft Entra ID Integration — Code Review

> **Resolved in v0.1.2 (2026-06-29)** — The three 🔴 Critical items
> below (#1 tid verification, #2 email-link removal, #3 single-use
> state) have all been fixed. See `CHANGELOG.md` and
> `ENTRA_FIXES_REPORT.md` for the implementation details.
> Remaining 🟠/🟡 items are still open.


Scope: mysecureprint-server `@ ac89825` and the iOS app
`printix-mcp-addon/MobileApp/ios-client/MySecurePrint`. Focus on the
Web Auth Code Flow (admin Web-SSO), the Device-Code Flow (legacy
desktop), the PKCE flow (iOS), and the user-identity contract between
the two sides.

Verdict: **GO-with-fixes.** No show-stopping vulnerabilities, but
several issues should be fixed before broader rollout — most importantly
the **`tid` issuer-trust gap** (any Microsoft tenant can sign in unless
`entra_tenant_id` is set) and **`state` re-use across requests**.

---

## Server side (`mysecureprint-server`)

### 🟢 Strengths

- **`src/entra.py:186-198`** — PKCE pair generation is correct:
  `secrets.token_urlsafe(64)[:96]` produces a 96-char verifier (well
  inside RFC 7636's 43–128 range, ~70 bytes entropy after the slice),
  and the challenge is `BASE64URL(SHA256(verifier))` without padding.
- **`src/entra.py:248-257`** — Explicit comment + correct decision to
  **omit `client_secret`** on the PKCE token exchange (Microsoft would
  return `AADSTS700025` otherwise). This is the correct mobile/native
  contract.
- **`src/web/desktop_routes.py:1200-1232,1274-1282`** — Verifier and
  `state` are stored **server-side only** in
  `desktop_entra_authcode_pending`; the client never sees the verifier.
  `state` is validated server-side at `1274`. Good.
- **`src/web/desktop_routes.py:1336-1339`** — Pending row is **deleted
  after success**, preventing replay of the same `session_id`.
- **`src/entra.py:769-787`** — JWT payload decode without signature
  validation is intentional and documented: token came from MS over TLS
  POST, so transport authenticity stands in for signature checks.
  Acceptable given the threat model.
- **`src/desktop_auth.py:49-62`** — Desktop bearer tokens are
  `secrets.token_urlsafe(32)` = 256 bits of entropy. Strong.
- **`src/web/app.py:949-1075`** Web-SSO uses HTTPOnly session for
  `entra_state` and pops it on callback — correct CSRF defence pattern.

### 🔴 Critical

1. **[✅ RESOLVED v0.1.2]** **No `tid` (issuer-tenant) verification — any Entra tenant can sign
   in if you configured `entra_tenant_id="common"`** (which the default
   in `get_config()` falls back to via `tenant_id or "common"`,
   `entra.py:96, 117, 217, 246`).
   - Effect: if an operator deploys with `tenant_id` left empty, the
     authorize URL becomes `…/common/…` and **any user in any
     Microsoft tenant** (including consumer MSA) who knows the redirect
     URL can sign in. `get_or_create_entra_user` then either creates a
     fresh account (if `entra_auto_approve=1`) or links by email
     (mid-collision risk, see Critical #2).
   - Fix: in `exchange_code_pkce` and `exchange_code_for_user`, after
     extracting the `id_token`/`/me` data, **reject** if the configured
     `entra_tenant_id` is non-empty and the token's `tid` claim doesn't
     match. For multi-tenant deployments add an explicit
     `entra_allowed_tenants` allowlist (CSV) — never rely on
     `common`+email matching alone.

2. **[✅ RESOLVED v0.1.2]** **Email-based account linking is unverified** (`db.py:1325-1337`).
   `get_or_create_entra_user` will silently attach an Entra OID to any
   local account whose email matches case-insensitively. Combined with
   #1, an attacker on a *different* Entra tenant who creates a user
   with the victim's email can hijack the local account on first Entra
   login.
   - Fix: only auto-link if either (a) the configured tenant matches,
     or (b) the local account has `email_verified=1` AND the Entra
     token's `email_verified`/`upn` matches the local email exactly.
     Otherwise require admin to manually link. At minimum, log/audit
     the link as a security event.

3. **[✅ RESOLVED v0.1.2]** **Race / re-use window on `state`** (`desktop_routes.py:1200-1232`).
   The state is stored once and validated, but the row is only deleted
   on **success** (line 1336). On exchange failure (state mismatch,
   token exchange 502, user not approved) the row remains for up to
   600 s and can be retried with a fresh `code` from a man-in-the-
   browser. Mitigation is partial because Microsoft codes are
   single-use, but state re-use weakens CSRF guarantees.
   - Fix: delete the pending row at the start of `exchange` (or mark it
     `consumed=1`) before doing anything else, so each `session_id` is
     a strict one-shot.

### 🟠 Important

4. **No garbage collection of pending tables.**
   `desktop_entra_pending` (Device Code) and
   `desktop_entra_authcode_pending` (PKCE) accumulate rows on aborts,
   expired flows, or network failures. There is **no cron / startup
   sweep** that deletes `expires_at < now()`.
   - Fix: add a background task or call a `cleanup_entra_pending()`
     helper at the top of each `start` route.

5. **`prefers­EphemeralWebBrowserSession = false` + multi-account
   server** (`LoginView.swift:203`). The server uses `prompt=select_
   account` (`entra.py:104, 225`) which mitigates this client-side, but
   the combination means a user who logs out of *the app* still has a
   Microsoft session cookie in iOS's shared web auth cookie jar. Next
   login auto-selects the previous identity unless the user manually
   switches. Document this; consider making it a Settings toggle
   ("Force account chooser") for shared iPads.

6. **`auto_register_app` creates Multi-Tenant apps**
   (`entra.py:478, signInAudience: "AzureADMultipleOrgs"`). Combined
   with Critical #1, this **silently widens** the trust scope: even
   admins who think they bound the app to their own tenant get a
   multi-tenant registration. The Web SSO redirect template later sets
   `entra_tenant_id` to the admin's tenant (`app.py:1241`), which
   helps — but `get_config()` still falls back to `"common"` if that
   setting is later cleared.
   - Fix: default to `AzureADMyOrg` for new auto-registered apps. Offer
     "multi-tenant" only as an explicit checkbox in the Auto-Setup
     wizard.

7. **Web SSO flow uses `_SCOPES = "openid profile email"`**
   (`entra.py:31`), no `User.Read`, but then `exchange_code_for_user`
   relies on the **id_token** instead of Graph `/me` — that's correct.
   However the PKCE flow (`_SCOPES_GRAPH_USER_READ`) asks for
   `offline_access` and `User.Read` even though the server **discards
   the refresh_token immediately** (`entra.py:280-298` only reads
   `access_token`).
   - Fix: drop `offline_access` from the PKCE scope unless you intend
     to persist + use the refresh_token (you don't today). Smaller
     consent prompt + less attack surface.

8. **`auto_register_app` hardcodes a year-2099 secret expiry**
   (`entra.py:516`). Entra silently caps `passwordCredential` lifetime
   at 24 months for newly created secrets — the server logs success,
   but the secret will silently expire after 2 years and Web SSO will
   break with no warning.
   - Fix: detect the actual `endDateTime` from Graph's response and
     surface a "secret expires on YYYY-MM-DD" badge in the admin UI;
     add a 30-days-before warning.

9. **`get_or_create_entra_user` derives the username from the email
   local-part** (`db.py:1344`) with a numeric collision suffix. For two
   users `alice@acme.com` and `alice@beta.com` whose accounts are
   linked separately, you get `alice` and `alice1`. Login-by-username
   becomes ambiguous and the audit log shows confusing names.
   - Fix: prefer `oid` short-hash or the full UPN as the username for
     Entra-created users; never collide silently.

10. **Token-revocation gap on Entra disable**. Disabling Entra in the
    admin settings (`entra_enabled=0`) does not revoke existing
    desktop tokens that were issued via Entra. A compromised Entra
    account stays signed in to the iOS app indefinitely.
    - Fix: add `revoke_tokens_for_user(user_id)` and call it when an
      admin disables a user OR disables Entra globally OR rotates the
      client secret.

### 🟡 Nice-to-have

- **Constant-time state comparison**. `desktop_routes.py:1274` does
  `state != row["state"]`. With short strings this is fine, but use
  `secrets.compare_digest` for defence-in-depth.
- **`exchange_code_pkce` logs `resp.text[:500]`** (`entra.py:277`) on
  failure. Microsoft error responses occasionally echo back the
  `error_description` containing the `code` or other request-bound
  data; cap to 200 chars or strip query-like fragments.
- **No rate limit** on `/desktop/auth/entra/authcode/exchange`.
  Combine with #3 and `state` brute force becomes (very weakly)
  conceivable.
- **`User.Read` scope only** — fine today; if you ever add group
  sync from Entra you'll need `GroupMember.Read.All` (delegated). Plan
  for that scope addition's UX (re-consent prompt).
- **`me_data.get("id")` is the **directory object id**, not the same
  thing as the `oid` claim** in JWT for guest users in some edge cases.
  The id_token's `oid` is the canonical identifier — consider keeping
  both and indexing on the OID claim.

---

## iOS side (MySecurePrint)

### 🟢 Strengths

- **`KeychainTokenStore.swift:32-43`** — Token stored with
  `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` + shared
  access-group for the Share Extension. Matches the C-4 fix.
- **`SettingsStore.swift:155-167`** — One-shot migration of the legacy
  UserDefaults token into Keychain, guarded by `migrated_to_keychain_v1`
  to prevent re-write of an empty token after sign-out.
- **`LoginView.swift:158-165`** — State + code are explicitly extracted
  from the callback URL via `URLComponents`, not regex parsing.
- **`LoginView.swift:170-178`** — Server-side `state` is the
  authoritative comparator; the iOS app passes the value through but
  does not assume it's valid.
- **`LoginView.swift:28, 221-231`** — `WebAuthAnchor` is held as
  `@State` and looked up against the foreground-active window — fixes
  the lifecycle issue from I-2.
- **Custom-URL scheme registered correctly**
  (`MySecurePrint-Info.plist:31-40`) with a bundle-unique
  `CFBundleURLName = de.nimtz.mysecureprint.oauth`.

### 🔴 Critical

11. **Custom URL scheme is not unique to the app** —
    `mysecureprint://` is registered as `CFBundleURLSchemes`
    (`MySecurePrint-Info.plist:38-40`). Any other iOS app can register
    the same scheme and intercept the OAuth callback. The standard
    mitigation is `state` + server-side validation (which we do) plus
    PKCE (which we do), so this is **defence-in-depth, not a direct
    compromise**. But:
    - On a hijack, the attacker sees the `code` and `state` in their
      app, but cannot exchange the code without the verifier (held on
      our server) and cannot replay state because the server is the
      only consumer.
    - **Real risk**: a malicious app can register the scheme **first**,
      so `ASWebAuthenticationSession` may not return the URL to *us*
      at all — Apple's docs say AS-WebAuth is supposed to be immune
      because it owns the in-app Safari, but Apple has had bugs here.
    - **Recommended hardening**: migrate to **Universal Links**
      (`https://your-server/oauth/callback`) with an
      apple-app-site-association file. Schemes were okay for MVP;
      Universal Links is the App-Store-grade choice. Effort ~4 h.

### 🟠 Important

12. **Server URL is unauthenticated input** (`SetupView.swift`). The
    user pastes any HTTPS URL — the app blindly trusts it as the
    server. A phishing QR code (or a typo onto an attacker-controlled
    host) would dump credentials and the eventual bearer token to the
    wrong server. PKCE protects the *Microsoft side* but not the
    *MySecurePrint side*.
    - Fix: require the server to present a signed configuration
      (JWT signed by a vendor key burned into the iOS bundle, or at
      least pin the cert/SPKI of `*.azurewebsites.net` if you bundle
      to Azure App Service only). At minimum, after entering the URL
      do a `GET /healthz` + verify a `Server: mysecureprint/<ver>`
      header or a known JSON shape, so the user gets a clear error
      before typing their MS password.

13. **`bearerToken` flows back to UserDefaults via `didSet` order**.
    `SettingsStore.swift:56-62` writes to Keychain and then
    `defaults.removeObject(...)`, but if Keychain `set` fails (return
    value is ignored), the in-memory `bearerToken` is still the new
    value while no on-disk copy exists. A subsequent app restart finds
    an empty Keychain and silently signs the user out.
    - Fix: check the return of `KeychainTokenStore.set` and if it
      returns false, surface an error to the user (or fall back to a
      transient session-only token rather than silently losing it).

14. **`prefersEphemeralWebBrowserSession = false`** (`LoginView.swift:
    203`). For a print app on a personal device this is reasonable
    (SSO, no re-MFA every login), but for **shared kiosk iPads** this
    leaks identity between users. The reset-from-default policy in
    SettingsStore does not extend to the web auth cookie jar.
    - Fix: ship a Settings toggle "Shared device — sign out fully"
      that flips this to `true` and clears the cookie jar on logout.

### 🟡 Nice-to-have

- **Localized error messages** mix `String(localized:)` with
  `error.localizedDescription`. The latter often returns
  Apple-English strings even on a German device — wrap the common
  network errors into your `L10n` table.
- **No app-level error log** of failed Entra exchanges. A user who
  hits "no_match" sees a single line of text and has no way to send
  the diagnostic info to support. Add a "Copy diagnostics" button.
- **`webAuthAnchor`** is recreated every `LoginView` init; harmless,
  but you can make it a stored property without `@State` since
  identity doesn't need to trigger view updates.
- **No biometric guard** on the bearer token. Anyone who unlocks the
  iPhone gets to print as the signed-in user. `LAContext.evaluate`
  before showing the upload tab is ~30 min of code.

---

## Cross-cutting (server ↔ iOS contract)

### State/PKCE end-to-end

```
iOS                            Server                       Microsoft
 │  start (deviceName,         │                              │
 │   redirect_uri)             │                              │
 ├────────────────────────────►│                              │
 │                             │ gen verifier+state+session_id│
 │                             │ store in pending (10 min)    │
 │                             │ build_authorize_url_pkce()   │
 │  {session_id, auth_url,     │                              │
 │   state, expires_in}        │                              │
 │◄────────────────────────────┤                              │
 │                                                            │
 │  ASWebAuth open(auth_url)                                  │
 ├───────────────────────────────────────────────────────────►│
 │                                                            │ user signs in
 │  redirect mysecureprint://oauth/callback?code=&state=      │
 │◄───────────────────────────────────────────────────────────┤
 │  exchange(session_id,       │                              │
 │   code, state)              │                              │
 ├────────────────────────────►│                              │
 │                             │ state match? (NOT constant-  │
 │                             │   time)                      │
 │                             │ POST /token w/ verifier      │
 │                             ├─────────────────────────────►│
 │                             │ access_token                 │
 │                             │◄─────────────────────────────┤
 │                             │ GET /me ─────────────────────►
 │                             │ profile (oid,email,name)◄────┤
 │                             │ get_or_create_entra_user()   │
 │                             │   (link by email if found)   │
 │                             │ create_token() — Bearer 32B  │
 │                             │ DELETE pending row           │
 │  {status=ok, token, user}   │                              │
 │◄────────────────────────────┤                              │
 │  Keychain.set(token)        │                              │
```

**Gaps**: (a) no `tid` check after `/me`; (b) pending row not deleted
on failure; (c) email-based linking trusts whatever MS returns.

### Token lifecycle

- **Issuance**: server-side `secrets.token_urlsafe(32)` per device.
- **Storage server**: plaintext in `desktop_tokens` (NOT
  Fernet-encrypted). The DB row is the credential.
- **Storage iOS**: Keychain, shared with Share Extension.
- **Rotation**: none. Tokens are valid forever until explicit
  `revoke_token`.
- **Revocation**: only via `/desktop/auth/logout` from the same device,
  or admin DB intervention. No "log out all sessions" UI surface
  visible to the user.
- **Audit**: `last_used_at` is bumped but there is no IP/UA log of
  token usage.

**Recommendation**: add `expires_at` to `desktop_tokens` (90 days
default), a `last_used_ip` column, and a `/account/sessions` UI for the
employee to see and revoke their devices. Effort ~3 h.

### Error-path behaviour

| Failure                                         | Server          | iOS              |
|-------------------------------------------------|-----------------|------------------|
| User cancels MS sign-in                         | pending row TTL | silent return ✅ |
| MS returns `error=consent_required`             | exchange fails  | shows raw text   |
| Network drops mid-exchange                      | pending row TTL | shows network err |
| state mismatch                                  | 400, row stays  | shows error      |
| Local user `status=suspended`                   | `no_match` JSON | shows error      |
| Entra disabled mid-flow (`entra_enabled=0`)     | exchange 400    | shows error      |
| Server URL wrong / unreachable                  | n/a             | network timeout  |
| `oid` empty (shouldn't happen)                  | exchange 502    | shows error      |

All paths either fail closed (no token issued) or recoverable. Good.
Two improvements:
- Cancel should explicitly call a `DELETE /desktop/auth/entra/authcode/
  cancel?session_id=…` so the pending row is reclaimed immediately,
  not after 10 min.
- The `no_match` response should distinguish "user not in DB" from
  "user suspended" — today both show the same text.

---

## Summary verdict

**GO-with-fixes.** The PKCE plumbing is correct, secrets are stored
sensibly, and the iOS Keychain migration landed. Three issues warrant
fixing before you invite external customers / open TestFlight more
widely:

1. **Verify `tid` claim against `entra_tenant_id`** to prevent
   cross-tenant sign-in (Critical #1). ~2 h.
2. **Tighten email-based account linking** to require a verified email
   or admin approval (Critical #2). ~3 h.
3. **One-shot the pending row** at the start of `exchange`
   (Critical #3). ~30 min.

Recommended within the same release:

4. Default `signInAudience` to `AzureADMyOrg` in `auto_register_app`
   (Important #6). ~30 min.
5. Pending-table GC sweep on startup (Important #4). ~30 min.
6. Universal Links migration (Critical #11). ~4 h.

**Effort estimate**: ~10 hours to land all critical + important items.
The architecture is sound; this is hygiene work, not a rewrite.
