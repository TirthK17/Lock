"""
intruder_log.py — Intruder log UI displaying failed login attempts and webcam captures.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.lockout import LockoutManager
from ..system.webcam import decrypt_intruder_photo
from . import icons
from .components import Badge, DangerButton, DialogShell, EmptyState, SecondaryButton
from .design_system import ThemePalette, Tokens

logger = logging.getLogger(__name__)


class PhotoViewerDialog(DialogShell):
    """Dialog displaying the captured webcam photo inside unified DialogShell."""

    def __init__(self, photo_path: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            title="Intruder Capture",
            subtitle=photo_path.name,
            icon_name="camera",
            parent=parent,
            min_width=480,
            max_width=680,
        )

        lbl = QLabel(self.body_widget)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setMinimumSize(420, 320)
        lbl.setStyleSheet(
            f"background-color: {Tokens.colors.bg}; border: 1px solid {Tokens.colors.border}; "
            f"border-radius: {Tokens.radius.CONTROL}px; color: {Tokens.colors.text_muted};"
        )
        decrypted_bytes = decrypt_intruder_photo(photo_path)
        px = QPixmap()
        if decrypted_bytes:
            px.loadFromData(decrypted_bytes)
        if not px.isNull():
            lbl.setPixmap(px.scaled(600, 420, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            lbl.setText("Unable to load capture image.")

        self.body_layout.addWidget(lbl)

        close_btn = SecondaryButton("Close", parent=self)
        close_btn.clicked.connect(self.accept)
        self.add_footer_button(close_btn)


class IntruderLogUI(QWidget):
    """View embedded in the Dashboard to inspect failed attempt logs."""

    def __init__(self, lockout_manager: LockoutManager, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.lockout = lockout_manager
        self._build_ui()
        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self._apply_theme()
        self.refresh_log()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self._apply_theme()

    def _apply_theme(self) -> None:
        p = Tokens.colors
        self._header_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")
        self._desc_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")
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
        self._header_lbl = QLabel("Intruder Log", self)
        self._header_lbl.setFont(Tokens.type.font(Tokens.type.PAGE_TITLE, Tokens.type.WEIGHT_TITLE))

        top_row.addWidget(self._header_lbl)
        top_row.addStretch(1)

        self._clear_btn = DangerButton("Clear Log", icon_name="trash", parent=self)
        self._clear_btn.clicked.connect(self._clear_log)
        top_row.addWidget(self._clear_btn)

        layout.addLayout(top_row)

        self._desc_lbl = QLabel(
            "Failed attempts to unlock the vault or locked applications are recorded here. "
            "When webcam capture is active, unauthorized photos can be inspected securely.",
            self,
        )
        self._desc_lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY))
        self._desc_lbl.setWordWrap(True)
        layout.addWidget(self._desc_lbl)

        self._empty_state = EmptyState(
            "shield_check",
            "No intruder activity",
            "No failed unlock attempts or captures have been recorded.",
            parent=self,
        )
        layout.addWidget(self._empty_state, 1)

        # Log Table
        self.table = QTableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Timestamp", "Attempts", "Webcam Capture", "Action"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

    def refresh_log(self) -> None:
        entries = self.lockout.get_intruder_log()
        self.table.setRowCount(0)

        has_entries = bool(entries)
        self.table.setVisible(has_entries)
        self._empty_state.setVisible(not has_entries)
        self._clear_btn.setEnabled(has_entries)

        if not has_entries:
            return

        p = Tokens.colors
        for entry in reversed(entries):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, 44)

            # Timestamp
            ts_item = QTableWidgetItem(entry.get("timestamp", "Unknown"))
            self.table.setItem(row, 0, ts_item)

            # Count badge
            count = entry.get("fail_count", 1)
            count_badge = Badge(f"Attempt #{count}", "danger" if count >= 3 else "warning", self.table)
            self.table.setCellWidget(row, 1, count_badge)

            # Photo status
            has_photo = bool(entry.get("has_photo", False))
            photo_file = entry.get("photo_file")
            status_item = QTableWidgetItem("Captured" if has_photo else "No Photo")
            if has_photo:
                status_item.setIcon(icons.get_icon("camera", color=p.warning, active_color=p.warning, size=14))
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, status_item)

            # Action button
            if has_photo and photo_file:
                view_btn = SecondaryButton("View Photo", icon_name="eye", parent=self.table)
                view_btn.setFixedSize(112, 30)
                view_btn.clicked.connect(lambda _=False, pf=photo_file: self._view_photo(pf))
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
            DialogShell.warn(self, "Photo Missing", "The capture file was moved or deleted.")

    def _clear_log(self) -> None:
        confirmed = DialogShell.confirm(
            self,
            "Clear Intruder Log",
            "Delete all recorded failed login history and references?",
            confirm_text="Clear Log",
            danger=True,
        )
        if confirmed:
            self.lockout.clear_intruder_log()
            self.refresh_log()


__all__ = ["IntruderLogUI", "PhotoViewerDialog"]

