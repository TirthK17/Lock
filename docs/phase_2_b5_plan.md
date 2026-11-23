# Phase 2 — Backlog Item B5 Plan: Route App Lock Attempts Through Lockout Counter

## 1. Problem Statement & Root Cause
In `mylocker/app/applock/prompt.py` and `mylocker/main.py`, when a protected application is launched and intercepted by App Lock, the user is presented with `AppLockPrompt`. Currently:
- Failed password entries only increment an isolated in-memory counter (`self.attempts += 1`, max 3 attempts) before terminating the target app.
- Failed attempts are **never** reported to `LockoutManager`, leaving the shared vault lockout counter unchanged.
- An attacker or unauthorized user can repeatedly attempt password guesses on intercepted apps without triggering the progressive rate-limiting delays (e.g. 30s, 60s, 15m), intruder log recording, webcam capture, vault auto-lock, or emergency self-destruct wipe policies.

## 2. Proposed Changes

### A. `mylocker/app/applock/prompt.py`
1. **Extend `AppLockPrompt.__init__`**:
   ```python
   def __init__(
       self,
       pid: int,
       exe_name: str,
       verify_password_fn: Callable[[str], bool],
       parent: Optional[QWidget] = None,
       lockout: Optional[LockoutManager] = None,
       settings_store: Optional[SettingsStore] = None,
       keystore: Optional[Keystore] = None,
       on_lockout_triggered: Optional[Callable[[], None]] = None,
   ) -> None:
   ```
2. **Initial Lockout Check**:
   - If `self.lockout` is provided, evaluate `check_lockout()`.
   - If a `LockoutError` is active (`self.lockout.get_lockout_seconds() > 0`):
     - Display lockout duration banner/label: `"Too many failed attempts. Wait {remaining:.0f}s."`
     - Disable `pw_input` and unlock button.
     - Invoke `on_lockout_triggered()` if provided.
     - Schedule `_terminate()` after a short delay (e.g., 1.5s).
3. **Verification and Lockout Enforcement (`_verify()`)**:
   - Check `self.lockout.check_lockout()` before processing entered text.
   - On **successful** authentication:
     - If `self.lockout`: call `self.lockout.record_success()`.
     - Resume target process and emit `unlocked`.
   - On **failed** authentication:
     - Increment local attempt counter (`self.attempts += 1`).
     - If `self.lockout`:
       - `count = self.lockout.record_failure()`
       - `remaining = self.lockout.get_lockout_seconds()`
       - Spawn background webcam capture via `IntruderPhotoCaptureThread` (track thread and clean up in `closeEvent`).
     - **Wipe Policy**: If `settings_store` has `self_destruct_enabled=True` and `count >= 20` and `keystore` is provided:
       - Execute `keystore.self_destruct()`.
       - Display `"Vault self-destruct activated after 20 failed attempts."`.
       - Disable input, call `on_lockout_triggered()`, and schedule `_terminate()`.
     - **Lockout Policy**: If `remaining > 0`:
       - Display `"Too many failed attempts. Wait {remaining:.0f}s. Terminating app…"`.
       - Disable input, call `on_lockout_triggered()`, and schedule `_terminate()`.
     - **Local Attempt Limit**: If `remaining <= 0` and local limit reached, terminate app as before; otherwise display remaining attempts.
4. **Thread Teardown**:
   - Maintain `self._photo_threads: list[QThread] = []` and wait on running capture threads in `closeEvent`.

### B. `mylocker/main.py`
In `_show_applock_prompt(self, pid: int, exe_name: str)`:
- Pass `lockout=self.lockout`, `settings_store=self.settings`, `keystore=self.keystore`, and `on_lockout_triggered=lambda: self.lock_vault("lockout")` to `AppLockPrompt`.

## 3. Test Strategy & Verification

### Test File: `mylocker/tests/test_applock_lockout.py`
1. `test_applock_prompt_success_resets_lockout_counter`:
   - Verifies that successful entry calls `lockout.record_success()`.
2. `test_applock_prompt_failure_increments_lockout_counter`:
   - Verifies that entering a wrong password in `AppLockPrompt` increments `lockout.get_fail_count()`, writes to intruder log, and shows countdown.
3. `test_applock_prompt_active_lockout_blocks_entry`:
   - Verifies that when `lockout` is active upon prompt creation, input is disabled and app is terminated.
4. `test_applock_prompt_lockout_triggers_lock_callback`:
   - Verifies that reaching lockout threshold invokes `on_lockout_triggered`.
5. `test_applock_prompt_self_destruct_wipe_policy`:
   - Verifies that 20 failures with self-destruct enabled calls `keystore.self_destruct()`.
6. `test_applock_prompt_captures_intruder_photo_on_failure`:
   - Verifies that `IntruderPhotoCaptureThread` is spawned on wrong password entry.

### Verification Checklist:
- `python -m ruff check mylocker`
- `python -m pytest mylocker/tests/test_applock_lockout.py -v`
- `python -m pytest -q` (all ~203 tests green)
- Git commit message: `fix: route app lock password attempts through lockout counter (B5)`
