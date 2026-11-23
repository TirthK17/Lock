# Phase 2 — Plan for Backlog Item B2: Intruder Webcam Capture Off GUI Thread

## Item Summary
- **ID**: B2
- **Severity**: Medium
- **Scope**: `mylocker/app/system/webcam.py`, `mylocker/app/ui/login.py`
- **Problem**: When a user or intruder enters an invalid password, `_capture_intruder_photo()` runs synchronously on the GUI thread (triggered by `QTimer.singleShot(50, ...)`). The webcam capture routine probes DirectShow / OpenCV video devices and QtMultimedia sensors, which can take 1.5 to 2.0 seconds. During this time, the GUI thread is completely frozen: animations halt, inputs are blocked, and the login dialog feels unresponsive.
- **Goal**: Move intruder webcam capture and encrypted photo storage into a dedicated background `QThread` (`IntruderPhotoCaptureThread`) in `webcam.py`, invoked asynchronously by `login.py`. The GUI remains 100% responsive, the photo is captured and encrypted in the background, and linked to the intruder log upon completion without thread leaks.

---

## 1. Files to Touch & Exact Changes

### 1. `mylocker/app/system/webcam.py`
- Create `IntruderPhotoCaptureThread(QThread)`:
  - Signals:
    - `photo_captured = Signal(str)` (emits the saved relative path `intruder_photos/capture_<timestamp>.enc` on success, or empty string on failure).
  - Attributes:
    - `_vault_dir: Path`
    - `_lockout: Optional[LockoutManager]`
  - `run()`:
    - Generates relative path and output destination path.
    - Calls `capture_intruder_photo(out_path, enabled=True)`.
    - If capture succeeds and `_lockout` is provided, calls `_lockout.attach_photo_to_latest(rel)` on the background thread.
    - Emits `photo_captured`.
  - Expose `IntruderPhotoCaptureThread` in `__all__`.

### 2. `mylocker/app/ui/login.py`
- In `LoginDialog`:
  - Initialize `self._photo_threads: list[QThread] = []`.
  - In `_capture_intruder_photo()`:
    - Instantiate `thread = IntruderPhotoCaptureThread(self._lockout.vault_dir, self._lockout, parent=self)`.
    - Connect `thread.finished.connect(lambda: self._photo_threads.remove(thread) if thread in self._photo_threads else None)`.
    - Track in `self._photo_threads` and call `thread.start()`.
  - In `closeEvent()` / teardown:
    - Wait on any running photo threads (`th.wait(1000)`).
- In `LoginScreen`:
  - Initialize `self._photo_threads: list[QThread] = []`.
  - In `_capture_intruder_photo()`:
    - Instantiate `thread = IntruderPhotoCaptureThread(self._lockout.vault_dir, self._lockout, parent=self)`.
    - Connect `thread.finished.connect(lambda: self._photo_threads.remove(thread) if thread in self._photo_threads else None)`.
    - Track in `self._photo_threads` and call `thread.start()`.
  - In `cleanup()`:
    - Wait on any running photo threads (`th.wait(1000)`).

---

## 2. Risk to Existing Behaviour
- **GUI Responsiveness**: Zero GUI blocking on failed password attempts. The user sees the error message and countdown timer immediately.
- **Crypto & Security**: Identical DPAPI encryption (`win32crypt.CryptProtectData`) and identical file storage (`intruder_photos/capture_<ms>.enc`).
- **Thread Safety**: The worker operates on its own paths and calls `attach_photo_to_latest` without touching Qt widgets.
- **Thread Lifecycle**: Strong references kept until completion (`finished` signal removes thread from list); cleanup waits on running threads.

---

## 3. Tests to Add
Add new test suite in `mylocker/tests/test_webcam_thread.py`:
1. `test_intruder_capture_thread_runs_and_emits`:
   - Runs `IntruderPhotoCaptureThread` in a mock environment (with `capture_intruder_photo` mocked to return `True`), confirms `photo_captured` signal emits relative path, and verifies `attach_photo_to_latest` is called.
2. `test_login_screen_failed_attempt_does_not_block_gui`:
   - Enters wrong password on `LoginScreen`.
   - Confirms error is displayed immediately without waiting for webcam capture.
   - Verifies capture thread starts and cleans up on completion.
3. `test_login_dialog_failed_attempt_spawns_thread`:
   - Verifies `LoginDialog` correctly spawns capture thread and tracks it in `_photo_threads`.

---

## 4. Verification Commands
- `python -m ruff check mylocker`
- `python -m pytest mylocker/tests/test_webcam_thread.py -v`
- `python -m pytest -q`
