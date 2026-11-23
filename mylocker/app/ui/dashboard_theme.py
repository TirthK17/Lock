"""
dashboard_theme.py — Design system & UI components for Personal Vault Dashboard.
Replicates the ultra-clean, minimal aesthetic reference with:
  - Deep space midnight theme (#070B14 / #0A0F1D)
  - 4 Stat Metric Cards with glowing neon sine-wave sparklines
  - Modern Category Filter Pills (All Files, Photos, Videos, Documents, Favorites)
  - Flat vector file-type badges (PDF in red, Word in blue, ZIP in amber, etc.)
  - Video play overlay badge with duration tag
  - Topbar search with 'Ctrl + F' badge & notification bell
  - Sidebar with glowing shield logo, mountain backdrop, and protection status
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
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

# ─── Color Palette ────────────────────────────────────────────────────────────

class DashColors:
    BG_DARKEST = "#060B16"
    BG_SIDEBAR = "#08101F"
    BG_CONTENT = "#091122"
    BG_POPUP = "#0B1120"
    BG_INPUT = "#0E1626"
    BG_CARD = "rgba(16, 27, 48, 0.78)"
    BG_CARD_HOVER = "rgba(24, 38, 68, 0.90)"
    BORDER_SUBTLE = "rgba(125, 160, 220, 0.18)"
    BORDER_HOVER = "rgba(91, 140, 255, 0.45)"
    BORDER_ACTIVE = "#5B8CFF"

    ACCENT_BLUE = "#5B8CFF"
    ACCENT_BRIGHT_BLUE = "#70A7FF"
    ACCENT_PURPLE = "#A66CFF"
    ACCENT_CYAN = "#43D9C5"
    ACCENT_GOLD = "#F4C76B"
    ACCENT_AMBER = "#F4C76B"
    ACCENT_GREEN = "#39D6A2"
    ACCENT_RED = "#FF6F86"

    ACCENT_GRAD_START = "#4C65E8"
    ACCENT_GRAD_END = "#637EFF"
    ACCENT_GRAD_HOVER_START = "#5C75F8"
    ACCENT_GRAD_HOVER_END = "#738EFF"

    WHITE = "#FFFFFF"
    TEXT_PRIMARY = "#F4F7FF"
    TEXT_SECONDARY = "#AAB7CC"
    TEXT_MUTED = "#6E7D96"


# ─── Glowing Sine-Wave Sparkline ──────────────────────────────────────────────

class WaveSparkline(QWidget):
    """Paints a smooth, glowing ambient wave curve at the bottom of a stat card."""

    def __init__(self, color_hex: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor(color_hex)
        self._phase = 0.0
        self.setFixedHeight(36)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # Gentle breathing / shimmer animation (stops on hide/reduced motion)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(80)
        self._anim_timer.timeout.connect(self._on_tick)
        self._anim_timer.start()

    def _on_tick(self) -> None:
        if self.isVisible():
            self._phase += 0.05
            if self._phase > 2.0 * math.pi:
                self._phase -= 2.0 * math.pi
            self.update()

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

        y_base = h - 14.0
        # Gentle dynamic oscillation
        dy = math.sin(self._phase) * 2.0

        path = QPainterPath()
        path.moveTo(0, y_base + 6.0 + dy)
        path.cubicTo(
            w * 0.25, y_base + 10.0 - dy,
            w * 0.45, y_base - 14.0 + dy,
            w * 0.70, y_base - 2.0 - dy,
        )
        path.cubicTo(
            w * 0.85, y_base + 6.0 + dy,
            w * 0.95, y_base - 6.0 - dy,
            w, y_base - 8.0 + dy,
        )

        # Gradient fill below curve
        fill_path = QPainterPath(path)
        fill_path.lineTo(w, h)
        fill_path.lineTo(0, h)
        fill_path.closeSubpath()

        grad = QLinearGradient(0, y_base - 16.0, 0, h)
        grad.setColorAt(0, QColor(self._color.red(), self._color.green(), self._color.blue(), 45))
        grad.setColorAt(1, QColor(self._color.red(), self._color.green(), self._color.blue(), 0))
        p.fillPath(fill_path, grad)

        # Outer soft glow stroke
        glow_pen = QPen(
            QColor(self._color.red(), self._color.green(), self._color.blue(), 55),
            5.5,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        p.setPen(glow_pen)
        p.drawPath(path)

        # Inner crisp neon stroke
        line_pen = QPen(
            QColor(self._color.red(), self._color.green(), self._color.blue(), 230),
            2.2,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        p.setPen(line_pen)
        p.drawPath(path)


# ─── Metric / Stat Card ───────────────────────────────────────────────────────

class MetricCard(QFrame):
    """Frosted glass metric card displaying an icon, big counter, label, and glowing wave."""

    def __init__(
        self,
        icon_name: str,
        value: str,
        label: str,
        accent_color_hex: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._accent_color = QColor(accent_color_hex)
        self._value_str = value
        self._label_str = label
        self.setFixedHeight(94)
        self.setMinimumWidth(180)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(), self.sizePolicy().verticalPolicy())
        self.setStyleSheet(f"""
            QFrame {{
                background: {DashColors.BG_CARD};
                border: 1px solid {DashColors.BORDER_SUBTLE};
                border-radius: 16px;
            }}
            QFrame:hover {{
                border: 1px solid {self._accent_color.name()};
                background: {DashColors.BG_CARD_HOVER};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 14, 14)
        layout.setSpacing(14)

        # Icon in soft rounded square
        self._icon_box = QLabel()
        self._icon_box.setFixedSize(42, 42)
        self._icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_box.setStyleSheet(f"""
            background: rgba({self._accent_color.red()}, {self._accent_color.green()}, {self._accent_color.blue()}, 0.16);
            border: 1px solid rgba({self._accent_color.red()}, {self._accent_color.green()}, {self._accent_color.blue()}, 0.35);
            border-radius: 12px;
        """)
        self._icon_box.setPixmap(icons.get_pixmap(icon_name, size=22, color=accent_color_hex))
        layout.addWidget(self._icon_box)

        # Numbers column
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self._val_lbl = QLabel(value)
        self._val_lbl.setFont(QFont("Segoe UI Variable", 17, QFont.Weight.Bold))
        self._val_lbl.setStyleSheet(f"color: {DashColors.TEXT_PRIMARY}; background: transparent;")
        text_col.addWidget(self._val_lbl)

        self._lbl = QLabel(label)
        self._lbl.setFont(QFont("Segoe UI Variable", 10, QFont.Weight.Medium))
        self._lbl.setStyleSheet(f"color: {DashColors.TEXT_SECONDARY}; background: transparent;")
        text_col.addWidget(self._lbl)

        layout.addLayout(text_col)
        layout.addStretch(1)

        # Ambient wave sparkline on right half
        self._wave = WaveSparkline(accent_color_hex, self)
        self._wave.setFixedWidth(85)
        layout.addWidget(self._wave, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_value(self, val: str) -> None:
        self._value_str = val
        self._val_lbl.setText(val)


# ─── Category Filter Pill ─────────────────────────────────────────────────────

class CategoryPill(QPushButton):
    """Pill-shaped category selector tab with active gradient glow and icon."""

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

        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(QFont("Segoe UI Variable", 11, QFont.Weight.Medium))
        self._update_appearance()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self._update_appearance()

    def is_active(self) -> bool:
        return self._active

    def _update_appearance(self) -> None:
        if self._active:
            self.setText(f"  {self._label}")
            self.setIcon(icons.get_icon(self._icon_name, color="#FFFFFF"))
            self.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4C65E8, stop:1 #637EFF);
                    color: #FFFFFF;
                    border: 1px solid rgba(255, 255, 255, 0.28);
                    border-radius: 18px;
                    padding: 0 18px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5C75F8, stop:1 #738EFF);
                }
            """)
        else:
            self.setText(f"  {self._label}")
            self.setIcon(icons.get_icon(self._icon_name, color=DashColors.TEXT_SECONDARY))
            self.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255, 255, 255, 0.04);
                    color: {DashColors.TEXT_SECONDARY};
                    border: 1px solid {DashColors.BORDER_SUBTLE};
                    border-radius: 18px;
                    padding: 0 16px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background: rgba(255, 255, 255, 0.08);
                    color: {DashColors.TEXT_PRIMARY};
                    border: 1px solid rgba(125, 160, 220, 0.35);
                }}
            """)


# ─── Flat Vector Document Badges ──────────────────────────────────────────────

class DocBadgeGenerator:
    """Generates crisp flat-vector thumbnail sheets with colored file badges (PDF, DOC, ZIP, etc.)."""

    _CACHE: dict[str, QPixmap] = {}

    @classmethod
    def get_badge(cls, ext: str, width: int = 180, height: int = 100) -> QPixmap:
        ext_clean = ext.lower().lstrip(".")
        cache_key = f"{ext_clean}_{width}_{height}"
        if cache_key in cls._CACHE:
            return cls._CACHE[cache_key]

        # Determine label and theme color
        if ext_clean in ("pdf",):
            badge_label = "PDF"
            color_hex = "#EF4444"  # Red
        elif ext_clean in ("doc", "docx", "rtf", "odt"):
            badge_label = "DOC" if ext_clean == "doc" else "DOCX"
            color_hex = "#2563EB"  # Blue
        elif ext_clean in ("xls", "xlsx", "csv", "tsv", "ods"):
            badge_label = "XLS" if ext_clean == "xls" else "SHEET"
            color_hex = "#10B981"  # Emerald
        elif ext_clean in ("ppt", "pptx", "odp"):
            badge_label = "PPT"
            color_hex = "#F97316"  # Orange
        elif ext_clean in ("zip", "rar", "7z", "tar", "gz", "bz2"):
            badge_label = "ZIP"
            color_hex = "#F59E0B"  # Amber
        elif ext_clean in ("mp3", "wav", "flac", "aac", "ogg", "m4a"):
            badge_label = "AUDIO"
            color_hex = "#8B5CF6"  # Purple
        elif ext_clean in ("py", "js", "ts", "json", "html", "css", "cpp", "c", "rs", "go"):
            badge_label = ext_clean.upper()[:4]
            color_hex = "#06B6D4"  # Cyan
        elif ext_clean in ("txt", "md", "log"):
            badge_label = "TXT"
            color_hex = "#64748B"  # Slate
        else:
            badge_label = ext_clean.upper()[:4] if ext_clean else "FILE"
            color_hex = "#475569"

        img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(11, 17, 32, 0))  # transparent
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Document paper silhouette in center (scaled dynamically to badge height)
        cx, cy = float(width) / 2.0, float(height) / 2.0
        doc_h = max(32.0, round(float(height) * 0.56))
        doc_w = max(24.0, round(doc_h * 0.76))
        x, y = cx - doc_w / 2.0, cy - doc_h / 2.0
        corner = max(6.0, round(doc_w * 0.28))
        r = 5.0

        # Paper body with folded top-right corner
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

        p.fillPath(path, QColor(255, 255, 255, 240))

        # Folded corner triangle
        fold = QPainterPath()
        fold.moveTo(x + doc_w - corner, y)
        fold.lineTo(x + doc_w - corner, y + corner - 2)
        fold.quadTo(x + doc_w - corner, y + corner, x + doc_w - corner + 2, y + corner)
        fold.lineTo(x + doc_w, y + corner)
        p.fillPath(fold, QColor(203, 213, 225, 240))

        # Badge banner pill with label
        badge_w, badge_h = doc_w - 4.0, max(12.0, round(doc_h * 0.30))
        bx, by = cx - badge_w / 2.0, cy + 2.0
        badge_path = QPainterPath()
        badge_path.addRoundedRect(bx, by, badge_w, badge_h, 3.5, 3.5)
        p.fillPath(badge_path, QColor(color_hex))

        # Badge text
        p.setPen(QColor(255, 255, 255))
        f_size = 7 if doc_h < 46 else 8
        p.setFont(QFont("Segoe UI Variable", f_size, QFont.Weight.Bold))
        p.drawText(QRectF(bx, by, badge_w, badge_h), Qt.AlignmentFlag.AlignCenter, badge_label)

        p.end()
        pix = QPixmap.fromImage(img)
        cls._CACHE[cache_key] = pix
        return pix


# ─── Dark Media Placeholders (Photos & Videos) ────────────────────────────────

class MediaBadgeGenerator:
    """Generates sleek dark-mode placeholder badges for photos and videos."""

    _CACHE: dict[str, QPixmap] = {}

    @classmethod
    def get_image_placeholder(cls, width: int = 164, height: int = 96) -> QPixmap:
        cache_key = f"img_{width}_{height}"
        if cache_key in cls._CACHE:
            return cls._CACHE[cache_key]

        img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(11, 17, 32, 255))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Subtle dark gradient background
        grad = QLinearGradient(0, 0, width, height)
        grad.setColorAt(0.0, QColor(16, 24, 44))
        grad.setColorAt(1.0, QColor(9, 13, 24))
        p.fillRect(QRectF(0, 0, width, height), grad)

        # Subtle border
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.drawRoundedRect(QRectF(1, 1, width - 2, height - 2), 10, 10)

        cx, cy = float(width) / 2.0, float(height) / 2.0

        # Ambient glow behind photo icon
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(56, 189, 248, 30))  # Glowing sky blue circle
        p.drawEllipse(QPointF(cx, cy - 2), 22, 22)

        # Camera / landscape glyph
        p.setPen(QPen(QColor(56, 189, 248, 220), 1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(cx - 13, cy - 10, 26, 20), 3, 3)
        p.drawEllipse(QPointF(cx - 3, cy - 1), 4.5, 4.5)
        p.setBrush(QColor(56, 189, 248, 220))
        p.drawEllipse(QPointF(cx + 6, cy - 5), 1.5, 1.5)

        p.end()
        pix = QPixmap.fromImage(img)
        cls._CACHE[cache_key] = pix
        return pix

    @classmethod
    def get_video_placeholder(cls, width: int = 164, height: int = 96) -> QPixmap:
        cache_key = f"vid_{width}_{height}"
        if cache_key in cls._CACHE:
            return cls._CACHE[cache_key]

        img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(11, 17, 32, 255))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dark cinematic gradient
        grad = QLinearGradient(0, 0, width, height)
        grad.setColorAt(0.0, QColor(18, 24, 46))
        grad.setColorAt(1.0, QColor(10, 14, 28))
        p.fillRect(QRectF(0, 0, width, height), grad)

        # Subtle border
        p.setPen(QPen(QColor(255, 255, 255, 20), 1))
        p.drawRoundedRect(QRectF(1, 1, width - 2, height - 2), 10, 10)

        # Subtle filmstrip notches at top and bottom edge
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 12))
        for x_notch in range(12, width - 12, 20):
            p.drawRoundedRect(QRectF(x_notch, 4, 10, 5), 1.5, 1.5)
            p.drawRoundedRect(QRectF(x_notch, height - 9, 10, 5), 1.5, 1.5)

        # Ambient glow behind play button
        cx, cy = float(width) / 2.0, float(height) / 2.0
        p.setBrush(QColor(139, 92, 246, 40))  # Violet ambient glow
        p.drawEllipse(QPointF(cx, cy), 24, 24)

        p.end()
        pix = QPixmap.fromImage(img)
        cls._CACHE[cache_key] = pix
        return pix


# ─── Sidebar Logo & Protection Widget ─────────────────────────────────────────

class SidebarBrandWidget(QWidget):
    """Displays the glowing shield emblem and 'Personal Vault' title with subtitle."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(56)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 8, 6)
        lay.setSpacing(12)

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(34, 34)
        self._icon_lbl.setScaledContents(True)

        from pathlib import Path
        shield_path = Path(__file__).resolve().parent / "resources" / "shield_brand.png"
        if shield_path.exists():
            self._icon_lbl.setPixmap(QPixmap(str(shield_path)).scaled(34, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self._icon_lbl.setPixmap(icons.get_pixmap("shield", size=30, color="#5B8CFF"))

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self._title_lbl = QLabel("Personal Vault")
        self._title_lbl.setFont(QFont("Segoe UI Variable", 13, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(f"color: {DashColors.TEXT_PRIMARY}; background: transparent;")

        self._sub_lbl = QLabel("Private • Secure • Yours")
        self._sub_lbl.setFont(QFont("Segoe UI Variable", 9, QFont.Weight.Medium))
        self._sub_lbl.setStyleSheet(f"color: {DashColors.TEXT_MUTED}; background: transparent;")

        text_col.addWidget(self._title_lbl)
        text_col.addWidget(self._sub_lbl)

        lay.addWidget(self._icon_lbl)
        lay.addLayout(text_col)
        lay.addStretch()


class SidebarProtectionStatus(QWidget):
    """Bottom sidebar status widget with shield, 'Vault protection active' and pulsing green dot."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(40)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(8)

        shield_icon = QLabel()
        shield_icon.setPixmap(icons.get_pixmap("shield_check", size=18, color=DashColors.ACCENT_CYAN))
        shield_icon.setStyleSheet("background: transparent;")
        lay.addWidget(shield_icon)

        text_lbl = QLabel("Vault protection active")
        text_lbl.setFont(QFont("Segoe UI Variable", 10, QFont.Weight.Medium))
        text_lbl.setStyleSheet(f"color: {DashColors.TEXT_SECONDARY}; background: transparent;")
        lay.addWidget(text_lbl)

        # Pulsing emerald dot
        self._dot = QLabel("●")
        self._dot.setFont(QFont("Segoe UI Variable", 11, QFont.Weight.Bold))
        self._dot.setStyleSheet(f"color: {DashColors.ACCENT_GREEN}; background: transparent;")
        lay.addWidget(self._dot)
        lay.addStretch()


# ─── Vault Unlocked Header Badge ──────────────────────────────────────────────

class VaultUnlockedBadge(QFrame):
    """Header card in top right displaying 'Vault Unlocked — Secured & Encrypted'."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(50)
        self.setStyleSheet("""
            QFrame {
                background: rgba(14, 38, 36, 0.78);
                border: 1px solid rgba(57, 214, 162, 0.35);
                border-radius: 14px;
            }
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 16, 6)
        lay.setSpacing(10)

        # Emerald lock icon box
        icon_box = QLabel()
        icon_box.setFixedSize(30, 30)
        icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_box.setStyleSheet("""
            background: rgba(57, 214, 162, 0.18);
            border: 1px solid rgba(57, 214, 162, 0.40);
            border-radius: 8px;
        """)
        icon_box.setPixmap(icons.get_pixmap("unlock", size=16, color=DashColors.ACCENT_GREEN))
        lay.addWidget(icon_box)

        # Text column
        text_lay = QVBoxLayout()
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(1)

        title = QLabel("Vault Unlocked")
        title.setFont(QFont("Segoe UI Variable", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {DashColors.TEXT_PRIMARY}; background: transparent;")
        text_lay.addWidget(title)

        subtitle = QLabel("Secured & Encrypted")
        subtitle.setFont(QFont("Segoe UI Variable", 9, QFont.Weight.Medium))
        subtitle.setStyleSheet(f"color: {DashColors.ACCENT_GREEN}; background: transparent;")
        text_lay.addWidget(subtitle)

        lay.addLayout(text_lay)


# ─── Vault Search Bar ─────────────────────────────────────────────────────────

class VaultSearchBar(QFrame):
    """Pill search bar matching reference with search icon, Ctrl+F badge, and glow."""

    textChanged = Signal(str)

    def __init__(self, placeholder: str = "Search in your vault…", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setFixedWidth(420)
        self.setObjectName("VaultSearchBar")
        self.setStyleSheet(f"""
            #VaultSearchBar {{
                background: rgba(24, 37, 62, 0.65);
                border: 1px solid {DashColors.BORDER_SUBTLE};
                border-radius: 22px;
            }}
            #VaultSearchBar:hover {{
                border: 1px solid rgba(125, 160, 220, 0.35);
                background: rgba(28, 44, 74, 0.78);
            }}
            #VaultSearchBar:focus-within {{
                border: 1px solid {DashColors.ACCENT_BLUE};
                background: rgba(30, 48, 80, 0.90);
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 14, 0)
        lay.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(icons.get_pixmap("search", size=16, color=DashColors.TEXT_MUTED))
        icon_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(icon_lbl)

        self._input = QLineEdit()
        self._input.setPlaceholderText(placeholder)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: transparent;
                border: none;
                color: {DashColors.TEXT_PRIMARY};
                font-family: 'Segoe UI Variable', 'Segoe UI';
                font-size: 13px;
                selection-background-color: {DashColors.ACCENT_BLUE};
            }}
        """)
        self._input.textChanged.connect(self.textChanged.emit)
        lay.addWidget(self._input, 1)

        # Ctrl + F badge
        badge = QLabel("Ctrl + F")
        badge.setFont(QFont("Segoe UI Variable", 9, QFont.Weight.DemiBold))
        badge.setStyleSheet(f"""
            background: rgba(255, 255, 255, 0.06);
            color: {DashColors.TEXT_MUTED};
            border: 1px solid rgba(255, 255, 255, 0.10);
            border-radius: 6px;
            padding: 2px 7px;
        """)
        lay.addWidget(badge)

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
    """Notification bell button with red unread notification badge dot."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Notifications & Security Alerts")
        self.setStyleSheet(f"""
            QPushButton {{
                background: rgba(24, 37, 62, 0.65);
                border: 1px solid {DashColors.BORDER_SUBTLE};
                border-radius: 20px;
            }}
            QPushButton:hover {{
                background: rgba(36, 54, 90, 0.85);
                border: 1px solid rgba(125, 160, 220, 0.35);
            }}
        """)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw bell icon in center
        bell_pix = icons.get_pixmap("bell", size=18, color=DashColors.TEXT_SECONDARY)
        bx = (self.width() - bell_pix.width()) // 2
        by = (self.height() - bell_pix.height()) // 2
        p.drawPixmap(bx, by, bell_pix)

        # Draw small glowing red unread dot at top-right
        dot_x, dot_y = self.width() - 12, 10
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(DashColors.ACCENT_RED))
        p.drawEllipse(QPointF(dot_x, dot_y), 3.5, 3.5)
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

