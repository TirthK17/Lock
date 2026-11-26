"""
login_theme.py — Design tokens alias and restrained graphite UI components
for the Personal Vault Login Screen.

Preserves all public symbols and test hooks (`LoginColors`, `LoginTypography`,
`AmbientLoginBackdrop`, `ShieldLockEmblem`, `DividerWithSparkle`, `LoginCard`,
`PasswordInputField`, `KeyFileSelector`, `UnlockPrimaryButton`,
`SecurityStatusBadge`, `is_reduced_motion`) while replacing bright blue/glow
artwork with the calm Graphite/Periwinkle design system.
"""

from __future__ import annotations

import math
import os
import re
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from . import icons
from .design_system import Tokens, get_ui_resource_path


def get_login_resource_path(filename: str) -> Optional[str]:
    """Resolve UI resource path cleanly in dev and frozen bundles."""
    path = get_ui_resource_path(filename)
    return str(path) if path is not None else None


_RGBA_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([0-9.]+))?\s*\)")


def _parse_color(css_color: str, alpha_override: Optional[int] = None) -> QColor:
    m = _RGBA_RE.match(css_color.strip())
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = int(round(float(m.group(4)) * 255)) if m.group(4) is not None else 255
        if alpha_override is not None:
            a = alpha_override
        return QColor(r, g, b, a)
    c = QColor(css_color)
    if alpha_override is not None:
        c.setAlpha(alpha_override)
    return c


# ─── Design Tokens Alias ──────────────────────────────────────────────────────

def _login_color_map() -> dict[str, object]:
    p = Tokens.colors
    return {
        "BG_PRIMARY": p.bg,
        "BG_NAVY": p.bg_secondary,
        "CARD_BG": _parse_color(p.card),
        "CARD_BG_SUCCESS": _parse_color(p.elevated),
        "CARD_BORDER": _parse_color(p.border),
        "CARD_GLOW": _parse_color(p.accent_tint),
        "INPUT_BG": _parse_color(p.input_bg),
        "INPUT_BG_HEX": p.input_bg,
        "INPUT_BORDER": _parse_color(p.border_strong),
        "INPUT_FOCUS": p.accent,
        "TEXT_PRIMARY": p.text,
        "TEXT_TITLE": p.text,
        "TEXT_SECONDARY": p.text_secondary,
        "TEXT_MUTED": p.text_muted,
        "SUBTITLE": p.text_secondary,
        "LINK_HOVER": p.accent_hover,
        "TITLE_PERSONAL": p.text,
        "TITLE_VAULT": p.accent,
        "ICE_BLUE": p.accent_hover,
        "PRIMARY_BLUE": p.accent,
        "CYAN_BRIGHT": p.accent,
        "DEEP_BLUE": p.accent_pressed,
        "RECOVERY_BLUE": p.accent,
        "HALO": p.accent,
        "MOUNTAIN_FG": p.bg,
        "MOUNTAIN_BG": p.bg_secondary,
        "MOUNTAIN_HIGHLIGHT": p.border_strong,
        "SUCCESS": p.success,
        "STATUS_SHIELD": p.success,
        "STATUS_TEXT": p.text_secondary,
        "ERROR": p.danger,
        "WARNING": p.warning,
    }


class _LoginColorsMeta(type):
    def __getattr__(cls, name: str):
        mapping = _login_color_map()
        if name in mapping:
            return mapping[name]
        raise AttributeError(f"LoginColors has no attribute {name!r}")


class LoginColors(metaclass=_LoginColorsMeta):
    """Thin dynamic alias mapping login palette tokens to Tokens.colors."""


class LoginTypography:
    FAMILY = Tokens.type.FAMILY_PRIMARY
    FALLBACK = Tokens.type.FAMILY_FALLBACK

    @classmethod
    def font(cls, px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        return Tokens.type.font(px, weight)


def is_reduced_motion(settings_store=None) -> bool:
    """Respect explicit reduced-motion settings and Windows system animations setting."""
    from .motion import is_reduced_motion as _motion_is_reduced_motion

    return _motion_is_reduced_motion(settings_store)


# ─── Quiet Graphite Canvas Backdrop ───────────────────────────────────────────

class AmbientLoginBackdrop(QWidget):
    """Minimal quiet graphite/light canvas replacing legacy aurora mountains."""

    def __init__(self, reduced_motion: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._reduced_motion = reduced_motion
        self._hero_center_y_ratio = 0.14
        # Keep timer attribute for test API parity without unnecessary background repaints
        self._drift_timer = QTimer(self)
        self._drift_timer.setInterval(60)
        self._drift_timer.timeout.connect(self._tick_drift)

    def set_reduced_motion(self, reduced: bool) -> None:
        self._reduced_motion = bool(reduced)
        if self._reduced_motion:
            self._drift_timer.stop()

    def set_hero_anchor_y(self, y_px: int) -> None:
        if self.height() > 0:
            self._hero_center_y_ratio = max(0.06, min(0.32, y_px / float(self.height())))

    def start_ambient(self) -> None:
        # Honor test contract expecting drift timer active only while shown & motion allowed
        if not self._reduced_motion and self.isVisible() and not self._drift_timer.isActive():
            self._drift_timer.start()

    def stop_ambient(self) -> None:
        self._drift_timer.stop()

    def _tick_drift(self) -> None:
        win = self.window()
        minimized = bool(win.isMinimized()) if win is not None else False
        if self._reduced_motion or not self.isVisible() or minimized:
            self._drift_timer.stop()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(Tokens.colors.bg))


# ─── Flat Vector Brand Emblem ─────────────────────────────────────────────────

class ShieldLockEmblem(QWidget):
    """Clean flat-vector graphite/periwinkle vault emblem."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(56, 56)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._opacity = 1.0
        self._success_boost = 0.0
        self._played_entrance = False

        self._entrance_anim = QVariantAnimation(self)
        self._entrance_anim.setDuration(Tokens.motion.NORMAL)
        self._entrance_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._entrance_anim.valueChanged.connect(self._on_entrance_step)

        self._glow_anim = QVariantAnimation(self)
        self._glow_anim.setDuration(Tokens.motion.NORMAL)
        self._glow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._glow_anim.valueChanged.connect(self._on_glow_step)

    def _on_entrance_step(self, val) -> None:
        self._opacity = float(val)
        self.update()

    def _on_glow_step(self, val) -> None:
        self._success_boost = float(val)
        self.update()

    def play_entrance(self, reduced_motion: bool = False) -> None:
        if self._played_entrance:
            return
        self._played_entrance = True
        if reduced_motion:
            self._opacity = 1.0
            self.update()
            return
        self._opacity = 0.0
        self._entrance_anim.stop()
        self._entrance_anim.setStartValue(0.0)
        self._entrance_anim.setEndValue(1.0)
        self._entrance_anim.start()

    def flash_success(self, reduced_motion: bool = False) -> None:
        if reduced_motion:
            self._success_boost = 1.0
            self.update()
            return
        self._glow_anim.stop()
        self._glow_anim.setStartValue(0.0)
        self._glow_anim.setEndValue(1.0)
        self._glow_anim.start()

    def stop_all(self) -> None:
        self._entrance_anim.stop()
        self._glow_anim.stop()

    def paintEvent(self, _event) -> None:
        p = Tokens.colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self._opacity)

        rect = QRectF(4.0, 4.0, float(self.width()) - 8.0, float(self.height()) - 8.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 14.0, 14.0)

        bg = _parse_color(p.success_tint) if self._success_boost > 0.3 else QColor(p.card)
        border = QColor(p.success) if self._success_boost > 0.3 else _parse_color(p.border_strong)
        painter.fillPath(path, bg)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(path)

        ico_name = "shield_check" if self._success_boost > 0.3 else "shield_lock"
        ico_color = p.success if self._success_boost > 0.3 else p.accent
        px = icons.get_pixmap(ico_name, color=ico_color, size=24)
        sz = px.deviceIndependentSize()
        painter.drawPixmap(
            QPointF((self.width() - sz.width()) / 2.0, (self.height() - sz.height()) / 2.0),
            px,
        )


# ─── Quiet Hairline Divider ───────────────────────────────────────────────────

class DividerWithSparkle(QWidget):
    """Minimal 1px hairline divider."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(8)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        cy = float(self.height()) / 2.0
        painter.setPen(QPen(_parse_color(Tokens.colors.border), 1.0))
        painter.drawLine(QPointF(0.0, cy), QPointF(float(self.width()), cy))


# ─── Flat Graphite Login Card ─────────────────────────────────────────────────

class LoginCard(QFrame):
    """Calm 1px hairline card with damped invalid-shake animation."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("LoginCard")
        self.setMinimumWidth(360)
        self.setMaximumWidth(400)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._success_boost = 0.0
        self._shake_offset = 0.0

        self._shake_anim = QVariantAnimation(self)
        self._shake_anim.setDuration(Tokens.motion.NORMAL)
        self._shake_anim.valueChanged.connect(self._on_shake_step)

        self._success_anim = QVariantAnimation(self)
        self._success_anim.setDuration(Tokens.motion.NORMAL)
        self._success_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._success_anim.valueChanged.connect(self._on_success_step)

    def _on_shake_step(self, val) -> None:
        p = float(val)
        self._shake_offset = math.sin(p * math.pi * 4.0) * 6.0 * (1.0 - p)
        self.update()

    def _on_success_step(self, val) -> None:
        self._success_boost = float(val)
        self.update()

    def trigger_shake(self, reduced_motion: bool = False) -> None:
        if reduced_motion:
            return
        self._shake_anim.stop()
        self._shake_anim.setStartValue(0.0)
        self._shake_anim.setEndValue(1.0)
        self._shake_anim.start()

    def trigger_success(self, reduced_motion: bool = False) -> None:
        if reduced_motion:
            self._success_boost = 1.0
            self.update()
            return
        self._success_anim.stop()
        self._success_anim.setStartValue(0.0)
        self._success_anim.setEndValue(1.0)
        self._success_anim.start()

    def reset_visual_state(self) -> None:
        self._shake_anim.stop()
        self._success_anim.stop()
        self._shake_offset = 0.0
        self._success_boost = 0.0
        self.update()

    def stop_all(self) -> None:
        self._shake_anim.stop()
        self._success_anim.stop()

    def paintEvent(self, _event) -> None:
        pal = Tokens.colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if abs(self._shake_offset) > 0.05:
            painter.translate(self._shake_offset, 0.0)

        rect = QRectF(0.5, 0.5, float(self.width()) - 1.0, float(self.height()) - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, float(Tokens.radius.CARD), float(Tokens.radius.CARD))

        painter.fillPath(path, QColor(pal.card))
        border_col = QColor(pal.success) if self._success_boost > 0.3 else _parse_color(pal.border)
        painter.setPen(QPen(border_col, 1.0))
        painter.drawPath(path)


# ─── Password Input Field ─────────────────────────────────────────────────────

class _InnerPasswordLineEdit(QLineEdit):
    """QLineEdit reporting focus changes natively."""

    def __init__(self, on_focus_changed, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._on_focus_changed = on_focus_changed

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._on_focus_changed(True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._on_focus_changed(False)


class PasswordInputField(QFrame):
    """Clean bordered password input with eye reveal toggle."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(54)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._focused = False
        self._error_state = False
        self._focus_progress = 0.0

        self._focus_anim = QVariantAnimation(self)
        self._focus_anim.setDuration(Tokens.motion.FAST)
        self._focus_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._focus_anim.valueChanged.connect(self._on_focus_step)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 2, 6, 2)
        layout.setSpacing(8)

        self._lock_icon = QLabel(self)
        self._lock_icon.setFixedSize(16, 16)
        self._lock_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lock_icon.setStyleSheet("background: transparent;")

        self.line_edit = _InnerPasswordLineEdit(self._set_focused, self)
        self.line_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.line_edit.setPlaceholderText("Master password")
        self.line_edit.setAccessibleName("Master password")
        self.setFocusProxy(self.line_edit)

        self._eye_btn = QPushButton(self)
        self._eye_btn.setFixedSize(28, 28)
        self._eye_btn.setProperty("variant", "icon")
        self._eye_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._eye_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._eye_btn.setToolTip("Show or hide password")
        self._eye_btn.setAccessibleName("Show or hide password")
        self._eye_btn.setIconSize(QSize(16, 16))
        self._eye_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: none; border-radius: 6px;
                min-height: 28px; max-height: 28px; min-width: 28px; max-width: 28px; padding: 0;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.07); }
        """)
        self._eye_btn.clicked.connect(self.toggle_visibility)

        layout.addWidget(self._lock_icon)
        layout.addWidget(self.line_edit, 1)
        layout.addWidget(self._eye_btn)

        self.sync_eye_icon_from_mode()

    def _update_input_style(self, is_password: bool) -> None:
        pal = Tokens.colors
        if is_password:
            f = Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY)
            self.line_edit.setFont(f)
            self.line_edit.setStyleSheet(f"""
                QLineEdit {{
                    background: transparent; border: none; min-height: 0px;
                    font-size: {Tokens.type.BODY}px; font-weight: 400;
                    color: {pal.text}; selection-background-color: {pal.selection_bg}; padding: 0 2px;
                }}
            """)
        else:
            f = Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_LABEL)
            self.line_edit.setFont(f)
            self.line_edit.setStyleSheet(f"""
                QLineEdit {{
                    background: transparent; border: none; min-height: 0px;
                    font-size: {Tokens.type.BODY}px; font-weight: 500;
                    color: {pal.text}; selection-background-color: {pal.selection_bg}; padding: 0 2px;
                }}
            """)

    def _on_focus_step(self, val) -> None:
        self._focus_progress = float(val)
        self.update()

    def set_error_state(self, is_error: bool) -> None:
        if is_error != self._error_state:
            self._error_state = is_error
            self.update()

    def toggle_visibility(self) -> None:
        if self.line_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.line_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self.line_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.sync_eye_icon_from_mode()

    def sync_eye_icon_from_mode(self) -> None:
        pal = Tokens.colors
        is_hidden = self.line_edit.echoMode() == QLineEdit.EchoMode.Password
        self._update_input_style(is_password=is_hidden)
        self._lock_icon.setPixmap(icons.get_pixmap("lock", size=16, color=pal.text_secondary))
        self._eye_btn.setIcon(
            icons.get_icon(
                "eye" if is_hidden else "eye_off",
                color=pal.text_secondary if is_hidden else pal.accent,
                active_color=pal.text,
                size=16,
            )
        )

    def _set_focused(self, focused: bool) -> None:
        self._focused = bool(focused)
        self._focus_anim.stop()
        self._focus_anim.setStartValue(self._focus_progress)
        self._focus_anim.setEndValue(1.0 if focused else 0.0)
        self._focus_anim.start()

    def stop_all(self) -> None:
        self._focus_anim.stop()

    def paintEvent(self, _event) -> None:
        pal = Tokens.colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(1.0, 1.0, float(self.width()) - 2.0, float(self.height()) - 2.0)
        path = QPainterPath()
        path.addRoundedRect(rect, float(Tokens.radius.CONTROL), float(Tokens.radius.CONTROL))
        painter.fillPath(path, QColor(pal.input_bg))

        if self._error_state:
            painter.setPen(QPen(QColor(pal.danger), 1.5))
        elif self._focus_progress > 0.1:
            painter.setPen(QPen(QColor(pal.accent), 2.0))
        else:
            painter.setPen(QPen(_parse_color(pal.border_strong), 1.0))
        painter.drawPath(path)


# ─── Compact Key File Selector ────────────────────────────────────────────────

class KeyFileSelector(QFrame):
    """Quiet key-file picker row preserving backing QLineEdit test compatibility."""

    browse_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._state = "empty"
        self._required = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 6, 4)
        layout.setSpacing(8)

        self._file_icon = QLabel(self)
        self._file_icon.setFixedSize(16, 16)
        self._file_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._file_icon.setStyleSheet("background: transparent;")

        self._status_lbl = QLabel("No key file selected", self)
        self._status_lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_BODY))
        self._status_lbl.setMinimumWidth(60)

        self.backing_edit = QLineEdit(self)
        self.backing_edit.setVisible(False)
        self.backing_edit.textChanged.connect(self._on_path_text_changed)

        self.browse_btn = QPushButton("Browse", self)
        self.browse_btn.setProperty("variant", "secondary")
        self.browse_btn.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL))
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.setAccessibleName("Browse for key file")
        self.browse_btn.setFixedHeight(28)
        self.browse_btn.setStyleSheet(
            "QPushButton { min-height: 26px; max-height: 28px; padding: 0 10px; font-size: 12px; }"
        )
        self.browse_btn.clicked.connect(self.browse_clicked.emit)

        layout.addWidget(self._file_icon)
        layout.addWidget(self._status_lbl, 1)
        layout.addWidget(self.browse_btn)
        self.set_state("empty")

    def set_required(self, required: bool) -> None:
        self._required = bool(required)
        if not self.backing_edit.text().strip():
            self.set_state("empty")

    def set_state(self, state: str, filename: str = "") -> None:
        self._state = state
        pal = Tokens.colors
        base_css = f"background: transparent; font-size: {Tokens.type.LABEL}px;"
        if state == "loading":
            self._status_lbl.setText("Reading key file…")
            self._status_lbl.setStyleSheet(f"color: {pal.accent}; {base_css}")
            self._file_icon.setPixmap(icons.get_pixmap("file", size=16, color=pal.accent))
        elif state == "ready":
            txt = f"Key file ready · {filename}" if filename else "Key file ready"
            self._status_lbl.setText(txt)
            self._status_lbl.setStyleSheet(f"color: {pal.success}; {base_css}")
            self._file_icon.setPixmap(icons.get_pixmap("check", size=16, color=pal.success))
        elif state == "selected":
            txt = f"Key file selected · {filename}" if filename else "Key file selected"
            self._status_lbl.setText(txt)
            self._status_lbl.setStyleSheet(f"color: {pal.text}; {base_css}")
            self._file_icon.setPixmap(icons.get_pixmap("file", size=16, color=pal.accent))
        elif state == "error":
            self._status_lbl.setText("Unable to read key file")
            self._status_lbl.setStyleSheet(f"color: {pal.danger}; {base_css}")
            self._file_icon.setPixmap(icons.get_pixmap("alert_triangle", size=16, color=pal.danger))
        else:
            txt = "No key file selected (Required)" if self._required else "No key file selected"
            self._status_lbl.setText(txt)
            self._status_lbl.setStyleSheet(f"color: {pal.text_secondary}; {base_css}")
            self._file_icon.setPixmap(icons.get_pixmap("file", size=16, color=pal.text_muted))
        self.update()

    def _on_path_text_changed(self, text: str) -> None:
        p = text.strip()
        if not p:
            self.set_state("empty")
            self.setToolTip("")
            return
        self.setToolTip(p)
        name = os.path.basename(p)
        if not os.path.isfile(p):
            self.set_state("error", name)
            return
        try:
            with open(p, "rb") as fh:
                fh.read(1)
            self.set_state("ready", name)
        except OSError:
            self.set_state("error", name)

    def paintEvent(self, _event) -> None:
        pal = Tokens.colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0.5, 0.5, float(self.width()) - 1.0, float(self.height()) - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, float(Tokens.radius.CONTROL), float(Tokens.radius.CONTROL))
        painter.fillPath(path, QColor(pal.input_bg))

        if self._state == "error":
            border = QColor(pal.danger)
        elif self._state == "ready":
            border = _parse_color(pal.success_border)
        else:
            border = _parse_color(pal.border)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(path)


# ─── Primary Unlock CTA Button ────────────────────────────────────────────────

class UnlockPrimaryButton(QPushButton):
    """Solid calm periwinkle primary CTA without gradients or drop-shadow glow."""

    def __init__(self, text: str = "Unlock Vault", parent: Optional[QWidget] = None, height: int = 56) -> None:
        super().__init__(text, parent)
        self.setObjectName("UnlockPrimaryButton")
        self.setProperty("variant", "primary")
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._base_text = text
        self._mode = "normal"
        self._hovered = False
        self._pressed = False
        self._spin_angle = 0.0

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(16)
        self._spin_timer.timeout.connect(self._tick_spin)

        self.setStyleSheet(
            f"QPushButton#UnlockPrimaryButton {{ background: transparent; border: none; min-height: {height}px; max-height: {height}px; padding: 0; }}"
        )

    def _tick_spin(self) -> None:
        self._spin_angle = (self._spin_angle + 7.5) % 360.0
        self.update()

    def set_loading(self, loading: bool) -> None:
        if loading:
            self._mode = "loading"
            self.setText("Unlocking…")
            self.setEnabled(False)
            if not self._spin_timer.isActive():
                self._spin_timer.start()
        else:
            self._spin_timer.stop()
            self._mode = "normal"
            self.setText(self._base_text)
            self.setEnabled(True)
        self.update()

    def set_success(self) -> None:
        self._spin_timer.stop()
        self._mode = "success"
        self.setText("Unlocked")
        self.setEnabled(False)
        self.update()

    def reset_state(self) -> None:
        self._spin_timer.stop()
        self._mode = "normal"
        self.setText(self._base_text)
        self.setEnabled(True)
        self._pressed = False
        self.update()

    def stop_all(self) -> None:
        self._spin_timer.stop()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        pal = Tokens.colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        if not self.isEnabled() and self._mode == "normal":
            painter.setOpacity(0.55)

        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, float(Tokens.radius.CONTROL), float(Tokens.radius.CONTROL))

        if self._mode == "success":
            bg = QColor(pal.success)
            fg = QColor(pal.on_accent)
        elif self._pressed:
            bg = QColor(pal.accent_pressed)
            fg = QColor(pal.on_accent)
        elif self._hovered and self.isEnabled():
            bg = QColor(pal.accent_hover)
            fg = QColor(pal.on_accent)
        else:
            bg = QColor(pal.accent)
            fg = QColor(pal.on_accent)

        painter.fillPath(path, bg)

        if self.hasFocus():
            painter.setPen(QPen(QColor(pal.text), 2.0))
            painter.drawPath(path)

        label = self.text().strip()
        painter.setFont(Tokens.type.font(Tokens.type.SECTION, Tokens.type.WEIGHT_LABEL))
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(label)
        ico_sz = 18
        gap = 8
        total_w = ico_sz + gap + text_w
        start_x = (w - total_w) / 2.0
        iy = (h - ico_sz) / 2.0

        if self._mode == "loading":
            cx = start_x + ico_sz / 2.0
            cy = h / 2.0
            r = 7.0
            painter.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 80), 2.0))
            painter.drawEllipse(QPointF(cx, cy), r, r)
            painter.setPen(QPen(fg, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(cx - r, cy - r, r * 2.0, r * 2.0), int(-self._spin_angle * 16), int(110 * 16))
        else:
            ico_name = "check" if self._mode == "success" else "unlock"
            px = icons.get_pixmap(ico_name, color=fg.name(), size=ico_sz)
            painter.drawPixmap(QPointF(start_x, iy), px)

        painter.setPen(fg)
        painter.drawText(
            QRectF(start_x + ico_sz + gap, 0.0, float(text_w + 8), h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            label,
        )


# ─── Honest Security Status Badge ─────────────────────────────────────────────

class _PulseDot(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._color = QColor(Tokens.colors.success)
        self._intensity = 0.9

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.update()

    def set_intensity(self, val: float) -> None:
        self._intensity = max(0.35, min(1.0, float(val)))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self._color)
        c.setAlpha(int(255 * self._intensity))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c)
        painter.drawEllipse(QPointF(self.width() / 2.0, self.height() / 2.0), 3.5, 3.5)


class SecurityStatusBadge(QWidget):
    """Quiet bottom status indicator reflecting verified vault readiness."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._active = False
        self._pulse_phase = 0.0
        self._reduced_motion = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._shield_lbl = QLabel(self)
        self._shield_lbl.setFixedSize(16, 16)
        self._shield_lbl.setStyleSheet("background: transparent;")

        self._text_lbl = QLabel("Vault protection active", self)
        self._text_lbl.setFont(Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_LABEL))

        self._dot = _PulseDot(self)

        lay.addWidget(self._shield_lbl)
        lay.addWidget(self._text_lbl)
        lay.addWidget(self._dot)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(90)
        self._pulse_timer.timeout.connect(self._tick_pulse)

        self.set_protection_active(False)

    def set_reduced_motion(self, reduced: bool) -> None:
        self._reduced_motion = bool(reduced)
        if reduced:
            self._pulse_timer.stop()
            self._dot.set_intensity(0.9)
        elif self._active and self.isVisible():
            self._pulse_timer.start()

    def set_protection_active(self, active: bool, custom_text: Optional[str] = None) -> None:
        pal = Tokens.colors
        self._active = bool(active)
        base_f = f"background: transparent; font-size: {Tokens.type.META}px; font-weight: 500;"
        if active:
            self._shield_lbl.setPixmap(icons.get_pixmap("shield_check", size=16, color=pal.success))
            self._text_lbl.setText(custom_text or "Vault protection active")
            self._text_lbl.setStyleSheet(f"color: {pal.text_secondary}; {base_f}")
            self._dot.set_color(QColor(pal.success))
            self._dot.show()
            if not self._reduced_motion and self.isVisible():
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._shield_lbl.setPixmap(icons.get_pixmap("shield_alert", size=16, color=pal.warning))
            self._text_lbl.setText(custom_text or "Vault configuration needed")
            self._text_lbl.setStyleSheet(f"color: {pal.text_muted}; {base_f}")
            self._dot.set_color(QColor(pal.warning))
            self._dot.show()

    def stop_all(self) -> None:
        self._pulse_timer.stop()

    def _tick_pulse(self) -> None:
        if self._reduced_motion or not self.isVisible() or not self._active:
            self._pulse_timer.stop()
            return
        self._pulse_phase = (self._pulse_phase + 0.12) % (2.0 * math.pi)
        self._dot.set_intensity(0.65 + 0.35 * (0.5 + 0.5 * math.sin(self._pulse_phase)))


__all__ = [
    "AmbientLoginBackdrop",
    "DividerWithSparkle",
    "KeyFileSelector",
    "LoginCard",
    "LoginColors",
    "LoginTypography",
    "PasswordInputField",
    "SecurityStatusBadge",
    "ShieldLockEmblem",
    "UnlockPrimaryButton",
    "is_reduced_motion",
    "get_login_resource_path",
]
