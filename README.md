# MySecurePrint Server

**Self-hosted print backend for the [MySecurePrint iOS app](https://apps.apple.com/de/app/mysecureprint/id6785880823) and the [macOS Send client](https://github.com/mnimtz/mysecureprint-server/releases).**

Deploys to your own Azure App Service in ~5 minutes. One Printix tenant per deployment, N end-users via Microsoft Entra ID and/or local accounts. Document conversion (Word / JPG / PDF → PCL XL via LibreOffice + Ghostscript) included. iOS app connects via OAuth PKCE + Bearer, no proprietary auth.

> ⚠ Not affiliated with or endorsed by Tungsten Automation Corp. (the maker of Printix). This is an independent third-party companion server. "Printix" is used only to describe API compatibility.

---

## 🚀 Deploy to Azure

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Fmnimtz%2Fmysecureprint-server%2Fmain%2Fdeploy%2Fazure%2Fazuredeploy.json)

Click → Azure portal opens → fill in 3 fields (resource group, app name, region) → Deploy. After ~5 minutes you have a running server at `https://<your-name>.azurewebsites.net`. Open it, register the first admin account, enter your Printix API credentials, and pair the iOS app.

Alternative: `az webapp` CLI walkthrough in [`docs/azure-deploy-guide.md`](docs/azure-deploy-guide.md).

---

## ✨ Features

### End-user (iOS / macOS clients)

- **Sign in with Microsoft** (Entra ID) via native OAuth Authorization-Code + PKCE — no in-app browser, no client secrets on device.
- **Local username/password** fallback — works when Entra has issues.
- **iOS Share Sheet integration** — share any PDF/image from any app to MySecurePrint → uploads in the background, survives app switching.
- **NFC card enrolment** — read Mifare / ISO-14443 / ISO-15693 UIDs and link them to the user's Printix identity.
- **Multi-target print** — send one job to multiple SecurePrint queues at once.
- **Print delegation** — send jobs to another user's SecurePrint queue on their behalf (admin-controlled toggle, off by default for GDPR).
- **Print job history** — live status per target (queued, ready to pick up, failed, deleted).
- **iOS AirPrint profiles** (v0.8.0) — install a native printer profile once and print from **any iOS/iPadOS/macOS app** directly to your SecurePrint queue. No Bonjour, no VPN — works over cellular, guest Wi-Fi, everywhere. Config profiles are personalised (URL-token per user × queue), so every job arrives at Printix with the real user as owner.
- **Home Screen widget + Live Activities** (v0.7.152+) — see queued jobs and card status in the Lock Screen / Dynamic Island without opening the app.

### AI document analysis (v0.7.171+)

- **Automatic tag extraction, summary, sensitivity** — when a print job arrives, an AI model (Gemini, OpenAI GPT-4o-mini or a local Ollama) reads the document and adds tags ("invoice", "contract"), a one-sentence summary, and a suggested sensitivity level ("public" / "internal" / "confidential").
- **Language follows the user's locale** — tags/summary are generated in the same language the iOS app is set to.
- **Provider choice** — pick Gemini (cheapest per page for large PDFs), OpenAI (best for Word/Excel/PowerPoint conversion pipeline), or a self-hosted Ollama model.
- **Prompt customisation** — configure your own extraction prompts per queue if the defaults don't match your document types.
- **Custom prompts** — admin can define named variables ("invoice_number", "customer_id") that get extracted and shown in the iOS job list.
- **Runs in background** — the print job itself never waits for AI; analysis populates the iOS job details asynchronously.

### Admin (web UI)

- **User management** — invite via email, bulk CSV-import, individual create, edit, suspend, delete.
- **Merge duplicate accounts** — one-click UI to merge a local account with the Entra auto-created one (identity linking with tenant-`tid` verification).
- **Entra auto-setup** — device-code flow creates the App Registration in your tenant, sets redirect URIs, generates client secret, grants tenant-wide admin consent for openid/profile/email/User.Read (and optionally Mail.Send / Mail.Read).
- **Printix credentials wizard** — separate slots for Print, Card, and User-Management API pairs.
- **Groups + default queues** — assign default SecurePrint queues per Entra group.
- **Email templates** — customise the mobile-invite email per language, with `{qr_code}` and `{app_store_url}` placeholders.
- **Audit log** — full JSON-detailed history of admin + user actions.
- **API-Trace dashboard** — inspect outbound Printix/Graph HTTP calls per admin toggle (off by default).
- **8 UI languages** — de / en / fr / es / it / nl / nb (Bokmål) / sv, plus 7 fun-mode dialects.

### Print pipeline

- **Format conversion** — Office (`.docx`, `.xlsx`, `.pptx`), text, images (`png`, `jpg`, `heic`), and PDFs are all normalised into a printable stream via LibreOffice + Ghostscript before being handed to the Printix Cloud Print API.
- **Multi-stage submit** — LPR-compatible ingestion, 5-stage Printix submission with retry on 5xx.
- **Automatic OOM/kill retry** — background worker survives Cloudflare's 100 s cap.

### Guest-Print / Email-to-Print gateway (opt-in, v0.7.28+)

- **Watch an O365 mailbox** — Graph Mail.Read polls a service inbox at configurable interval.
- **Guest whitelist** — external email addresses can print via mail on behalf of themselves; each entry has a printer, queue, and validity window (TTL in days).
- **Internal-user recognition** — if the sender is a known server user, their print job goes to their own SecurePrint queue automatically, no whitelist needed.
- **Attachment guardrails** — MIME whitelist (PDF / PNG / JPEG), configurable size cap, safe-filename sanitisation.
- **Idempotency + multi-worker-safe** — atomic `try_acquire_poll_lock` prevents double-print if you run multiple uvicorn workers.
- **Job log with audit trail** — every accepted or rejected email visible in `/admin/guestprint`.

### Mail delivery

- **Two providers** — Resend (default, works without O365) or **Microsoft Graph** (uses the Entra app already registered in your tenant + a service mailbox). Failover configurable.
- **Auto-consent** — v0.7.32 auto-grants tenant Mail.Send admin consent in the same device-code setup, no manual Azure Portal click needed for the common case.

### Legal / GDPR

- **/privacy, /imprint, /legal** — rendered per your `legal_operator_*` settings, TMG-compliant German variant included.
- **GDPR data-export tool** — one click gives an admin (or the user themselves) a JSON dump of everything the DB knows about them.
- **RBAC / MCP-permissions** — role model (Global-Admin / Tenant-Admin / User / Guest-User) with an admin UI for group-level overrides.

### Security posture

Hardened over v0.7.29–0.7.40 based on adversarial audits:

- `hmac.compare_digest` on Bearer-token + OAuth secret comparisons — no timing side-channel.
- Session-fixation defence: `request.session.clear()` before setting `user_id` on login.
- Session cookie `https_only=True`, SameSite=Lax (override for dev via `SESSION_COOKIE_INSECURE=1`).
- Rate limiting on `/desktop/auth/login` — 8 tries per 5 min, dual IP + username bucket, `429` with `Retry-After`.
- Open-redirect defence on Referer-based redirect targets (Same-Host allowlist).
- User-enumeration defence — bcrypt cost even on user-not-found path.
- Multi-worker-safe scheduling (BG tasks retained in `_BG_TASKS`, DB advisory locks on the Guest-Print poller).

---

## Feature scope

| Area | Status |
|---|---|
| **iOS + macOS app backend** — Entra PKCE, NFC card enrolment, multi-target print, delegation, share-sheet upload | ✅ |
| **iOS/iPadOS/macOS AirPrint profiles** — native printer profile per user × queue, install once and print from every app | ✅ (v0.8.0+) |
| **AI document analysis** — Gemini / OpenAI / Ollama tags, summary, sensitivity per print job, prompt-configurable per queue | ✅ (v0.7.171+) |
| **Live job-status polling** — adaptive interval (20 s for fresh jobs → 30 min for waiting Anywhere), server-side Printix cross-check, Web-UI-delete detection | ✅ (v0.7.190+) |
| **Print delegation** — send jobs to other users' SecurePrint queues (admin-controlled toggle, off by default for GDPR) | ✅ |
| **Guest-Print / Email-to-Print gateway** — watch an O365 mailbox, print attachments on behalf of whitelisted external senders + auto-recognized internal users | ✅ (v0.7.28+) |
| **Document conversion** — Word / Excel / PowerPoint / images (HEIC/PNG/JPG) / plain text / PDF → PCL XL via LibreOffice + Ghostscript | ✅ |
| **Microsoft Graph mail** — send system mails from your own O365 tenant, or fall back to Resend HTTP API | ✅ (v0.7.0+) |
| **Entra ID auto-setup** — device-code wizard creates the App Registration, generates client secret, grants tenant-wide admin consent (openid/profile/email/User.Read + optional Mail.Send/Mail.Read) | ✅ |
| **Entra ↔ local account linking** — same email in both auth systems auto-links on next login (with tid verification against `entra_tenant_id`) | ✅ (v0.7.32+) |
| **User management** — invite via email, bulk CSV-import, individual create, edit, suspend, delete, **merge duplicates** | ✅ |
| **End-user self-service portal** — my jobs, my delegates, my cloud-print settings | ✅ |
| **8 UI languages** — de / en / fr / es / it / nl / nb / sv + 7 fun-mode dialects | ✅ (v0.7.31+) |
| **MCP tool exposure** — Claude / ChatGPT / Make.com integration via a tenant-scoped MCP endpoint | ⚠ partial — MCP OAuth + Bearer scaffolding is in (`/admin/mcp-access`, `/admin/mcp-permissions`) but the tool catalog is smaller than in the operational Printix-MCP fork |
| **Rate limiting on desktop-login** — 8 tries per 5 min, dual IP + username bucket | ✅ (v0.7.30+) |
| **HTTPS setup** — Cloudflare Tunnel wizard, Let's-Encrypt Auto-TLS on custom domain, manual cert import | ✅ |
| **GDPR data export + Privacy/Imprint pages** — full-JSON per-user dump, TMG-compliant legal pages generated from operator settings | ✅ |
| **Audit log + API-Trace dashboard** — every admin action + outbound Printix/Graph call visible with per-admin toggle | ✅ |
| **Backups** — encrypted daily blob-backup to Azure Storage, manual on-demand export | ✅ |
| **Reports + Scheduler + Report-Mail** | ❌ — not shipped in this build |
| **Capture webhook (paper→OCR pipeline)** | ❌ — not shipped in this build |
| **IPP/IPPS cloud-print listener** | ❌ — not shipped in this build |
| **Target hosting** | Azure App Service (1-click) or any Docker host |
| **Container size** | ~600 MB (LibreOffice + Ghostscript included) |

Optimised for teams who want a self-hosted, Azure-native companion for the MySecurePrint iOS/macOS apps — with the option to expose Printix data to AI assistants (Claude, ChatGPT) via MCP.

---

## After deploy — first-run setup

1. Open `https://<your-name>.azurewebsites.net` in your browser.
2. **Register the first admin account** — local username / password (bootstrap; the first user is auto-approved as Global-Admin).
3. Wizard steps you through:
   - **Printix API credentials** — Print + Card + User-Management scopes from your Printix subscription.
   - **Microsoft Entra Auto-Setup** (optional) — device-code flow creates the App Registration in your Entra tenant, sets redirect URIs, generates the client secret, and grants admin consent for `openid` / `profile` / `email` / `User.Read`. If you toggle Mail.Send during setup, that permission is granted too.
4. Go to `/admin/settings#legal` — fill in operator name, address, email so `/privacy` and `/imprint` are App-Store-review-ready.
5. Add end users via `/admin/users` (or let them register via Entra SSO if `entra_auto_approve` is on).
6. On the iOS app: Setup → enter the server URL → "Sign in with Microsoft" or local account.
7. (Optional) `/admin/guestprint` → configure a watched O365 mailbox to enable email-to-print.

---

## Architecture

```
[iOS App MySecurePrint]  [macOS Send]  [Web Admin UI]
        ↓ HTTPS, Bearer Token / Session
[Azure App Service — uvicorn / FastAPI]
   ├─ /login, /account, /admin/*        Web admin console
   ├─ /desktop/auth/entra/*             Entra OAuth PKCE for native apps
   ├─ /desktop/auth/login               Password login (rate-limited)
   ├─ /desktop/cards/*                  NFC card enrolment + lookup
   ├─ /desktop/management/*             Printers, users, sites
   ├─ /desktop/send                     File upload → conversion → Printix
   ├─ /my/*                             Employee self-service portal
   ├─ /privacy + /imprint + /legal      Public legal pages
   └─ /admin/guestprint                 Email-to-Print gateway config
        ↓ background asyncio tasks
   ├─ Guest-Print poller                Graph Mail.Read every N seconds
   ├─ Blob-backup scheduler             Daily encrypted upload to Azure Blob
   ├─ Entra continuous-eval             Detect revoked users
   └─ Printix user-sync                 Periodic Printix ↔ local user reconcile
        ↓
[Azure Files mount /data]
   ├─ printix_multi.db                  SQLite, Fernet-field-encrypted secrets
   ├─ fernet.key                        DB-field encryption key
   ├─ tls/, letsencrypt/                Optional custom-domain cert state
   └─ backups/                          Encrypted operator backups
        ↓
[Printix Cloud API]
[Microsoft Graph (Entra tenant)]
[Resend HTTP API]                       (optional, only if Graph mail is off)
```

---

## Hosting cost (Azure)

| Tier | Per month | Suitable for |
|---|---|---|
| **F1 (free)** | 0 € | Apple-Review demo only; sleeps after 20 min idle, 1 GB RAM (tight for LibreOffice on large Word docs) |
| **B1 (basic)** | ~10 € | Recommended default. Always-on, 1.75 GB RAM, plenty for print conversion |
| **B2 / S1** | ~20-50 € | Higher RAM if you regularly convert large documents (>20 MB Office files) or run the Guest-Print poller under heavy load |

Plus Azure Files storage: **<1 € / month** for typical use (DB + cert state + a few backups).

---

## Updates

`main` branch pushes trigger a container build to `ghcr.io/mnimtz/mysecureprint-server:main`. Tagged releases (`v0.7.x`) additionally publish `:0.7.x`, `:latest`, `:stable`.

Azure pulls the image on restart:

```bash
az webapp restart --resource-group <rg> --name <appname>
```

Or in the Azure Portal: App Service → Restart. Configure Continuous Deployment in Deployment Center to auto-pull on new `:latest`.

See [CHANGELOG.md](CHANGELOG.md) for the full history.

---

## Repo layout (mono-repo)

```
mysecureprint-server/
├── src/            Server (Python, FastAPI, uvicorn)
├── docs/           Operator docs (deploy, Apple review, GDPR, ...)
├── deploy/azure/   1-click Deploy-to-Azure template
├── clients/
│   ├── ios/        MySecurePrint iOS app (Xcode project, MySecurePrint.xcodeproj)
│   └── macos/      MySecurePrint macOS Send helper + PrintixSendCore SwiftPM
└── ...
```

Server + client apps live in the same repo so a server-API change and the
corresponding iOS/macOS client update can ship as one commit / one PR.

## Documents in `docs/`

- [`azure-deploy-guide.md`](docs/azure-deploy-guide.md) — Manual `az` CLI commands as alternative to the deploy-to-azure button.
- [`ios-app-pairing.md`](docs/ios-app-pairing.md) — End-user instructions for pairing the MySecurePrint iOS app.
- [`airprint-setup-guide.md`](docs/airprint-setup-guide.md) — 🆕 iOS/iPadOS/macOS AirPrint profiles: admin setup + user onboarding + troubleshooting.
- [`AIRPRINT_PROFILE_DESIGN.md`](docs/AIRPRINT_PROFILE_DESIGN.md) — 🆕 Design document (architecture, threat model, phased plan) for the AirPrint feature.
- [`document-conversion.md`](docs/document-conversion.md) — What gets converted, debugging conversion failures.
- [`mail-via-graph.md`](docs/mail-via-graph.md) — Microsoft Graph mail configuration + ApplicationAccessPolicy scoping.
- [`apple-review-checklist.md`](docs/apple-review-checklist.md) — Steps from "App-ID registered" to "live in App Store".
- [`GDPR_COMPLIANCE_GUIDE.md`](docs/GDPR_COMPLIANCE_GUIDE.md) — Article-by-article coverage of the compliance posture.
- [`PERMISSION_MATRIX.md`](docs/PERMISSION_MATRIX.md) — Role → tool mapping for the MCP RBAC model.

---

## Local development

```bash
docker compose up -d
# Web UI at http://localhost:8080
```

A `data/` directory is mounted as a volume. To wipe state, stop the container and `rm -rf data/`.

For local Python (no container):

```bash
uv sync
uv run uvicorn --app-dir src web.app:app --reload --port 8080
```

---

## Contributing

Bug reports + PRs welcome. Please keep changes focused (one feature or fix per PR) and include a CHANGELOG entry.

**Security disclosures**: please email marcus@nimtz.email instead of opening a public issue.

---

## License

Same license as upstream `printix-mcp-docker`. See [`LICENSE`](LICENSE).
