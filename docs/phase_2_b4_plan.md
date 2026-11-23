# Phase 2 — Plan for Backlog Item B4: Hotkey Registration Failure Shown in UI

## Item Summary
- **ID**: B4
- **Severity**: Medium
- **Scope**: `mylocker/app/system/hotkeys.py`, `mylocker/app/system/lock_controller.py`, `mylocker/app/ui/dashboard.py`
- **Problem**: In `system/hotkeys.py`, if the global panic hotkey (`Ctrl+Alt+Shift+L`) fails to register (e.g. `RegisterHotKey` returns `FALSE` because another application or system process has bound the shortcut, or due to restricted permissions), the error is only logged to `logger.warning`. The application never surfaces this failure to the UI or user, leaving the user with a false sense of security believing the panic hotkey is active when it is not.
- **Goal**: Return registration status and error code from `GlobalHotkeyThread` / `PanicHotkeyManager`, propagate it through `LockController.hotkey_registration_failed`, and show a clear warning banner and tooltip in the `Dashboard` UI so the user is immediately aware.

---

## 1. Files to Touch & Exact Changes

### 1. `mylocker/app/system/hotkeys.py`
- In `GlobalHotkeyThread`:
  - Add parameter `on_registration_failed: Optional[Callable[[int], None]] = None`.
  - Add attribute `self.is_registered: bool = False`.
  - In `run()`:
    - If `RegisterHotKey` fails:
      - Query `err = ctypes.windll.kernel32.GetLastError()`.
      - Invoke `self.on_registration_failed(err)` if provided.
      - Return early.
    - If successful:
      - Set `self.is_registered = True`.
- In `PanicHotkeyManager`:
  - Add signal `registration_failed = Signal(int, str)`: emits `(error_code, user_friendly_message)`.
  - Add properties `is_registered: bool` and `last_error: Optional[int]`.
  - Pass error callback to `GlobalHotkeyThread`.
  - On failure, emit `registration_failed(err, "Global panic hotkey (Ctrl+Alt+Shift+L) could not be registered. It is already in use by another application.")`.

### 2. `mylocker/app/system/lock_controller.py`
- Add signal `hotkey_registration_failed = Signal(str)` to `LockController`.
- In `__init__`:
  - Connect `self._hotkey_manager.registration_failed` to `self.hotkey_registration_failed`.
- Add properties `is_hotkey_registered: bool` and `hotkey_error_message: Optional[str]`.

### 3. `mylocker/app/ui/dashboard.py`
- In `Dashboard._build_ui()`:
  - Add `self._hotkey_banner = _AutoDismissBanner(timeout_ms=7000)` styled with red/warning accent.
  - Add warning icon, descriptive label (`self._hotkey_lbl`), and dismiss button.
- In `_setup_central_lock_coordination()`:
  - Connect `lc.hotkey_registration_failed.connect(self._on_hotkey_registration_failed)`.
- Add slot `_on_hotkey_registration_failed(self, msg: str)`:
  - Sets `self._hotkey_lbl.setText(msg)`.
  - Shows `self._hotkey_banner.show_animated()`.
  - Updates `self._sidebar_protection_status.setToolTip("Panic hotkey inactive (already in use)")`.
  - Updates `self._notif_btn.setToolTip(msg)`.
- In `cleanup()`:
  - Call `self._hotkey_banner.stop_all()`.

---

## 2. Risk to Existing Behaviour
- **Standard Operation**: When hotkey registration succeeds, behaviour is completely unchanged (no banner is shown).
- **Security & User Awareness**: When registration fails, the user is visibly warned via a smooth non-blocking banner and tooltip rather than silently failing.
- **Crypto & Formats**: Zero impact on crypto or storage formats.

---

## 3. Tests to Add
Add new test suite in `mylocker/tests/test_hotkey_failure_ui.py`:
1. `test_global_hotkey_thread_failure_callback`:
   - Simulates `RegisterHotKey` returning 0, verifies callback is called with error code and `is_registered` remains `False`.
2. `test_panic_hotkey_manager_emits_failure`:
   - Verifies `PanicHotkeyManager.registration_failed` emits error code and user-friendly message.
3. `test_lock_controller_forwards_hotkey_failure`:
   - Verifies `LockController.hotkey_registration_failed` emits when hotkey fails.
4. `test_dashboard_displays_hotkey_warning_banner`:
   - Simulates hotkey registration failure on `Dashboard`.
   - Verifies `_hotkey_banner` becomes visible with appropriate warning message.

---

## 4. Verification Commands
- `python -m ruff check mylocker`
- `python -m pytest mylocker/tests/test_hotkey_failure_ui.py -v`
- `python -m pytest -q`
