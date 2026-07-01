# MySecurePrint — Privacy Policy (Template)

_Last updated: 2026-06-30._

> Public copy hosted at the **server URL the user configures** in the
> app, e.g. `https://<your-printix-mcp-server>/privacy`. The reference
> deployment serves it at <https://printix-sp.azurewebsites.net/privacy>.

MySecurePrint ("the app") is an independent, open-source companion
client for the self-hosted `printix-mcp` server. The app is published
by Marcus Nimtz (Germany). The app is **not affiliated with** Tungsten
Automation Corp., HP, Konica Minolta, Brother, Lexmark, PaperCut, or
any other print-management or printer vendor.

## Data we collect

The app does not have its own backend. All data the app sends leaves
your device only to the **server URL you configure yourself** on
first launch (typically your own LAN host running `printix-mcp`).

Specifically, the following items are stored or transmitted:

| Item | Where | Why |
|---|---|---|
| Email address | iOS Keychain + your server | Identifies the signed-in user. |
| Full name | App-Group `UserDefaults` + your server | Display name in the UI. |
| Device name | App-Group `UserDefaults` + your server | Lets you find this device in the server's session list. |
| Bearer / OAuth token | iOS Keychain (access-group shared with the share extension) | Authenticates API calls to your server. |
| NFC card UID (optional) | Sent to your server when you tap "register card" | Enrols your access card for print release. |

The app contains **no third-party analytics, no advertising SDKs, no
crash reporters, no telemetry**.

## Data we do NOT collect

- We do not collect location data.
- We do not track you across apps or websites.
- We do not sell or share any data with third parties.

## Required-reason API declarations

Per Apple's privacy-manifest rules, the app declares use of:

- `NSPrivacyAccessedAPICategoryUserDefaults` — reason `CA92.1`
  (storing user settings in an app-group container shared with the
  share extension).
- `NSPrivacyAccessedAPICategoryFileTimestamp` — reason `C617.1`
  (timestamping temporary upload files in the app's sandbox).

## Your rights

You can delete all locally stored data by deleting the app from
your device. Data already sent to your `printix-mcp` server is
under your sole control — consult that server's documentation for
deletion procedures.

## Contact

Bug reports / privacy enquiries: please file an issue at
`github.com/marcus-nimtz/printix-mcp/issues` or contact
`marcus@nimtz.email`.
