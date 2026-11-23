# Phase 2 — Plan for Backlog Item B1: Task Cancellation Support

## Item Summary
- **ID**: B1
- **Severity**: High
- **Scope**: `mylocker/app/core/chunked_io.py`, `mylocker/app/core/vault.py`, `mylocker/app/core/backup.py`, `mylocker/app/core/service.py`, `mylocker/app/ui/dashboard.py`
- **Problem**: Cancel does nothing for `restore`, `verify`, `backup`, `restore-backup`, and `key rotation`. If a long-running task is in progress when the app locks or the user presses Cancel, the task continues executing. Furthermore, on shutdown or lock, the worker thread can outlive the lock operation, risking crashes or data corruption when `Session.lock()` zeroes the master key.
- **Goal**: Propagate `cancel_event` through all chunk processing loops and operations (`restore_file`, `export_file`, `verify_all`, `create_vault_backup`, `restore_vault_backup`, `rotate_vault_keys`), ensure partial files/directories are cleaned on abort, and register a pre-lock hook in `DashboardWindow` so running tasks abort immediately before `Session.lock()` zeroes the master key.

---

## 1. Files to Touch & Exact Changes

### 1. `mylocker/app/core/chunked_io.py`
- In `verify_file_integrity(source_path, master_key, cancel_event=None)`:
  - Add explicit handling: `except OperationCancelledError: raise` before general `except Exception as exc: return False, str(exc)` so cancellation propagates rather than being treated as an integrity failure.

### 2. `mylocker/app/core/vault.py`
- In `restore_file(self, file_id, dest_dir, remove_from_vault=False, progress=None, cancel_event=None)`:
  - Accept `cancel_event: Optional[threading.Event] = None`.
  - Pass `cancel_event=cancel_event` into `decrypt_file(..., cancel_event=cancel_event)`.
  - On cancellation (`OperationCancelledError`), ensure partial output is removed and index/blob deletion is not reached.
- In `export_file(self, file_id, dest_path, progress=None, cancel_event=None)`:
  - Accept `cancel_event: Optional[threading.Event] = None`.
  - Pass `cancel_event=cancel_event` into `decrypt_file(..., cancel_event=cancel_event)`.
- In `verify_all(self, progress=None, cancel_event=None)`:
  - Accept `cancel_event: Optional[threading.Event] = None`.
  - In loop over entries: check `if cancel_event and cancel_event.is_set(): raise OperationCancelledError("Operation cancelled.")`.
  - Pass `cancel_event=cancel_event` to `verify_file_integrity(blob_path, mk, cancel_event=cancel_event)`.

### 3. `mylocker/app/core/backup.py`
- In `create_vault_backup(vault_dir, backup_tar_path, progress=None, cancel_event=None)`:
  - Accept `cancel_event: Optional[threading.Event] = None`.
  - In SHA-256 chunk reading loop: check `if cancel_event and cancel_event.is_set(): raise OperationCancelledError("Operation cancelled.")`.
  - In tar compression loop: check `if cancel_event and cancel_event.is_set(): raise OperationCancelledError("Operation cancelled.")`.
  - If cancelled: clean up `backup_tar_path.unlink(missing_ok=True)` and `manifest_path.unlink(missing_ok=True)`.
  - Propagate `OperationCancelledError`.
- In `restore_vault_backup(backup_tar_path, restore_target_dir, password=None, keyfile_path=None, progress=None, cancel_event=None)`:
  - Accept `cancel_event: Optional[threading.Event] = None`.
  - Check `cancel_event` during member inspection, chunk hashing, and credential test.
  - If cancelled: clean up `staging_dir` and do not perform atomic replacement of target vault.
  - Propagate `OperationCancelledError`.
- In `rotate_vault_keys(vault_dir, old_master_key, password, keyfile_path=None, progress=None, recovery_key_out=None, cancel_event=None)`:
  - Accept `cancel_event: Optional[threading.Event] = None`.
  - In re-encryption loop over entries:
    - Check `if cancel_event and cancel_event.is_set(): raise OperationCancelledError("Operation cancelled.")`.
    - Pass `cancel_event=cancel_event` to `decrypt_file` and `encrypt_file`.
  - In verification loop of new blobs: check `cancel_event` and pass to `verify_file_integrity`.
  - If cancelled: clean up `vault_new`, securely wipe all temporary decrypted buffers, zero `new_mk`, and leave original `vault_dir` completely unchanged.
  - Propagate `OperationCancelledError`.

### 4. `mylocker/app/core/service.py`
- In `VaultWorker._execute_task`:
  - `restore_file`: pass `cancel_event=self._cancel_event` to `self._vault.restore_file`.
  - `verify_all`: pass `cancel_event=self._cancel_event` to `self._vault.verify_all`.
  - `backup`: pass `cancel_event=self._cancel_event` to `create_vault_backup`.
  - `restore_backup`: pass `cancel_event=self._cancel_event` to `restore_vault_backup`.
  - `rotate_keys`: pass `cancel_event=self._cancel_event` to `rotate_vault_keys`.
- Catch `OperationCancelledError` in `_execute_task` cleanly, emitting `self.task_finished.emit(task.task_id, False, "Operation was cancelled.")`.

### 5. `mylocker/app/ui/dashboard.py`
- In `DashboardWindow._build_ui` / initialization:
  - Register a pre-lock hook with `get_lock_controller().register_pre_lock_hook(self._on_pre_lock)`.
  - `self._on_pre_lock`:
    - Calls `self._vault_service.cancel_current()` and stops any active action workers before the lock zeroing proceeds.
- In `DashboardWindow.cleanup()`:
  - Unregister the pre-lock hook: `get_lock_controller().unregister_pre_lock_hook(self._on_pre_lock)`.

---

## 2. Risk to Existing Behaviour
- **Crypto / File Formats**: Zero changes to crypto formats, KDF parameters, file headers, trailers, or index schemas.
- **Normal Operations**: If `cancel_event` is `None` (or not set), all operations execute identically to their existing behaviour.
- **Security Guarantee**: Solves the race condition where a background thread could attempt to read from a zeroed `master_key` when a lock occurs during a long operation.

---

## 3. Tests to Add
Add new test suite in `mylocker/tests/test_cancellation.py`:
1. `test_restore_file_cancellation`: Cancelling mid-stream raises `OperationCancelledError`, cleans up partial destination file, and does not alter the vault.
2. `test_verify_all_cancellation`: Cancelling mid-verification halts immediately without false failure logs.
3. `test_create_vault_backup_cancellation`: Cancelling during backup creation removes incomplete `.tar.gz` and temp manifest.
4. `test_restore_vault_backup_cancellation`: Cancelling during restore cleans staging directory without altering target vault.
5. `test_rotate_vault_keys_cancellation`: Cancelling during re-encryption cleans `vault.new` and leaves original vault fully intact and unlocked.
6. `test_dashboard_pre_lock_hook_cancels_service_worker`: Verifies that triggering lock aborts an active worker task before `session.lock()` executes.

---

## 4. Verification Commands
- `python -m ruff check mylocker`
- `python -m pytest mylocker/tests/test_cancellation.py -v`
- `python -m pytest -q` (all 176+ tests)
