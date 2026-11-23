# Personal Vault – agent rules

## Project facts
- Python 3.12 + PySide6 (Qt) Windows desktop app. NOT web, NOT Electron, NOT React.
- Code: mylocker/main.py, mylocker/app/{core,system,applock,ui}. Tests: mylocker/tests.
- core/ = crypto, vault, index, journal, lockout (no Qt). system/ = Win32 + hotkeys + lock controller.
- UI colours come from token classes: ui/theme.py (Colors), ui/dashboard_theme.py (DashColors),
  ui/login_theme.py (LoginColors). Change tokens, not scattered hex values.

## Hard rules
1. Never change crypto formats, KDF parameters, file/index/journal formats, or vault.config schema.
2. Never weaken security: no plaintext on disk, no logging secrets, no skipping lockout checks.
3. Never remove a feature, button, setting or shortcut. UI work = appearance and layout only.
4. Qt: touch widgets only on the GUI thread. From other threads use a Signal to a @Slot on a QObject
   (see _MainThreadInvoker in main.py). Never QTimer.singleShot from a plain Python thread.
5. Anything that holds the master key must finish or be cancelled BEFORE Session.lock() zeroes it.
6. Windows ctypes calls must set argtypes/restype; treat DWORD/HANDLE as unsigned/pointer-sized.
7. Catch specific exceptions. No bare `except Exception: pass` around security or I/O code.

## Workflow
- Plan first. List files you will touch and tests you will run. Wait for approval.
- One topic per commit, message: `fix: ...` or `style: ...`.
- After every change run: ruff check mylocker && python -m pytest -q
- Add or update a test for every bug fix. Report anything you could not verify.
- Do not invent features. If something looks like a missing feature, list it, do not build it.
