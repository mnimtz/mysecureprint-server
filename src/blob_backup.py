"""Azure Blob auto-backup helpers.

Wraps backup_manager.create_backup() with an upload to an Azure Blob Storage
container so the encrypted backup archive lives outside the App Service's
mounted Azure Files share. If the Storage Account that holds /data is ever
deleted, manually-mirrored backups in a separate container (or another
account entirely) survive.

Configuration lives in the `settings` table:

  blob_backup_enabled            "0" / "1"
  blob_backup_connection_string  Fernet-encrypted Azure Storage connection string.
                                 If empty, falls back to the AZURE_STORAGE_CONNECTION_STRING env var
                                 (which the ARM template pre-populates from the
                                 same storage account that hosts /data).
  blob_backup_container          default "mysecureprint-backups"
  blob_backup_passphrase         Fernet-encrypted passphrase used to encrypt
                                 the backup archive itself (defense in depth —
                                 backups stored in blob storage are double-
                                 encrypted: Fernet by us + Azure-side-at-rest).
  blob_backup_retention_days     default "30" — older blobs get pruned by run_once().
  blob_backup_last_run_at        ISO-8601 timestamp of the last attempt.
  blob_backup_last_result        JSON: {"ok": bool, "blob_name": str, "size": int,
                                        "error": str}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("printix.blob_backup")


DEFAULT_CONTAINER = "mysecureprint-backups"
DEFAULT_RETENTION_DAYS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connection_string() -> str:
    """Return the configured Azure Storage connection string, or '' if none."""
    try:
        from db import get_setting
        from crypto import decrypt
    except Exception:
        return ""
    raw = get_setting("blob_backup_connection_string", "")
    if raw:
        try:
            return decrypt(raw)
        except Exception as e:
            logger.warning("blob_backup_connection_string decrypt failed: %s", e)
            return ""
    # Fallback: env var (ARM template pre-populates this).
    return os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")


def _passphrase() -> str:
    """Return the configured backup passphrase, or '' if none."""
    try:
        from db import get_setting
        from crypto import decrypt
    except Exception:
        return ""
    raw = get_setting("blob_backup_passphrase", "")
    if not raw:
        return ""
    try:
        return decrypt(raw)
    except Exception as e:
        logger.warning("blob_backup_passphrase decrypt failed: %s", e)
        return ""


def _container_name() -> str:
    try:
        from db import get_setting
        v = get_setting("blob_backup_container", "").strip()
        return v or DEFAULT_CONTAINER
    except Exception:
        return DEFAULT_CONTAINER


def _retention_days() -> int:
    try:
        from db import get_setting
        return int(get_setting("blob_backup_retention_days",
                               str(DEFAULT_RETENTION_DAYS)) or DEFAULT_RETENTION_DAYS)
    except Exception:
        return DEFAULT_RETENTION_DAYS


def is_configured() -> bool:
    """True when both a connection string AND a passphrase are available."""
    return bool(_connection_string()) and bool(_passphrase())


def is_enabled() -> bool:
    """True when the user has toggled on the daily auto-backup."""
    try:
        from db import get_setting
        return get_setting("blob_backup_enabled", "0") == "1"
    except Exception:
        return False


def _get_container_client(container: Optional[str] = None):
    """Return a BlobServiceClient.ContainerClient, creating the container if missing."""
    from azure.storage.blob import BlobServiceClient

    conn = _connection_string()
    if not conn:
        raise RuntimeError("No Azure Storage connection string configured "
                           "(blob_backup_connection_string setting or "
                           "AZURE_STORAGE_CONNECTION_STRING env var).")

    name = container or _container_name()
    svc = BlobServiceClient.from_connection_string(conn)
    cc = svc.get_container_client(name)
    if not cc.exists():
        cc.create_container()
    return cc


def upload_existing(local_path: Path, blob_name: Optional[str] = None) -> dict:
    """Upload an already-created backup ZIP to Azure Blob.

    Returns {"ok": True, "blob_name": str, "size": int} on success, raises on
    failure. `local_path` is left untouched on disk.
    """
    if not isinstance(local_path, Path):
        local_path = Path(local_path)
    if not local_path.is_file():
        raise FileNotFoundError(f"backup file not found: {local_path}")

    cc = _get_container_client()
    bn = blob_name or local_path.name
    with local_path.open("rb") as fh:
        cc.upload_blob(name=bn, data=fh, overwrite=True)

    size = local_path.stat().st_size
    logger.info("blob backup uploaded: %s (%d bytes)", bn, size)
    return {"ok": True, "blob_name": bn, "size": size}


def list_blobs() -> list[dict]:
    """List all backup blobs in the configured container, newest first."""
    try:
        cc = _get_container_client()
    except Exception as e:
        logger.warning("list_blobs failed: %s", e)
        return []
    items = []
    for b in cc.list_blobs():
        items.append({
            "name":     b.name,
            "size":     b.size,
            "modified": b.last_modified.isoformat() if b.last_modified else "",
        })
    items.sort(key=lambda x: x["modified"], reverse=True)
    return items


def download_blob(blob_name: str, local_path: Path) -> dict:
    """Download a specific blob to the given local path."""
    if not isinstance(local_path, Path):
        local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    cc = _get_container_client()
    bc = cc.get_blob_client(blob_name)
    with local_path.open("wb") as fh:
        fh.write(bc.download_blob().readall())
    return {"ok": True, "blob_name": blob_name, "local_path": str(local_path)}


def cleanup_old(retention_days: Optional[int] = None) -> int:
    """Delete blobs older than `retention_days`. Returns count of deletions."""
    days = retention_days if retention_days is not None else _retention_days()
    if days <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cc = _get_container_client()
    removed = 0
    for b in cc.list_blobs():
        last_mod = b.last_modified
        if last_mod is None:
            continue
        if last_mod < cutoff:
            try:
                cc.delete_blob(b.name)
                removed += 1
            except Exception as e:
                logger.warning("cleanup_old failed to delete %s: %s", b.name, e)
    if removed:
        logger.info("blob backup cleanup: pruned %d old blob(s) (> %dd)", removed, days)
    return removed


def run_once() -> dict:
    """Create a fresh local backup and push it to Azure Blob.

    Used by both the daily scheduler and the manual "Run now" admin button.
    Records the result in settings so the admin UI can display the last status.
    Returns the same dict that gets persisted (`blob_backup_last_result`).
    """
    from db import set_setting

    result: dict = {"ok": False, "blob_name": "", "size": 0, "error": ""}

    try:
        if not _connection_string():
            raise RuntimeError("missing connection string")
        pp = _passphrase()
        if not pp:
            raise RuntimeError("missing backup passphrase "
                               "(blob_backup_passphrase setting)")

        from backup_manager import create_backup
        local = create_backup(passphrase=pp)
        local_path = Path(local["path"])

        up = upload_existing(local_path)
        result["ok"] = True
        result["blob_name"] = up["blob_name"]
        result["size"] = up["size"]

        try:
            pruned = cleanup_old()
            if pruned:
                result["pruned"] = pruned
        except Exception as e:
            logger.warning("cleanup_old failed: %s", e)

    except Exception as e:
        logger.error("blob backup run_once failed: %s", e)
        result["error"] = str(e)

    try:
        set_setting("blob_backup_last_run_at", _now_iso())
        set_setting("blob_backup_last_result", json.dumps(result))
    except Exception as e:
        logger.debug("failed to persist last_run state: %s", e)

    return result
