"""
widgets.py — Shared custom widgets with animations.

Includes:
  - AnimatedCard: hover glow + scale
  - PasswordStrengthBar: live strength meter
  - ProgressOverlay: modal progress with cancel
  - IconButton: icon-only button with tooltip
  - FadeLabel: label that fades in/out
  - SearchBar: styled search input
  - StatusBadge: coloured status pill
  - SmoothScrollArea: frameless scrollarea
  - GlowButton: primary CTA button with glow effect
  - SidebarItem: animated sidebar navigation item
"""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .theme import Colors

# ─── GlowButton ──────────────────────────────────────────────────────────────

class GlowButton(QPushButton):
    """Primary button with animated glow on hover."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setColor(QColor(Colors.ACCENT))
        self._shadow.setBlurRadius(0)
        self._shadow.setOffset(0, 0)
        self.setGraphicsEffect(self._shadow)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._anim = QPropertyAnimation(self._shadow, b"blurRadius", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def enterEvent(self, event):
        self._anim.setStartValue(self._shadow.blurRadius())
        self._anim.setEndValue(20)
        self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._anim.setStartValue(self._shadow.blurRadius())
        self._anim.setEndValue(0)
        self._anim.start()
        super().leaveEvent(event)


# ─── AnimatedCard ─────────────────────────────────────────────────────────────

class AnimatedCard(QFrame):
    """Lightweight interactive card widget with crisp hover border/fill feedback."""

    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("AnimatedCard")
        self.setStyleSheet(f"""
            #AnimatedCard {{
                background-color: {Colors.BG_CARD};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
            #AnimatedCard:hover {{
                border: 1px solid {Colors.ACCENT};
                background-color: {Colors.BG_CARD_HOVER};
            }}
        """)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ─── PasswordStrengthBar ──────────────────────────────────────────────────────

class PasswordStrengthBar(QWidget):
    """Live password strength meter with animated colour transition."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(4)

        self._label = QLabel("Enter a password")
        self._label.setProperty("secondary", True)
        self._label.setFont(QFont("Segoe UI", 11))

        layout.addWidget(self._bar)
        layout.addWidget(self._label)

        self._anim = QPropertyAnimation(self._bar, b"value", self)
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def update_password(self, password: str) -> int:
        """Evaluate password with zxcvbn and update bar. Returns score 0-100."""
        from mylocker.app.core.password_policy import validate_password
        ok, msg, score_0_4 = validate_password(password)
        score_percent = score_0_4 * 25

        if not password:
            score_percent = 0
            text = "Enter a password (min 12 chars)"
            colour = Colors.TEXT_MUTED
        elif not ok:
            text = f"Weak: {msg}"
            colour = Colors.DANGER
        elif score_0_4 == 3:
            colour = Colors.PRIMARY
            text = "Good strength ✓"
        else:
            colour = Colors.SUCCESS
            text = "Very Strong ✓"

        from .login_theme import is_reduced_motion
        if is_reduced_motion():
            self._bar.setValue(score_percent)
        else:
            self._anim.setStartValue(self._bar.value())
            self._anim.setEndValue(score_percent)
            self._anim.start()

        self._bar.setStyleSheet(f"""
            QProgressBar::chunk {{
                background: {colour};
                border-radius: 2px;
            }}
        """)
        self._label.setText(text)
        return score_percent

    def _score(self, password: str) -> int:
        if not password:
            return 0
        score = 0
        score += min(len(password) * 4, 40)
        if re.search(r"[A-Z]", password):
            score += 10
        if re.search(r"[a-z]", password):
            score += 10
        if re.search(r"\d", password):
            score += 10
        if re.search(r"[^A-Za-z0-9]", password):
            score += 15
        if len(password) >= 16:
            score += 15
        # Penalty for common patterns
        if re.search(r"(.)\1{2,}", password):
            score -= 10
        if password.lower() in _COMMON_PASSWORDS:
            score = 5
        return max(0, min(100, score))


_COMMON_PASSWORDS = {
    "password", "123456", "12345678", "qwerty", "abc123", "password1",
    "admin", "letmein", "welcome", "monkey", "dragon", "master",
    "sunshine", "princess", "football", "shadow", "superman", "michael",
}


# ─── SearchBar ───────────────────────────────────────────────────────────────

class SearchBar(QLineEdit):
    """Styled search input with icon prefix."""

    def __init__(self, placeholder: str = "Search…", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setStyleSheet(f"""
            QLineEdit {{
                background: {Colors.BG_INPUT};
                border: 1px solid {Colors.BORDER};
                border-radius: 20px;
                padding: 7px 14px 7px 36px;
                color: {Colors.TEXT_PRIMARY};
                font-size: 13px;
                min-height: 36px;
            }}
            QLineEdit:focus {{
                border-color: {Colors.BORDER_FOCUS};
            }}
        """)


# ─── StatusBadge ─────────────────────────────────────────────────────────────

class StatusBadge(QLabel):
    """Small coloured pill label."""

    def __init__(self, text: str, colour: str = Colors.SUCCESS,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.set_colour(text, colour)

    def set_colour(self, text: str, colour: str) -> None:
        self.setText(text)
        self.setStyleSheet(f"""
            QLabel {{
                background: {colour}22;
                color: {colour};
                border: 1px solid {colour}44;
                border-radius: 10px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }}
        """)


# ─── SidebarItem ─────────────────────────────────────────────────────────────

class SidebarItem(QPushButton):
    """Animated sidebar navigation button supporting vector SVG icons."""

    def __init__(self, icon_key_or_text: str, label: str,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon_key = icon_key_or_text
        self._label = label
        self._selected = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(24, 24)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._text_lbl = QLabel(label)
        self._text_lbl.setFont(QFont("Segoe UI", 13))

        layout.addWidget(self._icon_lbl)
        layout.addWidget(self._text_lbl)
        layout.addStretch()

        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()

    def set_selected(self, sel: bool) -> None:
        self._selected = sel
        self._update_style()

    def _update_style(self) -> None:
        if self._selected:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4C65E8, stop:1 #637EFF)"
            fg = "#FFFFFF"
            border = "border: 1px solid rgba(255, 255, 255, 0.25);"
        else:
            bg = "transparent"
            fg = "#AAB7CC"
            border = "border: 1px solid transparent;"

        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                {border}
                border-radius: 14px;
                text-align: left;
                padding-left: 6px;
            }}
            QPushButton:hover {{
                background: {'qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5C75F8, stop:1 #738EFF)' if self._selected else 'rgba(255, 255, 255, 0.05)'};
            }}
        """)
        self._text_lbl.setStyleSheet(f"color: {fg}; background: transparent; font-weight: {'600' if self._selected else '500'}; font-size: 13px;")

        try:
            from . import icons
            pix = icons.get_pixmap(self._icon_key, size=18, color=fg)
            self._icon_lbl.setPixmap(pix)
        except Exception:
            self._icon_lbl.setText(self._icon_key)
            self._icon_lbl.setStyleSheet(f"color: {fg}; background: transparent;")


# ─── ProgressOverlay ─────────────────────────────────────────────────────────

class ProgressOverlay(QDialog):
    """Modal progress dialog with cancel button."""

    cancelled = Signal()

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedSize(400, 180)

        outer = QWidget(self)
        outer.setGeometry(0, 0, 400, 180)
        outer.setStyleSheet(f"""
            QWidget {{
                background: {Colors.BG_CARD};
                border: 1px solid {Colors.BORDER};
                border-radius: 16px;
            }}
        """)

        layout = QVBoxLayout(outer)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        self._title_lbl = QLabel(title)
        self._title_lbl.setProperty("subtitle", True)
        self._title_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 15px; font-weight: 600; background: transparent;")

        self._status_lbl = QLabel("Starting…")
        self._status_lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px; background: transparent;")

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setProperty("ghost", True)
        self._cancel_btn.setFixedWidth(100)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._cancel_btn)

        layout.addWidget(self._title_lbl)
        layout.addWidget(self._status_lbl)
        layout.addWidget(self._bar)
        layout.addLayout(btn_row)

    def _on_cancel_clicked(self) -> None:
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Cancelling…")
        self._status_lbl.setText("Cancelling safely…")
        self.cancelled.emit()

    def set_progress(self, done: int, total: int, label: str = "") -> None:
        pct = int(done / total * 100) if total > 0 else 0
        self._bar.setValue(pct)
        if label:
            self._status_lbl.setText(label)

    def set_indeterminate(self, label: str = "Working…") -> None:
        self._bar.setRange(0, 0)
        self._status_lbl.setText(label)


# ─── FadeLabel ───────────────────────────────────────────────────────────────

class FadeLabel(QLabel):
    """Status label that displays coloured feedback instantaneously."""

    def show_text(self, text: str, colour: str = Colors.DANGER) -> None:
        self.setText(text)
        self.setStyleSheet(f"color: {colour}; background: transparent; font-size: 12px;")

    def auto_clear(self, ms: int = 4000) -> None:
        QTimer.singleShot(ms, self.clear)


# ─── SmoothScrollArea ─────────────────────────────────────────────────────────

class SmoothScrollArea(QScrollArea):
    """Frameless scroll area."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)


# ─── SectionHeader ────────────────────────────────────────────────────────────

class SectionHeader(QLabel):
    """Small all-caps section header label."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setStyleSheet(f"""
            color: {Colors.TEXT_MUTED};
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1.5px;
            background: transparent;
        """)


__all__ = [
    "GlowButton", "AnimatedCard", "PasswordStrengthBar", "SearchBar",
    "StatusBadge", "SidebarItem", "ProgressOverlay", "FadeLabel",
    "SmoothScrollArea", "SectionHeader",
]

