# U0 Read-Only UI Audit Report

**Date**: 2026-11-26  
**Scope**: All screens and UI support modules under `mylocker/app/ui/`, `mylocker/app/applock/prompt.py`, `mylocker/app/system/tray.py`, and `mylocker/main.py`.  
**Baseline Status**: 0 codebase edits performed in U0. All before-state screenshots saved to `docs/ui-screens/before/`.

---

## 1. Screen & Module Inventory

| File | Primary Components & Screens | `setStyleSheet` | Raw Colors (Hex + RGBA) | `QFont` / CSS Font | Fixed Sizes | Raw Dialogs | Emojis |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `mylocker/app/ui/__init__.py` | __init__.py | 0 | 0 | 0 | 0 | 0 | 0 |
| `mylocker/app/ui/applock_ui.py` | AppLockUI, running/installed process pickers | 5 | 0 | 3 | 2 | 13 | 6 |
| `mylocker/app/ui/calculator_disguise.py` | CalculatorDisguise | 7 | 0 | 3 | 4 | 0 | 0 |
| `mylocker/app/ui/dashboard.py` | Dashboard, SidebarWidget, FileCard, _AutoDismissBanner, Multi-select bar, DropHint, StatusBar | 46 | 45 | 41 | 27 | 29 | 3 |
| `mylocker/app/ui/dashboard_theme.py` | Colors palette & GLOBAL_QSS stylesheet | 20 | 63 | 15 | 14 | 0 | 0 |
| `mylocker/app/ui/gallery.py` | GalleryViewer, ZoomableImageLabel | 11 | 0 | 0 | 8 | 0 | 3 |
| `mylocker/app/ui/icons.py` | SVG icon registry & renderer (stroke-width=2 default, 24px default) | 0 | 0 | 0 | 0 | 0 | 0 |
| `mylocker/app/ui/intruder_log.py` | IntruderLogUI, PhotoViewerDialog | 5 | 0 | 2 | 2 | 5 | 2 |
| `mylocker/app/ui/login.py` | LoginScreen, RecoveryKeyDialog, ForgotPasswordDialog | 32 | 2 | 24 | 20 | 3 | 2 |
| `mylocker/app/ui/login_theme.py` | Colors palette & GLOBAL_QSS stylesheet | 17 | 73 | 13 | 15 | 0 | 0 |
| `mylocker/app/ui/settings.py` | SettingsUI, _create_card, ChangePassword/Decoy/OptimizeSpeed/RotatedRecovery/SelfDestruct dialogs | 21 | 1 | 11 | 12 | 44 | 2 |
| `mylocker/app/ui/setup_wizard.py` | SetupWizard (Welcome/Location, Password, Keyfile, Processing, Recovery Key, Verification) | 18 | 0 | 12 | 7 | 8 | 2 |
| `mylocker/app/ui/theme.py` | Colors palette & GLOBAL_QSS stylesheet | 1 | 34 | 16 | 0 | 0 | 0 |
| `mylocker/app/ui/video_player.py` | VideoPlayer, VideoColors, _ShellContainer, _VideoCanvas, _ScrimBar, _TimelineScrubber, _VolumeCluster, _GlassCard popovers | 20 | 40 | 39 | 31 | 2 | 5 |
| `mylocker/app/ui/widgets.py` | GlowButton, AnimatedCard, PasswordStrengthBar, SearchBar, StatusBadge, SidebarItem, ProgressOverlay, FadeLabel, SmoothScrollArea, SectionHeader | 12 | 8 | 13 | 6 | 0 | 2 |
| `mylocker/app/applock/prompt.py` | mylocker/app/applock/prompt.py | 5 | 1 | 3 | 4 | 0 | 1 |
| `mylocker/app/system/tray.py` | mylocker/app/system/tray.py | 0 | 0 | 1 | 0 | 0 | 7 |
| `mylocker/main.py` | mylocker/main.py | 0 | 0 | 0 | 0 | 4 | 0 |
| **TOTAL** | — | **220** | **267** | **196** | **152** | **108** | **35** |

---

## 2. Control & Styling Drift Across Screens

1. **Three Competing Color Systems**:
   - `theme.py` (`Colors`: `#0B1020`, `#111A2A`, `#172235`, accent `#8B9CFF`)
   - `dashboard_theme.py` (`DashColors`: `#060B16`, `#08101F`, `#091122`, accent `#5B8CFF` + multi-stop gradient `#4C65E8 -> #637EFF`)
   - `login_theme.py` (`LoginColors`: `#050A12`, `#08111D`, `#070E18`, accent `#38BDF8` / `#58A6FF` / `#00D2FF`)
   - `video_player.py` (`VideoColors`: `#07080C`, `#0B0D12`, `#10121A`, accent `#7B8EFF` / `#A998FF`)
   - **Impact**: Four incompatible background darks and four incompatible accent hues across views.
2. **Button Implementation Drift (9+ Separate Styles)**:
   - Global `QPushButton` in `theme.py` (`min-height: 36px`, `#8B9CFF` fill)
   - `GlowButton` (`widgets.py`) attaching heavy `QGraphicsDropShadowEffect` glow on hover
   - `UnlockPrimaryButton` (`login_theme.py`) using custom painted gradient, sheen curve, shadow rings, scale anim
   - `CategoryPill` (`dashboard_theme.py`) using linear gradient pill `4C65E8 -> 637EFF`
   - `SidebarItem` (`widgets.py`) hardcoding `#AAB7CC` and gradient active fill
   - `Dashboard._fab_add` (`dashboard.py`) inline pill gradient
   - `Dashboard._select_btn`, `_ss_btn`, `_btn_grid_mode`, `_btn_list_mode`, `_filter_btn`, `_optimize_btn`, `_sel_delete_btn` each define separate inline rounded rectangles/rgba fills
   - `SettingsUI.destruct_btn` and `_confirm_self_destruct_dialog.destroy_btn` use two different inline red button styles
   - `VideoPlayer._retry_btn`, `_quality_pill`, `_SettingsPopover`, `_ActionPopover` define separate inline button rules
3. **Fragmented Font Families & Ad-Hoc Type Scale**:
   - Mix of `'Segoe UI'`, `'Segoe UI Variable'`, `'Arial'`, `'Segoe UI Emoji'`, `'Consolas'`, `'Cascadia Code'`
   - Font sizes span `8px`, `9px`, `10px`, `11px`, `12px`, `13px`, `14px`, `15px`, `16px`, `17px`, `18px`, `20px`, `22px`, `26px`, `30px`, `36px`, `42px` (Qt point sizes vs CSS pixel sizes mixed arbitrarily, causing DPI scaling drift at 125%-175%).
4. **Inconsistent Modal Dialogs & Alerts**:
   - Unstyled native `QMessageBox.{information,warning,critical,question}` and `QInputDialog.getItem/getText` calls appear in `main.py`, `setup_wizard.py`, `dashboard.py`, `settings.py`, `intruder_log.py`, and `applock_ui.py` instead of a unified `DialogShell`.
   - Custom dialogs (`RecoveryKeyDialog`, `ForgotPasswordDialog`, `ChangePassword`, `SetupDecoy`, `OptimizeSpeed`, `RotatedRecovery`, `ConfirmSelfDestruct`, `PhotoViewerDialog`, `ProgressOverlay`) each build custom frames, padding, headers, and button rows.
5. **Boolean Controls & Lists**:
   - Settings booleans use standard `QCheckBox` while no animated `Toggle` control exists; `QRadioButton` indicator rules are missing in `GLOBAL_QSS`.
   - `Dashboard` list view toggle (`_btn_list_mode`) is not wired and no virtualized `QListView` exists for >500 items.
   - Raw emojis (`🛡️`, `🔍`, `📂`, `🔑`, `🗑️`, `📸`, `🔒`, `▶`, `⏹`, `🔍+`, `⤢`, `⚠`, `✓`) appear in `setup_wizard.py`, `dashboard.py`, `gallery.py`, `intruder_log.py`, `applock_ui.py`, `settings.py`, `prompt.py`, `tray.py`.
6. **Excessive Shadows, Gradients & Continuous Idle Repaint Timers**:
   - `WaveSparkline._anim_timer` ticks every 80ms continuously while Dashboard is visible.
   - `AmbientLoginBackdrop._drift_timer` and `SecurityStatusBadge._pulse_timer` tick every 45ms.
   - Heavy glow/gradients on `LoginCard`, `UnlockPrimaryButton`, `GlowButton`, `CategoryPill`, `SidebarItem`, `MediaBadgeGenerator`, and `QProgressBar::chunk`.

---

## 3. Clipping Risks (Fixed Widths / Heights & Window Bounds)

High-risk fixed dimensions observed (especially at `900x600` or `125%–175%` Windows scaling):
- `Dashboard.setMinimumSize(1000, 650)` exceeds the `900x600` minimum target window (`dashboard.py:939`).
- `SidebarWidget.setFixedWidth(260)` takes ~29% of `900px` width without collapsing to an icon rail (`dashboard.py:167,1102`).
- `VaultSearchBar.setFixedWidth(420)` + topbar buttons + `DashboardTopBar` margins easily squeeze below 1000px (`dashboard_theme.py:620`).
- `MetricCard.setFixedHeight(94)` + `setMinimumWidth(180)` across 4 horizontal cards + 260px sidebar requires >= `1060px` width -> horizontal crowding/clipping at `900x600` (`dashboard_theme.py:181-182`).
- `FileCard`: `setFixedSize(182, 124)`, character-count `_truncate(..., 19)` instead of `QFontMetrics.elidedText`, and hardcoded absolute positioning (`move(77, 28)`, `move(132, 57)`, `move(150, 11)`) + grid divisor `172` (`dashboard.py:232,255,269,331,2225`).
- `SetupWizard`: `_card.setFixedWidth(500)` / `520` without scroll container clips vertically on short/175%-DPI viewports (`setup_wizard.py:123`).
- `SettingsUI`: fixed-width action buttons (`220`, `260`, `280`), fixed-size modal dialogs (`380x220`, `420x270`, `440x260`, `520x290`) clip text at 150%-175% DPI (`settings.py:222,233,239,246,311,490,587,647,819,881`).
- `AppLockUI`: `grace_spin.setFixedWidth(60)` (2 digits + spin arrows tight at 175% DPI), `del_btn.setFixedSize(80, 28)` (`applock_ui.py:134,201`).
- `IntruderLogUI`: fixed `view_btn.setFixedSize(90, 28)` + `PhotoViewerDialog.setFixedSize(640, 520)` (`intruder_log.py:41,151`).
- `CalculatorDisguise`: `card.setFixedSize(360, 480)`, `btn.setFixedSize(68, 52)` / `(144, 52)` (`calculator_disguise.py:137,187,229`).
- `VideoPlayer`: `_time_lbl.setFixedWidth(96)` risks truncating `H:MM:SS / H:MM:SS` at larger DPI (`video_player.py:2022`).
- `Dashboard._fab_add.setFixedSize(142, 42)` risks label clipping at >125% DPI (`dashboard.py:1706`).

---

## 4. Per-File Detailed Findings

### `mylocker/app/ui/__init__.py`

### `mylocker/app/ui/applock_ui.py`

**Inline `setStyleSheet` calls:**
- `L94`: `header.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")`
- `L105`: `desc.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 13px; background: transparent;")`
- `L111`: `opts_card.setStyleSheet(f"""`
- `L130`: `grace_lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent;")`
- `L172`: `self.table.setStyleSheet(f"""`

**Font declarations (3):**
- `QFont("Segoe UI", 12)`
- `QFont("Segoe UI", 20, QFont.Weight.Bold)`
- `font-size: 13px`

**Fixed dimensions (2):**
- `L134`: `.setFixedWidth(60)`
- `L201`: `.setFixedSize(80, 28)`

**Raw Qt modal calls (13):**
- `L21`: `QFileDialog,`
- `L26`: `QMessageBox,`
- `L214`: `path, _ = QFileDialog.getOpenFileName(self, "Select Executable to Lock", "", "Executables & Shortcuts (*.exe *.lnk)")`
- `L227`: `QMessageBox.information(self, "Running Apps", "No additional user apps detected.")`
- `L231`: `from PySide6.QtWidgets import QInputDialog`
- `L232`: `item, ok = QInputDialog.getItem(self, "Protect Running Application", "Select an active process to lock:", names, 0, Fals`
- `L274`: `QMessageBox.information(self, "Installed Apps", "No installed apps catalogued.")`
- `L278`: `from PySide6.QtWidgets import QInputDialog`
- `L279`: `item, ok = QInputDialog.getItem(self, "Protect Installed App", "Select an installed app:", names, 0, False)`
- `L302`: `from PySide6.QtWidgets import QInputDialog, QLineEdit`
- `L303`: `pw, ok = QInputDialog.getText(`
- `L311`: `QMessageBox.warning(self, "Too Short", "App Lock password must be at least 4 characters.")`
- `L315`: `QMessageBox.information(self, "App Lock Password Set", "Dedicated App Lock password saved.")`

**Raw emoji literals (6):**
- `L95` (`🛡`): `self.status_badge = StatusBadge("🛡️ Active", Colors.SUCCESS)`
- `L143` (`🔍`): `scan_running_btn = QPushButton("🔍 Scan Running Apps")`
- `L146` (`📂`): `self._scan_installed_btn = QPushButton("📂 Browse Installed")`
- `L149` (`🔑`): `set_pw_btn = QPushButton("🔑 Set App Lock PIN")`
- `L189` (`🔒`): `name_item = QTableWidgetItem(f"🔒 {exe}")`
- `L272` (`📂`): `self._scan_installed_btn.setText("📂 Browse Installed")`

### `mylocker/app/ui/calculator_disguise.py`

**Inline `setStyleSheet` calls:**
- `L130`: `self.setStyleSheet(f"background-color: {Colors.BG_DARKEST};")`
- `L138`: `card.setStyleSheet(f"""`
- `L152`: `title.setStyleSheet(f"color: {Colors.TEXT_MUTED}; background: transparent;")`
- `L161`: `self.display.setStyleSheet(f"""`
- `L191`: `btn.setStyleSheet(f"""`
- `L202`: `btn.setStyleSheet(f"""`
- `L214`: `btn.setStyleSheet(f"""`

**Font declarations (3):**
- `QFont("Segoe UI", 12, QFont.Weight.Bold)`
- `QFont("Segoe UI", 14, QFont.Weight.Medium)`
- `QFont("Segoe UI", 26)`

**Fixed dimensions (4):**
- `L137`: `.setFixedSize(360, 480)`
- `L160`: `.setFixedHeight(64)`
- `L187`: `.setFixedSize(68 if text != "0" else 68, 52)`
- `L229`: `.setFixedSize(144, 52)`

### `mylocker/app/ui/dashboard.py`

**Inline `setStyleSheet` calls:**
- `L234`: `self.setStyleSheet(_FILECARD_STYLE)`
- `L258`: `self._video_play_badge.setStyleSheet(`
- `L272`: `self._video_duration_badge.setStyleSheet(`
- `L287`: `bottom_widget.setStyleSheet("background: transparent;")`
- `L1003`: `central.setStyleSheet(f"background: {DashColors.BG_DARKEST};")`
- `L1017`: `div.setStyleSheet(f"background: {DashColors.BORDER_SUBTLE};")`
- `L1022`: `content.setStyleSheet(f"background: {DashColors.BG_CONTENT};")`
- `L1048`: `self._status_bar.setStyleSheet(f"""`
- `L1145`: `nav_div.setStyleSheet(f"background: {DashColors.BORDER_SUBTLE};")`
- `L1182`: `lock_btn.setStyleSheet(f"""`
- `L1209`: `bar.setStyleSheet(f"""`
- `L1223`: `self._page_title.setStyleSheet(f"color: {DashColors.TEXT_PRIMARY}; background: transparent;")`
- `L1249`: `self._select_btn.setStyleSheet(f"""`
- `L1278`: `self._ss_btn.setStyleSheet(f"""`
- `L1301`: `page.setStyleSheet(f"background: {DashColors.BG_CONTENT};")`
- `L1317`: `welcome_sub.setStyleSheet(f"color: {DashColors.TEXT_SECONDARY}; background: transparent;")`
- `L1322`: `welcome_title.setStyleSheet(f"color: {DashColors.TEXT_PRIMARY}; background: transparent; letter-spacing: -0.5px;")`
- `L1327`: `welcome_desc.setStyleSheet(f"color: {DashColors.TEXT_MUTED}; background: transparent;")`
- `L1382`: `self._btn_grid_mode.setStyleSheet("""`
- `L1394`: `self._btn_list_mode.setStyleSheet("""`
- `L1420`: `self._section_title_lbl.setStyleSheet(f"color: {DashColors.TEXT_PRIMARY}; background: transparent;")`
- `L1425`: `self._section_sub_lbl.setStyleSheet(f"color: {DashColors.TEXT_MUTED}; background: transparent;")`
- `L1435`: `self._sort_combo.setStyleSheet(f"""`
- `L1466`: `self._filter_btn.setStyleSheet(f"""`
- `L1484`: `self._speed_banner.setStyleSheet("""`
- `L1498`: `sb_icon.setStyleSheet("background: transparent;")`
- `L1505`: `self._speed_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 12px; background: transparent;")`
- `L1513`: `self._optimize_btn.setStyleSheet(f"""`
- `L1547`: `self._hotkey_banner.setStyleSheet("""`
- `L1561`: `hb_icon.setStyleSheet("background: transparent;")`
- `L1566`: `self._hotkey_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 12px; background: transparent;")`
- `L1582`: `self._selection_bar.setStyleSheet("""`
- `L1596`: `self._sel_count_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")`
- `L1616`: `self._sel_delete_btn.setStyleSheet(f"""`
- `L1651`: `self._drop_hint.setStyleSheet("""`
- `L1666`: `drop_icon.setStyleSheet("background: transparent;")`
- `L1671`: `drop_text.setStyleSheet(f"color: {DashColors.TEXT_MUTED}; background: transparent; line-height: 1.6;")`
- `L1683`: `self._grid_container.setStyleSheet("background: transparent;")`
- `L1708`: `self._fab_add.setStyleSheet(f"""`
- `L1767`: `page.setStyleSheet(f"background: {Colors.BG_DARK};")`
- `L1773`: `lbl.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 14px; background: transparent;")`
- `L1782`: `page.setStyleSheet(f"background: {Colors.BG_DARK};")`
- `L1788`: `lbl.setStyleSheet(f"color: {Colors.TEXT_MUTED}; background: transparent;")`
- `L1923`: `menu.setStyleSheet(f"""`
- `L2596`: `self._drop_hint.setStyleSheet(f"""`
- `L2610`: `self._drop_hint.setStyleSheet(f"""`

**Hardcoded color literals (45 occurrences, 37 unique):**
- `rgba(0, 0, 0, 0.78)`, `rgba(10, 14, 26, 0.76)`, `rgba(125, 160, 220, 0.08)`, `rgba(125, 160, 220, 0.12)`, `rgba(125, 160, 220, 0.22)`, `rgba(125, 160, 220, 0.25)`, `rgba(125, 160, 220, 0.35)`, `rgba(139, 156, 255, 0.08)`, `rgba(139, 156, 255, 0.10)`, `rgba(139, 156, 255, 0.12)`, `rgba(139, 156, 255, 0.35)`, `rgba(15, 19, 32, 0.78)`, `rgba(15, 19, 32, 0.92)`, `rgba(16, 27, 48, 0.50)`, `rgba(239, 68, 68, 0.12)`, `rgba(239, 68, 68, 0.35)`, `rgba(24, 37, 62, 0.65)`, `rgba(24, 37, 62, 0.85)`, `rgba(255, 111, 134, 0.15)`, `rgba(255, 111, 134, 0.35)`, `rgba(255, 113, 131, 0.18)`, `rgba(255, 113, 131, 0.30)`, `rgba(255, 113, 131, 0.45)`, `rgba(255, 255, 255, 0.04)`, `rgba(255, 255, 255, 0.06)`, `rgba(255, 255, 255, 0.08)`, `rgba(255, 255, 255, 0.22)`, `rgba(255, 255, 255, 0.28)`, `rgba(255, 255, 255, 0.45)`, `rgba(255, 255, 255, 0.55)` …

**Font declarations (41):**
- `QFont("Segoe UI Emoji, Segoe UI", 36)`
- `QFont("Segoe UI Variable", 10)`
- `QFont("Segoe UI Variable", 11, QFont.Weight.Normal)`
- `QFont("Segoe UI Variable", 12, QFont.Weight.DemiBold)`
- `QFont("Segoe UI Variable", 12, QFont.Weight.Medium)`
- `QFont("Segoe UI Variable", 13)`
- `QFont("Segoe UI Variable", 14, QFont.Weight.Bold)`
- `QFont("Segoe UI Variable", 15, QFont.Weight.Bold)`
- `QFont("Segoe UI Variable", 26, QFont.Weight.Bold)`
- `QFont("Segoe UI Variable", 8)`
- `QFont("Segoe UI Variable", 9, QFont.Weight.DemiBold)`
- `font-family: `
- `font-size: 10px`
- `font-size: 11px`
- `font-size: 12px`
- `font-size: 13px`
- `font-size: 14px`
- `font-size: 8px`
- `font-weight: 500`
- `font-weight: 600`

**Fixed dimensions (27):**
- `L167`: `.setFixedWidth(260)`
- `L232`: `.setFixedSize(182, 124)`
- `L238`: `.setFixedSize(168, 70)`
- `L245`: `.setFixedSize(168, 70)`
- `L254`: `.setFixedSize(28, 28)`
- `L268`: `.setFixedSize(38, 16)`
- `L319`: `.setFixedSize(20, 20)`
- `L330`: `.setFixedSize(20, 20)`
- `L939`: `.setMinimumSize(1000, 650)`
- `L1016`: `.setFixedWidth(1)`
- `L1102`: `.setFixedWidth(260)`
- `L1144`: `.setFixedHeight(1)`
- `L1208`: `.setFixedHeight(64)`
- `L1245`: `.setFixedHeight(36)`
- `L1275`: `.setFixedHeight(36)`
- `L1379`: `.setFixedSize(36, 36)`
- `L1391`: `.setFixedSize(36, 36)`
- `L1434`: `.setFixedHeight(34)`
- `L1464`: `.setFixedSize(34, 34)`
- `L1512`: `.setFixedHeight(32)`
- `L1536`: `.setFixedHeight(30)`
- `L1571`: `.setFixedHeight(30)`
- `L1602`: `.setFixedHeight(32)`
- `L1608`: `.setFixedHeight(32)`
- `L1614`: `.setFixedHeight(32)`
- … and 2 more

**Raw Qt modal calls (29):**
- `L42`: `QFileDialog,`
- `L50`: `QMessageBox,`
- `L2075`: `QMessageBox.critical(`
- `L2082`: `from PySide6.QtWidgets import QInputDialog`
- `L2084`: `choice, ok = QInputDialog.getItem(`
- `L2101`: `QMessageBox.information(self, "Recovered", f"Vault index successfully recovered from {choice}.")`
- `L2103`: `QMessageBox.critical(`
- `L2341`: `reply = QMessageBox.question(`
- `L2345`: `QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,`
- `L2347`: `if reply != QMessageBox.StandardButton.Yes:`
- `L2363`: `QMessageBox.critical(self, "Error", "Failed to delete selected files. [ERR-DEL-02]")`
- `L2398`: `QMessageBox.critical(self, "Vault Error", f"Operation failed [{err_code}]:\n{error_msg}")`
- `L2401`: `paths, _ = QFileDialog.getOpenFileNames(self, "Select Files to Encrypt")`
- `L2406`: `folder = QFileDialog.getExistingDirectory(self, "Select Folder to Encrypt")`
- `L2420`: `dest = QFileDialog.getExistingDirectory(self, "Restore to…")`
- `L2430`: `reply = QMessageBox.question(`
- `L2433`: `QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel`
- `L2435`: `if reply == QMessageBox.StandardButton.Yes:`
- `L2444`: `QMessageBox.critical(self, "Error", "Failed to delete file from vault. [ERR-DEL-01]")`
- `L2502`: `QMessageBox.critical(self, "Error", "Could not open image in viewer. [ERR-GAL-01]")`
- `L2508`: `QMessageBox.critical(self, "Error", "Could not open image in viewer. [ERR-GAL-01]")`
- `L2520`: `QMessageBox.critical(self, "Error", "Could not open video in player. [ERR-VID-01]")`
- `L2536`: `QMessageBox.warning(self, "Cannot Open File", "Unable to open this file with its default application. [ERR-EXT-01]")`
- `L2542`: `QMessageBox.critical(self, "Error", "Failed to open external file. [ERR-EXT-02]")`
- `L2560`: `reply = QMessageBox.question(`
- `L2564`: `QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,`
- `L2566`: `if reply == QMessageBox.StandardButton.Yes and file_path.exists():`
- `L2574`: `QMessageBox.information(self, "Updated", f"'{name}' successfully updated in vault.")`
- `L2576`: `QMessageBox.warning(self, "Save Failed", "Could not update file in vault. [ERR-UPD-01]")`

**Raw emoji literals (3):**
- `L297` (`★`): `prefix = "★ " if self._entry.is_favorite else ""`
- `L1074` (`📸`): `self._intruder_ui = self._build_placeholder("Intruder Log", "📸")`
- `L1097` (`⚙`): `self._settings_ui = self._build_placeholder("Settings", "⚙️")`

### `mylocker/app/ui/dashboard_theme.py`

**Inline `setStyleSheet` calls:**
- `L184`: `self.setStyleSheet(f"""`
- `L204`: `self._icon_box.setStyleSheet(f"""`
- `L219`: `self._val_lbl.setStyleSheet(f"color: {DashColors.TEXT_PRIMARY}; background: transparent;")`
- `L224`: `self._lbl.setStyleSheet(f"color: {DashColors.TEXT_SECONDARY}; background: transparent;")`
- `L273`: `self.setStyleSheet("""`
- `L289`: `self.setStyleSheet(f"""`
- `L519`: `self._title_lbl.setStyleSheet(f"color: {DashColors.TEXT_PRIMARY}; background: transparent;")`
- `L523`: `self._sub_lbl.setStyleSheet(f"color: {DashColors.TEXT_MUTED}; background: transparent;")`
- `L545`: `shield_icon.setStyleSheet("background: transparent;")`
- `L550`: `text_lbl.setStyleSheet(f"color: {DashColors.TEXT_SECONDARY}; background: transparent;")`
- `L556`: `self._dot.setStyleSheet(f"color: {DashColors.ACCENT_GREEN}; background: transparent;")`
- `L569`: `self.setStyleSheet("""`
- `L584`: `icon_box.setStyleSheet("""`
- `L599`: `title.setStyleSheet(f"color: {DashColors.TEXT_PRIMARY}; background: transparent;")`
- `L604`: `subtitle.setStyleSheet(f"color: {DashColors.ACCENT_GREEN}; background: transparent;")`
- `L622`: `self.setStyleSheet(f"""`
- `L643`: `icon_lbl.setStyleSheet("background: transparent;")`
- `L648`: `self._input.setStyleSheet(f"""`
- `L664`: `badge.setStyleSheet(f"""`
- `L699`: `self.setStyleSheet(f"""`

**Hardcoded color literals (63 occurrences, 50 unique):**
- `#060B16`, `#06B6D4`, `#070B14`, `#08101F`, `#091122`, `#0A0F1D`, `#0B1120`, `#0E1626`, `#10B981`, `#2563EB`, `#39D6A2`, `#43D9C5`, `#475569`, `#4C65E8`, `#5B8CFF`, `#5C75F8`, `#637EFF`, `#64748B`, `#6E7D96`, `#70A7FF`, `#738EFF`, `#8B5CF6`, `#A66CFF`, `#AAB7CC`, `#EF4444`, `#F4C76B`, `#F4F7FF`, `#F59E0B`, `#F97316`, `#FF6F86` …

**Font declarations (15):**
- `QFont("Segoe UI Variable", 10, QFont.Weight.Medium)`
- `QFont("Segoe UI Variable", 11, QFont.Weight.Bold)`
- `QFont("Segoe UI Variable", 11, QFont.Weight.Medium)`
- `QFont("Segoe UI Variable", 13, QFont.Weight.Bold)`
- `QFont("Segoe UI Variable", 17, QFont.Weight.Bold)`
- `QFont("Segoe UI Variable", 9, QFont.Weight.DemiBold)`
- `QFont("Segoe UI Variable", 9, QFont.Weight.Medium)`
- `QFont("Segoe UI Variable", f_size, QFont.Weight.Bold)`
- `font-family: `
- `font-size: 13px`
- `font-weight: 500`
- `font-weight: 600`

**Fixed dimensions (14):**
- `L84`: `.setFixedHeight(36)`
- `L181`: `.setFixedHeight(94)`
- `L182`: `.setMinimumWidth(180)`
- `L202`: `.setFixedSize(42, 42)`
- `L232`: `.setFixedWidth(85)`
- `L257`: `.setFixedHeight(36)`
- `L497`: `.setFixedHeight(56)`
- `L503`: `.setFixedSize(34, 34)`
- `L538`: `.setFixedHeight(40)`
- `L568`: `.setFixedHeight(50)`
- `L582`: `.setFixedSize(30, 30)`
- `L619`: `.setFixedHeight(44)`
- `L620`: `.setFixedWidth(420)`
- `L696`: `.setFixedSize(40, 40)`

### `mylocker/app/ui/gallery.py`

**Inline `setStyleSheet` calls:**
- `L81`: `self.setStyleSheet(f"background: {Colors.BG_DARKEST};")`
- `L162`: `self.setStyleSheet(f"background: {Colors.BG_DARKEST};")`
- `L192`: `toolbar.setStyleSheet(f"background: {Colors.GLASS}; border-bottom: 1px solid {Colors.BORDER};")`
- `L197`: `self._counter_lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent;")`
- `L229`: `self._img_scroll.setStyleSheet(f"background: {Colors.BG_DARKEST};")`
- `L237`: `nav.setStyleSheet(f"background: {Colors.GLASS}; border-top: 1px solid {Colors.BORDER};")`
- `L251`: `self._name_lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent;")`
- `L313`: `self._img_lbl.setStyleSheet(f"background: {Colors.BG_DARKEST}; color: {Colors.TEXT_MUTED};")`
- `L335`: `self._img_lbl.setStyleSheet(f"background: {Colors.BG_DARKEST}; color: {Colors.DANGER};")`
- `L356`: `self._img_lbl.setStyleSheet(f"background: {Colors.BG_DARKEST}; color: {Colors.TEXT_MUTED};")`
- `L360`: `self._img_lbl.setStyleSheet(f"background: {Colors.BG_DARKEST};")`

**Fixed dimensions (8):**
- `L98`: `.setFixedSize(scaled.size()`
- `L191`: `.setFixedHeight(50)`
- `L236`: `.setFixedHeight(60)`
- `L242`: `.setFixedWidth(130)`
- `L246`: `.setFixedWidth(130)`
- `L311`: `.setFixedSize(400, 120)`
- `L333`: `.setFixedSize(400, 120)`
- `L354`: `.setFixedSize(400, 120)`

**Raw emoji literals (3):**
- `L199` (`✕`): `close_btn = QPushButton("✕ Close")`
- `L207` (`🔍`): `zoom_in = QPushButton("🔍+")`
- `L211` (`🔍`): `zoom_out = QPushButton("🔍-")`

### `mylocker/app/ui/icons.py`

### `mylocker/app/ui/intruder_log.py`

**Inline `setStyleSheet` calls:**
- `L40`: `self.setStyleSheet(f"background: {Colors.BG_DARKEST}; color: {Colors.TEXT_PRIMARY};")`
- `L56`: `lbl.setStyleSheet(f"color: {Colors.TEXT_MUTED};")`
- `L83`: `header.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")`
- `L99`: `desc.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 13px; background: transparent;")`
- `L113`: `self.table.setStyleSheet(f"""`

**Font declarations (2):**
- `QFont("Segoe UI", 20, QFont.Weight.Bold)`
- `font-size: 13px`

**Fixed dimensions (2):**
- `L41`: `.setFixedSize(640, 520)`
- `L151`: `.setFixedSize(90, 28)`

**Raw Qt modal calls (5):**
- `L18`: `QMessageBox,`
- `L165`: `QMessageBox.warning(self, "Photo Missing", "The capture file was moved or deleted.")`
- `L168`: `res = QMessageBox.question(`
- `L172`: `QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,`
- `L174`: `if res == QMessageBox.StandardButton.Yes:`

**Raw emoji literals (2):**
- `L88` (`🗑`): `clear_btn = QPushButton("🗑️ Clear Log")`
- `L144` (`📸`): `status_item = QTableWidgetItem("📸 Captured" if has_photo else "No Photo")`

### `mylocker/app/ui/login.py`

**Inline `setStyleSheet` calls:**
- `L181`: `self.setStyleSheet(`
- `L196`: `icon_lbl.setStyleSheet("background: transparent;")`
- `L199`: `title_lbl.setStyleSheet(f"color: {LoginColors.TEXT_PRIMARY}; background: transparent;")`
- `L211`: `sub_lbl.setStyleSheet(f"color: {LoginColors.TEXT_SECONDARY}; background: transparent;")`
- `L221`: `self._key_input.setStyleSheet(f"""`
- `L245`: `self._error_lbl.setStyleSheet(f"color: {LoginColors.ERROR}; font-size: 12px; background: transparent;")`
- `L378`: `self.setStyleSheet(`
- `L407`: `icon_lbl.setStyleSheet("background: transparent;")`
- `L410`: `title_lbl.setStyleSheet(f"color: {LoginColors.TEXT_PRIMARY}; background: transparent;")`
- `L422`: `sub_lbl.setStyleSheet(f"color: {LoginColors.TEXT_SECONDARY}; background: transparent;")`
- `L427`: `rec_lbl.setStyleSheet(f"color: {LoginColors.TEXT_MUTED}; font-size: 12px; background: transparent;")`
- `L435`: `self._key_input.setStyleSheet(f"""`
- `L464`: `self._pw_container.setStyleSheet("background: transparent;")`
- `L501`: `self._kf_row_w.setStyleSheet("background: transparent;")`
- `L524`: `self._error_lbl.setStyleSheet(f"color: {LoginColors.ERROR}; font-size: 12px; background: transparent;")`
- `L730`: `self.setStyleSheet(f"#LoginScreen {{ background: {LoginColors.BG_PRIMARY}; }}")`
- `L748`: `self._scroll.setStyleSheet(`
- `L752`: `self._scroll.viewport().setStyleSheet("background: transparent;")`
- `L755`: `self._stage.setStyleSheet("background: transparent;")`
- `L780`: `self._title_lbl.setStyleSheet(`
- `L791`: `self._subtitle_lbl.setStyleSheet(`
- `L822`: `pw_ico.setStyleSheet("background: transparent;")`
- `L825`: `pw_title.setStyleSheet(f"color: {LoginColors.TEXT_TITLE}; background: transparent; font-size: 14px; font-weight: 600;")`
- `L846`: `kf_ico.setStyleSheet("background: transparent;")`
- `L849`: `self._kf_title_lbl.setStyleSheet(f"color: {LoginColors.TEXT_TITLE}; background: transparent; font-size: 14px; font-weigh`
- `L868`: `self._error_lbl.setStyleSheet(f"color: {LoginColors.ERROR}; background: transparent;")`
- `L889`: `self._recovery_btn.setStyleSheet(f"""`
- `L912`: `self._switch_btn.setStyleSheet("""`
- `L990`: `self._title_lbl.setStyleSheet(`
- `L995`: `self._subtitle_lbl.setStyleSheet(`
- `L1291`: `self._error_lbl.setStyleSheet(f"color: {LoginColors.ERROR}; background: transparent;")`
- `L1296`: `self._error_lbl.setStyleSheet(f"color: {LoginColors.WARNING}; background: transparent;")`

**Hardcoded color literals (2 occurrences, 1 unique):**
- `rgba(110, 150, 190, 0.28)`

**Font declarations (24):**
- `QFont("Cascadia Code, Consolas, Courier New", 11)`
- `font-family: `
- `font-size: 11px`
- `font-size: 12px`
- `font-size: 13px`
- `font-size: 14px`
- `font-size: 26px`
- `font-size: {sub_fs}px`
- `font-size: {title_fs}px`
- `font-weight: 400`
- `font-weight: 500`
- `font-weight: 600`

**Fixed dimensions (20):**
- `L180`: `.setFixedSize(520, 360)`
- `L220`: `.setFixedHeight(44)`
- `L238`: `.setFixedHeight(44)`
- `L260`: `.setMinimumWidth(190)`
- `L377`: `.setMinimumWidth(520)`
- `L434`: `.setFixedHeight(42)`
- `L451`: `.setFixedHeight(42)`
- `L472`: `.setFixedHeight(42)`
- `L483`: `.setFixedHeight(42)`
- `L506`: `.setFixedHeight(36)`
- `L508`: `.setFixedHeight(36)`
- `L729`: `.setMinimumSize(460, 560)`
- `L799`: `.setFixedHeight(10)`
- `L800`: `.setFixedWidth(240)`
- `L810`: `.setFixedWidth(428)`
- `L820`: `.setFixedSize(16, 16)`
- `L844`: `.setFixedSize(16, 16)`
- `L943`: `.setFixedHeight(24)`
- `L977`: `.setFixedWidth(target_card_w)`
- `L978`: `.setFixedWidth(min(320, max(220, target_card_w - 60)`

**Raw Qt modal calls (3):**
- `L38`: `QFileDialog,`
- `L555`: `path, _ = QFileDialog.getOpenFileName(self, "Select USB Key File")`
- `L1122`: `path, _ = QFileDialog.getOpenFileName(self, "Select Key File")`

**Raw emoji literals (2):**
- `L651` (`⚠`): `self._error_lbl.setText(f"⚠ {msg}")`
- `L683` (`⚠`): `self._error_lbl.setText(f"⚠ {msg}")`

### `mylocker/app/ui/login_theme.py`

**Inline `setStyleSheet` calls:**
- `L864`: `self._lock_icon.setStyleSheet("background: transparent;")`
- `L878`: `self._eye_btn.setStyleSheet("""`
- `L909`: `self.line_edit.setStyleSheet("""`
- `L926`: `self.line_edit.setStyleSheet(f"""`
- `L1041`: `self._file_icon.setStyleSheet("background: transparent;")`
- `L1045`: `self._status_lbl.setStyleSheet(f"color: {LoginColors.TEXT_SECONDARY}; background: transparent;")`
- `L1059`: `self.browse_btn.setStyleSheet(f"""`
- `L1095`: `self._status_lbl.setStyleSheet(f"color: {LoginColors.ICE_BLUE}; {base_f}")`
- `L1100`: `self._status_lbl.setStyleSheet(f"color: {LoginColors.SUCCESS}; {base_f}")`
- `L1105`: `self._status_lbl.setStyleSheet(f"color: {LoginColors.TEXT_PRIMARY}; {base_f}")`
- `L1109`: `self._status_lbl.setStyleSheet(f"color: {LoginColors.ERROR}; {base_f}")`
- `L1114`: `self._status_lbl.setStyleSheet(f"color: {LoginColors.TEXT_SECONDARY}; {base_f}")`
- `L1166`: `self.setStyleSheet(f"""`
- `L1399`: `self._shield_lbl.setStyleSheet("background: transparent;")`
- `L1403`: `self._text_lbl.setStyleSheet(f"color: {LoginColors.STATUS_TEXT}; background: transparent;")`
- `L1431`: `self._text_lbl.setStyleSheet(f"color: {LoginColors.STATUS_TEXT}; {base_f}")`
- `L1440`: `self._text_lbl.setStyleSheet(f"color: {LoginColors.TEXT_MUTED}; {base_f}")`

**Hardcoded color literals (73 occurrences, 42 unique):**
- `#00D2FF`, `#04080F`, `#047857`, `#050A12`, `#070E18`, `#07101B`, `#08111D`, `#0B1726`, `#0D47A1`, `#10B981`, `#22D3EE`, `#2563EB`, `#34D399`, `#38BDF8`, `#3B8DFF`, `#4FE3B0`, `#58A6FF`, `#64748B`, `#6F9BC8`, `#6FA8FF`, `#70C8FF`, `#70D6FF`, `#83C7FF`, `#8FA0B8`, `#8FC8FF`, `#92BBEA`, `#B7DFFF`, `#CBD5E1`, `#E2F1FF`, `#F0F5FC` …

**Font declarations (13):**
- `QFont("Arial")`
- `QFont(cls.FAMILY)`
- `font-family: `
- `font-family: Arial, sans-serif`
- `font-size: 12px`
- `font-size: 13px`
- `font-size: 14px`
- `font-size: 15px`
- `font-size: 22px`
- `font-weight: 500`
- `font-weight: bold`

**Fixed dimensions (15):**
- `L477`: `.setFixedSize(130, 84)`
- `L654`: `.setFixedHeight(16)`
- `L728`: `.setMinimumWidth(360)`
- `L729`: `.setMaximumWidth(452)`
- `L844`: `.setFixedHeight(54)`
- `L861`: `.setFixedSize(16, 16)`
- `L872`: `.setFixedSize(28, 28)`
- `L1028`: `.setFixedHeight(52)`
- `L1038`: `.setFixedSize(16, 16)`
- `L1046`: `.setMinimumWidth(60)`
- `L1058`: `.setFixedHeight(26)`
- `L1165`: `.setFixedHeight(height)`
- `L1385`: `.setFixedHeight(24)`
- `L1398`: `.setFixedSize(18, 18)`
- `L1458`: `.setFixedSize(16, 16)`

### `mylocker/app/ui/settings.py`

**Inline `setStyleSheet` calls:**
- `L141`: `header.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")`
- `L147`: `container.setStyleSheet("background: transparent;")`
- `L158`: `idle_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")`
- `L195`: `pin_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")`
- `L216`: `decoy_desc.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px; background: transparent;")`
- `L287`: `destruct_desc.setStyleSheet(f"color: {Colors.DANGER}; font-size: 12px; background: transparent;")`
- `L297`: `destruct_btn.setStyleSheet(f"""`
- `L323`: `card.setStyleSheet(f"""`
- `L340`: `icon_lbl.setStyleSheet("background: transparent;")`
- `L345`: `lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")`
- `L491`: `dlg.setStyleSheet(f"background: {Colors.BG_CARD}; color: {Colors.TEXT_PRIMARY};")`
- `L588`: `dlg.setStyleSheet(f"background: {Colors.BG_CARD}; color: {Colors.TEXT_PRIMARY};")`
- `L648`: `dlg.setStyleSheet(f"background: {Colors.BG_CARD}; color: {Colors.TEXT_PRIMARY};")`
- `L659`: `info.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px; background: transparent;")`
- `L820`: `dlg.setStyleSheet(f"background: {Colors.BG_CARD}; color: {Colors.TEXT_PRIMARY};")`
- `L830`: `info.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px; background: transparent;")`
- `L836`: `key_lbl.setStyleSheet(`
- `L882`: `dlg.setStyleSheet(f"background: {Colors.BG_CARD}; color: {Colors.TEXT_PRIMARY};")`
- `L894`: `warn_lbl.setStyleSheet(f"color: {Colors.DANGER}; font-size: 12px; font-weight: bold;")`
- `L899`: `confirm_input.setStyleSheet(f"padding: 8px; border: 1px solid {Colors.DANGER}; border-radius: 6px;")`
- `L907`: `destroy_btn.setStyleSheet(f"background-color: {Colors.DANGER}; color: {Colors.WHITE}; font-weight: bold; padding: 8px 16`

**Hardcoded color literals (1 occurrences, 1 unique):**
- `rgba(255, 113, 131, 0.15)`

**Font declarations (11):**
- `QFont("Cascadia Code, Consolas, Courier New", 11, QFont.Weight.Bold)`
- `QFont("Segoe UI Variable", 14, QFont.Weight.Bold)`
- `QFont("Segoe UI Variable", 20, QFont.Weight.Bold)`
- `font-size: 12px`
- `font-weight: 600`
- `font-weight: bold`

**Fixed dimensions (12):**
- `L162`: `.setFixedWidth(70)`
- `L197`: `.setFixedWidth(100)`
- `L222`: `.setFixedWidth(220)`
- `L233`: `.setFixedWidth(220)`
- `L239`: `.setFixedWidth(260)`
- `L246`: `.setFixedWidth(260)`
- `L311`: `.setFixedWidth(280)`
- `L490`: `.setFixedSize(420, 270)`
- `L587`: `.setFixedSize(380, 220)`
- `L647`: `.setFixedSize(440, 260)`
- `L819`: `.setFixedSize(520, 290)`
- `L881`: `.setFixedSize(440, 260)`

**Raw Qt modal calls (44):**
- `L24`: `QFileDialog,`
- `L27`: `QInputDialog,`
- `L30`: `QMessageBox,`
- `L371`: `QMessageBox.critical(self, "Operation Failed", f"{msg} [ERR-SET-01]")`
- `L379`: `reply = QMessageBox.warning(`
- `L385`: `QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,`
- `L387`: `if reply != QMessageBox.StandardButton.Yes:`
- `L395`: `save_path, _ = QFileDialog.getSaveFileName(`
- `L410`: `QMessageBox.information(self, "Backup Complete", "Encrypted backup created successfully.")`
- `L412`: `QMessageBox.critical(self, "Backup Error", "Failed to create encrypted backup. Please check destination permissions and `
- `L417`: `file_path, _ = QFileDialog.getOpenFileName(`
- `L423`: `reply = QMessageBox.warning(`
- `L427`: `QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,`
- `L429`: `if reply != QMessageBox.StandardButton.Yes:`
- `L432`: `pw, pw_ok = QInputDialog.getText(`
- `L446`: `chosen_kf, _ = QFileDialog.getOpenFileName(self, "Select Key File (Optional)")`
- `L466`: `QMessageBox.information(self, "Restore Succeeded", "Vault restored. Please lock and re-login.")`
- `L469`: `QMessageBox.critical(self, "Restore Error", "Failed to restore backup. Check your password and archive integrity, then t`
- `L481`: `QMessageBox.information(self, "Integrity Check", f"All {total} files verified successfully! No corruption found.")`
- `L483`: `QMessageBox.warning(self, "Corruption Detected", f"{len(failures)} out of {total} files failed integrity verification!")`
- `L525`: `QMessageBox.warning(dlg, "Weak Password", pw_msg)`
- `L528`: `QMessageBox.warning(dlg, "Error", "New passwords do not match.")`
- `L535`: `kf_path, _ = QFileDialog.getOpenFileName(dlg, "Select Current USB Key File")`
- `L539`: `QMessageBox.critical(dlg, "Error", "Could not read keystore configuration. [ERR-PW-01]")`
- `L566`: `QMessageBox.information(dlg, "Success", "Master password updated successfully.")`
- `L573`: `QMessageBox.critical(dlg, "Error", "Failed to change password. Check current credentials and try again. [ERR-PW-01]")`
- `L614`: `QMessageBox.warning(dlg, "Error", "Passwords must match and not be empty.")`
- `L626`: `QMessageBox.information(dlg, "Decoy Active", "Decoy password configured. Logging in with it opens a fake safe.")`
- `L633`: `QMessageBox.critical(dlg, "Error", "Could not setup decoy password. Please try again. [ERR-DEC-01]")`
- `L680`: `QMessageBox.warning(dlg, "Password Required", "Please enter your current master password.")`
- `L687`: `kf_path, _ = QFileDialog.getOpenFileName(dlg, "Select Current USB Key File")`
- `L691`: `QMessageBox.critical(dlg, "Error", "Could not read keystore configuration. [ERR-OPT-01]")`
- `L712`: `QMessageBox.information(`
- `L724`: `QMessageBox.critical(`
- `L740`: `pw, ok = QInputDialog.getText(`
- `L754`: `kf_path, _ = QFileDialog.getOpenFileName(self, "Select Current USB Key File")`
- `L788`: `QMessageBox.critical(self, "Authentication Failed", "Incorrect master password or key file. [ERR-ROT-01]")`
- `L804`: `QMessageBox.information(`
- `L811`: `QMessageBox.critical(self, "Error", "Key rotation failed. Your original vault remains intact. [ERR-ROT-02]")`
- `L851`: `path, _ = QFileDialog.getSaveFileName(`
- `L863`: `QMessageBox.warning(dlg, "Save Error", "Could not save recovery key file. [ERR-ROT-03]")`
- `L911`: `QMessageBox.warning(dlg, "Confirmation Failed", "You must type 'DESTROY MY VAULT' exactly to confirm.")`
- `L915`: `QMessageBox.critical(dlg, "Vault Destroyed", "All key material has been wiped. The vault is now permanently locked.")`
- `L919`: `QMessageBox.critical(dlg, "Error", "Self-destruct encountered an error. [ERR-DESTRUCT-01]")`

**Raw emoji literals (2):**
- `L888` (`⚠`): `"⚠️ PERMANENT VAULT DESTRUCTION ⚠️\n\n"`
- `L888` (`⚠`): `"⚠️ PERMANENT VAULT DESTRUCTION ⚠️\n\n"`

### `mylocker/app/ui/setup_wizard.py`

**Inline `setStyleSheet` calls:**
- `L87`: `self.setStyleSheet(f"background: {Colors.BG_DARKEST};")`
- `L124`: `card.setStyleSheet(f"""`
- `L138`: `page.setStyleSheet(f"background: {Colors.BG_DARKEST};")`
- `L176`: `self._bl_lbl.setStyleSheet(`
- `L207`: `self._bl_lbl.setStyleSheet(`
- `L310`: `self._kf_check.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")`
- `L369`: `self._proc_status.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 13px; background: transparent;")`
- `L435`: `self._rec_display.setStyleSheet(f"""`
- `L470`: `ack_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 12px; background: transparent;")`
- `L500`: `self._verify_subtext.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent;")`
- `L510`: `lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")`
- `L604`: `d.setStyleSheet(f"color: {Colors.ACCENT}; background: transparent;")`
- `L606`: `d.setStyleSheet(f"color: {Colors.SUCCESS}; background: transparent;")`
- `L608`: `d.setStyleSheet(f"color: {Colors.TEXT_MUTED}; background: transparent;")`
- `L631`: `lbl.setStyleSheet("background: transparent;")`
- `L638`: `lbl.setStyleSheet("background: transparent;")`
- `L646`: `lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")`
- `L654`: `lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent;")`

**Font declarations (12):**
- `QFont("Cascadia Code, Consolas", 12)`
- `QFont("Cascadia Code, Consolas, Courier New", 12)`
- `QFont("Segoe UI Emoji", size)`
- `QFont("Segoe UI", 10)`
- `QFont("Segoe UI", 11, QFont.Weight.Medium)`
- `QFont("Segoe UI", 12)`
- `QFont("Segoe UI", 20, QFont.Weight.Bold)`
- `font-size: 11px`
- `font-size: 12px`
- `font-size: 13px`

**Fixed dimensions (7):**
- `L123`: `.setFixedWidth(width)`
- `L167`: `.setFixedWidth(90)`
- `L247`: `.setFixedHeight(44)`
- `L253`: `.setFixedHeight(44)`
- `L319`: `.setFixedWidth(90)`
- `L373`: `.setFixedHeight(8)`
- `L513`: `.setFixedHeight(40)`

**Raw Qt modal calls (8):**
- `L25`: `QFileDialog,`
- `L30`: `QMessageBox,`
- `L215`: `d = QFileDialog.getExistingDirectory(self, "Choose Vault Location", str(self._vault_dir))`
- `L346`: `path, _ = QFileDialog.getOpenFileName(self, "Select Key File")`
- `L354`: `QMessageBox.warning(self, "Key File Required", "Please select a valid key file.")`
- `L412`: `QMessageBox.critical(`
- `L617`: `path, _ = QFileDialog.getSaveFileName(`
- `L625`: `QMessageBox.information(self, "Saved", "Recovery key saved successfully.")`

**Raw emoji literals (2):**
- `L290` (`⚠`): `self._pw_error.show_text(f"⚠ {msg}", Colors.DANGER)`
- `L293` (`⚠`): `self._pw_error.show_text("⚠ Passwords do not match.", Colors.DANGER)`

### `mylocker/app/ui/theme.py`

**Inline `setStyleSheet` calls:**
- `L406`: `app.setStyleSheet(GLOBAL_QSS)`

**Hardcoded color literals (34 occurrences, 22 unique):**
- `#0B1020`, `#111A2A`, `#131D2D`, `#172235`, `#1B2940`, `#28364B`, `#55C7A4`, `#7385E8`, `#748298`, `#8B9CFF`, `#A2B0FF`, `#A78BFA`, `#A7B3C5`, `#D95365`, `#F1F5FB`, `#F4BD68`, `#F87171`, `#FF7183`, `#FFFFFF`, `rgba(108, 99, 255, 0.1)`, `rgba(108, 99, 255, 0.2)`, `rgba(17, 26, 42, 0.92)`

**Font declarations (16):**
- `font-family: `
- `font-size: 11px`
- `font-size: 12px`
- `font-size: 13px`
- `font-size: 15px`
- `font-size: 22px`
- `font-weight: 500`
- `font-weight: 600`
- `font-weight: 700`

### `mylocker/app/ui/video_player.py`

**Inline `setStyleSheet` calls:**
- `L1396`: `self._quality_lbl.setStyleSheet(`
- `L1426`: `lbl.setStyleSheet(`
- `L1435`: `line.setStyleSheet("background: rgba(255,255,255,0.09); border: none;")`
- `L1442`: `btn.setStyleSheet(`
- `L1448`: `btn.setStyleSheet(`
- `L1468`: `btn.setStyleSheet(`
- `L1474`: `btn.setStyleSheet(`
- `L1517`: `note.setStyleSheet("color: rgba(148,163,184,0.75); font-size: 11px; padding: 2px 10px; background: transparent;")`
- `L1550`: `hdr.setStyleSheet(`
- `L1561`: `k_lbl.setStyleSheet(f"color: {VideoColors.TEXT_MUTED}; font-size: 12px; background: transparent;")`
- `L1564`: `v_lbl.setStyleSheet(f"color: {VideoColors.TEXT_BRIGHT}; font-size: 12px; font-weight: 600; background: transparent;")`
- `L1608`: `icon_lbl.setStyleSheet("background: transparent;")`
- `L1612`: `self._title_lbl.setStyleSheet(`
- `L1619`: `self._msg_lbl.setStyleSheet(`
- `L1627`: `self._retry_btn.setStyleSheet(`
- `L1679`: `btn.setStyleSheet(`
- `L1878`: `self._title_lbl.setStyleSheet(`
- `L1884`: `self._subtitle_lbl.setStyleSheet(`
- `L2018`: `self._time_lbl.setStyleSheet(`
- `L2045`: `self._quality_pill.setStyleSheet(`

**Hardcoded color literals (40 occurrences, 36 unique):**
- `#07080C`, `#0B0D12`, `#10121A`, `#7B8EFF`, `#8896AE`, `#8B9CFF`, `#92A2FF`, `#A998FF`, `#B7C2FF`, `#CBD5E1`, `#E2E8F4`, `#EEF2FF`, `#F4F7FF`, `#FF8A98`, `#FFFFFF`, `rgba(139,156,255,0.16)`, `rgba(139,156,255,0.20)`, `rgba(139,156,255,0.22)`, `rgba(139,156,255,0.28)`, `rgba(139,156,255,0.34)`, `rgba(139,156,255,0.38)`, `rgba(139,156,255,0.45)`, `rgba(139,156,255,0.55)`, `rgba(139,156,255,0.65)`, `rgba(14,16,24,0.92)`, `rgba(148,163,184,0.75)`, `rgba(167,179,197,0.85)`, `rgba(182, 194, 214, 0.78)`, `rgba(255,255,255,0.05)`, `rgba(255,255,255,0.06)` …

**Font declarations (39):**
- `QFont("Segoe UI", 10, QFont.Weight.DemiBold)`
- `QFont("Segoe UI", 10, QFont.Weight.Medium)`
- `QFont("Segoe UI", 11, QFont.Weight.DemiBold)`
- `font-family: `
- `font-size: 10px`
- `font-size: 11px`
- `font-size: 12px`
- `font-size: 14px`
- `font-size: 15px`
- `font-weight: 500`
- `font-weight: 600`
- `font-weight: 700`

**Fixed dimensions (31):**
- `L408`: `.setFixedSize(size, size)`
- `L446`: `.setFixedSize(size, size)`
- `L556`: `.setFixedSize(148, 108)`
- `L560`: `.setFixedSize(w, 30)`
- `L613`: `.setFixedHeight(26)`
- `L813`: `.setFixedHeight(26)`
- `L918`: `.setMaximumWidth(0)`
- `L919`: `.setMinimumWidth(0)`
- `L942`: `.setFixedWidth(w)`
- `L1230`: `.setFixedSize(160, 96)`
- `L1319`: `.setFixedSize(w, 38)`
- `L1385`: `.setFixedHeight(28)`
- `L1421`: `.setFixedWidth(296)`
- `L1434`: `.setFixedHeight(1)`
- `L1466`: `.setFixedHeight(28)`
- `L1560`: `.setFixedWidth(74)`
- `L1570`: `.setFixedWidth(308)`
- `L1626`: `.setFixedHeight(36)`
- `L1641`: `.setFixedWidth(340)`
- `L1662`: `.setFixedWidth(216)`
- `L1678`: `.setFixedHeight(32)`
- `L1796`: `.setMinimumSize(500, 320)`
- `L1881`: `.setMinimumWidth(80)`
- `L1974`: `.setFixedSize(228, 86)`
- `L2008`: `.setFixedSize(48, 186)`
- … and 6 more

**Raw Qt modal calls (2):**
- `L67`: `QFileDialog,`
- `L2807`: `dest_str, _ = QFileDialog.getSaveFileName(`

**Raw emoji literals (5):**
- `L1463` (`✓`): `btn = QPushButton(f"✓  {label}" if selected else f"    {label}")`
- `L2583` (`🔇`): `self._hud.flash("🔇 Muted" if self._is_muted else f"🔊 {val}%")`
- `L2583` (`🔊`): `self._hud.flash("🔇 Muted" if self._is_muted else f"🔊 {val}%")`
- `L2598` (`🔇`): `self._hud.flash("🔇 Muted" if self._is_muted else f"🔊 {vol}%")`
- `L2598` (`🔊`): `self._hud.flash("🔇 Muted" if self._is_muted else f"🔊 {vol}%")`

### `mylocker/app/ui/widgets.py`

**Inline `setStyleSheet` calls:**
- `L90`: `self.setStyleSheet(f"""`
- `L165`: `self._bar.setStyleSheet(f"""`
- `L212`: `self.setStyleSheet(f"""`
- `L240`: `self.setStyleSheet(f"""`
- `L298`: `self.setStyleSheet(f"""`
- `L310`: `self._text_lbl.setStyleSheet(f"color: {fg}; background: transparent; font-weight: {'600' if self._selected else '500'}; `
- `L318`: `self._icon_lbl.setStyleSheet(f"color: {fg}; background: transparent;")`
- `L341`: `outer.setStyleSheet(f"""`
- `L355`: `self._title_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 15px; font-weight: 600; background: transparent`
- `L358`: `self._status_lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px; background: transparent;")`
- `L404`: `self.setStyleSheet(f"color: {colour}; background: transparent; font-size: 12px;")`
- `L429`: `self.setStyleSheet(f"""`

**Hardcoded color literals (8 occurrences, 8 unique):**
- `#4C65E8`, `#5C75F8`, `#637EFF`, `#738EFF`, `#AAB7CC`, `#FFFFFF`, `rgba(255, 255, 255, 0.05)`, `rgba(255, 255, 255, 0.25)`

**Font declarations (13):**
- `QFont("Segoe UI", 11)`
- `QFont("Segoe UI", 13)`
- `font-size: 10px`
- `font-size: 11px`
- `font-size: 12px`
- `font-size: 13px`
- `font-size: 15px`
- `font-weight: 600`
- `font-weight: 700`
- `font-weight: {`

**Fixed dimensions (6):**
- `L124`: `.setFixedHeight(4)`
- `L270`: `.setFixedSize(24, 24)`
- `L280`: `.setFixedHeight(44)`
- `L337`: `.setFixedSize(400, 180)`
- `L364`: `.setFixedHeight(6)`
- `L368`: `.setFixedWidth(100)`

**Raw emoji literals (2):**
- `L152` (`✓`): `text = "Good strength ✓"`
- `L155` (`✓`): `text = "Very Strong ✓"`

### `mylocker/app/applock/prompt.py`

**Inline `setStyleSheet` calls:**
- `L98`: `dimmer.setStyleSheet("background-color: rgba(5, 7, 12, 0.85);")`
- `L108`: `card.setStyleSheet(f"""`
- `L129`: `icon_lbl.setStyleSheet("background: transparent;")`
- `L136`: `title_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")`
- `L143`: `sub_lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent;")`

**Hardcoded color literals (1 occurrences, 1 unique):**
- `rgba(5, 7, 12, 0.85)`

**Font declarations (3):**
- `QFont("Segoe UI Emoji", 42)`
- `QFont("Segoe UI", 12)`
- `QFont("Segoe UI", 20, QFont.Weight.Bold)`

**Fixed dimensions (4):**
- `L107`: `.setFixedWidth(420)`
- `L151`: `.setFixedHeight(44)`
- `L166`: `.setFixedHeight(42)`
- `L171`: `.setFixedHeight(42)`

**Raw emoji literals (1):**
- `L126` (`🛡`): `icon_lbl = QLabel("🛡️")`

### `mylocker/app/system/tray.py`

**Font declarations (1):**
- `QFont("Segoe UI Emoji", 20)`

**Raw emoji literals (7):**
- `L5` (`🔒`): `- Tray icon reflects vault status (locked 🔒 vs unlocked 🔓)`
- `L5` (`🔓`): `- Tray icon reflects vault status (locked 🔒 vs unlocked 🔓)`
- `L29` (`🔒`): `p.drawText(0, 26, "🔒" if locked else "🔓")`
- `L29` (`🔓`): `p.drawText(0, 26, "🔒" if locked else "🔓")`
- `L50` (`🔓`): `self._open_act = menu.addAction("🔓 Open Vault", self.open_requested.emit)`
- `L51` (`🔒`): `self._lock_act = menu.addAction("🔒 Lock Now", self.lock_requested.emit)`
- `L53` (`🚨`): `menu.addAction("🚨 Panic (Lock & Hide)", self.panic_requested.emit)`

### `mylocker/main.py`

**Raw Qt modal calls (4):**
- `L32`: `from PySide6.QtWidgets import QApplication, QMessageBox, QStackedWidget`
- `L320`: `from PySide6.QtWidgets import QFileDialog`
- `L321`: `chosen = QFileDialog.getExistingDirectory(`
- `L407`: `QMessageBox.critical(`

---

## 5. Before-Redesign Offscreen Screenshots (`docs/ui-screens/before/`)

| Screen | Resolution | File | Size (bytes) |
| :--- | :---: | :--- | ---: |
| `login` | 900×600 | `docs/ui-screens/before/login_900x600.png` | 215,797 |
| `login` | 1280×800 | `docs/ui-screens/before/login_1280x800.png` | 339,607 |
| `login` | 1920×1080 | `docs/ui-screens/before/login_1920x1080.png` | 553,643 |
| `setup_wizard` | 900×600 | `docs/ui-screens/before/setup_wizard_900x600.png` | 7,910 |
| `setup_wizard` | 1280×800 | `docs/ui-screens/before/setup_wizard_1280x800.png` | 9,767 |
| `setup_wizard` | 1920×1080 | `docs/ui-screens/before/setup_wizard_1920x1080.png` | 13,694 |
| `dashboard` | 900×600 | `docs/ui-screens/before/dashboard_900x600.png` | 75,709 |
| `dashboard` | 1280×800 | `docs/ui-screens/before/dashboard_1280x800.png` | 86,931 |
| `dashboard` | 1920×1080 | `docs/ui-screens/before/dashboard_1920x1080.png` | 93,560 |
| `settings` | 900×600 | `docs/ui-screens/before/settings_900x600.png` | 7,899 |
| `settings` | 1280×800 | `docs/ui-screens/before/settings_1280x800.png` | 12,741 |
| `settings` | 1920×1080 | `docs/ui-screens/before/settings_1920x1080.png` | 18,895 |
| `intruder_log` | 900×600 | `docs/ui-screens/before/intruder_log_900x600.png` | 5,029 |
| `intruder_log` | 1280×800 | `docs/ui-screens/before/intruder_log_1280x800.png` | 6,358 |
| `intruder_log` | 1920×1080 | `docs/ui-screens/before/intruder_log_1920x1080.png` | 10,128 |
| `applock` | 900×600 | `docs/ui-screens/before/applock_900x600.png` | 7,809 |
| `applock` | 1280×800 | `docs/ui-screens/before/applock_1280x800.png` | 9,283 |
| `applock` | 1920×1080 | `docs/ui-screens/before/applock_1920x1080.png` | 13,050 |
| `applock_prompt` | 900×600 | `docs/ui-screens/before/applock_prompt_900x600.png` | 10,215 |
| `applock_prompt` | 1280×800 | `docs/ui-screens/before/applock_prompt_1280x800.png` | 12,707 |
| `applock_prompt` | 1920×1080 | `docs/ui-screens/before/applock_prompt_1920x1080.png` | 17,615 |
| `gallery` | 900×600 | `docs/ui-screens/before/gallery_900x600.png` | 4,086 |
| `gallery` | 1280×800 | `docs/ui-screens/before/gallery_1280x800.png` | 5,953 |
| `gallery` | 1920×1080 | `docs/ui-screens/before/gallery_1920x1080.png` | 9,800 |
| `video_player` | 900×600 | `docs/ui-screens/before/video_player_900x600.png` | 47,360 |
| `video_player` | 1280×800 | `docs/ui-screens/before/video_player_1280x800.png` | 28,975 |
| `video_player` | 1920×1080 | `docs/ui-screens/before/video_player_1920x1080.png` | 34,165 |
| `calculator` | 900×600 | `docs/ui-screens/before/calculator_900x600.png` | 10,922 |
| `calculator` | 1280×800 | `docs/ui-screens/before/calculator_1280x800.png` | 12,965 |
| `calculator` | 1920×1080 | `docs/ui-screens/before/calculator_1920x1080.png` | 17,014 |
