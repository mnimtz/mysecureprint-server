# Entra Hardening Report — v0.1.3 (2026-06-29)

Implements 🟠 items #4, #6, #7, #8, #10 from `ENTRA_REVIEW.md`.

## 1. Files changed

| File | Change | Net lines |
|------|--------|-----------|
| `VERSION`                       | bump 0.1.2 → 0.1.3                | ±0 |
| `CHANGELOG.md`                  | new 0.1.3 section                 | +33 |
| `ENTRA_REVIEW.md`               | mark resolved items + header note | +8 |
| `src/db.py`                     | new columns + `cleanup_expired_pending()` | +44 |
| `src/desktop_auth.py`           | `revoke_all_tokens_for_user()`    | +14 |
| `src/entra.py`                  | audience default; capture secret-expiry; refresh_access_token; run_continuous_evaluation_sweep; rotate_client_secret; refresh_token in PKCE return | +220 |
| `src/web/app.py`                | Auto-Setup persists audience+expiry+object_id; welcome status warning; pending-GC + continuous-eval startup tasks | +90 |
| `src/web/desktop_routes.py`     | persist refresh_token on PKCE exchange when continuous-eval enabled | +18 |

## 2. Item-by-item

### Item 1 — Pending-Tables GC sweep
- New helper `db.cleanup_expired_pending()` deletes `expires_at < now` from both `desktop_entra_pending` and `desktop_entra_authcode_pending`. Idempotent — checks `sqlite_master` before deleting; returns row count.
- Background task in `app.py` (`_start_entra_pending_gc`) runs every 300s via `asyncio.create_task`; fail-soft try/except.

### Item 2 — Single-Tenant default
- `entra.auto_register_app()` now reads `entra_app_audience` setting (defaults `AzureADMyOrg`) and validates against the four legal MS values. Old value `AzureADMultipleOrgs` removed from default path.
- Audience captured into settings on auto-setup, so the admin can see what was chosen.

### Item 3 — Continuous evaluation
- `users.entra_refresh_token` (Fernet) + `users.entra_last_refresh_at` columns added via idempotent `ALTER TABLE`-when-missing migration.
- `entra.refresh_access_token(rt)` calls MS `/oauth2/v2.0/token` with `grant_type=refresh_token`.
- `entra.run_continuous_evaluation_sweep()` walks users with stored refresh_token, refreshes each, revokes server bearer tokens when MS returns `invalid_grant`/`interaction_required`/…
- `desktop_auth.revoke_all_tokens_for_user(uid)` mass-deletes.
- `_start_entra_continuous_eval` startup task: 24h interval, gated on `entra_continuous_eval_enabled=1` (default off).
- PKCE exchange stores the refresh_token only when the feature flag is on.

### Item 4 — Secret-Expiry tracking
- Removed `endDateTime: "2099-12-31T23:59:59Z"` hardcode in `auto_register_app`. MS now picks its own (24-month cap), and we capture the actual `endDateTime` from the response into setting `entra_secret_expires_at` (and `entra_app_object_id` for later rotation).
- `_get_entra_status()` returns `"warning"` instead of `"configured"` when expiry < 60 days — Welcome page can render yellow.
- `entra.rotate_client_secret(obj_id, access_token)` provided as the building block for the admin "Rotate now" action.

### Item 5 — Token revocation on Entra disable
- Covered by Item 3: the continuous-eval sweep deletes all desktop tokens of a user whose MS refresh fails, within 24h.

## 3. Validation

```
$ python3 -c "import ast; ast.parse(open('src/entra.py').read()); \
  ast.parse(open('src/db.py').read()); \
  ast.parse(open('src/web/app.py').read()); \
  ast.parse(open('src/web/desktop_routes.py').read()); \
  ast.parse(open('src/desktop_auth.py').read()); print('OK')"
OK
```

```
$ grep -rn "AzureADMultipleOrgs" src/
src/entra.py:564:    # "AzureADMultipleOrgs" — das hat in Kombination mit fehlender
src/entra.py:567:    # moeglich via `audience="AzureADMultipleOrgs"` oder dem
src/entra.py:576:    if audience not in ("AzureADMyOrg", "AzureADMultipleOrgs",
src/web/templates/admin_settings.html:558:  --sign-in-audience AzureADMultipleOrgs `
src/web/templates/admin_settings.html:578:  --sign-in-audience AzureADMultipleOrgs \
```
Remaining hits are: (a) inline comments explaining the change, (b) the allowlist that permits opt-in multi-tenant, (c) admin-settings-template CLI hint strings (manual-setup instructions, untouched). No hardcoded default path uses multi-tenant any longer.

## 4. Migration safety

- `users.entra_refresh_token`, `users.entra_last_refresh_at`, `tenants.entra_secret_expires_at`: added via the existing `PRAGMA table_info`-guarded `ALTER TABLE` pattern in `init_db()`. Default `''` so existing rows remain valid.
- `cleanup_expired_pending` does a `sqlite_master` lookup before deleting, so a fresh DB without the pending tables (no Entra flows yet run) is a no-op rather than an error.
- The continuous-eval task is opt-in (`entra_continuous_eval_enabled` defaults `0`), so existing deployments see zero behavioural change unless the operator turns it on.

## 5. Commit + tag + push

Pending — performed by the next step (see git output).

## 6. ENTRA_REVIEW.md status

Items #4, #6, #7, #8, #10 marked **[✅ RESOLVED v0.1.3]** with cross-link
to this report. Items #5 (iOS multi-account), #9 (username collision),
and all 🟡 nice-to-haves remain open.
