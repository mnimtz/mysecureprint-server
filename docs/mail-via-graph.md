# System mail via Microsoft Graph (instead of Resend)

**Since:** v0.7.0
**Status:** stable for mobile-invite / welcome / GDPR-export / reports.
**Default provider stays Resend** — operators who don't touch anything
keep the old behaviour.

## When to use which

| Advantage of Graph | Advantage of Resend |
|---|---|
| No external subscription, no DNS setup | Works without an O365 licence |
| Sender = your own company domain, no extra work | Simpler spam behaviour |
| Audit trail in Exchange Online (Sent Items) | Bounce tracking out-of-the-box |
| Tenant admins already know the permission model | Independent of Entra auto-setup |
| No additional third party in the data flow | |

Recommendation: if you're doing Entra auto-setup anyway, register
both permissions (`Mail.Send` and — as a prep step — `Mail.Read`) in
the same click. Admin consent is the long step; you don't want to
click through it a second time.

## Setup steps

1. **In the admin UI:** `/admin/settings?section=entra` → auto-setup
   card. When registering the Entra app, tick the checkbox
   *"Send mail via this O365 account (Mail.Send)"*. Optionally also
   *"Prepare Email-to-Print gateway (Mail.Read)"* — the feature itself
   ships in v0.8.0 but the permission is registered up front.
2. **Grant admin consent** (Azure portal → *Entra ID* →
   *App registrations* → your new app → *API permissions* → **Grant
   admin consent for &lt;tenant&gt;**). Without this click Graph returns 403.
3. **Pick a service mailbox.** Best practice: a dedicated mailbox
   `noreply@company.com` (not a real user's mailbox — this way you can
   restrict access cleanly with a policy).
4. **Set an Application Access Policy in Exchange Online** (IMPORTANT):

   ```powershell
   Connect-ExchangeOnline
   New-ApplicationAccessPolicy `
       -AppId <client_id> `
       -PolicyScopeGroupId noreply@company.com `
       -AccessRight RestrictAccess `
       -Description "MySecurePrint Mail.Send only on noreply mailbox"
   ```

   Without this policy the app identity can send **from every** mailbox
   in the tenant — that's an intentional Microsoft default weakness.
   With the policy, only from the one service mailbox.

5. **Switch the provider:** `/admin/settings?section=general` → card
   "Global mail fallback" → provider dropdown to
   *"Microsoft Graph"* + fill in the service mailbox + save.

## Fallback behaviour

If provider = Graph and sending fails (token gone, mailbox down,
policy conflict, …), the server automatically tries Resend — if
there's an API key + sender configured for it. The log then shows a
`WARNING` line: `mail: Graph send failed (...) — falling back to
Resend.`

If you deliberately don't want Resend as fallback, leave the Resend
API key blank; then sending fails cleanly instead of routing to
Resend.

## Troubleshooting

| HTTP status | Cause | Fix |
|---|---|---|
| 401 | Token rejected — secret expired? | Rotate the Entra client secret in the admin UI. |
| 403 | Admin consent missing OR Application Access Policy denies this mailbox. | Grant consent in the Azure portal, or check the policy with `Get-ApplicationAccessPolicy`. |
| 404 | Sender mailbox does not exist in the tenant. | Verify the UPN/address — must be a real Exchange Online mailbox, not just a mail-enabled user. |

## Email-to-Print (preparation)

The `Mail.Read` application permission is registered starting v0.7.0.
The feature itself ships in **v0.8.0**:

- Polls a dedicated service mailbox.
- Attachments from an authenticated tenant user get printed on their
  behalf to their default print queue.
- Subject prefixes control target queue / options.

The admin toggle `email_to_print_enabled` is off in v0.7.0. Operators
who already registered the permission can enable it in v0.8.0 without
a second consent round-trip.
