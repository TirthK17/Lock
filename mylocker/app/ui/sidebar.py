"""
sidebar.py — Collapsible 232px <-> 64px navigation sidebar with sliding active indicator.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .components import IconButton
from .dashboard_theme import SidebarBrandWidget
from .design_system import ThemePalette, Tokens
from .motion import _effective_reduced_motion, animate_indicator_slide
from .widgets import SidebarItem


def _reduced_motion() -> bool:
    return bool(_effective_reduced_motion())


class _SlidingIndicator(QFrame):
    """Floating active pill behind sidebar items: 12% accent fill + 2px left accent bar."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarActiveIndicator")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._bar = QFrame(self)
        self._bar.setFixedWidth(2)
        self.hide()
        self.refresh_theme()

    def refresh_theme(self) -> None:
        p = Tokens.colors
        self.setStyleSheet(f"""
            #SidebarActiveIndicator {{
                background-color: {p.accent_tint};
                border-radius: {Tokens.radius.CONTROL}px;
                border: none;
            }}
        """)
        self._bar.setStyleSheet(f"background-color: {p.accent}; border-radius: 1px;")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._bar.setGeometry(0, 8, 2, max(8, self.height() - 16))

    def move_to_item(self, target: QWidget, animate: bool = True) -> None:
        if not animate:
            geo = target.geometry()
            if geo.width() > 0 and geo.height() > 0:
                self.show()
                self.lower()
                self.setGeometry(geo)
            return
        animate_indicator_slide(self, target, duration_ms=Tokens.motion.FAST)


class SidebarWidget(QFrame):
    """Collapsible navigation rail (232px expanded <-> 64px icon rail)."""

    EXPANDED_WIDTH = 232
    COLLAPSED_WIDTH = 64

    nav_clicked = Signal(int, object)   # (page_idx, SidebarItem)
    bin_clicked = Signal()
    lock_clicked = Signal()
    collapsed_changed = Signal(bool)

    def __init__(self, is_decoy: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardSidebar")
        self._is_decoy = bool(is_decoy)
        self._collapsed = False
        self._active_item: Optional[SidebarItem] = None

        self.setMinimumWidth(self.EXPANDED_WIDTH)
        self.setMaximumWidth(self.EXPANDED_WIDTH)

        self._width_min_anim = QPropertyAnimation(self, b"minimumWidth", self)
        self._width_min_anim.setDuration(Tokens.motion.NORMAL)
        self._width_min_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._width_max_anim = QPropertyAnimation(self, b"maximumWidth", self)
        self._width_max_anim.setDuration(Tokens.motion.NORMAL)
        self._width_max_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._build_ui()
        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self.refresh_theme()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self.refresh_theme()

    def _build_ui(self) -> None:
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 12, 10, 12)
        self._layout.setSpacing(4)

        # Header row: Brand + collapse toggle
        hdr = QHBoxLayout()
        hdr.setContentsMargins(2, 0, 2, 0)
        hdr.setSpacing(4)

        self.brand_widget = SidebarBrandWidget(self)
        self.collapse_btn = IconButton(
            "sidebar_collapse",
            tooltip="Collapse sidebar",
            size=28,
            icon_size=16,
            parent=self,
        )
        self.collapse_btn.clicked.connect(self.toggle_collapsed)

        hdr.addWidget(self.brand_widget, 1)
        hdr.addWidget(self.collapse_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._layout.addLayout(hdr)
        self._layout.addSpacing(8)

        # Navigation items list container (holds sliding active indicator)
        self._nav_container = QWidget(self)
        self._nav_container.setStyleSheet("background: transparent;")
        nav_lay = QVBoxLayout(self._nav_container)
        nav_lay.setContentsMargins(0, 0, 0, 0)
        nav_lay.setSpacing(2)

        self._indicator = _SlidingIndicator(self._nav_container)

        item_all = SidebarItem("folder", "All Files", self._nav_container)
        item_photos = SidebarItem("image", "Photos", self._nav_container)
        item_videos = SidebarItem("video", "Videos", self._nav_container)
        item_docs = SidebarItem("file_text", "Documents", self._nav_container)
        item_favs = SidebarItem("star", "Favorites", self._nav_container)
        self.nav_bin = SidebarItem("trash", "Bin", self._nav_container)

        item_all.clicked.connect(lambda: self.nav_clicked.emit(0, item_all))
        item_photos.clicked.connect(lambda: self.nav_clicked.emit(1, item_photos))
        item_videos.clicked.connect(lambda: self.nav_clicked.emit(2, item_videos))
        item_docs.clicked.connect(lambda: self.nav_clicked.emit(3, item_docs))
        item_favs.clicked.connect(lambda: self.nav_clicked.emit(4, item_favs))
        self.nav_bin.clicked.connect(self.bin_clicked.emit)

        for w in (item_all, item_photos, item_videos, item_docs, item_favs, self.nav_bin):
            nav_lay.addWidget(w)

        nav_lay.addSpacing(8)
        self._divider = QFrame(self._nav_container)
        self._divider.setFixedHeight(1)
        nav_lay.addWidget(self._divider)
        nav_lay.addSpacing(8)

        item_intruder = SidebarItem("shield_alert", "Intruder Log", self._nav_container)
        item_applock = SidebarItem("shield_check", "App Lock", self._nav_container)
        item_settings = SidebarItem("settings", "Settings", self._nav_container)

        item_intruder.clicked.connect(lambda: self.nav_clicked.emit(5, item_intruder))
        item_applock.clicked.connect(lambda: self.nav_clicked.emit(6, item_applock))
        item_settings.clicked.connect(lambda: self.nav_clicked.emit(7, item_settings))

        nav_lay.addWidget(item_intruder)
        nav_lay.addWidget(item_applock)
        nav_lay.addWidget(item_settings)

        if self._is_decoy:
            item_intruder.hide()
            item_applock.hide()
            item_settings.hide()
            self._divider.hide()

        self.nav_items: list[SidebarItem] = [
            item_all, item_photos, item_videos, item_docs, item_favs,
            item_intruder, item_applock, item_settings,
        ]
        self._all_items = self.nav_items + [self.nav_bin]

        self._layout.addWidget(self._nav_container)
        self._layout.addStretch(1)

        # Bottom Lock Vault button
        self.lock_btn = QPushButton(" Lock Vault", self)
        self.lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock_btn.setAccessibleName("Lock Vault")
        self.lock_btn.setToolTip("Lock Vault (Ctrl+Alt+L)")
        self.lock_btn.setFixedHeight(36)
        self.lock_btn.setIconSize(QSize(16, 16))
        self.lock_btn.clicked.connect(self.lock_clicked.emit)
        self._layout.addWidget(self.lock_btn)

        self.set_active_item(item_all, animate=False)

    def set_active_item(self, item: Optional[SidebarItem], animate: bool = True) -> None:
        self._active_item = item
        for nav in self._all_items:
            nav.set_selected(nav is item)
        if item is not None:
            self._indicator.move_to_item(item, animate=animate)
        else:
            self._indicator.hide()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool, animate: bool = True) -> None:
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        target_w = self.COLLAPSED_WIDTH if collapsed else self.EXPANDED_WIDTH

        self.brand_widget.set_compact(collapsed)
        for nav in self._all_items:
            nav.set_compact(collapsed)

        self.collapse_btn.set_icon_name("sidebar_expand" if collapsed else "sidebar_collapse")
        self.collapse_btn.setToolTip("Expand sidebar" if collapsed else "Collapse sidebar")
        self.lock_btn.setText("" if collapsed else " Lock Vault")

        if animate and not _reduced_motion() and self.isVisible():
            self._width_min_anim.stop()
            self._width_max_anim.stop()
            self._width_min_anim.setStartValue(self.width())
            self._width_min_anim.setEndValue(target_w)
            self._width_max_anim.setStartValue(self.width())
            self._width_max_anim.setEndValue(target_w)
            self._width_min_anim.start()
            self._width_max_anim.start()
        else:
            self.setMinimumWidth(target_w)
            self.setMaximumWidth(target_w)

        if self._active_item is not None:
            self._indicator.move_to_item(self._active_item, animate=False)
        self.collapsed_changed.emit(collapsed)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed, animate=True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._active_item is not None:
            self._indicator.move_to_item(self._active_item, animate=False)

    def refresh_theme(self) -> None:
        p = Tokens.colors
        self.setStyleSheet(f"""
            #DashboardSidebar {{
                background-color: {p.bg_secondary};
                border-right: 1px solid {p.border};
            }}
        """)
        self._divider.setStyleSheet(f"background-color: {p.border}; border: none;")
        self.brand_widget.refresh_theme()
        self._indicator.refresh_theme()
        for nav in self._all_items:
            nav._update_style()
        self.lock_btn.setIcon(icons.get_icon("lock", color=p.text_secondary, active_color=p.danger, size=16))
        self.lock_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {p.text_secondary};
                border: 1px solid {p.border};
                border-radius: {Tokens.radius.CONTROL}px;
                padding: 0 10px;
                font-size: {Tokens.type.LABEL}px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {p.danger_tint};
                color: {p.danger};
                border-color: {p.danger_border};
            }}
            QPushButton:focus {{
                border: 2px solid {p.accent};
            }}
        """)


__all__ = ["SidebarWidget"]
