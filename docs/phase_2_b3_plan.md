# Phase 2 — Plan for Backlog Item B3: update_file Crash-Safe Ordering and Rollback

## Item Summary
- **ID**: B3
- **Severity**: Medium
- **Scope**: `mylocker/app/core/vault.py`
- **Problem**: In `Vault.update_file()`, the existing encrypted blob is replaced (`new_part_path.replace(blob_path)`) before the index is saved (`self._save_index(mk)`). If `_save_index()` fails (e.g. disk full, permission error, power failure, or process crash), the old encrypted file has already been destroyed, while the index remains out of sync with old size, modified time, hash, and thumbnail. Furthermore, the `except` block has no rollback logic.
- **Goal**: Make `update_file()` crash-safe by staging the old blob to `.old`, committing the new blob and index, and rolling back both the filesystem blob and in-memory metadata if `_save_index()` or any intermediate step fails.

---

## 1. Files to Touch & Exact Changes

### 1. `mylocker/app/core/vault.py` (`update_file`)
- Snapshot original metadata attributes before mutation:
  - `orig_size = entry.size_bytes`
  - `orig_mod = entry.modified_time`
  - `orig_hash = entry.source_hash`
  - `orig_mime = entry.mime_type`
  - `orig_thumb = entry.thumbnail_b64`
- Prepare a unique backup path for the old blob:
  - `old_blob_backup = self.blobs_dir / f"{blob_name}.{secrets.token_hex(4)}.old"`
- Safe atomic swap sequence:
  1. Rename existing `blob_path` → `old_blob_backup`.
  2. Rename `new_part_path` → `blob_path`.
  3. Update `FileEntry` fields in memory.
  4. Save index: `self._save_index(mk)`.
  5. On success: remove `old_blob_backup.unlink(missing_ok=True)` (or `secure_delete_file`).
- Rollback in `except Exception:`:
  - Revert in-memory `entry` attributes back to `orig_*`.
  - If `old_blob_backup.exists()`:
    - Remove replacement `blob_path.unlink(missing_ok=True)`.
    - Restore original `old_blob_backup.replace(blob_path)`.
  - Clean up `new_part_path.unlink(missing_ok=True)`.
  - Re-raise error cleanly.

---

## 2. Risk to Existing Behaviour
- **Crypto & File Formats**: Zero changes to crypto formats, KDF parameters, or index schema.
- **Normal Operations**: If the update succeeds, the old blob backup is removed and the updated file is saved exactly as before.
- **Failure Resilience**: If index writing or verification fails midway, the original encrypted file and its metadata remain completely intact and valid.

---

## 3. Tests to Add
Add new test suite in `mylocker/tests/test_update_file_rollback.py`:
1. `test_update_file_successful_commit`:
   - Updates a file with new content.
   - Verifies size, modified time, SHA-256 hash, and contents update correctly and no `.old` or `.part` files remain.
2. `test_update_file_rollback_on_index_save_failure`:
   - Stubs `_save_index` to raise an `OSError("Disk full")`.
   - Verifies `update_file` raises `VaultError`.
   - Verifies that the original encrypted blob is restored and intact.
   - Verifies that decrypting the file still yields the original content and original metadata.
3. `test_update_file_rollback_on_hash_mismatch`:
   - Verifies that if hash verification fails before swap, the original blob is untouched and the partial file is cleaned.

---

## 4. Verification Commands
- `python -m ruff check mylocker`
- `python -m pytest mylocker/tests/test_update_file_rollback.py -v`
- `python -m pytest -q`
