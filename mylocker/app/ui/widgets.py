"""
widgets.py — Shared custom widgets built on ui/components.py and design_system.Tokens.

Preserves every existing widget name (`GlowButton`, `AnimatedCard`,
`PasswordStrengthBar`, `SearchBar`, `StatusBadge`, `SidebarItem`,
`ProgressOverlay`, `FadeLabel`, `SmoothScrollArea`, `SectionHeader`)
without gradients or drop-shadow button glow effects.
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
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .components import Badge, Card, PrimaryButton, SearchBar, SecondaryButton, SectionHeader, _apply_dialog_shadow
from .design_system import Tokens
from .theme import Colors

# ─── GlowButton (Clean Primary CTA without GPU-heavy blur glow) ──────────────


class GlowButton(PrimaryButton):
    """Primary CTA button (preserved symbol over calm PrimaryButton without blur effect)."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent=parent)


# ─── AnimatedCard ─────────────────────────────────────────────────────────────


class AnimatedCard(Card):
    """Lightweight interactive card widget with crisp hover border/fill feedback."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(interactive=True, parent=parent)
        self.setObjectName("AnimatedCard")


# ─── PasswordStrengthBar ──────────────────────────────────────────────────────


class PasswordStrengthBar(QWidget):
    """Live password strength meter with smooth value transition."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(4)

        self._label = QLabel("Enter a password", self)
        self._label.setProperty("secondary", True)
        self._label.setFont(Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_BODY))

        layout.addWidget(self._bar)
        layout.addWidget(self._label)

        self._anim = QPropertyAnimation(self._bar, b"value", self)
        self._anim.setDuration(Tokens.motion.SLOW)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def update_password(self, password: str) -> int:
        """Evaluate password with zxcvbn and update bar. Returns score 0-100."""
        from mylocker.app.core.password_policy import validate_password
        ok, msg, score_0_4 = validate_password(password)
        score_percent = score_0_4 * 25
        pal = Tokens.colors

        if not password:
            score_percent = 0
            text = "Enter a password (min 12 chars)"
            colour = pal.text_muted
        elif not ok:
            text = f"Weak: {msg}"
            colour = pal.danger
        elif score_0_4 == 3:
            colour = pal.accent
            text = "Good strength"
        else:
            colour = pal.success
            text = "Very strong"

        from .motion import _effective_reduced_motion
        if _effective_reduced_motion():
            self._bar.setValue(score_percent)
        else:
            self._anim.setStartValue(self._bar.value())
            self._anim.setEndValue(score_percent)
            self._anim.start()

        self._bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {colour}; border-radius: 2px; }}"
        )
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


# ─── StatusBadge ──────────────────────────────────────────────────────────────


class StatusBadge(Badge):
    """Small coloured status pill preserving legacy `.set_colour(text, colour)` API."""

    def __init__(self, text: str, colour: str = Colors.SUCCESS, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, variant=self._map_colour(colour), parent=parent)

    @staticmethod
    def _map_colour(colour: str) -> str:
        pal = Tokens.colors
        if colour == pal.danger:
            return "danger"
        if colour == pal.warning:
            return "warning"
        if colour == pal.accent:
            return "accent"
        if colour == pal.success:
            return "success"
        return "neutral"

    def set_colour(self, text: str, colour: str) -> None:
        self.set_variant(text, self._map_colour(colour))


# ─── SidebarItem ──────────────────────────────────────────────────────────────


class SidebarItem(QPushButton):
    """Sidebar navigation item with 12% accent pill + 2px left accent bar when active."""

    def __init__(self, icon_key_or_text: str, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon_key = icon_key_or_text
        self._label = label
        self._selected = False
        self._compact = False
        self.setAccessibleName(label)
        self.setToolTip(label)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(10)

        self._bar = QWidget(self)
        self._bar.setFixedSize(2, 18)
        layout.addWidget(self._bar)

        self._icon_lbl = QLabel(self)
        self._icon_lbl.setFixedSize(20, 20)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._icon_lbl)

        self._text_lbl = QLabel(label, self)
        self._text_lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_LABEL))
        layout.addWidget(self._text_lbl)
        layout.addStretch()

        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()

    def set_selected(self, sel: bool) -> None:
        self._selected = bool(sel)
        self._update_style()

    def set_compact(self, compact: bool) -> None:
        self._compact = bool(compact)
        self._text_lbl.setVisible(not compact)
        self._bar.setVisible(not compact or self._selected)
        self._update_style()

    def _update_style(self) -> None:
        p = Tokens.colors
        if self._selected:
            bg = p.accent_tint
            fg = p.text
            ico_col = p.accent
            self._bar.setStyleSheet(f"background-color: {p.accent}; border-radius: 1px;")
        else:
            bg = "transparent"
            fg = p.text_secondary
            ico_col = p.text_secondary
            self._bar.setStyleSheet("background-color: transparent;")

        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                border: 1px solid transparent;
                border-radius: {Tokens.radius.CONTROL}px;
                text-align: left;
                padding: 0;
                min-height: 36px;
            }}
            QPushButton:hover {{
                background-color: {p.accent_tint if self._selected else p.hover_fill};
            }}
            QPushButton:focus {{
                border: 2px solid {p.accent};
            }}
        """)
        self._text_lbl.setStyleSheet(
            f"color: {fg}; background: transparent; font-weight: {'600' if self._selected else '500'};"
        )
        try:
            self._icon_lbl.setPixmap(icons.get_pixmap(self._icon_key, size=18, color=ico_col))
        except Exception:
            self._icon_lbl.setText(self._icon_key)
            self._icon_lbl.setStyleSheet(f"color: {fg}; background: transparent;")


# ─── ProgressOverlay ──────────────────────────────────────────────────────────


class ProgressOverlay(QDialog):
    """Modal progress dialog with cancel button built cleanly on the Dialog surface."""

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
        self.setFixedSize(416, 196)

        outer_lay = QVBoxLayout(self)
        outer_lay.setContentsMargins(12, 12, 12, 12)

        surface = QFrame(self)
        surface.setObjectName("ProgressOverlaySurface")
        p = Tokens.colors
        surface.setStyleSheet(f"""
            #ProgressOverlaySurface {{
                background-color: {p.card};
                border: 1px solid {p.border_strong};
                border-radius: {Tokens.radius.DIALOG}px;
            }}
        """)
        _apply_dialog_shadow(surface)
        outer_lay.addWidget(surface)

        layout = QVBoxLayout(surface)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._title_lbl = QLabel(title, surface)
        self._title_lbl.setFont(Tokens.type.font(Tokens.type.SECTION, Tokens.type.WEIGHT_TITLE))
        self._title_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")

        self._status_lbl = QLabel("Starting…", surface)
        self._status_lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_BODY))
        self._status_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")

        self._bar = QProgressBar(surface)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)

        self._cancel_btn = SecondaryButton("Cancel", parent=surface)
        self._cancel_btn.setFixedWidth(104)
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


# ─── FadeLabel & SmoothScrollArea ─────────────────────────────────────────────


class FadeLabel(QLabel):
    """Status label that displays coloured feedback cleanly."""

    def show_text(self, text: str, colour: str = Colors.DANGER) -> None:
        self.setText(text)
        self.setStyleSheet(
            f"color: {colour}; background: transparent; font-size: {Tokens.type.LABEL}px;"
        )

    def auto_clear(self, ms: int = 4000) -> None:
        QTimer.singleShot(ms, self.clear)


class SmoothScrollArea(QScrollArea):
    """Frameless scroll area."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)


__all__ = [
    "GlowButton",
    "AnimatedCard",
    "PasswordStrengthBar",
    "SearchBar",
    "StatusBadge",
    "SidebarItem",
    "ProgressOverlay",
    "FadeLabel",
    "SmoothScrollArea",
    "SectionHeader",
]
