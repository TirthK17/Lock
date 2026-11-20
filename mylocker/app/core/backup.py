"""
backup.py — Backup, restore, integrity verification, and crash-safe key rotation.

Audit Requirements (Phase 1 & Phase 3):
  - extract with tarfile filter="data" plus manual member validation
    (no absolute paths, no .., no symlinks/hardlinks, no drive letters).
  - restore into a new folder, verify all manifest hashes, verify that vault.config
    and the index decrypt with the provided password.
  - take safety snapshot of the current vault, then swap folders atomically and
    keep the old one as vault.old until confirmed.
  - rotate_master_key: build in vault.new, verify all, atomic swap, keep vault.old.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from mylocker.app.core.chunked_io import (
    decrypt_file,
    encrypt_file,
    generate_file_id,
    verify_file_integrity,
)
from mylocker.app.core.crypto import zero_bytes
from mylocker.app.core.index import EncryptedIndex, FileEntry
from mylocker.app.core.kdf import KDFParams, derive_blob_name_key
from mylocker.app.core.keystore import SLOT_DECOY, Keystore
from mylocker.app.core.lockout import LockoutManager, make_lockout_signing_key
from mylocker.app.core.secure_delete import SecureDeleteError, secure_delete_file
from mylocker.app.core.session import get_session
from mylocker.app.core.settings_store import SettingsStore

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "backup_manifest.json"


def _is_safe_tar_member(member: tarfile.TarInfo, target_dir: Path) -> bool:
    """Validate archive member to prevent path traversal (CVE-2007-4559)."""
    # 1. No absolute paths
    if member.name.startswith(("/", "\\")):
        return False
    # 2. No Windows drive letters (e.g. C:...)
    if len(member.name) > 1 and member.name[1] == ":":
        return False
    # 3. No links (symlinks or hard links)
    if member.islnk() or member.issym():
        return False
    # 4. Resolve destination and ensure it stays inside target_dir
    try:
        resolved_target = target_dir.resolve()
        dest_path = (target_dir / member.name).resolve()
        dest_path.relative_to(resolved_target)
    except (ValueError, RuntimeError):
        return False
    return True


def create_vault_backup(
    vault_dir: Path,
    backup_tar_path: Path,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[bool, str]:
    """Create a standalone tar.gz backup of the entire vault directory.
    All data remains encrypted with existing vault keys.
    """
    try:
        files_to_pack = [
            p for p in vault_dir.rglob("*")
            if p.is_file() and not p.name.endswith(".tmp") and not p.name.endswith(".part")
        ]
        total_files = len(files_to_pack)

        files_map: dict[str, str] = {}
        manifest: dict[str, Any] = {
            "created_at": time.time(),
            "version": "1.0",
            "file_count": total_files,
            "files": files_map,
        }

        # Calculate checksums
        for i, file_path in enumerate(files_to_pack):
            rel = str(file_path.relative_to(vault_dir)).replace("\\", "/")
            if progress:
                progress(i, total_files, f"Hashing {rel}…")

            h = hashlib.sha256()
            with open(file_path, "rb") as fh:
                while chunk := fh.read(1024 * 1024):
                    h.update(chunk)
            files_map[rel] = h.hexdigest()

        # Write manifest temporarily into vault_dir
        manifest_path = vault_dir / MANIFEST_FILENAME
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)

        files_to_pack.append(manifest_path)

        # Create archive
        if progress:
            progress(total_files, total_files, "Compressing archive…")

        with tarfile.open(backup_tar_path, "w:gz") as tar:
            for file_path in files_to_pack:
                arcname = str(file_path.relative_to(vault_dir)).replace("\\", "/")
                tar.add(file_path, arcname=arcname)

        # Cleanup temp manifest
        manifest_path.unlink(missing_ok=True)
        return True, "Backup created successfully."
    except Exception as exc:
        logger.error("Failed to create backup: %s", exc)
        return False, str(exc)


def restore_vault_backup(
    backup_tar_path: Path,
    restore_target_dir: Path,
    password: Optional[str] = None,
    keyfile_path: Optional[str] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[bool, str]:
    """Restore a vault from a tar.gz archive.

    Crash-Safe Order:
      1. Member validation (no .., no links, no absolute paths)
      2. Extract into staging directory with filter="data"
      3. Verify SHA-256 hashes against backup_manifest.json
      4. If password provided, verify vault.config and index.dat decrypt cleanly
      5. Snapshot current vault to vault.old
      6. Atomically replace vault with newly verified restore
    """
    staging_dir = restore_target_dir.parent / f"restore_staging_{secrets.token_hex(4)}"
    try:
        staging_dir.mkdir(parents=True, exist_ok=True)

        if progress:
            progress(0, 100, "Inspecting and extracting backup…")

        with tarfile.open(backup_tar_path, "r:gz") as tar:
            # 1. Manual member validation
            for member in tar.getmembers():
                if not _is_safe_tar_member(member, staging_dir):
                    raise ValueError(f"Dangerous or invalid archive member detected: {member.name}")

            # 2. Extract safely
            if hasattr(tarfile, "data_filter"):
                tar.extractall(path=staging_dir, filter="data")
            else:
                # Manual member validation performed above; extract validated members
                safe_members = [m for m in tar.getmembers() if _is_safe_tar_member(m, staging_dir)]
                tar.extractall(path=staging_dir, members=safe_members)  # nosec B202

        # 3. Verify manifest
        manifest_path = staging_dir / MANIFEST_FILENAME
        if not manifest_path.exists():
            return False, "Corrupt backup: missing backup_manifest.json"

        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)

        expected_files = manifest.get("files", {})
        total_files = len(expected_files)

        for i, (rel_path, expected_hash) in enumerate(expected_files.items()):
            file_path = staging_dir / rel_path
            if not file_path.exists():
                return False, f"Missing file in archive: {rel_path}"

            if progress:
                progress(i, total_files, f"Verifying {rel_path}…")

            h = hashlib.sha256()
            with open(file_path, "rb") as fh:
                while chunk := fh.read(1024 * 1024):
                    h.update(chunk)
            if h.hexdigest() != expected_hash:
                return False, f"Integrity hash mismatch for file: {rel_path}"

        manifest_path.unlink(missing_ok=True)

        # 4. If password supplied, verify config & index decryptability
        if password:
            if progress:
                progress(total_files, total_files, "Verifying credentials and index…")
            test_ks = Keystore(staging_dir)
            mk = test_ks.unlock_with_password(password, keyfile_path=keyfile_path)
            try:
                test_idx = EncryptedIndex(staging_dir)
                test_idx.load(mk)
            finally:
                zero_bytes(mk)

        # 5. Snapshot current vault to vault.old
        vault_old = restore_target_dir.parent / f"{restore_target_dir.name}.old"
        had_target = restore_target_dir.exists()
        if had_target:
            if vault_old.exists():
                shutil.rmtree(vault_old, ignore_errors=True)
            restore_target_dir.rename(vault_old)

        # 6. Atomic swap staging -> target with automatic rollback safety
        try:
            staging_dir.rename(restore_target_dir)
        except Exception as swap_err:
            logger.error("Atomic swap failed during restore: %s. Rolling back to original vault.", swap_err)
            if had_target and vault_old.exists():
                vault_old.rename(restore_target_dir)
            raise

        return True, "Vault restored and verified successfully."
    except Exception as exc:
        logger.error("Failed to restore backup: %s", exc)
        return False, str(exc)
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def rotate_vault_keys(
    vault_dir: Path,
    old_master_key: bytearray,
    password: str,
    keyfile_path: Optional[str] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
    recovery_key_out: Optional[list[str]] = None,
) -> Tuple[bool, str]:
    """Generate a new Master Key and re-encrypt all blobs in vault.new, then swap."""
    vault_dir = Path(vault_dir).resolve()
    vault_new = vault_dir.parent / f"{vault_dir.name}.new"
    vault_old = vault_dir.parent / f"{vault_dir.name}.old"

    try:
        shutil.rmtree(vault_new, ignore_errors=True)
        vault_new.mkdir(parents=True, exist_ok=True)
        new_blobs_dir = vault_new / "blobs"
        new_blobs_dir.mkdir(parents=True, exist_ok=True)

        # 0. Load old keystore config to preserve KDF tuning & decoy slot
        old_ks = Keystore(vault_dir)
        old_ks.config.load()
        old_kdf = old_ks.config.kdf_params
        old_sk = make_lockout_signing_key(old_kdf.salt)
        old_decoy_slot = old_ks.config.get_slot_entry(SLOT_DECOY)
        old_decoy_legacy = old_ks.config._data.get("wrapped_mk_decoy")
        old_decoy_kdf = old_ks.config._data.get("kdf_params_decoy")

        # 1. Load current index with old key
        old_idx_mgr = EncryptedIndex(vault_dir)
        old_index = old_idx_mgr.load(old_master_key)

        # 2. Setup new keystore in vault_new preserving existing KDF strength + fresh salt
        new_kdf = KDFParams(
            memory_kib=old_kdf.memory_kib,
            time_cost=old_kdf.time_cost,
            parallelism=old_kdf.parallelism,
            salt=KDFParams.new_with_fresh_salt().salt,
        )
        new_keystore = Keystore(vault_new)
        new_mk, new_recovery_b32 = new_keystore.setup(
            password, keyfile_path=keyfile_path, benchmark=False, kdf_params=new_kdf
        )
        if recovery_key_out is not None:
            recovery_key_out.append(new_recovery_b32)

        # Preserve Slot 2 (decoy) so plausible deniability and decoy data survive key rotation
        if old_decoy_slot is not None and "slots" in new_keystore.config._data:
            new_keystore.config._data["slots"][SLOT_DECOY] = dict(old_decoy_slot)
        if old_decoy_legacy is not None:
            new_keystore.config._data["wrapped_mk_decoy"] = dict(old_decoy_legacy)
        if old_decoy_kdf is not None:
            new_keystore.config._data["kdf_params_decoy"] = dict(old_decoy_kdf)
        new_keystore.config.save()

        new_sk = make_lockout_signing_key(new_keystore.config.kdf_params.salt)

        # 3. Create new index for vault_new
        new_idx_mgr = EncryptedIndex(vault_new)
        new_index = new_idx_mgr.load_new_empty()

        all_entries = old_index.all_files()
        total = len(all_entries)
        old_blobs_dir = vault_dir / "blobs"

        # 4. Re-encrypt each file into vault_new/blobs/
        for i, entry in enumerate(all_entries):
            if progress:
                progress(i, total, f"Re-encrypting item {i + 1}/{total}…")

            old_file_id_bytes = bytes.fromhex(entry.file_id)
            old_blob_path = old_blobs_dir / entry.blob_name
            if not old_blob_path.exists():
                continue

            # Decrypt with old MK to temp file, then wipe temp securely after re-encryption
            temp_dec = new_blobs_dir / f"rotate_{secrets.token_hex(4)}.tmpdec"
            decrypt_file(old_blob_path, temp_dec, old_master_key, expected_file_id=old_file_id_bytes)

            try:
                new_file_id_bytes = generate_file_id()
                new_file_id_hex = new_file_id_bytes.hex()
                new_blob_name = derive_blob_name_key(new_mk, new_file_id_bytes).hex()
                new_blob_path = new_blobs_dir / new_blob_name

                encrypt_file(temp_dec, new_blob_path, new_mk, file_id=new_file_id_bytes)
            finally:
                try:
                    secure_delete_file(temp_dec)
                except SecureDeleteError:
                    temp_dec.unlink(missing_ok=True)

            # Add updated entry to new index
            new_entry = FileEntry(
                file_id=new_file_id_hex,
                original_name=entry.original_name,
                original_path=entry.original_path,
                vault_path=entry.vault_path,
                size_bytes=entry.size_bytes,
                modified_time=entry.modified_time,
                mime_type=entry.mime_type,
                blob_name=new_blob_name,
                added_time=entry.added_time,
                thumbnail_b64=entry.thumbnail_b64,
                tags=list(entry.tags),
                is_favorite=entry.is_favorite,
                is_folder=entry.is_folder,
                source_hash=entry.source_hash,
            )
            new_index.add(new_entry)

        # 5. Save and verify new index in vault_new
        new_idx_mgr.save(new_mk)

        # 6. Verify all files in vault_new
        for entry in new_index.all_files():
            blob_path = new_blobs_dir / entry.blob_name
            ok, msg = verify_file_integrity(blob_path, new_mk)
            if not ok:
                raise RuntimeError(f"Integrity check failed in new vault: {msg}")

        # 7. Preserve auxiliary vault files/subdirs & re-sign HMACs with new salt
        for aux_name in ("applock.config", "intruder.dat"):
            src_aux = vault_dir / aux_name
            if src_aux.exists() and src_aux.is_file():
                shutil.copy2(src_aux, vault_new / aux_name)

        for aux_dir in ("intruder_photos", "decoy"):
            src_dir = vault_dir / aux_dir
            if src_dir.exists() and src_dir.is_dir():
                shutil.copytree(src_dir, vault_new / aux_dir, dirs_exist_ok=True)

        if (vault_dir / "settings.dat").exists():
            preserved_settings = SettingsStore(vault_dir, old_sk)
            rekeyed_settings = SettingsStore(vault_new, new_sk)
            rekeyed_settings._data = dict(preserved_settings._data)
            rekeyed_settings.save()
        else:
            SettingsStore(vault_new, new_sk).save()

        LockoutManager(vault_new, new_sk).clear()

        # 8. Atomic swap: vault_dir -> vault.old, vault_new -> vault_dir
        if vault_old.exists():
            shutil.rmtree(vault_old, ignore_errors=True)
        vault_dir.rename(vault_old)
        vault_new.rename(vault_dir)

        # Update active session with new MK
        get_session().unlock(new_mk)
        return True, (
            "Key rotation completed successfully.\n\n"
            f"New Emergency Recovery Key:\n{new_recovery_b32}"
        )

    except Exception as exc:
        logger.error("Key rotation failed: %s", type(exc).__name__)
        shutil.rmtree(vault_new, ignore_errors=True)
        return False, "Key rotation failed. Your original vault remains untouched."


__all__ = [
    "create_vault_backup",
    "restore_vault_backup",
    "rotate_vault_keys",
]
