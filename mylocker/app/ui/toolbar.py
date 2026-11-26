"""
toolbar.py — Custom 36px window title strip and main workspace action toolbar.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from . import icons
from .components import IconButton, PrimaryButton, SegmentedControl
from .dashboard_theme import NotificationBellButton, VaultSearchBar, VaultUnlockedBadge
from .design_system import ThemePalette, Tokens


class TitleAreaBar(QFrame):
    """Minimal 36px custom-feeling window title bar with vault state & privacy controls."""

    screenshot_toggled = Signal()
    notifications_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleAreaBar")
        self.setFixedHeight(36)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 0, 24, 0)
        lay.setSpacing(10)

        self.page_title_lbl = QLabel("All Files", self)
        self.page_title_lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_TITLE))
        lay.addWidget(self.page_title_lbl)

        self.unlocked_badge = VaultUnlockedBadge(self)
        self.unlocked_badge.setMinimumHeight(24)
        lay.addWidget(self.unlocked_badge)

        lay.addStretch(1)

        self.ss_btn = QPushButton(" Protected", self)
        self.ss_btn.setMinimumHeight(26)
        self.ss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ss_btn.setAccessibleName("Toggle screenshot protection")
        self.ss_btn.clicked.connect(self.screenshot_toggled.emit)
        lay.addWidget(self.ss_btn)

        self.notif_btn = NotificationBellButton(self)
        self.notif_btn.setFixedSize(28, 28)
        self.notif_btn.clicked.connect(self.notifications_clicked.emit)
        lay.addWidget(self.notif_btn)

        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self._sync_height_for_font()
        self.refresh_theme(screenshot_blocked=True)

    def _sync_height_for_font(self) -> None:
        fm_h = self.fontMetrics().height()
        if fm_h > 20:
            self.setMinimumHeight(max(36, fm_h + 14))
            self.setMaximumHeight(16777215)
        else:
            self.setFixedHeight(36)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.ApplicationFontChange):
            self._sync_height_for_font()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self.refresh_theme()

    def refresh_theme(self, screenshot_blocked: Optional[bool] = None) -> None:
        p = Tokens.colors
        self.setStyleSheet(f"""
            #TitleAreaBar {{
                background-color: {p.bg};
                border-bottom: 1px solid {p.border};
            }}
        """)
        self.page_title_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")
        self.unlocked_badge.refresh_theme()
        if screenshot_blocked is not None:
            self.update_screenshot_state(screenshot_blocked)

    def update_screenshot_state(self, blocked: bool) -> None:
        p = Tokens.colors
        if blocked:
            self.ss_btn.setText(" Protected")
            self.ss_btn.setIcon(icons.get_icon("shield_check", color=p.success, active_color=p.success, size=14))
            self.ss_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {p.success_tint};
                    color: {p.success};
                    border: 1px solid {p.success_border};
                    border-radius: 6px;
                    padding: 0 10px;
                    font-size: {Tokens.type.META}px;
                    font-weight: 500;
                    min-height: 24px;
                }}
                QPushButton:hover {{ border-color: {p.success}; }}
            """)
        else:
            self.ss_btn.setText(" Unprotected")
            self.ss_btn.setIcon(icons.get_icon("shield_alert", color=p.warning, active_color=p.warning, size=14))
            self.ss_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {p.warning_tint};
                    color: {p.warning};
                    border: 1px solid {p.warning_border};
                    border-radius: 6px;
                    padding: 0 10px;
                    font-size: {Tokens.type.META}px;
                    font-weight: 500;
                    min-height: 24px;
                }}
                QPushButton:hover {{ border-color: {p.warning}; }}
            """)


class WorkspaceToolbar(QFrame):
    """Top toolbar containing search bar, grid/list toggle, sort selector, multi-select, and + Add."""

    search_changed = Signal(str)
    view_mode_changed = Signal(str)     # "grid" | "list"
    sort_changed = Signal(int)
    selection_toggled = Signal()
    add_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("WorkspaceToolbar")
        self.setMinimumHeight(48)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.search_bar = VaultSearchBar("Search vault…", parent=self)
        self.search_bar.textChanged.connect(self.search_changed.emit)
        lay.addWidget(self.search_bar, 1)

        # Grid vs List segmented toggle
        self.view_switch = SegmentedControl(
            [("grid", "Grid", "grid"), ("list", "List", "list")],
            current="grid",
            parent=self,
        )
        self.view_switch.selection_changed.connect(self.view_mode_changed.emit)
        # Preserve button handles for tests / shortcuts
        self.btn_grid_mode = self.view_switch._buttons["grid"]
        self.btn_list_mode = self.view_switch._buttons["list"]
        self.btn_grid_mode.setToolTip("Grid view")
        self.btn_list_mode.setToolTip("List view")
        lay.addWidget(self.view_switch)

        # Sort selector
        self.sort_combo = QComboBox(self)
        self.sort_combo.addItems(["Sort: Recent", "Sort: Name", "Sort: Size", "Sort: Type"])
        self.sort_combo.setMinimumHeight(34)
        self.sort_combo.setMinimumWidth(116)
        self.sort_combo.setAccessibleName("Sort files")
        self.sort_combo.currentIndexChanged.connect(self.sort_changed.emit)
        lay.addWidget(self.sort_combo)

        # Filter / multi-select button
        self.select_btn = IconButton("check", tooltip="Select multiple files", size=34, icon_size=16, checkable=True, parent=self)
        self.select_btn.clicked.connect(self.selection_toggled.emit)
        lay.addWidget(self.select_btn)

        # Primary + Add CTA
        self.add_btn = PrimaryButton("Add", icon_name="plus", parent=self)
        self.add_btn.setMinimumHeight(34)
        self.add_btn.setMinimumWidth(84)
        self.add_btn.setIconSize(QSize(16, 16))
        self.add_btn.setToolTip("Add files or folder to vault")
        self.add_btn.setAccessibleName("Add files to vault")
        self.add_btn.clicked.connect(self.add_clicked.emit)
        lay.addWidget(self.add_btn)

        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self.search_bar.refresh_theme()


__all__ = ["TitleAreaBar", "WorkspaceToolbar"]
