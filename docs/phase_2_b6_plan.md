# Phase 2 — Backlog Item B6 Plan: Align Documentation Hotkey Descriptions

## 1. Problem Statement
In `README.md` (line 21), the global panic hotkey is described as `Ctrl+Shift+L`. In `THREAT_MODEL.md` (line 29), it is described as `Win+Alt+L`.
However, the actual implementation in `mylocker/app/system/hotkeys.py` registers `MOD_CONTROL | MOD_ALT | MOD_SHIFT | ord('L')`, which is `Ctrl+Alt+Shift+L`.
This discrepancy can cause user confusion and failed attempts to trigger the panic hotkey.

## 2. Proposed Changes
- In `README.md`:
  - Update `Ctrl+Shift+L` to `Ctrl+Alt+Shift+L`.
- In `THREAT_MODEL.md`:
  - Update `Win+Alt+L` to `Ctrl+Alt+Shift+L`.

## 3. Test & Verification
- Run `ruff check mylocker`
- Run `python -m pytest -q`
- Commit message: `fix: align readme hotkey text with implementation (B6)`
