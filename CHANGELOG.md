# Changelog — MySecurePrint Server

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
