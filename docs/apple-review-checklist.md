# Apple App Store Review checklist

For operators who want to publish the **MySecurePrint** iOS app against
their own deployment of this server. The iOS app source lives at
[Printix-MCP](https://github.com/mnimtz/Printix-MCP) under
`MobileApp/ios-client/`.

## 1. Apple Developer Portal

- [ ] Active Apple Developer Program membership (99 EUR/year, or
      299 EUR for Enterprise — but enterprise is internal-only and won't
      reach the App Store)
- [ ] D-U-N-S number registered (only for Org-Account, 1-5 business days)
- [ ] **App ID** `de.nimtz.mysecureprint` — Explicit, with capabilities:
      NFC Tag Reading · App Groups · Keychain Sharing
- [ ] **App ID** `de.nimtz.mysecureprint.share` — App Extension
- [ ] **App Group** `group.de.nimtz.mysecureprint` registered + enabled on
      both App IDs
- [ ] Apple Distribution certificate
- [ ] App Store Provisioning Profile for both bundle IDs

## 2. Server-side (this repo)

- [ ] Deployed to a publicly-reachable HTTPS URL (Azure App Service
      provides this out-of-the-box)
- [ ] First admin account registered
- [ ] Printix credentials configured under `/admin/settings`
- [ ] `/admin/settings#legal` filled out completely (name, postal address,
      email, country) — otherwise `/privacy` and `/imprint` show a
      "not configured" warning that Apple Reviewer will see
- [ ] One demo end-user created (`reviewer@mysecureprint.test` or similar)
      with a strong password and assigned to a tenant with sample data
- [ ] HTTP-200 on `/privacy`, `/imprint`, `/health` confirmed via curl
      from outside your network

## 3. App Store Connect

- [ ] New app listing created with Bundle ID `de.nimtz.mysecureprint`
- [ ] **App Name**: MySecurePrint
- [ ] **Subtitle** (30 chars): "Secure print via printix-mcp" (or similar)
- [ ] **Keywords** (100 chars): "printix,printix-mcp,secure print,mobile print,nfc,self-hosted,airprint"
- [ ] **Description**: lead with "not affiliated with Tungsten Automation Corp."
- [ ] **Support URL**: GitHub issues page or own domain
- [ ] **Privacy Policy URL**: `https://<your-server>/privacy`
- [ ] **App Privacy questionnaire**: filled out honestly — data collected
      (email, name) is *Linked to user* but NOT used for *tracking*

## 4. Build + signing

- [ ] Open the iOS project in Xcode 16
- [ ] Automatic signing → select the team with the App IDs registered
      above
- [ ] Bump build number if necessary
- [ ] Product → Archive → Distribute App → App Store Connect → Upload
- [ ] Wait 5-15 min for App Store Connect to finish processing the build

## 5. Submit for review

- [ ] In App Store Connect → version 1.0.0 → select the uploaded build
- [ ] **App Review Information** filled out:
  - Sign-in required: **Yes**
  - Demo User: the reviewer-account credentials from step 2
  - Server URL: your Azure App Service URL
  - Contact info: your email
  - Notes:
    > MySecurePrint is a free, open-source companion app for the
    > self-hosted printix-mcp-style Docker server. It is NOT affiliated
    > with Tungsten Automation Corp. (the maker of Printix).
    >
    > To test:
    > 1. Open app → Setup → enter the server URL above
    > 2. Tap "Sign in with Microsoft" → use the provided demo
    >    credentials (Microsoft Entra test tenant)
    > 3. Cards tab shows enrolled NFC card UIDs
    > 4. Management tab shows the configured Printix tenant info
    > 5. Share Extension: from Files app → Share → MySecurePrint
    >
    > The custom URL scheme `mysecureprint://` is for the OAuth PKCE
    > redirect after Microsoft Entra sign-in. It serves no other
    > purpose.
- [ ] **Export Compliance**: encryption used? Yes, **but exempt**
      (HTTPS + iOS Keychain only)
- [ ] **Age Rating**: utility → 4+
- [ ] **Pricing**: Free
- [ ] Submit for Review

## 6. After approval

- [ ] Test the live App Store version by downloading from the store
- [ ] Verify TestFlight build promotion path if you want a beta-channel
- [ ] Monitor crash reports in Xcode Organizer

## Typical Apple-Review pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| 4.1 Copycat reject | App name too similar to Printix-branded | Already mitigated — MySecurePrint is distinct |
| 5.2.5 mentions other platforms | "Printix" in app name | Mitigated — only in subtitle/keywords/description as compatibility reference |
| 2.1 not enough info | Reviewer can't access server | Demo credentials + server URL must be in Review Information |
| 5.1.1 missing privacy policy | URL returns 4xx/5xx | `/privacy` must be reachable from anywhere, no login |
