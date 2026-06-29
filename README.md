# MySecurePrint Server

**Self-hosted print backend for the [MySecurePrint](https://github.com/mnimtz/Printix-MCP) iOS app.**

Deploys to your own Azure App Service in 5 minutes. One Printix tenant per
deployment, N end-users via Microsoft Entra ID or local accounts. Document
conversion (Word / JPG / PDF → PCL XL via LibreOffice + Ghostscript) included.

> ⚠ Not affiliated with or endorsed by Tungsten Automation Corp. (the maker
> of Printix). This is a third-party companion server. "Printix" is used here
> only as a compatibility reference.

---

## 🚀 Deploy to Azure

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Fmnimtz%2Fmysecureprint-server%2Fmain%2Fdeploy%2Fazure%2Fazuredeploy.json)

Click → Azure portal opens → fill in 3 fields (resource group, app name,
region) → Deploy. After ~5 minutes you have a running server at
`https://<your-name>.azurewebsites.net`. Open it, register the first admin
account, enter your Printix API credentials, and you're ready to pair the
iOS app.

---

## What this is / isn't

| | mysecureprint-server (this repo) | [printix-mcp-docker](https://github.com/mnimtz/printix-mcp-docker) |
|---|---|---|
| **Audience** | iOS app users | AI assistants + Web admins + iOS users |
| **MCP server (Claude / ChatGPT / Make.com)** | ❌ | ✅ ~132 tools |
| **Reports + Scheduler + Report-Mail** | ❌ | ✅ |
| **Capture webhook + Guest-Print mailboxes** | ❌ | ✅ |
| **IPP/IPPS cloud-print listener** | ❌ | ✅ optional |
| **Dashboard + Tenant browser** | ❌ | ✅ |
| **iOS app endpoints (Entra PKCE, Cards, Management)** | ✅ | ✅ |
| **Document conversion (Word/JPG/PDF → PCL XL)** | ✅ | ✅ |
| **End-user management (register, invite, Entra SSO)** | ✅ | ✅ |
| **HTTPS setup (Tunnel / Auto-TLS / cert import)** | ✅ | ✅ |
| **Audit log, backup, /privacy, /imprint** | ✅ | ✅ |
| **Target hosting** | Azure App Service (1-click) | Anywhere Docker runs |
| **Container size** | ~600 MB (LibreOffice + Ghostscript) | ~800 MB |

Use **this repo** if you only need the iOS-app backend with print conversion.
Use **printix-mcp-docker** if you also want AI-assistant integration or the
operational features above.

---

## After deploy — first-run setup

1. Open `https://<your-name>.azurewebsites.net` in your browser
2. **Register the first admin account** — local username / password
3. Wizard steps you through:
   - Printix API credentials (Print + Card + UM scopes from your Printix subscription)
   - Microsoft Entra Auto-Setup (optional — uses device-code flow to create the App Registration in your Entra tenant)
4. Go to `/admin/settings#legal` — fill in operator name, address, email
   so `/privacy` and `/imprint` are App-Store-Review-ready
5. Add end users via `/admin/users` (or let them register via Entra SSO if enabled)
6. On the iOS app: Setup → enter the server URL → "Sign in with Microsoft" or local account

---

## Architecture

```
[iOS App MySecurePrint]
        ↓ HTTPS, Bearer Token
[Azure App Service]
   ├─ /desktop/auth/entra/*     Entra Authorization-Code + PKCE
   ├─ /desktop/cards/*          NFC card enrolment + lookup
   ├─ /desktop/management/*     Printers, users, sites
   ├─ /my/upload                File upload → conversion → Printix queue
   ├─ /my/connect               Personal bearer token + setup info
   ├─ /privacy + /imprint       Public legal pages (App-Review)
   └─ /admin/*                  Operator setup + user management
        ↓
[Azure Files mount /data]
   ├─ printix_multi.db          SQLite, Fernet-field-encrypted secrets
   ├─ fernet.key                DB-field encryption key
   ├─ tls/, letsencrypt/        Optional custom-domain cert state
   └─ backups/                  Operator backups (optional AES-encrypted)
        ↓
[Printix Cloud API]
[Microsoft Graph (Entra)]
```

---

## Hosting cost (Azure)

| Tier | Per month | Suitable for |
|---|---|---|
| **F1 (free)** | 0 € | Apple-Review demo only; sleeps after 20 min idle, 1 GB RAM (tight for LibreOffice on large Word docs) |
| **B1 (basic)** | ~10 € | Recommended default. Always-on, 1.75 GB RAM, plenty for print conversion |
| **B2 / S1** | ~20-50 € | Higher RAM if you regularly convert large documents (>20 MB Office files) |

Plus Azure Files storage: **<1 € / month** for typical use (DB + cert state + a few backups).

---

## Updates

Pull the new container image (the GitHub Action publishes a new
`ghcr.io/mnimtz/mysecureprint-server:latest` on every release):

```bash
az webapp restart --resource-group <rg> --name <appname>
```

Or in the Azure Portal: App Service → Restart.

---

## Documents in `docs/`

- [`azure-deploy-guide.md`](docs/azure-deploy-guide.md) — Manual `az` CLI commands as alternative to the deploy-to-azure button
- [`ios-app-pairing.md`](docs/ios-app-pairing.md) — End-user instructions for pairing the MySecurePrint iOS app
- [`document-conversion.md`](docs/document-conversion.md) — What gets converted, debugging conversion failures
- [`apple-review-checklist.md`](docs/apple-review-checklist.md) — Steps from "App-ID registered" to "live in App Store"

---

## Local development

```bash
docker compose up -d
# Web UI at http://localhost:8080
```

A `data/` directory is mounted as a volume. To wipe state, stop the container and `rm -rf data/`.

---

## License

Same license as upstream `printix-mcp-docker`. See [`LICENSE`](LICENSE).
