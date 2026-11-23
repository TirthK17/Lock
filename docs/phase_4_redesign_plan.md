# Phase 4 Plan: Screen-by-Screen PySide6 Qt Desktop Redesign

## 1. Objectives & GEMINI.md Strict Guardrails
Phase 4 focuses on visual refinement, graphite palette consistency, and layout polish across all PySide6 Qt screens.
Per `GEMINI.md`:
- **Python 3.12 + PySide6 (Qt) Desktop only**: Absolutely NO React, Tailwind, webviews, or Electron.
- **Strictly Non-Destructive**: Never remove any feature, button, setting, input, shortcut, or state. Appearance, styling, and layout polish only.
- **Token Class Discipline**: UI colours must derive strictly from token classes (`ui/theme.py`, `ui/dashboard_theme.py`, `ui/login_theme.py`). Eliminate scattered hardcoded hex codes.
- **Qt Threading Safety**: Touch widgets strictly on the GUI main thread via Signals & Slots.
- **Test Integrity**: After every task/change, verify:
  ```bash
  python -m ruff check mylocker && python -m pytest -q
  ```
  Ensure 100% passing tests (203+ tests).

---

## 2. Screen-by-Screen Tasks Breakdown

### Task 1: `LoginScreen` & `ForgotPasswordDialog` (`login.py`, `login_theme.py`)
- Standardize all glassmorphic and card container styles with the refined graphite palette in `LoginColors`.
- Polish input focus rings, error shake animations, unlock progress spinner, and success flash.
- Ensure keyfile selector widget (`KeyfileSelectorWidget`) cleanly matches graphite borders and typography.
- Refine `ForgotPasswordDialog` with crisp typography, password strength indicator alignment, and unified action buttons.
- Verification: run `python -m pytest mylocker/tests/test_login_ui_redesign.py -v`.

### Task 2: `SetupWizard` (`setup_wizard.py`)
- Modernize the 5-step wizard container:
  - Polished step progression header with numbered breadcrumbs and accent checkmarks.
  - Consistent graphite background surfaces (`DashColors.BG_CARD`, `Colors.BG_CARD`).
  - Standardized directory browser row and free-space badge.
  - Interactive password entropy meter with color-coded token gradients.
  - Clean Base32 recovery key display block with one-click copy and print actions.
- Verification: run `python -m pytest mylocker/tests/test_ui_startup.py -v`.

### Task 3: `Dashboard` Main Window (`dashboard.py`, `dashboard_theme.py`)
- Centralize all hardcoded RGBA / hex values identified in Task E audit into `DashColors` token definitions.
- Refine Metric Cards: high-DPI SVG icons, subtle surface borders, clear typography hierarchy (`Segoe UI Variable`).
- Category filter tabs & sort controls: unified flat styling with subtle hover states.
- Floating multi-select action bar: modern glassmorphic graphite pill with crisp action buttons.
- Auto-dismissing banners (`_speed_banner`, `_hotkey_banner`): ensure smooth 90 FPS rendering without clipping or hover flickering.
- Virtualized file grid & list views: flat-vector file-type badges (PDF, DOCX, ZIP, MEDIA) and hover quick-action overlays.
- Status bar: clean status pills for vault lock state, encrypted bytes, and BitLocker protection.
- Verification: run `python -m pytest mylocker/tests/test_dashboard_ui.py -v` and `test_batch_performance_and_multiselect.py`.

### Task 4: `Settings` Dialog (`settings.py`)
- Modernize tab bar navigation (General, Security, Backup & Maintenance) with graphite active indicators.
- Replace remaining hardcoded colors with `Colors` / `DashColors` tokens.
- Refine interactive sliders (idle timeout), toggle switches, and dialog popovers (change master password, speed optimizer, decoy manager).
- Polish backup export/restore progress indicators.
- Verification: run `python -m pytest mylocker/tests/test_dashboard_ui.py -k "Settings" -v`.

### Task 5: `Gallery` Viewer & `VideoPlayer` (`gallery.py`, `video_player.py`)
- `GalleryViewer`:
  - Sleek dark canvas with floating translucent graphite control bar.
  - Crisp high-DPI vector icons for zoom, rotate, navigation, export, and close.
- `VideoPlayer`:
  - Polished translucent video controls overlay with smooth auto-hide on mouse idle.
  - Custom styled time scrubber, volume popup slider, speed selector, and PiP / fullscreen toggles.
  - Ensure zero flicker during chunk boundary seeking with `StreamingVaultIODevice`.
- Verification: run `python -m pytest mylocker/tests/test_video_player_ui.py -v`.

### Task 6: `IntruderLog`, `CalculatorDisguise`, `AppLockUI` & `AppLockPrompt`
- `IntruderLog`:
  - Graphite log table with status badges and photo thumbnails.
  - Clean enlarged photo view popover.
- `CalculatorDisguise`:
  - Pixel-perfect native Windows 10/11 Calculator lookalike with responsive keypad layout.
- `AppLockUI`:
  - Process list with crisp application icons and grace period selector.
- `AppLockPrompt`:
  - Topmost dimmed glassmorphic backdrop with centered graphite authentication card.
  - Responsive rate-limit lockout warning and terminate button.
- Verification: run `python -m pytest mylocker/tests/test_applock_lockout.py mylocker/tests/test_phase6.py mylocker/tests/test_phase7.py -v`.

---

## 3. Execution & Verification Workflow
- Execute sequentially from Task 1 to Task 6.
- One topic per commit: `style: polish <screen> with graphite theme tokens (Phase 4 Task N)`.
- Run `python -m ruff check mylocker && python -m pytest -q` after each task.
