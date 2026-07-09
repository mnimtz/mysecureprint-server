# iOS AirPrint — Admin & User Guide

Configure once, print from every iOS, iPadOS and macOS app to your
SecurePrint queue. No Bonjour, no VPN. This document walks through
setup and covers common problems.

---

## What it is

Users install a small **configuration profile** on their Apple device.
The profile registers a printer named "MySecurePrint" that talks to
your server over HTTPS. From then on, the printer appears in every
app's Print dialog (Safari, Mail, Photos, Files, Pages, Preview, …).

Every profile is **personalised** — one user × one queue. Jobs arrive
at Printix with the real user as the job owner, so pull-print at the
printer works exactly like a normal Printix job.

**Works on:** iOS 12+, iPadOS 13+, macOS Sequoia (15+).

---

## Prerequisites

Before you enable the feature:

1. **Printix API credentials** configured under *Configuration →
   Printix API* (the Print API scope at minimum).
2. **Public HTTPS URL** — the server must be reachable from user
   devices over TLS. Azure App Service gives you this automatically.
3. **At least one Anywhere or SecurePrint queue** on your Printix
   tenant. The dropdown reads the list live from the Printix API.

---

## Admin setup (5 min)

### 1. Enable the feature

*Sidebar → Configuration → iOS Mobile*

- Check ☑ **AirPrint profiles activate**.
- Pick the **default queue for new users** — typically your company's
  main SecurePrint Anywhere queue. Anywhere queues are marked with 🌐
  in the dropdown and sorted to the top.
- Optionally check ☑ **When inviting: attach mobileconfig
  automatically** — new users invited via `/admin/users/invite`
  will get the profile in their welcome email so they can print
  before installing the app.
- If your customer runs strict corporate mail filters that reject
  `.mobileconfig` attachments, check ☑ **Send as ZIP** — the file is
  packaged in a ZIP archive with a `README.txt`. Users have to unzip
  first, but the mail gets through.
- Fill in **Organization shown in profile** — this appears in the
  profile's metadata on the device ("Installed by: Acme Corp").
- Click **Save settings**.

### 2. Optional: upload a signing certificate

Without a certificate, iOS shows a *"Not Verified"* warning when the
user installs the profile. The install works — the warning is just
scarier than it needs to be. If you have an **Apple Developer
Enterprise Certificate** or a self-signed certificate, upload it:

- **Certificate**: public part in PEM format (`.pem`, `.crt`, `.cer`).
- **Private key**: matching key in PEM format (`.pem`, `.key`). Must
  not be password-encrypted (decrypt first with `openssl rsa -in
  encrypted.key -out plain.key` if needed).

After upload, iOS shows *"Verified"* with a green checkmark instead
of the warning.

### 3. Test yourself first

*Sidebar → Configuration → iOS Mobile — Users*

1. Search for your own account.
2. Click **Manage**.
3. Pick your default queue from the dropdown.
4. Click **Create profile**.
5. Download the `.mobileconfig` (or the ZIP variant) and email it to
   yourself, or open it directly on the Mac you're browsing from.

Your Mac / iPhone / iPad shows an install dialog → Install → the
printer appears in every Print dialog.

**Try printing something from Safari** and check the job appears at
Printix with your user as owner.

---

## User onboarding (auto)

If **"When inviting: attach mobileconfig automatically"** is on and
the user has permission on the default queue, every invitation email
sent via `/admin/users/invite` includes the profile as an attachment,
plus a short "how to install" block.

The user just taps the attachment on their iPhone → follows iOS'
install prompts → done. **They don't need the app installed** to
start printing — the app becomes useful for advanced things like NFC
card enrolment, job history, delegation.

---

## User onboarding (manual, from the app)

Users can also create additional profiles themselves (e.g. for a
direct printer they need occasionally):

1. Open the **MySecurePrint app**.
2. Non-admin users see an **AirPrint tab** in the main tab bar.
   Admins find the same view under **More → iOS Printers**.
3. Tap **Add new printer**.
4. Pick a queue from the dropdown (only queues the user has permission
   on are listed).
5. Optionally add a display name for their own reference ("iPhone
   Max").
6. Tap **Create** → iOS' install sheet appears → Install → done.

---

## Troubleshooting

### The printer doesn't show up in the Print dialog

- Check the profile really installed: **iOS Settings → General → VPN
  & Device Management → Profiles**. There should be an entry
  "MySecurePrint — [Queue name]".
- Restart the source app (Safari, Mail, etc.). Some apps cache
  printer lists until relaunch.
- On macOS: check **System Settings → Printers & Scanners**. If it's
  not listed there either, the profile is broken. Re-download and
  reinstall.

### iOS says "The certificate for this server is invalid"

The Apple device does not trust your server's TLS certificate. Two
common causes:

- **Self-signed or private CA on your server** — Apple devices don't
  trust these by default. Use Let's Encrypt / Azure Managed
  Certificate, or install your CA root on the device (via MDM or
  another profile).
- **Certificate expired** — check `curl -Iv https://your-server/`.

### Print jobs never arrive at Printix

- Open the admin **API-Trace** page and look for `POST
  /jobs/*/submit` calls after the user prints. If there are none,
  the request never reached the Printix API — see next point.
- Check the audit log for `airprint_forward_failed` events. The
  Printix HTTP status code + message is included.
- Verify the user's Printix account has permission on the queue you
  set as default. When permission is revoked in Printix, our server
  returns `429` and no job is submitted.

### The user revoked a profile but it still appears on the device

Revoking a profile server-side stops new print jobs, but the device
keeps showing the printer icon until the user removes it manually:

**iOS Settings → General → VPN & Device Management → tap the profile
→ Remove Profile → confirm with passcode.**

macOS: **System Settings → Profiles → select the profile → remove
button (–).**

This is an Apple limitation — only MDM-enrolled devices can be
server-force-removed. A future v0.9.x release will add a "removal
instructions" sheet in the app after revoke.

### "No Printix queues available" warning under Configuration

The Printix API is unreachable or the credentials are wrong. Fix:

1. Go to *Configuration → Printix API* and check the Print API
   credentials.
2. Reload the iOS Mobile section — the dropdown should now be
   populated.

If credentials look correct but the queue list is still empty, check
the *API-Trace* dashboard for `list_printers` calls; the Printix
error message tells you what's rejected.

---

## Admin bulk management

*Sidebar → Configuration → iOS Mobile — Users*

- Statistics card at the top (active profiles / users / total print
  jobs since feature launch).
- Search box for finding a specific user by email or username.
- Each row: number of active profiles + last-used timestamp + a
  Manage button.

Per-user detail view:

- List of existing profiles with download button (both `.mobileconfig`
  and `.zip` variants) and revoke button.
- Form to create a new profile in that user's name.
- Success card after creation with both download options right there.

Revoked profiles are shown greyed-out in the list with the reason
(`admin_manual` / `user_deleted_via_app` / `onboarding_email`).

---

## API reference (for MDM / scripting)

For the app-facing endpoints see also `docs/PERMISSION_MATRIX.md`.

### `POST /desktop/me/airprint/create`

Auth: user Bearer token.
Body: `{"queue_id": "...", "printer_id": "...", "queue_display_name":
"...", "display_name": "..."}`.
Returns: `{"profile_id": "...", "download_url": "..."}`.

### `GET /desktop/me/airprint/{profile_id}/download`

Returns the `.mobileconfig` file as
`application/x-apple-aspen-config`. Auth: same user as the profile.

### `POST /airprint/{profile_token}`

The IPP endpoint iOS talks to. Content-Type
`application/ipp`. Handles Print-Job (0x0002) and
Get-Printer-Attributes (0x000B).

### `POST /admin/airprint-users/{user_id}/create`

Admin-only. Same body shape as the user endpoint. Used by the admin
bulk UI and can be scripted for MDM-style rollouts.

---

## Design & implementation

See [`AIRPRINT_PROFILE_DESIGN.md`](AIRPRINT_PROFILE_DESIGN.md) for the
architecture decisions, threat model, and phased plan (Stage 1 in
v0.8.0, Stage 2 self-service portal in v0.9.0, MDM variable
substitution in v0.9.x+).
