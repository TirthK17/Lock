"""
applock_ui.py — Management UI for App Lock features.

Allows:
  - Scanning running processes and Start Menu to pick apps
  - Adding custom executable path
  - Enabling/disabling watcher and startup launch
  - Setting grace period (e.g. 5 minutes)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..applock.config import AppLockConfig
from ..applock.process_utils import get_running_gui_processes, resolve_shortcut_target, scan_installed_applications
from ..system.startup import is_run_at_startup_enabled, set_run_at_startup
from . import icons
from .components import (
    Badge,
    Card,
    DangerButton,
    DialogShell,
    EmptyState,
    PrimaryButton,
    SecondaryButton,
    Toggle,
)
from .design_system import ThemePalette, Tokens

logger = logging.getLogger(__name__)


class _AppLockAsyncWorker(QThread):
    """Generic lightweight background task for AppLockUI (startup/installed scan)."""

    completed = Signal(object)

    def __init__(self, fn) -> None:
        super().__init__(None)
        self._fn = fn

    def run(self) -> None:
        try:
            res = self._fn()
            self.completed.emit(res)
        except Exception as exc:
            logger.debug("AppLock async worker error: %s", type(exc).__name__)
            self.completed.emit(None)


class AppLockUI(QWidget):
    """View embedded in the Dashboard to configure and control App Lock."""

    protected_apps_changed = Signal(set)  # set of exe names in lowercase
    grace_period_changed = Signal(int)    # minutes

    def __init__(self, config_or_exes, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bg_workers: set[_AppLockAsyncWorker] = set()
        if isinstance(config_or_exes, AppLockConfig):
            self.config: Optional[AppLockConfig] = config_or_exes
            self.protected_exes = self.config.get_locked_apps()
        elif isinstance(config_or_exes, set):
            self.config = None
            self.protected_exes = set(config_or_exes)
        else:
            self.config = None
            self.protected_exes = set()

        self._build_ui()
        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self._apply_theme()
        self._load_table()
        self._probe_startup_async()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self._apply_theme()

    def _apply_theme(self) -> None:
        p = Tokens.colors
        self._header_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")
        self._desc_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")
        self._startup_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")
        self._grace_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {p.card};
                border: 1px solid {p.border};
                border-radius: {Tokens.radius.CARD}px;
                gridline-color: {p.border};
                color: {p.text};
            }}
        """)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        top_row = QHBoxLayout()
        self._header_lbl = QLabel("App Lock", self)
        self._header_lbl.setFont(Tokens.type.font(Tokens.type.PAGE_TITLE, Tokens.type.WEIGHT_TITLE))
        self.status_badge = Badge("Active", "success", self)
        top_row.addWidget(self._header_lbl)
        top_row.addWidget(self.status_badge)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        self._desc_lbl = QLabel(
            "Prevent unauthorized launch of selected desktop applications. "
            "When a protected app starts, it is suspended until authentication succeeds.",
            self,
        )
        self._desc_lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY))
        self._desc_lbl.setWordWrap(True)
        layout.addWidget(self._desc_lbl)

        # Options Card
        opts_card = Card(self)
        opts_layout = QHBoxLayout(opts_card)
        opts_layout.setContentsMargins(16, 12, 16, 12)
        opts_layout.setSpacing(12)

        self.startup_check = Toggle(checked=False, parent=opts_card)
        self.startup_check.setAccessibleName("Start App Lock automatically at Windows login")
        self.startup_check.setEnabled(False)
        self.startup_check.toggled.connect(self._toggle_startup)
        self._startup_lbl = QLabel("Start App Lock at Windows login", opts_card)
        self._startup_lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_LABEL))
        opts_layout.addWidget(self.startup_check)
        opts_layout.addWidget(self._startup_lbl)

        opts_layout.addStretch(1)

        self._grace_lbl = QLabel("Grace Period (mins):", opts_card)
        self._grace_lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL))
        self.grace_spin = QSpinBox(opts_card)
        self.grace_spin.setRange(0, 60)
        self.grace_spin.setValue(self.config.get_grace_minutes() if self.config else 5)
        self.grace_spin.setFixedWidth(72)
        self.grace_spin.setAccessibleName("App Lock grace period in minutes")
        self.grace_spin.valueChanged.connect(self._on_grace_changed)
        opts_layout.addWidget(self._grace_lbl)
        opts_layout.addWidget(self.grace_spin)

        layout.addWidget(opts_card)

        # Actions Row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        scan_running_btn = SecondaryButton("Scan Running", icon_name="search", parent=self)
        scan_running_btn.clicked.connect(self._scan_running)

        self._scan_installed_btn = SecondaryButton("Browse Installed", icon_name="folder", parent=self)
        self._scan_installed_btn.clicked.connect(self._scan_installed)

        set_pw_btn = SecondaryButton("Set PIN", icon_name="key", parent=self)
        set_pw_btn.clicked.connect(self._set_applock_password)

        add_manual_btn = PrimaryButton("Add Custom .EXE", icon_name="plus", parent=self)
        add_manual_btn.clicked.connect(self._add_custom_exe)

        btn_row.addWidget(scan_running_btn)
        btn_row.addWidget(self._scan_installed_btn)
        btn_row.addWidget(set_pw_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(add_manual_btn)
        layout.addLayout(btn_row)

        self._empty_state = EmptyState(
            "shield_check",
            "No protected applications",
            "Select running processes or browse installed apps to lock them with your vault credentials.",
            action_text="Add Custom .EXE",
            parent=self,
        )
        self._empty_state.action_clicked.connect(self._add_custom_exe)
        layout.addWidget(self._empty_state, 1)

        # Table of Protected Apps
        self.table = QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Executable Name", "Status", "Action"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

    def _load_table(self) -> None:
        self.table.setRowCount(0)
        has_apps = bool(self.protected_exes)
        self.table.setVisible(has_apps)
        self._empty_state.setVisible(not has_apps)
        if not has_apps:
            return

        p = Tokens.colors
        for exe in sorted(self.protected_exes):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, 44)

            name_item = QTableWidgetItem(exe)
            name_item.setIcon(icons.get_icon("lock", color=p.accent, active_color=p.accent, size=16))
            name_item.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_LABEL))
            self.table.setItem(row, 0, name_item)

            status_badge = Badge("Protected", "accent", self.table)
            self.table.setCellWidget(row, 1, status_badge)

            del_btn = DangerButton("Remove", parent=self.table)
            del_btn.setFixedSize(90, 30)
            del_btn.clicked.connect(lambda _=False, e=exe: self._remove_app(e))
            self.table.setCellWidget(row, 2, del_btn)

    def _remove_app(self, exe: str) -> None:
        if exe in self.protected_exes:
            self.protected_exes.remove(exe)
            if self.config:
                self.config.remove_locked_app(exe)
            self._load_table()
            self.protected_apps_changed.emit(self.protected_exes)

    def _add_custom_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Executable to Lock", "", "Executables & Shortcuts (*.exe *.lnk)")
        if path:
            target = resolve_shortcut_target(path)
            name = Path(target).name.lower()
            self.protected_exes.add(name)
            if self.config:
                self.config.add_locked_app(target)
            self._load_table()
            self.protected_apps_changed.emit(self.protected_exes)

    def _scan_running(self) -> None:
        procs = get_running_gui_processes()
        if not procs:
            DialogShell.inform(self, "Running Apps", "No additional user apps detected.")
            return

        names = [f"{p['name']} (PID: {p['pid']})" for p in procs]
        item, ok = DialogShell.choose_item(
            self,
            "Protect Running Application",
            "Select an active process to lock:",
            names,
            confirm_text="Protect App",
        )
        if ok and item:
            exe_name = item.split(" ")[0].lower()
            if self.config:
                exe_name = self.config.add_locked_app(exe_name)
            self.protected_exes.add(exe_name)
            self._load_table()
            self.protected_apps_changed.emit(self.protected_exes)

    def _start_worker(self, fn, on_done) -> None:
        import shiboken6
        worker = _AppLockAsyncWorker(fn)
        self._bg_workers.add(worker)

        def _handle(res: object) -> None:
            self._bg_workers.discard(worker)
            worker.deleteLater()
            if shiboken6.isValid(self):
                on_done(res)

        worker.completed.connect(_handle, Qt.ConnectionType.QueuedConnection)
        worker.start()

    def _probe_startup_async(self) -> None:
        def _apply(enabled: object) -> None:
            self.startup_check.blockSignals(True)
            self.startup_check.setChecked(bool(enabled))
            self.startup_check.blockSignals(False)
            self.startup_check.setEnabled(True)

        self._start_worker(is_run_at_startup_enabled, _apply)

    def _scan_installed(self) -> None:
        if not self._scan_installed_btn.isEnabled():
            return
        self._scan_installed_btn.setEnabled(False)
        self._scan_installed_btn.setText("Scanning Installed…")

        def _on_apps_scanned(apps: object) -> None:
            self._scan_installed_btn.setEnabled(True)
            self._scan_installed_btn.setText("Browse Installed")
            if not apps:
                DialogShell.inform(self, "Installed Apps", "No installed apps catalogued.")
                return

            names = [f"{a['name']}" for a in apps[:80]]
            item, ok = DialogShell.choose_item(
                self,
                "Protect Installed App",
                "Select an installed app:",
                names,
                confirm_text="Protect App",
            )
            if ok and item:
                chosen = next((a for a in apps if a["name"] == item), None)
                if chosen:
                    p = chosen["path"]
                    target = resolve_shortcut_target(p)
                    exe_name = Path(target).name.lower()
                    if not exe_name.endswith(".exe"):
                        exe_name = chosen["name"].lower().replace(" ", "") + ".exe"
                    if self.config:
                        exe_name = self.config.add_locked_app(exe_name)
                    self.protected_exes.add(exe_name)
                    self._load_table()
                    self.protected_apps_changed.emit(self.protected_exes)

        self._start_worker(scan_installed_applications, _on_apps_scanned)

    def _on_grace_changed(self, mins: int) -> None:
        if self.config:
            self.config.set_grace_minutes(mins)
        self.grace_period_changed.emit(mins)

    def _set_applock_password(self) -> None:
        pw, ok = DialogShell.get_text(
            self,
            "Set App Lock Password / PIN",
            "Enter a dedicated password for unlocking protected apps\n(Master password will also still work):",
            echo_mode=QLineEdit.EchoMode.Password,
            confirm_text="Save PIN",
        )
        if ok and pw:
            if len(pw) < 4:
                DialogShell.warn(self, "Too Short", "App Lock password must be at least 4 characters.")
                return
            if self.config:
                self.config.set_password(pw)
                DialogShell.inform(self, "App Lock Password Set", "Dedicated App Lock password saved.")

    def _toggle_startup(self, checked: bool) -> None:
        self.startup_check.setEnabled(False)

        def _done(ok: object) -> None:
            self.startup_check.setEnabled(True)
            if ok is False:
                self.startup_check.blockSignals(True)
                self.startup_check.setChecked(not checked)
                self.startup_check.blockSignals(False)

        self._start_worker(lambda: set_run_at_startup(checked), _done)

    def cleanup(self) -> None:
        for w in list(self._bg_workers):
            if w.isRunning():
                w.wait(1000)


__all__ = ["AppLockUI"]

