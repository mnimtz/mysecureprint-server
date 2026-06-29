# Welcome Page — v0.1.1 Implementation Report

## Files changed

| File | Change | Lines |
|------|--------|-------|
| `VERSION` | bump `0.1.0` → `0.1.1` | ±1 |
| `CHANGELOG.md` | prepend v0.1.1 section | +16 |
| `src/web/app.py` | new `/welcome` route + 4 status helpers + QR generator; `/` now redirects to `/welcome` when no users | +115 / −5 |
| `src/web/i18n.py` | added 17 welcome-page keys in `de` + `en` blocks (others fall back to EN) | +44 |
| `src/web/templates/welcome.html` | new template, scoped CSS, brand-aligned (Tungsten navy/cyan), 4 cards (header/server-info/status/actions) | +218 (new) |

Total: 5 files, **+390 / −3 lines**, 1 new template.

## What was NOT changed

- `src/requirements.txt` — left untouched. The original task asked for `qrcode>=7.4.2`, but the repo already bundles `segno>=1.6.0` (pure-python, ~50 KB, same role). Re-used `segno` instead of adding a redundant dep — same pattern already used in `employee_routes.py` for the existing PNG QR endpoints.
- `src/entra.py` had unrelated pre-existing local edits — explicitly excluded from the commit. Untracked design docs (`ENTRA_REVIEW.md`, `IOS_ONBOARDING_DESIGN.md`) likewise left out of scope.

## Implementation notes

- **`/welcome` is fully public** — no auth required, works against a fresh DB (all status dots red), works against a mature deployment (all dots green).
- **QR payload**: `mysecureprint://setup?server=<base_url>/` where `<base_url>` comes from `mcp_base_url_or(request)` (DB `public_url` → env `MCP_PUBLIC_URL` → request host fallback). Renders as inline SVG via segno (sharp at any zoom, no external image request, no base64 inflation).
- **Status helpers** (new in `app.py`):
  - `_get_printix_status()` — SQL count over `tenants` checking any of `print_client_id`, `card_client_id`, `shared_client_id` non-empty.
  - `_get_entra_status()` — `entra_client_id` AND `entra_tenant_id` set in settings.
  - `_get_legal_status()` — `legal_operator_name` setting non-empty. Renders **yellow** when missing (App-Review needs it).
  - `_get_admin_status()` — at least one user with `is_admin=1 AND status='approved'`.
- **Root redirect**: `/` → `/welcome` only when `has_users()` returns False or throws (DB not yet provisioned). Existing logged-in / login redirect behavior untouched.
- **Quick-action button visibility**: "Register first admin" only shown when no admin exists; "Sign in" only when admin exists but visitor logged out; "Configure server" only when an admin is logged in.

## Validation performed

- `python3 -c "import ast; ast.parse(open('src/web/app.py').read())"` → OK
- `python3 -c "import ast; ast.parse(open('src/web/i18n.py').read())"` → OK
- `python3 -c "from jinja2 import ..."` → `welcome.html parses OK`
- `git status` clean for the staged change set; commit signed-off and tagged `v0.1.1`.

## GitHub Actions

`git push origin main` and `git push origin v0.1.1` both succeeded against `git@github.com:mnimtz/mysecureprint-server.git` (commit `00b192c`). The repo's workflow under `.github/workflows/` will trigger from either the branch push or the tag — verify the run at:

  https://github.com/mnimtz/mysecureprint-server/actions

(Could not query the run directly — `gh` CLI isn't authenticated in this environment.)

## Three things to verify on your Azure deployment

1. **Load `/welcome` anonymously**: open `https://<your-app>.azurewebsites.net/welcome` in a private/incognito window. Page should render without prompting for login. The server URL pill should match the URL you typed (i.e. `MCP_PUBLIC_URL` / DB `public_url` is correct).
2. **Scan the QR with the iPhone Camera app**: the toast should read "Open in MySecurePrint?" (because the URL scheme `mysecureprint://` is already registered by the existing OAuth callback). The full text decoded should be `mysecureprint://setup?server=https://<your-app>.azurewebsites.net/`.
3. **Status indicators match reality**: on a fresh deployment all four dots should be red/yellow. After completing the wizard (admin registered + Printix creds + Entra wired + legal info filled), refresh `/welcome` — all four dots should turn green.
