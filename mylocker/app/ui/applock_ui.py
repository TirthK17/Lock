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
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..applock.config import AppLockConfig
from ..applock.process_utils import get_running_gui_processes, resolve_shortcut_target, scan_installed_applications
from ..system.startup import is_run_at_startup_enabled, set_run_at_startup
from .theme import Colors
from .widgets import GlowButton, StatusBadge

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

    def __init__(self, config_or_exes, parent: QWidget = None) -> None:
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
        self._load_table()
        self._probe_startup_async()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(20)

        # Header
        top_row = QHBoxLayout()
        header = QLabel("Application Locker")
        header.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        self.status_badge = StatusBadge("🛡️ Active", Colors.SUCCESS)
        top_row.addWidget(header)
        top_row.addWidget(self.status_badge)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        desc = QLabel(
            "Prevent unauthorized launch of selected desktop applications.\n"
            "When a protected app starts, it is suspended until the master password is provided."
        )
        desc.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 13px; background: transparent;")
        layout.addWidget(desc)

        # Options Card
        opts_card = QFrame()
        opts_card.setObjectName("AppLockOptsCard")
        opts_card.setStyleSheet(f"""
            #AppLockOptsCard {{
                background-color: {Colors.BG_CARD};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)
        opts_layout = QHBoxLayout(opts_card)
        opts_layout.setContentsMargins(18, 14, 18, 14)

        self.startup_check = QCheckBox("Start App Lock automatically at Windows login")
        self.startup_check.setChecked(False)
        self.startup_check.setEnabled(False)
        self.startup_check.toggled.connect(self._toggle_startup)
        opts_layout.addWidget(self.startup_check)

        opts_layout.addStretch(1)

        grace_lbl = QLabel("Grace Period (mins):")
        grace_lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent;")
        self.grace_spin = QSpinBox()
        self.grace_spin.setRange(0, 60)
        self.grace_spin.setValue(self.config.get_grace_minutes() if self.config else 5)
        self.grace_spin.setFixedWidth(60)
        self.grace_spin.valueChanged.connect(self._on_grace_changed)
        opts_layout.addWidget(grace_lbl)
        opts_layout.addWidget(self.grace_spin)

        layout.addWidget(opts_card)

        # Actions Row
        btn_row = QHBoxLayout()
        scan_running_btn = QPushButton("🔍 Scan Running Apps")
        scan_running_btn.clicked.connect(self._scan_running)

        self._scan_installed_btn = QPushButton("📂 Browse Installed")
        self._scan_installed_btn.clicked.connect(self._scan_installed)

        set_pw_btn = QPushButton("🔑 Set App Lock PIN")
        set_pw_btn.clicked.connect(self._set_applock_password)

        add_manual_btn = GlowButton("+ Add Custom .EXE")
        add_manual_btn.clicked.connect(self._add_custom_exe)

        btn_row.addWidget(scan_running_btn)
        btn_row.addWidget(self._scan_installed_btn)
        btn_row.addWidget(set_pw_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(add_manual_btn)
        layout.addLayout(btn_row)

        # Table of Protected Apps
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Executable Name", "Status", "Action"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {Colors.BG_CARD};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
                gridline-color: {Colors.BORDER};
            }}
        """)
        layout.addWidget(self.table, 1)

    def _load_table(self) -> None:
        self.table.setRowCount(0)
        for exe in sorted(self.protected_exes):
            row = self.table.rowCount()
            self.table.insertRow(row)

            # Name
            name_item = QTableWidgetItem(f"🔒 {exe}")
            name_item.setFont(QFont("Segoe UI", 12))
            self.table.setItem(row, 0, name_item)

            # Status
            status_item = QTableWidgetItem("Protected")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, status_item)

            # Delete button
            del_btn = QPushButton("Remove")
            del_btn.setProperty("danger", True)
            del_btn.setFixedSize(80, 28)
            del_btn.clicked.connect(lambda _, e=exe: self._remove_app(e))
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
            QMessageBox.information(self, "Running Apps", "No additional user apps detected.")
            return

        names = [f"{p['name']} (PID: {p['pid']})" for p in procs]
        from PySide6.QtWidgets import QInputDialog
        item, ok = QInputDialog.getItem(self, "Protect Running Application", "Select an active process to lock:", names, 0, False)
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
        self._scan_installed_btn.setText("Scanning Installed Apps…")

        def _on_apps_scanned(apps: object) -> None:
            self._scan_installed_btn.setEnabled(True)
            self._scan_installed_btn.setText("📂 Browse Installed")
            if not apps:
                QMessageBox.information(self, "Installed Apps", "No installed apps catalogued.")
                return

            names = [f"{a['name']}" for a in apps[:80]]
            from PySide6.QtWidgets import QInputDialog
            item, ok = QInputDialog.getItem(self, "Protect Installed App", "Select an installed app:", names, 0, False)
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
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        pw, ok = QInputDialog.getText(
            self,
            "Set App Lock Password / PIN",
            "Enter a dedicated password for unlocking protected apps\n(Master password will also still work):",
            QLineEdit.EchoMode.Password,
        )
        if ok and pw:
            if len(pw) < 4:
                QMessageBox.warning(self, "Too Short", "App Lock password must be at least 4 characters.")
                return
            if self.config:
                self.config.set_password(pw)
                QMessageBox.information(self, "App Lock Password Set", "Dedicated App Lock password saved.")

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

