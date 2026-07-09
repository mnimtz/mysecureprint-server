# Archived design & implementation reports

These files are historical documents from the initial fork of
`mysecureprint-server` out of `printix-mcp-docker` in June 2026. They
describe one-off migration steps and design decisions that were made
at a specific point in time.

**The reports are kept for auditability** — code reviewers, future
maintainers, or people investigating "why is this like it is?" can
still trace back the reasoning. They are *not* meant to be read as
current documentation.

For up-to-date operational documentation, see the main [`../`](../)
folder and the top-level [`../../README.md`](../../README.md).

## Contents

| File | Summary | Applies to |
|---|---|---|
| [`BUILD_REPORT.md`](BUILD_REPORT.md) | Initial slim cut from `printix-mcp-docker` v7.9.4: what was copied, deleted, and how the LOC dropped. | v0.1.0 (2026-06-29) |
| [`WELCOME_PAGE_REPORT.md`](WELCOME_PAGE_REPORT.md) | Landing/welcome page redesign — routes, templates, DB migrations. | v0.1.1 |
| [`IOS_ONBOARDING_DESIGN.md`](IOS_ONBOARDING_DESIGN.md) | Original design doc for the iOS app pairing flow (QR code, deep-link scheme, server exchange). | v0.2.0 |
| [`IOS_ONBOARDING_V0_2_REPORT.md`](IOS_ONBOARDING_V0_2_REPORT.md) | Implementation report for the v0.2.0 iOS onboarding server-side build. | v0.2.0 |
| [`ENTRA_REVIEW.md`](ENTRA_REVIEW.md) | Adversarial security review of the Entra ID authentication flow, with numbered findings. | v0.1.2 baseline |
| [`ENTRA_FIXES_REPORT.md`](ENTRA_FIXES_REPORT.md) | Fixes applied for three critical findings from `ENTRA_REVIEW.md`. | v0.1.2 |
| [`ENTRA_HARDENING_REPORT.md`](ENTRA_HARDENING_REPORT.md) | Additional Entra-ID hardening pass (session-fixation defence, cookie flags, rate-limiting). | v0.1.3 |

The actual behaviour these documents describe is now aggregated in the
main [`../../CHANGELOG.md`](../../CHANGELOG.md) under the
`0.1.x`–`0.7.x` entries. If a report contradicts current code, current
code wins — the reports are frozen at their commit date.
