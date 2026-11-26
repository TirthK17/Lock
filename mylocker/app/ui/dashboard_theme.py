"""
dashboard_theme.py — Dashboard visual widgets backed by design_system.Tokens.

Provides backward-compatible `DashColors` alias + refactored flat, calm
Graphite/Periwinkle metric cards, category pills, vector doc/media badges,
search bar, and status indicators (zero neon glow/gradients).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .design_system import Tokens


def _dash_attr_map() -> dict[str, str]:
    p = Tokens.colors
    return {
        "BG_DARKEST": p.bg,
        "BG_SIDEBAR": p.bg_secondary,
        "BG_CONTENT": p.bg,
        "BG_POPUP": p.card,
        "BG_INPUT": p.input_bg,
        "BG_CARD": p.card,
        "BG_CARD_HOVER": p.elevated,
        "BORDER_SUBTLE": p.border,
        "BORDER_HOVER": p.border_strong,
        "BORDER_ACTIVE": p.accent,
        "ACCENT_BLUE": p.accent,
        "ACCENT_BRIGHT_BLUE": p.accent_hover,
        "ACCENT_PURPLE": p.accent,
        "ACCENT_CYAN": p.success,
        "ACCENT_GOLD": p.warning,
        "ACCENT_AMBER": p.warning,
        "ACCENT_GREEN": p.success,
        "ACCENT_RED": p.danger,
        "ACCENT_GRAD_START": p.accent,
        "ACCENT_GRAD_END": p.accent,
        "ACCENT_GRAD_HOVER_START": p.accent_hover,
        "ACCENT_GRAD_HOVER_END": p.accent_hover,
        "WHITE": p.white,
        "TEXT_PRIMARY": p.text,
        "TEXT_SECONDARY": p.text_secondary,
        "TEXT_MUTED": p.text_muted,
    }


class _DashAliasMeta(type):
    def __getattr__(cls, name: str) -> str:
        mapping = _dash_attr_map()
        if name in mapping:
            return mapping[name]
        raise AttributeError(f"DashColors has no attribute {name!r}")


class DashColors(metaclass=_DashAliasMeta):
    """Thin backward-compatible alias to design_system.Tokens."""


# ─── Minimal Static Hairline Accent ───────────────────────────────────────────

class WaveSparkline(QWidget):
    """Minimal subtle hairline sparkline indicator without continuous CPU polling."""

    def __init__(self, color_hex: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor(color_hex)
        self.setFixedHeight(28)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Keep non-active timer attribute for backwards compatibility without burning idle CPU
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(80)

    def set_color(self, color_hex: str) -> None:
        self._color = QColor(color_hex)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        if w <= 10 or h <= 10:
            return

        y_base = h - 10.0
        path = QPainterPath()
        path.moveTo(0, y_base + 2.0)
        path.cubicTo(w * 0.25, y_base + 4.0, w * 0.45, y_base - 8.0, w * 0.70, y_base - 1.0)
        path.cubicTo(w * 0.85, y_base + 3.0, w * 0.95, y_base - 4.0, w, y_base - 5.0)

        pen = QPen(
            QColor(self._color.red(), self._color.green(), self._color.blue(), 160),
            1.5,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        p.setPen(pen)
        p.drawPath(path)


# ─── Metric / Stat Card ───────────────────────────────────────────────────────

class MetricCard(QFrame):
    """Calm hairline-bordered summary card displaying an icon, counter, and label."""

    def __init__(
        self,
        icon_name: str,
        value: str,
        label: str,
        accent_color_hex: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._accent_color = QColor(accent_color_hex)
        self._value_str = value
        self._label_str = label
        self.setProperty("card", True)
        self.setMinimumHeight(76)
        self.setMinimumWidth(140)
        self.setObjectName("MetricCard")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        self._icon_box = QLabel(self)
        self._icon_box.setFixedSize(36, 36)
        self._icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._icon_box)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self._val_lbl = QLabel(value, self)
        self._val_lbl.setFont(Tokens.type.font(Tokens.type.PAGE_TITLE, Tokens.type.WEIGHT_TITLE))
        text_col.addWidget(self._val_lbl)

        self._lbl = QLabel(label, self)
        self._lbl.setProperty("role", "secondary")
        self._lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL))
        text_col.addWidget(self._lbl)

        layout.addLayout(text_col, 1)

        self._wave = WaveSparkline(accent_color_hex, self)
        self._wave.hide()

        self._apply_palette()

    def _apply_palette(self) -> None:
        p = Tokens.colors
        self.setStyleSheet(f"""
            #MetricCard {{
                background-color: {p.card};
                border: 1px solid {p.border};
                border-radius: {Tokens.radius.CARD}px;
            }}
        """)
        self._icon_box.setStyleSheet(f"""
            background-color: {p.elevated};
            border: 1px solid {p.border};
            border-radius: {Tokens.radius.CONTROL}px;
        """)
        self._icon_box.setPixmap(icons.get_pixmap(self._icon_name, size=18, color=self._accent_color.name()))
        self._val_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")
        self._lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")

    def set_value(self, val: str) -> None:
        self._value_str = val
        self._val_lbl.setText(val)


# ─── Category Filter Pill ─────────────────────────────────────────────────────

class CategoryPill(QPushButton):
    """Segmented category selector button without gradients or glow."""

    def __init__(
        self,
        icon_name: str,
        label: str,
        active: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._label = label
        self._active = bool(active)

        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL))
        self.setAccessibleName(label)
        self._update_appearance()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self._update_appearance()

    def is_active(self) -> bool:
        return self._active

    def _update_appearance(self) -> None:
        p = Tokens.colors
        r = Tokens.radius.CONTROL
        self.setText(f" {self._label}")
        if self._active:
            self.setIcon(icons.get_icon(self._icon_name, color=p.accent, active_color=p.accent, size=16))
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {p.accent_tint};
                    color: {p.text};
                    border: 1px solid {p.accent_border};
                    border-radius: {r}px;
                    padding: 0 12px;
                    min-height: 32px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {p.accent_tint};
                    border-color: {p.accent};
                }}
                QPushButton:focus {{
                    border: 2px solid {p.accent};
                }}
            """)
        else:
            self.setIcon(icons.get_icon(self._icon_name, color=p.text_secondary, active_color=p.text, size=16))
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {p.text_secondary};
                    border: 1px solid transparent;
                    border-radius: {r}px;
                    padding: 0 12px;
                    min-height: 32px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {p.hover_fill};
                    color: {p.text};
                    border-color: {p.border};
                }}
                QPushButton:focus {{
                    border: 2px solid {p.accent};
                }}
            """)


# ─── Flat Vector Document Badges ──────────────────────────────────────────────

class DocBadgeGenerator:
    """Generates clean flat-vector thumbnail sheets with colored file badges."""

    _CACHE: dict[str, QPixmap] = {}

    @classmethod
    def get_badge(cls, ext: str, width: int = 180, height: int = 100) -> QPixmap:
        p_theme = Tokens.colors
        ext_clean = ext.lower().lstrip(".")
        cache_key = f"{p_theme.name}_{ext_clean}_{width}_{height}"
        if cache_key in cls._CACHE:
            return cls._CACHE[cache_key]

        if ext_clean in ("pdf",):
            badge_label, color_hex = "PDF", p_theme.danger
        elif ext_clean in ("doc", "docx", "rtf", "odt"):
            badge_label, color_hex = ("DOC" if ext_clean == "doc" else "DOCX"), p_theme.accent
        elif ext_clean in ("xls", "xlsx", "csv", "tsv", "ods"):
            badge_label, color_hex = ("XLS" if ext_clean == "xls" else "SHEET"), p_theme.success
        elif ext_clean in ("ppt", "pptx", "odp"):
            badge_label, color_hex = "PPT", p_theme.warning
        elif ext_clean in ("zip", "rar", "7z", "tar", "gz", "bz2"):
            badge_label, color_hex = "ZIP", p_theme.warning
        elif ext_clean in ("mp3", "wav", "flac", "aac", "ogg", "m4a"):
            badge_label, color_hex = "AUDIO", p_theme.accent
        elif ext_clean in ("py", "js", "ts", "json", "html", "css", "cpp", "c", "rs", "go"):
            badge_label, color_hex = ext_clean.upper()[:4], p_theme.success
        elif ext_clean in ("txt", "md", "log"):
            badge_label, color_hex = "TXT", p_theme.text_secondary
        else:
            badge_label = ext_clean.upper()[:4] if ext_clean else "FILE"
            color_hex = p_theme.text_muted

        img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = float(width) / 2.0, float(height) / 2.0
        doc_h = max(32.0, round(float(height) * 0.56))
        doc_w = max(24.0, round(doc_h * 0.76))
        x, y = cx - doc_w / 2.0, cy - doc_h / 2.0
        corner = max(6.0, round(doc_w * 0.28))
        r = 4.0

        path = QPainterPath()
        path.moveTo(x + r, y)
        path.lineTo(x + doc_w - corner, y)
        path.lineTo(x + doc_w, y + corner)
        path.lineTo(x + doc_w, y + doc_h - r)
        path.quadTo(x + doc_w, y + doc_h, x + doc_w - r, y + doc_h)
        path.lineTo(x + r, y + doc_h)
        path.quadTo(x, y + doc_h, x, y + doc_h - r)
        path.lineTo(x, y + r)
        path.quadTo(x, y, x + r, y)

        sheet_bg = QColor("#E7E9EF") if p_theme.is_dark else QColor("#FFFFFF")
        p.fillPath(path, sheet_bg)
        p.setPen(QPen(QColor(0, 0, 0, 28), 1.0))
        p.drawPath(path)

        fold = QPainterPath()
        fold.moveTo(x + doc_w - corner, y)
        fold.lineTo(x + doc_w - corner, y + corner - 2)
        fold.quadTo(x + doc_w - corner, y + corner, x + doc_w - corner + 2, y + corner)
        fold.lineTo(x + doc_w, y + corner)
        p.fillPath(fold, QColor("#C6CBD6"))

        badge_w, badge_h = doc_w - 4.0, max(12.0, round(doc_h * 0.30))
        bx, by = cx - badge_w / 2.0, cy + 2.0
        badge_path = QPainterPath()
        badge_path.addRoundedRect(bx, by, badge_w, badge_h, 3.0, 3.0)
        p.fillPath(badge_path, QColor(color_hex))

        p.setPen(QColor("#0B0D12" if p_theme.is_dark else "#FFFFFF"))
        p.setFont(Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_TITLE))
        p.drawText(QRectF(bx, by, badge_w, badge_h), Qt.AlignmentFlag.AlignCenter, badge_label)

        p.end()
        pix = QPixmap.fromImage(img)
        cls._CACHE[cache_key] = pix
        return pix


# ─── Flat Media Placeholders (Photos & Videos) ────────────────────────────────

class MediaBadgeGenerator:
    """Generates clean flat placeholder thumbnails for photos and videos (no gradients/glow)."""

    _CACHE: dict[str, QPixmap] = {}

    @classmethod
    def get_image_placeholder(cls, width: int = 164, height: int = 96) -> QPixmap:
        pal = Tokens.colors
        cache_key = f"{pal.name}_img_{width}_{height}"
        if cache_key in cls._CACHE:
            return cls._CACHE[cache_key]

        img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0.5, 0.5, width - 1.0, height - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)
        p.fillPath(path, QColor(pal.bg_secondary))
        p.setPen(QPen(QColor(255, 255, 255, 16) if pal.is_dark else QColor(0, 0, 0, 18), 1.0))
        p.drawPath(path)

        ico = icons.get_pixmap("image", color=pal.text_muted, size=24)
        p.drawPixmap(int((width - 24) / 2), int((height - 24) / 2), ico)
        p.end()

        pix = QPixmap.fromImage(img)
        cls._CACHE[cache_key] = pix
        return pix

    @classmethod
    def get_video_placeholder(cls, width: int = 164, height: int = 96) -> QPixmap:
        pal = Tokens.colors
        cache_key = f"{pal.name}_vid_{width}_{height}"
        if cache_key in cls._CACHE:
            return cls._CACHE[cache_key]

        img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0.5, 0.5, width - 1.0, height - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)
        p.fillPath(path, QColor(pal.bg_secondary))
        p.setPen(QPen(QColor(255, 255, 255, 16) if pal.is_dark else QColor(0, 0, 0, 18), 1.0))
        p.drawPath(path)

        ico = icons.get_pixmap("video", color=pal.text_muted, size=24)
        p.drawPixmap(int((width - 24) / 2), int((height - 24) / 2), ico)
        p.end()

        pix = QPixmap.fromImage(img)
        cls._CACHE[cache_key] = pix
        return pix


# ─── Sidebar Logo & Protection Widget ─────────────────────────────────────────

class SidebarBrandWidget(QWidget):
    """Minimal vector shield emblem + 'Personal Vault' brand mark."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(10)

        self._icon_lbl = QLabel(self)
        self._icon_lbl.setFixedSize(28, 28)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._text_col_w = QWidget(self)
        text_col = QVBoxLayout(self._text_col_w)
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)

        self._title_lbl = QLabel("Personal Vault", self._text_col_w)
        self._title_lbl.setFont(Tokens.type.font(Tokens.type.SECTION, Tokens.type.WEIGHT_TITLE))

        self._sub_lbl = QLabel("AES-256-GCM", self._text_col_w)
        self._sub_lbl.setFont(Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_LABEL))

        text_col.addWidget(self._title_lbl)
        text_col.addWidget(self._sub_lbl)

        lay.addWidget(self._icon_lbl)
        lay.addWidget(self._text_col_w, 1)
        self.refresh_theme()

    def set_compact(self, compact: bool) -> None:
        self._text_col_w.setVisible(not compact)

    def refresh_theme(self) -> None:
        p = Tokens.colors
        self._icon_lbl.setStyleSheet(
            f"background: {p.accent_tint}; border: 1px solid {p.accent_border}; border-radius: 8px;"
        )
        self._icon_lbl.setPixmap(icons.get_pixmap("shield_lock", size=18, color=p.accent))
        self._title_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")
        self._sub_lbl.setStyleSheet(f"color: {p.text_muted}; background: transparent;")


class SidebarProtectionStatus(QWidget):
    """Quiet footer status badge indicating active vault encryption."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(32)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 8, 4)
        lay.setSpacing(8)

        self._shield_icon = QLabel(self)
        lay.addWidget(self._shield_icon)

        self._text_lbl = QLabel("Vault protection active", self)
        self._text_lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL))
        lay.addWidget(self._text_lbl)

        self._dot = QLabel("●", self)
        self._dot.setFont(Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_TITLE))
        lay.addWidget(self._dot)
        lay.addStretch()
        self.refresh_theme()

    def refresh_theme(self) -> None:
        p = Tokens.colors
        self._shield_icon.setPixmap(icons.get_pixmap("shield_check", size=16, color=p.success))
        self._shield_icon.setStyleSheet("background: transparent;")
        self._text_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")
        self._dot.setStyleSheet(f"color: {p.success}; background: transparent;")


# ─── Vault Unlocked Header Badge ──────────────────────────────────────────────

class VaultUnlockedBadge(QFrame):
    """Calm hairline pill indicating 'Vault Unlocked' status."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setObjectName("VaultUnlockedBadge")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 12, 4)
        lay.setSpacing(8)

        self._icon_lbl = QLabel(self)
        self._icon_lbl.setFixedSize(20, 20)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._icon_lbl)

        self._title_lbl = QLabel("Unlocked", self)
        self._title_lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL))
        lay.addWidget(self._title_lbl)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        p = Tokens.colors
        self.setStyleSheet(f"""
            #VaultUnlockedBadge {{
                background-color: {p.success_tint};
                border: 1px solid {p.success_border};
                border-radius: {Tokens.radius.CONTROL}px;
            }}
        """)
        self._icon_lbl.setStyleSheet("background: transparent;")
        self._icon_lbl.setPixmap(icons.get_pixmap("unlock", size=16, color=p.success))
        self._title_lbl.setStyleSheet(f"color: {p.success}; background: transparent;")


# ─── Vault Search Bar ─────────────────────────────────────────────────────────

class VaultSearchBar(QFrame):
    """Calm Raycast-style search input with search icon and Ctrl+F hint badge."""

    textChanged = Signal(str)

    def __init__(self, placeholder: str = "Search vault…", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setMinimumWidth(180)
        self.setMaximumWidth(380)
        self.setObjectName("VaultSearchBar")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(8)

        self._icon_lbl = QLabel(self)
        self._icon_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._icon_lbl)

        self._input = QLineEdit(self)
        self._input.setPlaceholderText(placeholder)
        self._input.setAccessibleName("Search vault files")
        self._input.textChanged.connect(self.textChanged.emit)
        lay.addWidget(self._input, 1)

        self._shortcut_badge = QLabel("Ctrl+F", self)
        self._shortcut_badge.setFont(Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_LABEL))
        lay.addWidget(self._shortcut_badge)

        self.refresh_theme()

    def refresh_theme(self) -> None:
        p = Tokens.colors
        r = Tokens.radius.CONTROL
        self.setStyleSheet(f"""
            #VaultSearchBar {{
                background-color: {p.input_bg};
                border: 1px solid {p.border};
                border-radius: {r}px;
            }}
            #VaultSearchBar:hover {{
                border-color: {p.border_strong};
            }}
            #VaultSearchBar:focus-within {{
                border: 2px solid {p.accent};
            }}
        """)
        self._icon_lbl.setPixmap(icons.get_pixmap("search", size=16, color=p.text_muted))
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: transparent;
                border: none;
                color: {p.text};
                font-size: {Tokens.type.BODY}px;
                min-height: 28px;
                padding: 0;
                selection-background-color: {p.selection_bg};
            }}
        """)
        self._shortcut_badge.setStyleSheet(f"""
            background-color: {p.elevated};
            color: {p.text_muted};
            border: 1px solid {p.border};
            border-radius: 4px;
            padding: 1px 6px;
        """)

    def text(self) -> str:
        return self._input.text()

    def setText(self, t: str) -> None:
        self._input.setText(t)

    def setFocus(self) -> None:
        self._input.setFocus()

    def hasFocus(self) -> bool:
        return self._input.hasFocus()

    def clear(self) -> None:
        self._input.clear()


# ─── Notification Bell Button ─────────────────────────────────────────────────

class NotificationBellButton(QPushButton):
    """Subtle icon button with semantic unread indicator dot."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(34, 34)
        self.setProperty("variant", "icon")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Notifications & Security Alerts")
        self.setAccessibleName("Notifications and security alerts")
        self._unread = True

    def set_unread(self, unread: bool) -> None:
        self._unread = bool(unread)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p_theme = Tokens.colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        bell_pix = icons.get_pixmap("bell", size=18, color=p_theme.text_secondary)
        bx = (self.width() - bell_pix.deviceIndependentSize().width()) / 2
        by = (self.height() - bell_pix.deviceIndependentSize().height()) / 2
        p.drawPixmap(QPointF(bx, by), bell_pix)

        if self._unread:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(p_theme.danger))
            p.drawEllipse(QPointF(self.width() - 9, 9), 3.0, 3.0)
        p.end()


__all__ = [
    "DashColors",
    "WaveSparkline",
    "MetricCard",
    "CategoryPill",
    "DocBadgeGenerator",
    "MediaBadgeGenerator",
    "SidebarBrandWidget",
    "SidebarProtectionStatus",
    "VaultUnlockedBadge",
    "VaultSearchBar",
    "NotificationBellButton",
]
