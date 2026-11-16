"""
intruder_log.py — Intruder log UI displaying failed login attempts and webcam captures.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.lockout import LockoutManager
from ..system.webcam import decrypt_intruder_photo
from .theme import Colors
from .widgets import GlowButton

logger = logging.getLogger(__name__)


class PhotoViewerDialog(QDialog):
    """Dialog displaying the captured webcam photo."""

    def __init__(self, photo_path: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Intruder Capture")
        self.setStyleSheet(f"background: {Colors.BG_DARKEST};")
        self.setFixedSize(640, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        decrypted_bytes = decrypt_intruder_photo(photo_path)
        px = QPixmap()
        if decrypted_bytes:
            px.loadFromData(decrypted_bytes)
        if not px.isNull():
            lbl.setPixmap(px.scaled(600, 440, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            lbl.setText("Unable to load capture image.")
            lbl.setStyleSheet(f"color: {Colors.TEXT_MUTED};")

        layout.addWidget(lbl, 1)

        close_btn = GlowButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignCenter)


class IntruderLogUI(QWidget):
    """View embedded in the Dashboard to inspect failed attempt logs."""

    def __init__(self, lockout_manager: LockoutManager, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.lockout = lockout_manager
        self._build_ui()
        self.refresh_log()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(20)

        # Header
        top_row = QHBoxLayout()
        header = QLabel("Intruder Detection Log")
        header.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")

        top_row.addWidget(header)
        top_row.addStretch(1)

        clear_btn = QPushButton("🗑️ Clear Log")
        clear_btn.setProperty("danger", True)
        clear_btn.clicked.connect(self._clear_log)
        top_row.addWidget(clear_btn)

        layout.addLayout(top_row)

        desc = QLabel(
            "Every failed attempt to unlock the vault or locked applications is recorded here.\n"
            "If webcam capture is enabled, photos of unauthorized attempts are stored for inspection."
        )
        desc.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 13px; background: transparent;")
        layout.addWidget(desc)

        # Log Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Timestamp", "Attempt Count", "Webcam Capture", "Action"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {Colors.BG_CARD};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
                gridline-color: {Colors.BORDER};
            }}
        """)
        layout.addWidget(self.table, 1)

    def refresh_log(self) -> None:
        entries = self.lockout.get_intruder_log()
        self.table.setRowCount(0)

        for entry in reversed(entries):
            row = self.table.rowCount()
            self.table.insertRow(row)

            # Timestamp
            ts_item = QTableWidgetItem(entry.get("timestamp", "Unknown"))
            self.table.setItem(row, 0, ts_item)

            # Count
            count = entry.get("fail_count", 1)
            count_item = QTableWidgetItem(f"Failed Attempt #{count}")
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, count_item)

            # Photo status
            has_photo = entry.get("has_photo", False)
            photo_file = entry.get("photo_file")
            status_item = QTableWidgetItem("📸 Captured" if has_photo else "No Photo")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, status_item)

            # Action button
            if has_photo and photo_file:
                view_btn = QPushButton("View Photo")
                view_btn.setFixedSize(90, 28)
                view_btn.clicked.connect(lambda _, pf=photo_file: self._view_photo(pf))
                self.table.setCellWidget(row, 3, view_btn)
            else:
                na_item = QTableWidgetItem("—")
                na_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 3, na_item)

    def _view_photo(self, photo_rel: str) -> None:
        photo_path = self.lockout.vault_dir / photo_rel
        if photo_path.exists():
            dlg = PhotoViewerDialog(photo_path, self)
            dlg.exec()
        else:
            QMessageBox.warning(self, "Photo Missing", "The capture file was moved or deleted.")

    def _clear_log(self) -> None:
        res = QMessageBox.question(
            self,
            "Clear Intruder Log",
            "Are you sure you want to delete all intruder history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res == QMessageBox.StandardButton.Yes:
            self.lockout.clear_intruder_log()
            self.refresh_log()


__all__ = ["IntruderLogUI"]

