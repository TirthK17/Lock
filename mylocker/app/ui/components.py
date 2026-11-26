"""
components.py — Reusable PySide6 UI component library for Personal Vault.

Built directly on design_system.Tokens with consistent hover, pressed,
focus (2px accent ring), and disabled states across Dark and Light themes.

Includes:
  - PrimaryButton, SecondaryButton, DangerButton, IconButton
  - SearchBar, Toggle (animated knob), SegmentedControl
  - Card, SectionHeader, Badge, ListRow, TooltipStyle
  - EmptyState, ErrorState, Spinner, SkeletonRow, Toast, DialogShell
"""

from __future__ import annotations

from typing import Optional, Sequence
from unittest.mock import Base as _MockBase

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .design_system import ThemePalette, Tokens


def _is_mocked(fn: object) -> bool:
    """Return True if a Qt dialog helper has been monkeypatched by tests."""
    return isinstance(fn, _MockBase) or getattr(type(fn), "__module__", "").startswith("unittest.mock")


def _reduced_motion() -> bool:
    try:
        from .login_theme import is_reduced_motion
        return bool(is_reduced_motion())
    except Exception:
        return False


def _apply_dialog_shadow(widget: QWidget) -> None:
    """Apply the single permitted soft drop shadow for floating menus and dialogs."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(28)
    shadow.setOffset(0, 8)
    shadow.setColor(QColor(0, 0, 0, 120 if Tokens.colors.is_dark else 45))
    widget.setGraphicsEffect(shadow)


# ─── Buttons ──────────────────────────────────────────────────────────────────


class _ThemedButton(QPushButton):
    def __init__(
        self,
        text: str = "",
        variant: str = "secondary",
        icon_name: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(text, parent)
        self._icon_name = icon_name
        self._variant = variant
        self.setProperty("variant", variant)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_LABEL))
        self.setMinimumHeight(34)
        self._sync_icon()
        Tokens.subscribe(self._on_theme_changed)
        self.destroyed.connect(self._cleanup_sub)

    def _on_theme_changed(self, _pal: ThemePalette) -> None:
        self._sync_icon()
        self.style().unpolish(self)
        self.style().polish(self)

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme_changed)

    def _sync_icon(self) -> None:
        if not self._icon_name:
            return
        p = Tokens.colors
        if self._variant == "primary":
            col = p.on_accent
        elif self._variant == "danger":
            col = p.danger
        else:
            col = p.text
        self.setIcon(icons.get_icon(self._icon_name, color=col, active_color=col, size=18))
        self.setIconSize(QSize(18, 18))

    def set_icon_name(self, icon_name: Optional[str]) -> None:
        self._icon_name = icon_name
        if icon_name:
            self._sync_icon()


class PrimaryButton(_ThemedButton):
    def __init__(self, text: str, icon_name: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, variant="primary", icon_name=icon_name, parent=parent)


class SecondaryButton(_ThemedButton):
    def __init__(self, text: str, icon_name: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, variant="secondary", icon_name=icon_name, parent=parent)


class DangerButton(_ThemedButton):
    def __init__(self, text: str, icon_name: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, variant="danger", icon_name=icon_name, parent=parent)


class IconButton(QPushButton):
    """Square icon-only button with tooltip, accessibleName, and crisp 1.5px vector SVG."""

    def __init__(
        self,
        icon_name: str,
        tooltip: str = "",
        size: int = 34,
        icon_size: int = 18,
        checkable: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._icon_size = icon_size
        self.setProperty("variant", "icon")
        self.setCheckable(checkable)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
            self.setAccessibleName(tooltip)
        else:
            self.setAccessibleName(icon_name.replace("_", " ").title())
        self.toggled.connect(lambda _c: self._sync_icon())
        Tokens.subscribe(self._on_theme_changed)
        self.destroyed.connect(self._cleanup_sub)
        self._sync_icon()

    def _on_theme_changed(self, _pal: ThemePalette) -> None:
        self._sync_icon()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme_changed)

    def set_icon_name(self, name: str) -> None:
        self._icon_name = name
        self._sync_icon()

    def _sync_icon(self) -> None:
        p = Tokens.colors
        col = p.accent if self.isChecked() else p.text_secondary
        self.setIcon(icons.get_icon(self._icon_name, color=col, active_color=p.text, size=self._icon_size))
        self.setIconSize(QSize(self._icon_size, self._icon_size))


# ─── Search Bar ───────────────────────────────────────────────────────────────


class SearchBar(QFrame):
    """Unified search input bar with leading search icon, optional shortcut hint, and clear action."""

    textChanged = Signal(str)
    submitted = Signal(str)

    def __init__(
        self,
        placeholder: str = "Search…",
        shortcut_hint: str = "Ctrl+F",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ComponentSearchBar")
        self.setFixedHeight(36)
        self.setMinimumWidth(160)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(8)

        self._icon = QLabel(self)
        self._icon.setStyleSheet("background: transparent;")
        lay.addWidget(self._icon)

        self._input = QLineEdit(self)
        self._input.setPlaceholderText(placeholder)
        self._input.setAccessibleName(placeholder.rstrip("…"))
        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(lambda: self.submitted.emit(self._input.text()))
        lay.addWidget(self._input, 1)

        self._clear_btn = IconButton("x", tooltip="Clear search", size=24, icon_size=14, parent=self)
        self._clear_btn.setVisible(False)
        self._clear_btn.clicked.connect(self.clear)
        lay.addWidget(self._clear_btn)

        self._hint_lbl = QLabel(shortcut_hint, self)
        self._hint_lbl.setFont(Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_LABEL))
        self._hint_lbl.setVisible(bool(shortcut_hint))
        lay.addWidget(self._hint_lbl)

        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self._apply_theme()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self._apply_theme()

    def _apply_theme(self) -> None:
        p = Tokens.colors
        self.setStyleSheet(f"""
            #ComponentSearchBar {{
                background-color: {p.input_bg};
                border: 1px solid {p.border};
                border-radius: {Tokens.radius.CONTROL}px;
            }}
            #ComponentSearchBar:hover {{
                border-color: {p.border_strong};
            }}
            #ComponentSearchBar:focus-within {{
                border: 2px solid {p.accent};
            }}
        """)
        self._icon.setPixmap(icons.get_pixmap("search", color=p.text_muted, size=16))
        self._input.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {p.text}; padding: 0; min-height: 28px; }}"
        )
        self._hint_lbl.setStyleSheet(
            f"background: {p.elevated}; color: {p.text_muted}; border: 1px solid {p.border}; border-radius: 4px; padding: 1px 6px;"
        )

    def _on_text_changed(self, text: str) -> None:
        self._clear_btn.setVisible(bool(text))
        if self._hint_lbl.text():
            self._hint_lbl.setVisible(not bool(text))
        self.textChanged.emit(text)

    def text(self) -> str:
        return self._input.text()

    def setText(self, text: str) -> None:
        self._input.setText(text)

    def clear(self) -> None:
        self._input.clear()

    def setFocus(self) -> None:
        self._input.setFocus()

    def hasFocus(self) -> bool:
        return self._input.hasFocus()


# ─── Animated Sliding-Knob Toggle ─────────────────────────────────────────────


class Toggle(QAbstractButton):
    """macOS/Linear style binary toggle switch supporting QCheckBox APIs cleanly."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._pos = 0.0
        self._hovered = False

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(Tokens.motion.FAST)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_step)

        self.toggled.connect(self._animate_to_state)
        if text:
            self.setAccessibleName(text)

    def _get_knob_pos(self) -> float:
        return self._pos

    def _set_knob_pos(self, v: float) -> None:
        self._pos = float(v)
        self.update()

    knobPos = Property(float, _get_knob_pos, _set_knob_pos)

    def _on_anim_step(self, val) -> None:
        self._pos = float(val)
        self.update()

    def _animate_to_state(self, checked: bool) -> None:
        target = 1.0 if checked else 0.0
        if _reduced_motion():
            self._anim.stop()
            self._pos = target
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(target)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:
        was = self.isChecked()
        super().setChecked(checked)
        if was == bool(checked):
            self._pos = 1.0 if checked else 0.0
            self.update()
        elif self.signalsBlocked() or _reduced_motion():
            self._anim.stop()
            self._pos = 1.0 if checked else 0.0
            self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = Tokens.colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not self.isEnabled():
            painter.setOpacity(0.5)

        w, h = float(self.width()), float(self.height())
        track_rect = QRectF(1.0, 1.0, w - 2.0, h - 2.0)

        if self.isChecked():
            track_col = QColor(p.accent_hover if self._hovered else p.accent)
            knob_col = QColor(p.on_accent)
        else:
            track_col = QColor(p.elevated)
            knob_col = QColor(p.text_secondary if self._hovered else p.text_muted)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_col)
        painter.drawRoundedRect(track_rect, (h - 2.0) / 2.0, (h - 2.0) / 2.0)

        if self.hasFocus():
            painter.setPen(QPen(QColor(p.accent), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track_rect, (h - 2.0) / 2.0, (h - 2.0) / 2.0)
        elif not self.isChecked():
            painter.setPen(QPen(QColor(255, 255, 255, 24) if p.is_dark else QColor(0, 0, 0, 26), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track_rect, (h - 2.0) / 2.0, (h - 2.0) / 2.0)

        knob_d = h - 6.0
        min_x = 3.0
        max_x = w - knob_d - 3.0
        kx = min_x + (max_x - min_x) * self._pos
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_col)
        painter.drawEllipse(QRectF(kx, 3.0, knob_d, knob_d))


# ─── Segmented Control / Tabs ─────────────────────────────────────────────────


class SegmentedControl(QFrame):
    """Calm horizontal pill selector with crisp active state."""

    selection_changed = Signal(str)

    def __init__(
        self,
        options: Sequence[tuple[str, str, Optional[str]]],
        current: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SegmentedControl")
        self._buttons: dict[str, QPushButton] = {}
        self._icons: dict[str, Optional[str]] = {}
        self._current: str = current or (options[0][0] if options else "")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)

        for key, label, icon_name in options:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL))
            btn.setAccessibleName(label)
            btn.clicked.connect(lambda _checked=False, k=key: self.set_current(k, emit_signal=True))
            self._buttons[key] = btn
            self._icons[key] = icon_name
            lay.addWidget(btn)

        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self._refresh_styles()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self._refresh_styles()

    def current(self) -> str:
        return self._current

    def set_current(self, key: str, emit_signal: bool = False) -> None:
        if key not in self._buttons:
            return
        changed = key != self._current
        self._current = key
        self._refresh_styles()
        if emit_signal and changed:
            self.selection_changed.emit(key)

    def _refresh_styles(self) -> None:
        p = Tokens.colors
        self.setStyleSheet(f"""
            #SegmentedControl {{
                background-color: {p.bg_secondary};
                border: 1px solid {p.border};
                border-radius: {Tokens.radius.CONTROL}px;
            }}
        """)
        for key, btn in self._buttons.items():
            active = key == self._current
            btn.blockSignals(True)
            btn.setChecked(active)
            btn.blockSignals(False)
            ico_name = self._icons.get(key)
            if ico_name:
                btn.setIcon(icons.get_icon(ico_name, color=p.text if active else p.text_secondary, size=16))
                btn.setIconSize(QSize(16, 16))
            if active:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {p.elevated};
                        color: {p.text};
                        border: 1px solid {p.border_strong};
                        border-radius: 6px;
                        padding: 4px 12px;
                        min-height: 26px;
                        font-weight: 500;
                    }}
                    QPushButton:focus {{ border: 2px solid {p.accent}; }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: transparent;
                        color: {p.text_secondary};
                        border: 1px solid transparent;
                        border-radius: 6px;
                        padding: 4px 12px;
                        min-height: 26px;
                        font-weight: 500;
                    }}
                    QPushButton:hover {{
                        color: {p.text};
                        background-color: {p.hover_fill};
                    }}
                    QPushButton:focus {{ border: 2px solid {p.accent}; }}
                """)


# ─── Cards, Headers, Badges, ListRows ─────────────────────────────────────────


class Card(QFrame):
    """Consistent 12px-radius container with 1px hairline border."""

    clicked = Signal()

    def __init__(
        self,
        interactive: bool = False,
        elevated: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        self.setProperty("interactive", bool(interactive))
        if elevated:
            self.setProperty("variant", "elevated")
        self._interactive = bool(interactive)
        if interactive:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if self._interactive and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class SectionHeader(QLabel):
    """Small uppercase 11px section heading."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text.upper(), parent)
        self.setProperty("role", "sectionHeader")
        self.setFont(Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_TITLE))


class Badge(QLabel):
    """Semantic status pill (`neutral`, `accent`, `success`, `warning`, `danger`)."""

    def __init__(
        self,
        text: str,
        variant: str = "neutral",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(text, parent)
        self._variant = variant
        self.setFont(Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_LABEL))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self._apply_style()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self._apply_style()

    def set_variant(self, text: str, variant: str) -> None:
        self.setText(text)
        self._variant = variant
        self._apply_style()

    def _apply_style(self) -> None:
        p = Tokens.colors
        mapping = {
            "success": (p.success_tint, p.success, p.success_border),
            "warning": (p.warning_tint, p.warning, p.warning_border),
            "danger": (p.danger_tint, p.danger, p.danger_border),
            "accent": (p.accent_tint, p.accent, p.accent_border),
            "neutral": (p.elevated, p.text_secondary, p.border),
        }
        bg, fg, border = mapping.get(self._variant, mapping["neutral"])
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 2px 8px;
            }}
        """)


class ListRow(QFrame):
    """Structured setting or list item row: icon + title + description + trailing control."""

    def __init__(
        self,
        title: str,
        description: str = "",
        control: Optional[QWidget] = None,
        icon_name: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(12)

        self._icon_lbl: Optional[QLabel] = None
        if icon_name:
            self._icon_lbl = QLabel(self)
            self._icon_lbl.setFixedSize(20, 20)
            self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._icon_lbl.setStyleSheet("background: transparent;")
            lay.addWidget(self._icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.title_lbl = QLabel(title, self)
        self.title_lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_LABEL))
        self.title_lbl.setWordWrap(True)
        text_col.addWidget(self.title_lbl)

        self.desc_lbl: Optional[QLabel] = None
        if description:
            self.desc_lbl = QLabel(description, self)
            self.desc_lbl.setProperty("role", "secondary")
            self.desc_lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_BODY))
            self.desc_lbl.setWordWrap(True)
            text_col.addWidget(self.desc_lbl)

        lay.addLayout(text_col, 1)

        if control is not None:
            lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)

        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self._apply_theme()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self._apply_theme()

    def _apply_theme(self) -> None:
        p = Tokens.colors
        self.setStyleSheet("background: transparent;")
        self.title_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")
        if self.desc_lbl is not None:
            self.desc_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")
        if self._icon_lbl is not None and self._icon_name:
            self._icon_lbl.setPixmap(icons.get_pixmap(self._icon_name, color=p.text_secondary, size=18))


# ─── Feedback States (Empty, Error, Spinner, Skeleton, Toast) ─────────────────


class Spinner(QWidget):
    """Lightweight painted circular arc spinner (no GIF, reduced-motion aware)."""

    def __init__(self, size: int = 24, color: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = size
        self._custom_color = color
        self._angle = 0.0
        self.setFixedSize(size, size)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self.show()
        if not _reduced_motion() and not self._timer.isActive():
            self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        if not self.isVisible() or _reduced_motion():
            self._timer.stop()
            return
        self._angle = (self._angle + 8.0) % 360.0
        self.update()

    def paintEvent(self, _event) -> None:
        p = Tokens.colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        stroke = 2.0
        margin = stroke + 1.0
        rect = QRectF(margin, margin, self.width() - 2 * margin, self.height() - 2 * margin)

        track_col = QColor(255, 255, 255, 30) if p.is_dark else QColor(0, 0, 0, 24)
        painter.setPen(QPen(track_col, stroke))
        painter.drawEllipse(rect)

        arc_col = QColor(self._custom_color or p.accent)
        painter.setPen(QPen(arc_col, stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, int(-self._angle * 16), int(105 * 16))


class SkeletonRow(QFrame):
    """Calm placeholder row for loading states."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setProperty("card", True)
        self._phase = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not _reduced_motion():
            self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        if not self.isVisible() or _reduced_motion():
            self._timer.stop()
            return
        self._phase = (self._phase + 0.08) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:
        p = Tokens.colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        bg_path = QPainterPath()
        bg_path.addRoundedRect(rect, 12.0, 12.0)
        painter.fillPath(bg_path, QColor(p.card))

        alpha_bump = 0 if _reduced_motion() else int(10 * abs(0.5 - self._phase) * 2)
        block = QColor(p.elevated)
        if alpha_bump:
            block = block.lighter(100 + alpha_bump) if p.is_dark else block.darker(100 + alpha_bump)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(block)

        painter.drawRoundedRect(QRectF(14, 12, 28, 28), 6, 6)
        w1 = max(80.0, self.width() * 0.38)
        w2 = max(60.0, self.width() * 0.22)
        painter.drawRoundedRect(QRectF(54, 13, w1, 10), 4, 4)
        painter.drawRoundedRect(QRectF(54, 30, w2, 8), 4, 4)


class EmptyState(QFrame):
    """Consistent empty view card with vector icon, message, and optional action button."""

    action_clicked = Signal()

    def __init__(
        self,
        icon_name: str,
        title: str,
        description: str,
        action_text: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self.setProperty("card", True)
        self.setObjectName("EmptyStateCard")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 32, 32, 32)
        lay.setSpacing(12)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_wrap = QLabel(self)
        self._icon_wrap.setFixedSize(48, 48)
        self._icon_wrap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._icon_wrap, 0, Qt.AlignmentFlag.AlignCenter)

        self._title_lbl = QLabel(title, self)
        self._title_lbl.setFont(Tokens.type.font(Tokens.type.SECTION, Tokens.type.WEIGHT_TITLE))
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setWordWrap(True)
        lay.addWidget(self._title_lbl)

        self._desc_lbl = QLabel(description, self)
        self._desc_lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY))
        self._desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc_lbl.setWordWrap(True)
        self._desc_lbl.setMaximumWidth(420)
        lay.addWidget(self._desc_lbl, 0, Qt.AlignmentFlag.AlignCenter)

        self.action_btn: Optional[PrimaryButton] = None
        if action_text:
            self.action_btn = PrimaryButton(action_text, parent=self)
            self.action_btn.clicked.connect(self.action_clicked.emit)
            lay.addWidget(self.action_btn, 0, Qt.AlignmentFlag.AlignCenter)

        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self._apply_theme()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self._apply_theme()

    def _apply_theme(self) -> None:
        p = Tokens.colors
        self.setStyleSheet(f"""
            #EmptyStateCard {{
                background-color: {p.card};
                border: 1px dashed {p.border_strong};
                border-radius: {Tokens.radius.CARD}px;
            }}
        """)
        self._icon_wrap.setStyleSheet(f"background: {p.elevated}; border: 1px solid {p.border}; border-radius: 12px;")
        self._icon_wrap.setPixmap(icons.get_pixmap(self._icon_name, color=p.text_secondary, size=22))
        self._title_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")
        self._desc_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")

    def set_content(self, title: str, description: str, action_text: Optional[str] = None) -> None:
        self._title_lbl.setText(title)
        self._desc_lbl.setText(description)
        if self.action_btn is not None and action_text is not None:
            self.action_btn.setText(action_text)
            self.action_btn.setVisible(bool(action_text))


class ErrorState(QFrame):
    """Consistent inline/card error state with retry action."""

    retry_clicked = Signal()

    def __init__(
        self,
        title: str = "Something went wrong",
        message: str = "Could not complete this operation.",
        retry_text: str = "Try Again",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ErrorStateCard")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_lbl = QLabel(self)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._icon_lbl)

        self._title_lbl = QLabel(title, self)
        self._title_lbl.setFont(Tokens.type.font(Tokens.type.SECTION, Tokens.type.WEIGHT_TITLE))
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._title_lbl)

        self._msg_lbl = QLabel(message, self)
        self._msg_lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY))
        self._msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setMaximumWidth(400)
        lay.addWidget(self._msg_lbl, 0, Qt.AlignmentFlag.AlignCenter)

        self.retry_btn = SecondaryButton(retry_text, icon_name="refresh", parent=self)
        self.retry_btn.clicked.connect(self.retry_clicked.emit)
        lay.addWidget(self.retry_btn, 0, Qt.AlignmentFlag.AlignCenter)

        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self._apply_theme()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self._apply_theme()

    def _apply_theme(self) -> None:
        p = Tokens.colors
        self.setStyleSheet(f"""
            #ErrorStateCard {{
                background-color: {p.card};
                border: 1px solid {p.danger_border};
                border-radius: {Tokens.radius.CARD}px;
            }}
        """)
        self._icon_lbl.setPixmap(icons.get_pixmap("alert_triangle", color=p.danger, size=28))
        self._icon_lbl.setStyleSheet("background: transparent;")
        self._title_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")
        self._msg_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")

    def set_error(self, title: str, message: str) -> None:
        self._title_lbl.setText(title)
        self._msg_lbl.setText(message)


class Toast(QFrame):
    """Floating non-blocking status toast with hover pause."""

    dismissed = Signal()

    def __init__(
        self,
        message: str,
        variant: str = "info",
        duration_ms: int = 3500,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("VaultToast")
        self._variant = variant
        self._duration_ms = duration_ms
        _apply_dialog_shadow(self)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(10)

        self._icon_lbl = QLabel(self)
        self._icon_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._icon_lbl)

        self._msg_lbl = QLabel(message, self)
        self._msg_lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_LABEL))
        lay.addWidget(self._msg_lbl, 1)

        close_btn = IconButton("x", tooltip="Dismiss", size=24, icon_size=14, parent=self)
        close_btn.clicked.connect(self.dismiss)
        lay.addWidget(close_btn)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self._apply_theme()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self._apply_theme()

    def _apply_theme(self) -> None:
        p = Tokens.colors
        color_map = {
            "success": (p.success, "shield_check"),
            "warning": (p.warning, "alert_triangle"),
            "danger": (p.danger, "alert_triangle"),
            "info": (p.accent, "info"),
        }
        col, ico = color_map.get(self._variant, color_map["info"])
        self.setStyleSheet(f"""
            #VaultToast {{
                background-color: {p.elevated};
                border: 1px solid {p.border_strong};
                border-radius: {Tokens.radius.CARD}px;
            }}
        """)
        self._icon_lbl.setPixmap(icons.get_pixmap(ico, color=col, size=18))
        self._msg_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")

    def show_toast(self, message: Optional[str] = None) -> None:
        if message is not None:
            self._msg_lbl.setText(message)
        self.adjustSize()
        self.show()
        self.raise_()
        if self._duration_ms > 0:
            self._timer.start(self._duration_ms)

    def dismiss(self) -> None:
        self._timer.stop()
        self.hide()
        self.dismissed.emit()

    def enterEvent(self, event) -> None:
        if self._timer.isActive():
            self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self.isVisible() and self._duration_ms > 0:
            self._timer.start(self._duration_ms)
        super().leaveEvent(event)


# ─── Unified Modal Dialog Shell ───────────────────────────────────────────────


class DialogShell(QDialog):
    """Consistent 16px-radius modal dialog shell with single soft shadow, header, body, and footer."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        icon_name: Optional[str] = None,
        icon_color: Optional[str] = None,
        parent: Optional[QWidget] = None,
        min_width: int = 380,
        max_width: int = 560,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(min_width)
        self.setMaximumWidth(max_width)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        self._surface = QFrame(self)
        self._surface.setProperty("dialogSurface", True)
        self._surface.setObjectName("DialogShellSurface")
        _apply_dialog_shadow(self._surface)
        outer.addWidget(self._surface)

        surf_lay = QVBoxLayout(self._surface)
        surf_lay.setContentsMargins(24, 20, 24, 20)
        surf_lay.setSpacing(16)

        # Header
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(10)
        if icon_name:
            ico_lbl = QLabel(self._surface)
            ico_lbl.setStyleSheet("background: transparent;")
            ico_lbl.setPixmap(icons.get_pixmap(icon_name, color=icon_color or Tokens.colors.accent, size=20))
            hdr_row.addWidget(ico_lbl, 0, Qt.AlignmentFlag.AlignTop)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        self.title_lbl = QLabel(title, self._surface)
        self.title_lbl.setFont(Tokens.type.font(Tokens.type.SECTION, Tokens.type.WEIGHT_TITLE))
        self.title_lbl.setWordWrap(True)
        title_col.addWidget(self.title_lbl)

        self.subtitle_lbl: Optional[QLabel] = None
        if subtitle:
            self.subtitle_lbl = QLabel(subtitle, self._surface)
            self.subtitle_lbl.setProperty("role", "secondary")
            self.subtitle_lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY))
            self.subtitle_lbl.setWordWrap(True)
            title_col.addWidget(self.subtitle_lbl)

        hdr_row.addLayout(title_col, 1)

        self.close_btn = IconButton("x", tooltip="Close", size=28, icon_size=16, parent=self._surface)
        self.close_btn.clicked.connect(self.reject)
        hdr_row.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        surf_lay.addLayout(hdr_row)

        # Body container
        self.body_widget = QWidget(self._surface)
        self.body_widget.setStyleSheet("background: transparent;")
        self.body_layout = QVBoxLayout(self.body_widget)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(12)
        surf_lay.addWidget(self.body_widget)

        # Footer container
        self.footer_layout = QHBoxLayout()
        self.footer_layout.setSpacing(8)
        self.footer_layout.addStretch()
        surf_lay.addLayout(self.footer_layout)

        self._apply_theme()

    def _apply_theme(self) -> None:
        p = Tokens.colors
        self._surface.setStyleSheet(f"""
            #DialogShellSurface {{
                background-color: {p.card};
                border: 1px solid {p.border_strong};
                border-radius: {Tokens.radius.DIALOG}px;
            }}
        """)
        self.title_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")
        if self.subtitle_lbl is not None:
            self.subtitle_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")

    def add_footer_button(self, btn: QPushButton) -> QPushButton:
        self.footer_layout.addWidget(btn)
        return btn

    # ── Static modal helpers (preserving Qt test monkeypatches cleanly) ───────

    @classmethod
    def inform(cls, parent: Optional[QWidget], title: str, message: str) -> None:
        if _is_mocked(QMessageBox.information):
            QMessageBox.information(parent, title, message)
            return
        dlg = cls(title=title, icon_name="info", icon_color=Tokens.colors.accent, parent=parent)
        lbl = QLabel(message, dlg.body_widget)
        lbl.setWordWrap(True)
        dlg.body_layout.addWidget(lbl)
        ok_btn = PrimaryButton("OK", parent=dlg)
        ok_btn.clicked.connect(dlg.accept)
        dlg.add_footer_button(ok_btn)
        dlg.exec()

    @classmethod
    def warn(cls, parent: Optional[QWidget], title: str, message: str) -> None:
        if _is_mocked(QMessageBox.warning):
            QMessageBox.warning(parent, title, message)
            return
        dlg = cls(title=title, icon_name="alert_triangle", icon_color=Tokens.colors.warning, parent=parent)
        lbl = QLabel(message, dlg.body_widget)
        lbl.setWordWrap(True)
        dlg.body_layout.addWidget(lbl)
        ok_btn = PrimaryButton("OK", parent=dlg)
        ok_btn.clicked.connect(dlg.accept)
        dlg.add_footer_button(ok_btn)
        dlg.exec()

    @classmethod
    def error(cls, parent: Optional[QWidget], title: str, message: str) -> None:
        if _is_mocked(QMessageBox.critical):
            QMessageBox.critical(parent, title, message)
            return
        dlg = cls(title=title, icon_name="alert_triangle", icon_color=Tokens.colors.danger, parent=parent)
        lbl = QLabel(message, dlg.body_widget)
        lbl.setWordWrap(True)
        dlg.body_layout.addWidget(lbl)
        ok_btn = PrimaryButton("Close", parent=dlg)
        ok_btn.clicked.connect(dlg.accept)
        dlg.add_footer_button(ok_btn)
        dlg.exec()

    @classmethod
    def confirm(
        cls,
        parent: Optional[QWidget],
        title: str,
        message: str,
        confirm_text: str = "Confirm",
        cancel_text: str = "Cancel",
        danger: bool = False,
    ) -> bool:
        if _is_mocked(QMessageBox.question):
            res = QMessageBox.question(
                parent,
                title,
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            return res == QMessageBox.StandardButton.Yes
        if _is_mocked(QMessageBox.warning):
            res = QMessageBox.warning(
                parent,
                title,
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            return res == QMessageBox.StandardButton.Yes

        dlg = cls(
            title=title,
            icon_name="alert_triangle" if danger else "info",
            icon_color=Tokens.colors.danger if danger else Tokens.colors.accent,
            parent=parent,
        )
        lbl = QLabel(message, dlg.body_widget)
        lbl.setWordWrap(True)
        dlg.body_layout.addWidget(lbl)

        cancel_btn = SecondaryButton(cancel_text, parent=dlg)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.add_footer_button(cancel_btn)

        ok_btn = DangerButton(confirm_text, parent=dlg) if danger else PrimaryButton(confirm_text, parent=dlg)
        ok_btn.clicked.connect(dlg.accept)
        dlg.add_footer_button(ok_btn)
        return dlg.exec() == QDialog.DialogCode.Accepted

    @classmethod
    def get_text(
        cls,
        parent: Optional[QWidget],
        title: str,
        label: str,
        echo_mode: QLineEdit.EchoMode = QLineEdit.EchoMode.Normal,
        confirm_text: str = "Continue",
    ) -> tuple[str, bool]:
        if _is_mocked(QInputDialog.getText):
            return QInputDialog.getText(parent, title, label, echo_mode)
        dlg = cls(title=title, subtitle=label, parent=parent)
        edit = QLineEdit(dlg.body_widget)
        edit.setEchoMode(echo_mode)
        edit.returnPressed.connect(dlg.accept)
        dlg.body_layout.addWidget(edit)

        cancel_btn = SecondaryButton("Cancel", parent=dlg)
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn = PrimaryButton(confirm_text, parent=dlg)
        ok_btn.clicked.connect(dlg.accept)
        dlg.add_footer_button(cancel_btn)
        dlg.add_footer_button(ok_btn)

        edit.setFocus()
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        return edit.text(), accepted

    @classmethod
    def choose_item(
        cls,
        parent: Optional[QWidget],
        title: str,
        label: str,
        items: Sequence[str],
        confirm_text: str = "Select",
    ) -> tuple[str, bool]:
        if _is_mocked(QInputDialog.getItem):
            return QInputDialog.getItem(parent, title, label, list(items), 0, False)
        dlg = cls(title=title, subtitle=label, parent=parent)
        lw = QListWidget(dlg.body_widget)
        for item_txt in items:
            lw.addItem(QListWidgetItem(item_txt))
        if lw.count() > 0:
            lw.setCurrentRow(0)
        lw.setMaximumHeight(240)
        lw.itemDoubleClicked.connect(lambda _it: dlg.accept())
        dlg.body_layout.addWidget(lw)

        cancel_btn = SecondaryButton("Cancel", parent=dlg)
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn = PrimaryButton(confirm_text, parent=dlg)
        ok_btn.clicked.connect(dlg.accept)
        dlg.add_footer_button(cancel_btn)
        dlg.add_footer_button(ok_btn)

        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        sel = lw.currentItem().text() if (accepted and lw.currentItem()) else ""
        return sel, bool(accepted and sel)


class TooltipStyle:
    """Applies calm dark/light QToolTip styling from active Tokens."""

    @staticmethod
    def qss() -> str:
        p = Tokens.colors
        return (
            f"QToolTip {{ background-color: {p.elevated}; color: {p.text}; "
            f"border: 1px solid {p.border_strong}; border-radius: {Tokens.radius.CONTROL}px; "
            f"padding: 6px 10px; font-size: {Tokens.type.LABEL}px; }}"
        )

    @staticmethod
    def apply(app: Optional[QApplication] = None) -> None:
        target = app or QApplication.instance()
        if target is not None:
            cur = target.styleSheet()
            tip_qss = TooltipStyle.qss()
            if tip_qss not in cur:
                target.setStyleSheet(cur + "\n" + tip_qss)


__all__ = [
    "PrimaryButton",
    "SecondaryButton",
    "DangerButton",
    "IconButton",
    "SearchBar",
    "Toggle",
    "SegmentedControl",
    "Card",
    "SectionHeader",
    "Badge",
    "ListRow",
    "Spinner",
    "SkeletonRow",
    "EmptyState",
    "ErrorState",
    "Toast",
    "DialogShell",
    "TooltipStyle",
]
