# Entra ID Security Hygiene Fixes — Implementation Report

**Version**: v0.1.2
**Date**: 2026-06-29
**Scope**: Three critical items from `ENTRA_REVIEW.md`.

## Files changed

| File | Lines changed | Purpose |
|------|---------------|---------|
| `src/entra.py` | +73 / −10 | tid verification, refuse common-fallback |
| `src/db.py` | +30 / −19 | oid-only matching, bootstrap exception |
| `src/web/desktop_routes.py` | +28 / −7 | delete pending row at start, constant-time state compare |
| `VERSION` | +1 / −1 | 0.1.1 → 0.1.2 |
| `CHANGELOG.md` | +33 | new release section |
| `ENTRA_REVIEW.md` | +10 | mark items resolved |

## Fix #1 — Verify `tid` claim

**Before** (`src/entra.py`):

```python
def is_enabled() -> bool:
    cfg = get_config()
    return cfg["enabled"] and bool(cfg["client_id"]) and bool(cfg["client_secret"])

def build_authorize_url(redirect_uri, state):
    cfg = get_config()
    tenant = cfg["tenant_id"] or "common"   # ← any-tenant fallback
    ...

def exchange_code_for_user(...):
    # returned profile included tid but no validation
    return {"oid": ..., "email": ..., "tid": payload.get("tid", "")}
```

**After**:

```python
def is_enabled() -> bool:
    cfg = get_config()
    return (
        cfg["enabled"]
        and bool(cfg["client_id"])
        and bool(cfg["client_secret"])
        and bool((cfg.get("tenant_id") or "").strip())   # ← required
    )

def _require_tenant() -> str | None:
    tid = (get_config().get("tenant_id") or "").strip()
    if not tid:
        logger.error("Entra: kein entra_tenant_id konfiguriert — Flow abgebrochen.")
        return None
    return tid

def _verify_tid(token_tid: str) -> bool:
    expected = (get_config().get("tenant_id") or "").strip().lower()
    got = (token_tid or "").strip().lower()
    if not expected or not got or got != expected:
        logger.warning("Entra rejected signin: tid mismatch (got=%s expected=%s)", got, expected)
        return False
    return True

def build_authorize_url(redirect_uri, state) -> str | None:
    tenant = _require_tenant()
    if not tenant:
        return None
    ...

def exchange_code_for_user(...):
    ...
    token_tid = payload.get("tid", "")
    if not _verify_tid(token_tid):
        return None
    return {"oid": ..., "email": ..., "tid": token_tid}

def exchange_code_pkce(...):
    ...
    # decode id_token, check tid BEFORE accepting the access_token for Graph
    id_token = data.get("id_token", "")
    token_tid = (_decode_jwt_payload(id_token) or {}).get("tid", "")
    if not _verify_tid(token_tid):
        return None
    ...
```

`build_authorize_url_pkce` also returns `None` when tenant is unset
(same `_require_tenant()` guard).

## Fix #2 — `entra_oid`-only matching

**Before** (`src/db.py`):

```python
def get_or_create_entra_user(entra_oid, email, display_name):
    # 1. oid match
    # 2. EMAIL MATCH — links any local account whose email matches
    if email:
        row = conn.execute("SELECT * FROM users WHERE email COLLATE NOCASE = ?",
                           (email.strip(),)).fetchone()
        if row:
            conn.execute("UPDATE users SET entra_oid = ? WHERE id = ?",
                         (entra_oid, row["id"]))
            return _user_public(...)
    # 3. create new
```

**After**:

```python
def get_or_create_entra_user(entra_oid, email, display_name):
    # 1. oid match (only legitimate match path)
    ...
    # 2. NO email fallback — admin must link explicitly
    is_bootstrap = not has_users()
    if not is_bootstrap:
        if get_setting("entra_auto_approve", "0") != "1":
            logger.warning(
                "Entra get_or_create_entra_user: kein oid-match, "
                "Auto-Approve aus -> kein Account angelegt (oid=%s email='%s')",
                entra_oid[:10], email)
            return None
    # 3. create new — first-ever user (bootstrap) becomes admin
    if is_bootstrap:
        status, is_admin_flag, role_type_v, parent_uid = "approved", 1, "admin", ""
    else:
        ...
```

## Fix #3 — Single-use `state` (delete pending row at start)

**Before** (`src/web/desktop_routes.py`):

```python
@app.post("/desktop/auth/entra/authcode/exchange")
async def desktop_entra_authcode_exchange(...):
    with _conn() as conn:
        row = conn.execute("SELECT ... WHERE session_id = ?", ...).fetchone()
    if not row: return _json_error("unknown session", ...)
    if state != row["state"]: return _json_error("state mismatch", ...)

    # ... do Microsoft token exchange ...
    # ... call get_or_create_entra_user ...

    # only on success:
    token = create_token(...)
    with _conn() as conn:
        conn.execute("DELETE FROM desktop_entra_authcode_pending WHERE session_id = ?", ...)
```

**After**:

```python
@app.post("/desktop/auth/entra/authcode/exchange")
async def desktop_entra_authcode_exchange(...):
    with _conn() as conn:
        row = conn.execute("SELECT ... WHERE session_id = ?", ...).fetchone()
        if row:
            # SOFORT loeschen — single-use state, replay-safe
            conn.execute("DELETE FROM desktop_entra_authcode_pending WHERE session_id = ?", ...)
        # Bonus: opportunistisches GC abgelaufener Eintraege
        try:
            conn.execute("DELETE FROM desktop_entra_authcode_pending WHERE expires_at < ?",
                         (datetime.now(timezone.utc).isoformat(),))
        except Exception:
            pass
    if not row: return _json_error("unknown session", ...)

    import secrets as _secrets
    if not _secrets.compare_digest(state or "", row["state"] or ""):
        return _json_error("state mismatch", ...)

    # ... Microsoft exchange ... — failures no longer leave state usable
    token = create_token(...)
    # (no second DELETE — already gone)
```

## Validation results

```
$ python3 -c "import ast; ast.parse(open('src/entra.py').read()); ..."
AST OK

$ grep -n '"common"\|or "common"' src/entra.py src/web/app.py src/web/desktop_routes.py
src/entra.py:99:    Im Gegensatz zum frueheren `tenant_id or "common"` Fallback gibt
src/entra.py:432:def start_device_code_flow(tenant: str = "common", ...
src/entra.py:476:def poll_device_code_token(device_code: str, tenant: str = "common"):
src/entra.py:642:def start_device_code_flow_guestprint(tenant: str = "common"):
```

The remaining `"common"` literals are all in the **Device Code Flow
helpers used by the auto-setup wizard** (Graph CLI client_id for new
app-registration). They are NOT used for user authentication and
operate before any tenant is configured. That's the documented
correct usage of `common` for first-party Microsoft clients.

```
$ grep -n "email" src/db.py | grep -i "entra\|oid"
1345:                "Auto-Approve aus -> kein Account angelegt (oid=%s email='%s')",
1346:                entra_oid[:10], email,
```

Only a log line remains — no email-based matching.

## Boundaries respected

- `src/web/app.py`, `src/web/i18n.py`, `src/web/templates/welcome.html`,
  `src/web/templates/_brand_logo.svg` were **not modified** — Welcome
  agent's v0.1.1 commit `00b192c` came in cleanly via `git fetch`.
- All tid-validation logic lives **inside `entra.py`** so existing
  callers in `app.py` automatically get the protection without
  needing changes (they already correctly handle `None` return).

## Git operations

```
$ git fetch origin
$ git status     # local app.py changes were already at origin/main (Welcome agent's v0.1.1)
$ git add -A
$ git commit -m "v0.1.2: Entra ID security hygiene fixes ..."
$ git tag v0.1.2
$ git push origin main
$ git push origin v0.1.2
```
