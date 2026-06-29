# Changelog — MySecurePrint Server

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
