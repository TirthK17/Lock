"""

login_theme.py — Design tokens and modular custom UI components for the
Personal Vault Login Screen (Midnight Black + Deep Navy + Ice Blue + Electric Cyan).
"""

from __future__ import annotations

import math
import os
import sys
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
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
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


def get_login_resource_path(filename: str) -> Optional[str]:
    """Resolve path to UI assets in both standard development and frozen PyInstaller bundles."""
    base_dir = getattr(sys, "_MEIPASS", None)
    if base_dir:
        cand = os.path.join(base_dir, "mylocker", "app", "ui", "resources", filename)
        if os.path.isfile(cand):
            return cand
        cand = os.path.join(base_dir, "resources", filename)
        if os.path.isfile(cand):
            return cand
    local_cand = os.path.join(os.path.dirname(__file__), "resources", filename)
    if os.path.isfile(local_cand):
        return local_cand
    return None

# ─── Design Tokens ────────────────────────────────────────────────────────────


class LoginColors:
    BG_PRIMARY = "#050A12"
    BG_NAVY = "#08111D"
    CARD_BG = QColor(14, 24, 40, 228)         # rgba(14, 24, 40, 0.89)
    CARD_BG_SUCCESS = QColor(18, 34, 54, 224)
    CARD_BORDER = QColor(140, 200, 255, 110)
    CARD_GLOW = QColor(56, 189, 248, 55)

    INPUT_BG = QColor(10, 18, 30, 235)        # rgba(10, 18, 30, 0.92)
    INPUT_BORDER = QColor(70, 110, 160, 80)   # rgba(70, 110, 160, 0.31)
    INPUT_FOCUS = "#38BDF8"

    TEXT_PRIMARY = "#FFFFFF"
    TEXT_SECONDARY = "#8FA0B8"
    TEXT_MUTED = "#64748B"
    SUBTITLE = "#8FA0B8"

    TITLE_PERSONAL = "#FFFFFF"
    TITLE_VAULT = "#58A6FF"

    ICE_BLUE = "#B7DFFF"
    PRIMARY_BLUE = "#38BDF8"
    CYAN_BRIGHT = "#00D2FF"
    DEEP_BLUE = "#0D47A1"
    RECOVERY_BLUE = "#58A6FF"
    HALO = "#8FC8FF"

    MOUNTAIN_FG = "#07101B"
    MOUNTAIN_BG = "#0B1726"
    MOUNTAIN_HIGHLIGHT = "#6F9BC8"

    SUCCESS = "#10B981"
    STATUS_SHIELD = "#22D3EE"
    STATUS_TEXT = "#CBD5E1"
    ERROR = "#FF4D6D"
    WARNING = "#FBBF24"


class LoginTypography:
    FAMILY = "Segoe UI Variable"
    FALLBACK = "Segoe UI"

    @classmethod
    def font(cls, px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        f = QFont(cls.FAMILY)
        f.setFamilies([cls.FAMILY, cls.FALLBACK, "sans-serif"])
        f.setPixelSize(px)
        f.setWeight(weight)
        return f


def is_reduced_motion(settings_store=None) -> bool:
    """Respect explicit reduced-motion settings cleanly when present."""
    if settings_store is not None:
        try:
            if bool(settings_store.get("reduced_motion", False)):
                return True
        except Exception:
            pass
    return False


# ─── Atmospheric Background Backdrop Painter ──────────────────────────────────


class AmbientLoginBackdrop(QWidget):
    """Cached dark midnight canvas with soft circular halo, silky ribbons & mountain ridges."""

    def __init__(self, reduced_motion: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._reduced_motion = reduced_motion
        self._hero_center_y_ratio = 0.14
        self._base_cache: Optional[QPixmap] = None
        self._cache_key: Optional[tuple] = None
        self._drift_phase = 0.0

        # High-resolution photorealistic backdrop asset (mountains, halo & ribbons)
        self._bg_pixmap: Optional[QPixmap] = None
        bg_path = get_login_resource_path("login_bg.png")
        if bg_path and os.path.isfile(bg_path):
            self._bg_pixmap = QPixmap(bg_path)

        self._drift_timer = QTimer(self)
        self._drift_timer.setInterval(45)
        self._drift_timer.timeout.connect(self._tick_drift)

    def set_reduced_motion(self, reduced: bool) -> None:
        self._reduced_motion = bool(reduced)
        if self._reduced_motion:
            self._drift_timer.stop()
            self._drift_phase = 0.0
            self.update()
        elif self.isVisible():
            self._drift_timer.start()

    def set_hero_anchor_y(self, y_px: int) -> None:
        if self.height() > 0:
            ratio = max(0.06, min(0.32, y_px / float(self.height())))
            if abs(ratio - self._hero_center_y_ratio) > 0.015:
                self._hero_center_y_ratio = ratio
                self._base_cache = None
                self.update()

    def start_ambient(self) -> None:
        if not self._reduced_motion and self.isVisible() and not self._drift_timer.isActive():
            self._drift_timer.start()

    def stop_ambient(self) -> None:
        self._drift_timer.stop()

    def _tick_drift(self) -> None:
        win = self.window()
        minimized = bool(win.isMinimized()) if win is not None else False
        if self._reduced_motion or not self.isVisible() or minimized:
            self._drift_timer.stop()
            return
        self._drift_phase = (self._drift_phase + 0.025) % (2.0 * math.pi)
        self.update()

    def resizeEvent(self, event) -> None:
        self._base_cache = None
        super().resizeEvent(event)

    def _ensure_base_cache(self) -> QPixmap:
        w = max(1, self.width())
        h = max(1, self.height())
        dpr = max(1.0, float(self.devicePixelRatioF()))
        key = (w, h, round(dpr, 2), round(self._hero_center_y_ratio, 3))
        if self._base_cache is not None and self._cache_key == key:
            return self._base_cache

        px = QPixmap(int(round(w * dpr)), int(round(h * dpr)))
        px.setDevicePixelRatio(dpr)
        px.fill(QColor(LoginColors.BG_PRIMARY))

        painter = QPainter(px)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Primary render: draw photorealistic backdrop asset when available
        if self._bg_pixmap is not None and not self._bg_pixmap.isNull():
            bw = float(self._bg_pixmap.width())
            bh = float(self._bg_pixmap.height())
            scale_factor = max(w / bw, h / bh)
            sw = bw * scale_factor
            sh = bh * scale_factor
            ox = (w - sw) / 2.0
            oy = (h - sh) / 2.0
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(QRectF(ox, oy, sw, sh), self._bg_pixmap, QRectF(0.0, 0.0, bw, bh))
            painter.end()
            self._base_cache = px
            self._cache_key = key
            return px

        # Fallback procedural vector painter (if asset file is missing in headless/testing env)
        # 1. Subtle deep navy vertical/diagonal background tonal depth
        bg_grad = QLinearGradient(0.0, 0.0, float(w) * 0.65, float(h))
        bg_grad.setColorAt(0.0, QColor("#04080F"))
        bg_grad.setColorAt(0.50, QColor(LoginColors.BG_PRIMARY))
        bg_grad.setColorAt(1.0, QColor(LoginColors.BG_NAVY))
        painter.fillRect(QRectF(0.0, 0.0, float(w), float(h)), QBrush(bg_grad))

        # Subtle atmospheric radial light around upper hero
        cx = float(w) * 0.5
        hero_y = float(h) * self._hero_center_y_ratio
        ambient_top = QRadialGradient(cx, hero_y + 20.0, max(w, h) * 0.44)
        ambient_top.setColorAt(0.0, QColor(61, 157, 255, 36))
        ambient_top.setColorAt(0.45, QColor(23, 104, 216, 16))
        ambient_top.setColorAt(1.0, QColor(5, 10, 18, 0))
        painter.fillRect(QRectF(0.0, 0.0, float(w), float(h)), QBrush(ambient_top))

        # 2. Large subtle circular halo arc behind shield-lock logo
        halo_radius = max(170.0, min(260.0, min(w, h) * 0.32))
        halo_cy = max(hero_y + halo_radius * 0.64, halo_radius + 8.0)
        halo_rect = QRectF(cx - halo_radius, halo_cy - halo_radius, halo_radius * 2.0, halo_radius * 2.0)

        # Soft outer bloom + delicate fading top rim arc
        for stroke_w, alpha in ((18.0, 8), (9.0, 18), (3.2, 56), (1.3, 128)):
            arc_grad = QLinearGradient(cx, halo_cy - halo_radius, cx, halo_cy + halo_radius * 0.25)
            arc_grad.setColorAt(0.0, QColor(200, 232, 255, min(255, alpha + 28)))
            arc_grad.setColorAt(0.50, QColor(143, 200, 255, int(alpha * 0.72)))
            arc_grad.setColorAt(1.0, QColor(143, 200, 255, 0))
            pen = QPen(QBrush(arc_grad), stroke_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # Top ~110° crest-only arc (35° to 145° in Qt 1/16th degree units)
            painter.drawArc(halo_rect, 35 * 16, 110 * 16)

        # 3. Flowing translucent ice-blue light ribbons originating from left & right margins
        self._draw_ribbon(
            painter,
            start=QPointF(-w * 0.04, h * 0.25),
            c1=QPointF(w * 0.14, h * 0.32),
            c2=QPointF(w * 0.10, h * 0.52),
            end=QPointF(w * 0.38, h * 0.66),
            thickness=54.0,
            tint=QColor("#6FA8FF"),
            rim=QColor("#B7DFFF"),
            alpha_scale=0.95,
        )
        self._draw_ribbon(
            painter,
            start=QPointF(-w * 0.05, h * 0.42),
            c1=QPointF(w * 0.16, h * 0.56),
            c2=QPointF(w * 0.25, h * 0.46),
            end=QPointF(w * 0.44, h * 0.62),
            thickness=34.0,
            tint=QColor("#3B8DFF"),
            rim=QColor("#83C7FF"),
            alpha_scale=0.65,
        )
        self._draw_ribbon(
            painter,
            start=QPointF(w * 1.04, h * 0.22),
            c1=QPointF(w * 0.85, h * 0.30),
            c2=QPointF(w * 0.88, h * 0.52),
            end=QPointF(w * 0.64, h * 0.59),
            thickness=52.0,
            tint=QColor("#83C7FF"),
            rim=QColor("#B7DFFF"),
            alpha_scale=0.95,
        )
        self._draw_ribbon(
            painter,
            start=QPointF(w * 1.05, h * 0.38),
            c1=QPointF(w * 0.84, h * 0.52),
            c2=QPointF(w * 0.78, h * 0.40),
            end=QPointF(w * 0.60, h * 0.54),
            thickness=30.0,
            tint=QColor("#6FA8FF"),
            rim=QColor("#B7DFFF"),
            alpha_scale=0.62,
        )

        # 4. Subtle dark navy mountain silhouettes in bottom 10-18% corners
        self._draw_mountains(painter, float(w), float(h))

        painter.end()
        self._base_cache = px
        self._cache_key = key
        return px

    @staticmethod
    def _draw_ribbon(
        painter: QPainter,
        start: QPointF,
        c1: QPointF,
        c2: QPointF,
        end: QPointF,
        thickness: float,
        tint: QColor,
        rim: QColor,
        alpha_scale: float = 1.0,
    ) -> None:
        # Upper and lower edges offset gracefully to create soft tapered glass wave
        upper = QPainterPath(start)
        upper.cubicTo(c1, c2, end)

        lower = QPainterPath(QPointF(end.x(), end.y() + thickness * 0.45))
        lower.cubicTo(
            QPointF(c2.x(), c2.y() + thickness * 1.15),
            QPointF(c1.x(), c1.y() + thickness * 0.90),
            QPointF(start.x(), start.y() + thickness * 0.70),
        )

        band = QPainterPath(upper)
        band.connectPath(lower)
        band.closeSubpath()

        fill_grad = QLinearGradient(start, end)
        fill_grad.setColorAt(0.0, QColor(tint.red(), tint.green(), tint.blue(), int(46 * alpha_scale)))
        fill_grad.setColorAt(0.5, QColor(tint.red(), tint.green(), tint.blue(), int(22 * alpha_scale)))
        fill_grad.setColorAt(1.0, QColor(tint.red(), tint.green(), tint.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(band, QBrush(fill_grad))

        # Soft outer atmospheric bloom along ribbon crest
        wide_grad = QLinearGradient(start, end)
        wide_grad.setColorAt(0.0, QColor(rim.red(), rim.green(), rim.blue(), int(22 * alpha_scale)))
        wide_grad.setColorAt(0.60, QColor(rim.red(), rim.green(), rim.blue(), int(10 * alpha_scale)))
        wide_grad.setColorAt(1.0, QColor(rim.red(), rim.green(), rim.blue(), 0))
        painter.setPen(QPen(QBrush(wide_grad), 14.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(upper)

        bloom_grad = QLinearGradient(start, end)
        bloom_grad.setColorAt(0.0, QColor(rim.red(), rim.green(), rim.blue(), int(56 * alpha_scale)))
        bloom_grad.setColorAt(0.55, QColor(rim.red(), rim.green(), rim.blue(), int(28 * alpha_scale)))
        bloom_grad.setColorAt(1.0, QColor(rim.red(), rim.green(), rim.blue(), 0))
        painter.setPen(QPen(QBrush(bloom_grad), 5.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(upper)

        # Crisp thin bright hairline edge
        edge_grad = QLinearGradient(start, end)
        edge_grad.setColorAt(0.0, QColor(rim.red(), rim.green(), rim.blue(), int(145 * alpha_scale)))
        edge_grad.setColorAt(0.60, QColor(rim.red(), rim.green(), rim.blue(), int(72 * alpha_scale)))
        edge_grad.setColorAt(1.0, QColor(rim.red(), rim.green(), rim.blue(), 0))
        painter.setPen(QPen(QBrush(edge_grad), 1.25, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(upper)

    @staticmethod
    def _draw_mountains(painter: QPainter, w: float, h: float) -> None:
        base_y = h
        # Left distant ridge
        left_bg = QPolygonF([
            QPointF(0.0, base_y),
            QPointF(0.0, h * 0.80),
            QPointF(w * 0.08, h * 0.84),
            QPointF(w * 0.16, h * 0.77),
            QPointF(w * 0.27, h * 0.89),
            QPointF(w * 0.42, base_y),
        ])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(LoginColors.MOUNTAIN_BG))
        painter.drawPolygon(left_bg)

        # Left foreground silhouette
        left_fg = QPolygonF([
            QPointF(0.0, base_y),
            QPointF(0.0, h * 0.88),
            QPointF(w * 0.11, h * 0.82),
            QPointF(w * 0.21, h * 0.91),
            QPointF(w * 0.34, base_y),
        ])
        painter.setBrush(QColor(LoginColors.MOUNTAIN_FG))
        painter.drawPolygon(left_fg)

        # Right distant ridge
        right_bg = QPolygonF([
            QPointF(w * 0.56, base_y),
            QPointF(w * 0.73, h * 0.88),
            QPointF(w * 0.84, h * 0.76),
            QPointF(w * 0.92, h * 0.83),
            QPointF(w, h * 0.78),
            QPointF(w, base_y),
        ])
        painter.setBrush(QColor(LoginColors.MOUNTAIN_BG))
        painter.drawPolygon(right_bg)

        # Right foreground silhouette
        right_fg = QPolygonF([
            QPointF(w * 0.66, base_y),
            QPointF(w * 0.79, h * 0.90),
            QPointF(w * 0.89, h * 0.82),
            QPointF(w, h * 0.87),
            QPointF(w, base_y),
        ])
        painter.setBrush(QColor(LoginColors.MOUNTAIN_FG))
        painter.drawPolygon(right_fg)

        # Subtle ridge highlight rims fading gently into mist
        hl = QColor(LoginColors.MOUNTAIN_HIGHLIGHT)
        lg_m = QLinearGradient(w * 0.08, h * 0.77, w * 0.27, h * 0.90)
        lg_m.setColorAt(0.0, QColor(hl.red(), hl.green(), hl.blue(), 18))
        lg_m.setColorAt(0.45, QColor(hl.red(), hl.green(), hl.blue(), 64))
        lg_m.setColorAt(1.0, QColor(hl.red(), hl.green(), hl.blue(), 0))
        painter.setPen(QPen(QBrush(lg_m), 1.2))
        painter.drawPolyline(QPolygonF([QPointF(w * 0.08, h * 0.84), QPointF(w * 0.16, h * 0.77), QPointF(w * 0.27, h * 0.89)]))

        rg_m = QLinearGradient(w * 0.73, h * 0.88, w * 0.92, h * 0.76)
        rg_m.setColorAt(0.0, QColor(hl.red(), hl.green(), hl.blue(), 0))
        rg_m.setColorAt(0.55, QColor(hl.red(), hl.green(), hl.blue(), 64))
        rg_m.setColorAt(1.0, QColor(hl.red(), hl.green(), hl.blue(), 18))
        painter.setPen(QPen(QBrush(rg_m), 1.2))
        painter.drawPolyline(QPolygonF([QPointF(w * 0.73, h * 0.88), QPointF(w * 0.84, h * 0.76), QPointF(w * 0.92, h * 0.83)]))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        cached = self._ensure_base_cache()
        painter.drawPixmap(0, 0, cached)

        # Ultra-subtle low-opacity ambient drift layer (barely noticeable breathing)
        if not self._reduced_motion and self._drift_timer.isActive():
            dx = math.sin(self._drift_phase) * 10.0
            dy = math.cos(self._drift_phase * 0.7) * 5.0
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            glow = QRadialGradient(self.width() * 0.5 + dx, self.height() * self._hero_center_y_ratio + dy, self.width() * 0.26)
            glow.setColorAt(0.0, QColor(93, 215, 255, 14))
            glow.setColorAt(1.0, QColor(5, 10, 18, 0))
            painter.fillRect(self.rect(), QBrush(glow))


# ─── Central Shield-Lock Emblem ───────────────────────────────────────────────


class ShieldLockEmblem(QWidget):
    """Centered shield + lock brand emblem with one-time entrance sweep & success glow."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(130, 84)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._scale = 1.0
        self._opacity = 1.0
        self._sweep_pos = -1.0
        self._success_glow = 0.0
        self._played_entrance = False

        # Load reference brand shield asset
        self._shield_pixmap: Optional[QPixmap] = None
        shield_path = get_login_resource_path("shield_brand.png")
        if shield_path and os.path.isfile(shield_path):
            self._shield_pixmap = QPixmap(shield_path)

        self._entrance_anim = QVariantAnimation(self)
        self._entrance_anim.setDuration(320)
        self._entrance_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._entrance_anim.valueChanged.connect(self._on_entrance_step)

        self._sweep_anim = QVariantAnimation(self)
        self._sweep_anim.setDuration(550)
        self._sweep_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._sweep_anim.valueChanged.connect(self._on_sweep_step)

        self._glow_anim = QVariantAnimation(self)
        self._glow_anim.setDuration(280)
        self._glow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._glow_anim.valueChanged.connect(self._on_glow_step)

    def _on_entrance_step(self, val) -> None:
        p = float(val)
        self._opacity = p
        self._scale = 0.96 + 0.04 * p
        self.update()

    def _on_sweep_step(self, val) -> None:
        self._sweep_pos = float(val)
        self.update()

    def _on_glow_step(self, val) -> None:
        self._success_glow = float(val)
        self.update()

    def play_entrance(self, reduced_motion: bool = False) -> None:
        if self._played_entrance:
            return
        self._played_entrance = True
        if reduced_motion:
            self._opacity = 1.0
            self._scale = 1.0
            self._sweep_pos = -1.0
            self.update()
            return
        self._opacity = 0.0
        self._scale = 0.96
        self._entrance_anim.stop()
        self._entrance_anim.setStartValue(0.0)
        self._entrance_anim.setEndValue(1.0)
        self._entrance_anim.start()

        self._sweep_anim.stop()
        self._sweep_anim.setStartValue(-0.3)
        self._sweep_anim.setEndValue(1.4)
        self._sweep_anim.start()

    def flash_success(self, reduced_motion: bool = False) -> None:
        if reduced_motion:
            self._success_glow = 0.65
            self.update()
            return
        self._glow_anim.stop()
        self._glow_anim.setStartValue(0.0)
        self._glow_anim.setEndValue(1.0)
        self._glow_anim.start()

    def stop_all(self) -> None:
        self._entrance_anim.stop()
        self._sweep_anim.stop()
        self._glow_anim.stop()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self._opacity)

        w = float(self.width())
        h = float(self.height())
        painter.translate(w / 2.0, h / 2.0)
        painter.scale(self._scale, self._scale)
        painter.translate(-w / 2.0, -h / 2.0)

        cx = w / 2.0

        # 1. Soft radial ethereal glow behind shield
        glow_color = QColor(79, 227, 176) if self._success_glow > 0.4 else QColor(65, 175, 255)
        glow = QRadialGradient(cx, 40.0, 46.0)
        glow.setColorAt(0.0, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 75))
        glow.setColorAt(0.45, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 28))
        glow.setColorAt(1.0, QColor(5, 10, 18, 0))
        painter.fillRect(QRectF(0, 0, w, h), QBrush(glow))

        # 2. Shield geometry
        shield = QPainterPath()
        shield.moveTo(cx, 6.0)
        shield.lineTo(cx + 26.0, 16.0)
        shield.cubicTo(cx + 26.0, 42.0, cx + 18.0, 58.0, cx, 70.0)
        shield.cubicTo(cx - 18.0, 58.0, cx - 26.0, 42.0, cx - 26.0, 16.0)
        shield.closeSubpath()

        # Fill: dark smoked glass with deep navy gradient
        fill = QLinearGradient(cx, 6.0, cx, 70.0)
        fill.setColorAt(0.0, QColor(24, 45, 74, 235))
        fill.setColorAt(0.5, QColor(14, 26, 44, 245))
        fill.setColorAt(1.0, QColor(8, 16, 28, 250))
        painter.fillPath(shield, QBrush(fill))

        # Rim: beveled chrome-to-electric-cyan
        rim = QLinearGradient(cx - 26.0, 6.0, cx + 26.0, 70.0)
        if self._success_glow > 0.4:
            rim.setColorAt(0.0, QColor("#FFFFFF"))
            rim.setColorAt(0.5, QColor("#4FE3B0"))
            rim.setColorAt(1.0, QColor("#10B981"))
        else:
            rim.setColorAt(0.0, QColor("#FFFFFF"))
            rim.setColorAt(0.20, QColor("#E2F1FF"))
            rim.setColorAt(0.60, QColor("#70C8FF"))
            rim.setColorAt(1.0, QColor("#38BDF8"))
        painter.setPen(QPen(QBrush(rim), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(shield)

        # Inner specular rim line on top edge
        top_spec = QPainterPath()
        top_spec.moveTo(cx - 23.0, 17.0)
        top_spec.lineTo(cx, 7.5)
        top_spec.lineTo(cx + 23.0, 17.0)
        spec_grad = QLinearGradient(cx - 23.0, 17.0, cx + 23.0, 17.0)
        spec_grad.setColorAt(0.0, QColor(255, 255, 255, 0))
        spec_grad.setColorAt(0.5, QColor(255, 255, 255, 210))
        spec_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(QPen(QBrush(spec_grad), 1.0))
        painter.drawPath(top_spec)

        # Optional one-time subtle light sweep inside shield
        if 0.0 <= self._sweep_pos <= 1.25:
            painter.save()
            painter.setClipPath(shield)
            sx = cx - 36.0 + self._sweep_pos * 72.0
            sweep = QLinearGradient(sx - 14.0, 6.0, sx + 14.0, 70.0)
            sweep.setColorAt(0.0, QColor(255, 255, 255, 0))
            sweep.setColorAt(0.5, QColor(183, 223, 255, 80))
            sweep.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(shield, QBrush(sweep))
            painter.restore()

        # Crisp outline padlock inside shield
        lock_color = QColor("#4FE3B0") if self._success_glow > 0.4 else QColor("#FFFFFF")
        painter.setPen(QPen(lock_color, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Shackle
        painter.drawArc(QRectF(cx - 6.5, 24.0, 13.0, 14.0), 0, 180 * 16)
        # Lock body
        painter.drawRoundedRect(QRectF(cx - 10.0, 33.0, 20.0, 16.0), 3.0, 3.0)
        # Keyhole
        painter.setBrush(lock_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, 39.5), 1.8, 1.8)
        painter.drawRect(QRectF(cx - 0.9, 40.5, 1.8, 3.5))


# ─── Sparkle Horizontal Divider ───────────────────────────────────────────────


class DividerWithSparkle(QWidget):
    """Minimal horizontal blue-gray divider with central softly glowing 4-pt sparkle."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(16)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        cy = float(self.height()) / 2.0
        cx = w / 2.0
        half_span = min(145.0, max(60.0, w * 0.34))

        # Left line fading out at outer edge
        lg = QLinearGradient(cx - half_span, cy, cx - 10.0, cy)
        lg.setColorAt(0.0, QColor(130, 190, 255, 0))
        lg.setColorAt(1.0, QColor(130, 190, 255, 95))
        painter.setPen(QPen(QBrush(lg), 1.0))
        painter.drawLine(QPointF(cx - half_span, cy), QPointF(cx - 10.0, cy))

        # Right line fading out at outer edge
        rg = QLinearGradient(cx + 10.0, cy, cx + half_span, cy)
        rg.setColorAt(0.0, QColor(130, 190, 255, 95))
        rg.setColorAt(1.0, QColor(130, 190, 255, 0))
        painter.setPen(QPen(QBrush(rg), 1.0))
        painter.drawLine(QPointF(cx + 10.0, cy), QPointF(cx + half_span, cy))

        # Faint glow + 4-pt sparkle diamond
        glow = QRadialGradient(cx, cy, 9.0)
        glow.setColorAt(0.0, QColor(93, 215, 255, 110))
        glow.setColorAt(1.0, QColor(93, 215, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(QPointF(cx, cy), 9.0, 9.0)

        # Radiant cyan glow behind sparkle diamond
        glow_outer = QRadialGradient(cx, cy, 14.0)
        glow_outer.setColorAt(0.0, QColor(56, 189, 248, 170))
        glow_outer.setColorAt(0.45, QColor(56, 189, 248, 65))
        glow_outer.setColorAt(1.0, QColor(56, 189, 248, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow_outer))
        painter.drawEllipse(QPointF(cx, cy), 14.0, 14.0)

        # 4-point electric cyan diamond star matching reference exactly
        star = QPainterPath()
        r_out = 6.2
        r_in = 1.9
        star.moveTo(cx, cy - r_out)
        star.lineTo(cx + r_in, cy - r_in)
        star.lineTo(cx + r_out, cy)
        star.lineTo(cx + r_in, cy + r_in)
        star.lineTo(cx, cy + r_out)
        star.lineTo(cx - r_in, cy + r_in)
        star.lineTo(cx - r_out, cy)
        star.lineTo(cx - r_in, cy - r_in)
        star.closeSubpath()

        s_grad = QLinearGradient(cx, cy - r_out, cx, cy + r_out)
        s_grad.setColorAt(0.0, QColor("#FFFFFF"))
        s_grad.setColorAt(0.5, QColor("#70D6FF"))
        s_grad.setColorAt(1.0, QColor("#38BDF8"))
        painter.setBrush(QBrush(s_grad))
        painter.drawPath(star)


# ─── Smoked Glass Login Card ──────────────────────────────────────────────────


class LoginCard(QFrame):
    """Smoked glass central authentication card with edge highlights, shake & success glow."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("LoginCard")
        self.setMinimumWidth(360)
        self.setMaximumWidth(452)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._success_boost = 0.0
        self._shake_offset = 0.0

        self._shake_anim = QVariantAnimation(self)
        self._shake_anim.setDuration(200)
        self._shake_anim.valueChanged.connect(self._on_shake_step)

        self._success_anim = QVariantAnimation(self)
        self._success_anim.setDuration(260)
        self._success_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._success_anim.valueChanged.connect(self._on_success_step)

    def _on_shake_step(self, val) -> None:
        p = float(val)
        # Damped horizontal oscillation ~7px -> 0
        self._shake_offset = math.sin(p * math.pi * 4.0) * 7.0 * (1.0 - p)
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
            self._success_boost = 0.8
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if abs(self._shake_offset) > 0.05:
            painter.translate(self._shake_offset, 0.0)

        rect = QRectF(6.0, 6.0, float(self.width()) - 12.0, float(self.height()) - 12.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 22.0, 22.0)

        # 1. Subtle ambient deep-navy shadow behind the card (smooth, zero harsh stepped outlines)
        for spread, alpha in ((14.0, 10), (8.0, 18), (3.0, 32)):
            glow_rect = rect.adjusted(-spread, -spread, spread, spread)
            glow_path = QPainterPath()
            glow_path.addRoundedRect(glow_rect, 22.0 + spread, 22.0 + spread)
            painter.fillPath(glow_path, QColor(8, 16, 28, alpha))

        # 2. Smoked glass fill matching target card
        card_grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        boost = int(18 * self._success_boost)
        card_grad.setColorAt(0.0, QColor(13 + boost, 23 + boost, 37 + boost, 210))
        card_grad.setColorAt(0.50, QColor(10 + boost, 18 + boost, 29 + boost, 215))
        card_grad.setColorAt(1.0, QColor(7 + boost, 13 + boost, 22 + boost, 220))
        painter.fillPath(path, QBrush(card_grad))

        # 3. Translucent ice-blue rim border with bright top specular highlight
        border_grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        if self._success_boost > 0.3:
            border_grad.setColorAt(0.0, QColor(16, 185, 129, 180))
            border_grad.setColorAt(1.0, QColor(56, 189, 248, 90))
        else:
            border_grad.setColorAt(0.0, QColor(165, 210, 255, 80))
            border_grad.setColorAt(0.35, QColor(115, 170, 235, 48))
            border_grad.setColorAt(1.0, QColor(65, 115, 185, 32))
        painter.setPen(QPen(QBrush(border_grad), 1.0))
        painter.drawPath(path)


# ─── Animated Password Input Field ────────────────────────────────────────────


class _InnerPasswordLineEdit(QLineEdit):
    """QLineEdit reporting focus changes natively without eventFilter teardown hazards."""

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
    """Glass password field with smooth focus glow, lock icon, and eye toggle."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(54)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._focused = False
        self._error_state = False
        self._focus_progress = 0.0
        self._eye_hovered = False

        self._focus_anim = QVariantAnimation(self)
        self._focus_anim.setDuration(150)
        self._focus_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._focus_anim.valueChanged.connect(self._on_focus_step)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 2, 8, 2)
        layout.setSpacing(8)

        self._lock_icon = QLabel(self)
        self._lock_icon.setFixedSize(16, 16)
        self._lock_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lock_icon.setPixmap(icons.get_pixmap("lock", size=15, color="#FFFFFF"))
        self._lock_icon.setStyleSheet("background: transparent;")

        self.line_edit = _InnerPasswordLineEdit(self._set_focused, self)
        self.line_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.line_edit.setPlaceholderText("••••••••••••")
        self._update_input_style(is_password=True)

        self._eye_btn = QPushButton(self)
        self._eye_btn.setFixedSize(28, 28)
        self._eye_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._eye_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._eye_btn.setToolTip("Show / Hide password")
        self._eye_btn.setIcon(icons.get_icon("eye", color=LoginColors.TEXT_SECONDARY, active_color=LoginColors.ICE_BLUE, size=16))
        self._eye_btn.setIconSize(QSize(16, 16))
        self._eye_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 8px;
                min-height: 28px;
                max-height: 28px;
                min-width: 28px;
                max-width: 28px;
                padding: 0;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.08);
            }
            QPushButton:pressed {
                background: rgba(255, 255, 255, 0.14);
            }
        """)
        self._eye_btn.clicked.connect(self.toggle_visibility)

        layout.addWidget(self._lock_icon)
        layout.addWidget(self.line_edit, 1)
        layout.addWidget(self._eye_btn)

    def _update_input_style(self, is_password: bool) -> None:
        if is_password:
            f = QFont("Arial")
            f.setFamilies(["Arial", "Segoe UI", "sans-serif"])
            f.setPixelSize(22)
            f.setWeight(QFont.Weight.Bold)
            self.line_edit.setFont(f)
            self.line_edit.setStyleSheet("""
                QLineEdit {
                    background: transparent;
                    border: none;
                    min-height: 0px;
                    font-family: Arial, sans-serif;
                    font-size: 22px;
                    font-weight: bold;
                    letter-spacing: 3px;
                    color: #FFFFFF;
                    selection-background-color: rgba(66, 168, 255, 0.35);
                    padding: 0px 4px;
                }
            """)
        else:
            f = LoginTypography.font(15, QFont.Weight.Medium)
            self.line_edit.setFont(f)
            self.line_edit.setStyleSheet(f"""
                QLineEdit {{
                    background: transparent;
                    border: none;
                    min-height: 0px;
                    font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
                    font-size: 15px;
                    font-weight: 500;
                    letter-spacing: 0px;
                    color: {LoginColors.TEXT_PRIMARY};
                    selection-background-color: rgba(66, 168, 255, 0.35);
                    padding: 0px 4px;
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
            self._update_input_style(is_password=False)
            self._eye_btn.setIcon(icons.get_icon("eye_off", color=LoginColors.CYAN_BRIGHT, size=18))
        else:
            self.line_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._update_input_style(is_password=True)
            self._eye_btn.setIcon(icons.get_icon("eye", color=LoginColors.TEXT_SECONDARY, active_color=LoginColors.ICE_BLUE, size=18))

    def sync_eye_icon_from_mode(self) -> None:
        is_hidden = self.line_edit.echoMode() == QLineEdit.EchoMode.Password
        self._update_input_style(is_password=is_hidden)
        self._eye_btn.setIcon(
            icons.get_icon(
                "eye" if is_hidden else "eye_off",
                color=LoginColors.TEXT_SECONDARY if is_hidden else LoginColors.CYAN_BRIGHT,
                size=18,
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(1.5, 1.5, float(self.width()) - 3.0, float(self.height()) - 3.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 13.0, 13.0)

        # Outer electric-cyan focus glow matching reference
        if self._focus_progress > 0.02 and not self._error_state:
            for spread, alpha in (
                (3.5, int(22 * self._focus_progress)),
                (1.5, int(55 * self._focus_progress)),
            ):
                glow_path = QPainterPath()
                glow_path.addRoundedRect(
                    rect.adjusted(-spread, -spread, spread, spread),
                    13.0 + spread,
                    13.0 + spread,
                )
                painter.fillPath(glow_path, QColor(56, 189, 248, alpha))

        painter.fillPath(path, LoginColors.INPUT_BG)

        if self._error_state:
            painter.setPen(QPen(QColor(LoginColors.ERROR), 1.35))
        else:
            if self._focus_progress > 0.05:
                f_grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
                f_grad.setColorAt(0.0, QColor("#38BDF8"))
                f_grad.setColorAt(1.0, QColor("#2563EB"))
                painter.setPen(QPen(QBrush(f_grad), 1.35))
            else:
                painter.setPen(QPen(QColor(LoginColors.INPUT_BORDER), 1.0))
        painter.drawPath(path)


# ─── Compact Key File Selector ────────────────────────────────────────────────


class KeyFileSelector(QFrame):
    """Frosted key-file picker exposing a synced QLineEdit for full compatibility."""

    browse_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._state = "empty"  # empty | loading | ready | error
        self._required = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 2, 6, 2)
        layout.setSpacing(8)

        self._file_icon = QLabel(self)
        self._file_icon.setFixedSize(16, 16)
        self._file_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._file_icon.setPixmap(icons.get_pixmap("file", size=15, color="#FFFFFF"))
        self._file_icon.setStyleSheet("background: transparent;")

        self._status_lbl = QLabel("No key file selected", self)
        self._status_lbl.setFont(LoginTypography.font(13))
        self._status_lbl.setStyleSheet(f"color: {LoginColors.TEXT_SECONDARY}; background: transparent;")
        self._status_lbl.setMinimumWidth(60)

        # Hidden backing QLineEdit preserves `login._kf_edit.text()/setText()/setFocus()` API seamlessly
        self.backing_edit = QLineEdit(self)
        self.backing_edit.setVisible(False)
        self.backing_edit.textChanged.connect(self._on_path_text_changed)

        self.browse_btn = QPushButton(" Browse", self)
        self.browse_btn.setFont(LoginTypography.font(12, QFont.Weight.Medium))
        self.browse_btn.setIcon(icons.get_icon("folder", color=LoginColors.TEXT_PRIMARY, active_color="#FFFFFF", size=13))
        self.browse_btn.setIconSize(QSize(13, 13))
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.setFixedHeight(26)
        self.browse_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.08);
                color: {LoginColors.TEXT_PRIMARY};
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 6px;
                padding: 0 10px;
                font-size: 12px;
                font-weight: 500;
                min-height: 26px;
                max-height: 26px;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.14);
                border: 1px solid rgba(255, 255, 255, 0.28);
            }}
            QPushButton:pressed {{
                background: rgba(255, 255, 255, 0.05);
            }}
        """)
        self.browse_btn.clicked.connect(self.browse_clicked.emit)

        layout.addWidget(self._file_icon)
        layout.addWidget(self._status_lbl, 1)
        layout.addWidget(self.browse_btn)

    def set_required(self, required: bool) -> None:
        self._required = bool(required)
        if not self.backing_edit.text().strip():
            self.set_state("empty")

    def set_state(self, state: str, filename: str = "") -> None:
        self._state = state
        base_f = "background: transparent; font-size: 14px;"
        if state == "loading":
            self._status_lbl.setText("Reading key file…")
            self._status_lbl.setStyleSheet(f"color: {LoginColors.ICE_BLUE}; {base_f}")
            self._file_icon.setPixmap(icons.get_pixmap("file", size=18, color=LoginColors.ICE_BLUE))
        elif state == "ready":
            txt = f"Key file ready · {filename}" if filename else "Key file ready"
            self._status_lbl.setText(txt)
            self._status_lbl.setStyleSheet(f"color: {LoginColors.SUCCESS}; {base_f}")
            self._file_icon.setPixmap(icons.get_pixmap("check", size=18, color=LoginColors.SUCCESS))
        elif state == "selected":
            txt = f"Key file selected · {filename}" if filename else "Key file selected"
            self._status_lbl.setText(txt)
            self._status_lbl.setStyleSheet(f"color: {LoginColors.TEXT_PRIMARY}; {base_f}")
            self._file_icon.setPixmap(icons.get_pixmap("file", size=18, color=LoginColors.ICE_BLUE))
        elif state == "error":
            self._status_lbl.setText("Unable to read key file")
            self._status_lbl.setStyleSheet(f"color: {LoginColors.ERROR}; {base_f}")
            self._file_icon.setPixmap(icons.get_pixmap("alert_triangle", size=18, color=LoginColors.ERROR))
        else:
            txt = "No key file selected (Required)" if self._required else "No key file selected"
            self._status_lbl.setText(txt)
            self._status_lbl.setStyleSheet(f"color: {LoginColors.TEXT_SECONDARY}; {base_f}")
            self._file_icon.setPixmap(icons.get_pixmap("file", size=18, color="#FFFFFF"))
        self.update()

    def _on_path_text_changed(self, text: str) -> None:
        import os
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(1.0, 1.0, float(self.width()) - 2.0, float(self.height()) - 2.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 13.0, 13.0)
        painter.fillPath(path, QColor(7, 14, 24, 185))

        if self._state == "error":
            border = QColor(LoginColors.ERROR)
        elif self._state == "ready":
            border = QColor(79, 227, 176, 125)
        else:
            border = LoginColors.INPUT_BORDER
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(path)


# ─── Primary Unlock CTA Button ────────────────────────────────────────────────


class UnlockPrimaryButton(QPushButton):
    """Electric-cyan/blue gradient Unlock CTA with wave sheen, hover lift & loading states."""

    def __init__(self, text: str = "Unlock Vault", parent: Optional[QWidget] = None, height: int = 56) -> None:
        super().__init__(text, parent)
        self.setObjectName("UnlockPrimaryButton")
        self.setFixedHeight(height)
        self.setStyleSheet(f"""
            QPushButton#UnlockPrimaryButton {{
                background: transparent;
                border: none;
                padding: 0px;
                min-height: {height}px;
                max-height: {height}px;
            }}
        """)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._base_text = text
        self._mode = "normal"  # normal | loading | success
        self._hover_p = 0.0
        self._press_p = 0.0
        self._spin_angle = 0.0

        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(140)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_step)

        self._press_anim = QVariantAnimation(self)
        self._press_anim.setDuration(90)
        self._press_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._press_anim.valueChanged.connect(self._on_press_step)

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(16)
        self._spin_timer.timeout.connect(self._tick_spin)

    def _on_hover_step(self, val) -> None:
        self._hover_p = float(val)
        self.update()

    def _on_press_step(self, val) -> None:
        self._press_p = float(val)
        self.update()

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
        self._press_p = 0.0
        self.update()

    def stop_all(self) -> None:
        self._hover_anim.stop()
        self._press_anim.stop()
        self._spin_timer.stop()

    def enterEvent(self, event) -> None:
        if self.isEnabled():
            self._hover_anim.stop()
            self._hover_anim.setStartValue(self._hover_p)
            self._hover_anim.setEndValue(1.0)
            self._hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_p)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._press_anim.stop()
            self._press_anim.setStartValue(self._press_p)
            self._press_anim.setEndValue(1.0)
            self._press_anim.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press_anim.stop()
        self._press_anim.setStartValue(self._press_p)
        self._press_anim.setEndValue(0.0)
        self._press_anim.start()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w = float(self.width())
        h = float(self.height())
        if not self.isEnabled() and self._mode == "normal":
            painter.setOpacity(0.55)

        scale = 1.0 - 0.02 * self._press_p
        lift_y = -1.0 * self._hover_p * (1.0 - self._press_p)
        painter.translate(w / 2.0, h / 2.0 + lift_y)
        painter.scale(scale, scale)
        painter.translate(-w / 2.0, -h / 2.0)

        rect = QRectF(1.0, 2.0, w - 2.0, h - 4.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 15.0, 15.0)

        # Outer electric-cyan shadow/glow radiating on left
        for spread, alpha in ((8.0, 16), (4.0, 36), (1.5, 70)):
            b_glow = QPainterPath()
            b_glow.addRoundedRect(
                rect.adjusted(-spread, -spread + 1.0, spread, spread + 2.0),
                15.0 + spread,
                15.0 + spread,
            )
            painter.fillPath(b_glow, QColor(41, 182, 246, alpha))

        # Rich electric-blue to sapphire gradient matching reference button
        grad = QLinearGradient(0.0, 0.0, w, 0.0)
        if self._mode == "success":
            grad.setColorAt(0.0, QColor("#10B981"))
            grad.setColorAt(1.0, QColor("#047857"))
        else:
            b = int(12 * self._hover_p)
            grad.setColorAt(0.0, QColor(min(255, 45 + b), min(255, 140 + b), min(255, 230 + b)))
            grad.setColorAt(0.35, QColor(min(255, 22 + b), min(255, 75 + b), min(255, 140 + b)))
            grad.setColorAt(0.70, QColor(min(255, 14 + b), min(255, 45 + b), min(255, 90 + b)))
            grad.setColorAt(1.0, QColor(min(255, 18 + b), min(255, 65 + b), min(255, 130 + b)))
        painter.fillPath(path, QBrush(grad))

        # Translucent flowing silk wave accent sweeping across the right half of the button
        painter.save()
        painter.setClipPath(path)
        sheen = QPainterPath()
        sheen.moveTo(w * 0.52, h)
        sheen.cubicTo(w * 0.65, h * 0.72, w * 0.82, h * 0.38, w, h * 0.12)
        sheen.lineTo(w, h)
        sheen.closeSubpath()

        sheen_grad = QLinearGradient(QPointF(w * 0.52, h), QPointF(w * 0.95, h * 0.25))
        sheen_grad.setColorAt(0.0, QColor(56, 189, 248, 0))
        sheen_grad.setColorAt(0.35, QColor(60, 180, 255, 85))
        sheen_grad.setColorAt(0.70, QColor(145, 220, 255, 145))
        sheen_grad.setColorAt(1.0, QColor(220, 245, 255, 75))
        painter.fillPath(sheen, QBrush(sheen_grad))
        painter.restore()

        # Top specular highlight rim
        top_rim = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.top())
        top_rim.setColorAt(0.0, QColor(255, 255, 255, 180))
        top_rim.setColorAt(0.5, QColor(180, 230, 255, 120))
        top_rim.setColorAt(1.0, QColor(100, 180, 255, 60))
        painter.setPen(QPen(QBrush(top_rim), 1.0))
        painter.drawPath(path)

        # Center [Unlock Icon] + [Unlock Vault] label lockup, plus right arrow
        label = self.text().strip()
        painter.setFont(LoginTypography.font(16, QFont.Weight.DemiBold))
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(label)
        ico_sz = 20
        gap = 10
        total_w = ico_sz + gap + text_w
        start_x = (w - total_w) / 2.0
        iy = (h - ico_sz) / 2.0

        if self._mode == "loading":
            cx = start_x + ico_sz / 2.0
            cy = h / 2.0
            r = 8.0
            painter.setPen(QPen(QColor(255, 255, 255, 70), 2.2))
            painter.drawEllipse(QPointF(cx, cy), r, r)
            painter.setPen(QPen(QColor("#FFFFFF"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(cx - r, cy - r, r * 2.0, r * 2.0), int(-self._spin_angle * 16), int(110 * 16))
        else:
            ico_name = "check" if self._mode == "success" else "unlock"
            px = icons.get_pixmap(ico_name, color="#FFFFFF", size=ico_sz)
            painter.drawPixmap(QPointF(start_x, iy), px)

        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(
            QRectF(start_x + ico_sz + gap, 0.0, float(text_w + 8), h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            label,
        )

        if self._mode == "normal":
            arrow_px = icons.get_pixmap("arrow_right", color="#FFFFFF", size=18)
            painter.drawPixmap(QPointF(w - 36.0 + 2.0 * self._hover_p, (h - 18.0) / 2.0), arrow_px)


# ─── Honest Security Status Badge ─────────────────────────────────────────────


class SecurityStatusBadge(QWidget):
    """Subtle bottom status indicator reflecting verified keystore/protection readiness."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._active = False
        self._dot_alpha = 220
        self._pulse_phase = 0.0
        self._reduced_motion = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._shield_lbl = QLabel(self)
        self._shield_lbl.setFixedSize(18, 18)
        self._shield_lbl.setStyleSheet("background: transparent;")

        self._text_lbl = QLabel("Vault protection active", self)
        self._text_lbl.setFont(LoginTypography.font(13, QFont.Weight.Medium))
        self._text_lbl.setStyleSheet(f"color: {LoginColors.STATUS_TEXT}; background: transparent;")

        self._dot = _PulseDot(self)

        lay.addWidget(self._shield_lbl)
        lay.addWidget(self._text_lbl)
        lay.addWidget(self._dot)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(45)
        self._pulse_timer.timeout.connect(self._tick_pulse)

        self.set_protection_active(False)

    def set_reduced_motion(self, reduced: bool) -> None:
        self._reduced_motion = bool(reduced)
        if reduced:
            self._pulse_timer.stop()
            self._dot.set_intensity(0.85)
        elif self._active and self.isVisible():
            self._pulse_timer.start()

    def set_protection_active(self, active: bool, custom_text: Optional[str] = None) -> None:
        self._active = bool(active)
        base_f = "background: transparent; font-size: 13px; font-weight: 500;"
        if active:
            self._shield_lbl.setPixmap(icons.get_pixmap("shield_check", size=18, color=LoginColors.STATUS_SHIELD))
            self._text_lbl.setText(custom_text or "Vault protection active")
            self._text_lbl.setStyleSheet(f"color: {LoginColors.STATUS_TEXT}; {base_f}")
            self._dot.set_color(QColor(LoginColors.SUCCESS))
            self._dot.show()
            if not self._reduced_motion and self.isVisible():
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._shield_lbl.setPixmap(icons.get_pixmap("shield_alert", size=18, color=LoginColors.WARNING))
            self._text_lbl.setText(custom_text or "Vault configuration needed")
            self._text_lbl.setStyleSheet(f"color: {LoginColors.TEXT_MUTED}; {base_f}")
            self._dot.set_color(QColor(LoginColors.WARNING))
            self._dot.show()

    def stop_all(self) -> None:
        self._pulse_timer.stop()

    def _tick_pulse(self) -> None:
        if self._reduced_motion or not self.isVisible() or not self._active:
            self._pulse_timer.stop()
            return
        self._pulse_phase = (self._pulse_phase + 0.14) % (2.0 * math.pi)
        self._dot.set_intensity(0.55 + 0.45 * (0.5 + 0.5 * math.sin(self._pulse_phase)))


class _PulseDot(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._color = QColor(LoginColors.SUCCESS)
        self._intensity = 0.85

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.update()

    def set_intensity(self, val: float) -> None:
        self._intensity = max(0.2, min(1.0, float(val)))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = float(self.width()) / 2.0
        cy = float(self.height()) / 2.0
        halo = QRadialGradient(cx, cy, 9.0)
        halo.setColorAt(0.0, QColor(16, 185, 129, int(160 * self._intensity)))
        halo.setColorAt(0.5, QColor(16, 185, 129, int(70 * self._intensity)))
        halo.setColorAt(1.0, QColor(16, 185, 129, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(QPointF(cx, cy), 9.0, 9.0)
        painter.setBrush(QColor("#34D399"))
        painter.drawEllipse(QPointF(cx, cy), 4.2, 4.2)


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
]
