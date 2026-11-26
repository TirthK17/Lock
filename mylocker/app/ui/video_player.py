"""
video_player.py — Cinematic frosted-glass in-memory video player using QtMultimedia.

Security design:
- Uses StreamingVaultIODevice: a custom seekable QIODevice that decrypts 4 MB
  chunks on demand directly from the encrypted vault blob on disk.
- NEVER writes plaintext temporary files during playback, frame previewing, or
  thumbnail extraction.
- Keeps at most one decrypted 4 MB chunk in RAM and zeroes keys, chunk buffers,
  and cached preview frames immediately on close or vault lock.
- Protected against screen capture tools via apply_screenshot_protection() and
  registered with LockController.
"""

from __future__ import annotations

import base64
import datetime
import logging
import struct
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QIODevice,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtMultimedia import (
    QAudioOutput,
    QMediaMetaData,
    QMediaPlayer,
    QVideoFrame,
    QVideoSink,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.chunked_io import (
    CHUNK_SIZE,
    MAGIC,
    VERSION_V1,
    VERSION_V2,
    _make_ad_v1,
    _make_ad_v2,
)
from ..core.crypto import decrypt_aes_gcm, zero_bytes
from ..core.kdf import derive_file_key
from ..core.session import get_session
from ..system.lock_controller import get_lock_controller
from ..system.screenshot import apply_screenshot_protection
from . import icons
from .design_system import DARK_PALETTE

logger = logging.getLogger(__name__)


# ─── Design Tokens ───────────────────────────────────────────────────────────


class VideoColors:
    ACCENT_LIGHT = DARK_PALETTE.accent_hover
    ACCENT_MID = DARK_PALETTE.accent
    ACCENT_HOVER = DARK_PALETTE.accent_hover
    ACCENT_RING = DARK_PALETTE.accent
    BRAND = DARK_PALETTE.accent
    BG_DARKEST = DARK_PALETTE.bg
    BG_MID = DARK_PALETTE.bg_secondary
    BG_SURFACE = DARK_PALETTE.card
    TEXT_PRIMARY = DARK_PALETTE.text
    TEXT_SECONDARY = DARK_PALETTE.text_secondary
    TEXT_MUTED = DARK_PALETTE.text_muted
    TEXT_ROW = DARK_PALETTE.text
    TEXT_BRIGHT = DARK_PALETTE.white
    WHITE = DARK_PALETTE.white
    DANGER = DARK_PALETTE.danger


# ─── Formatting & Metadata Helpers ────────────────────────────────────────────


def _format_duration_ms(ms: int) -> str:
    """Format milliseconds into MM:SS or H:MM:SS cleanly."""
    if ms <= 0:
        return "00:00"
    total_sec = int(ms // 1000)
    hrs, rem = divmod(total_sec, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def _format_size(size_bytes: int) -> str:
    """Format byte count concisely (e.g. '1.2 GB', '42.8 MB')."""
    if size_bytes <= 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    val = float(size_bytes)
    idx = 0
    while val >= 1024.0 and idx < len(units) - 1:
        val /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(val)} B"
    if val >= 100:
        return f"{val:.0f} {units[idx]}"
    return f"{val:.1f} {units[idx]}"


def _quality_tier_from_size(width: int, height: int) -> str:
    """Map pixel dimensions to concise quality badge label."""
    if width <= 0 or height <= 0:
        return "HD"
    long_edge = max(width, height)
    short_edge = min(width, height)
    if long_edge >= 3840 or short_edge >= 2160:
        return "4K"
    if long_edge >= 2560 or short_edge >= 1440:
        return "1440p"
    if long_edge >= 1920 or short_edge >= 1080:
        return "1080p"
    if long_edge >= 1280 or short_edge >= 720:
        return "720p"
    if long_edge >= 854 or short_edge >= 480:
        return "480p"
    return "SD"


def _track_display_name(meta: QMediaMetaData, prefix: str, idx: int) -> str:
    """Build clean human-readable label for a QMediaPlayer audio/subtitle track."""
    parts: list[str] = []
    try:
        lang = meta.stringValue(QMediaMetaData.Key.Language)
        title = meta.stringValue(QMediaMetaData.Key.Title)
        if title:
            parts.append(title.strip())
        if lang and lang.strip() and lang.strip().lower() not in (title or "").lower():
            parts.append(lang.strip().upper() if len(lang.strip()) <= 3 else lang.strip())
    except Exception:
        pass
    return " · ".join(parts) if parts else f"{prefix} {idx + 1}"


# ─── Encrypted Chunked QIODevice ──────────────────────────────────────────────


class StreamingVaultIODevice(QIODevice):
    """Seekable QIODevice streaming decrypted chunks on demand from vault blob.

    Maintains at most one 4 MB decrypted chunk in RAM, allowing playback and seeking
    of multi-GB encrypted videos with minimal memory footprint.
    """

    def __init__(
        self,
        blob_path: Path,
        master_key: bytes | bytearray,
        file_id: bytes,
    ) -> None:
        super().__init__()
        self._blob_path = blob_path
        self._file_id = file_id
        # Derive file key into mutable bytearray so it can be wiped on close
        self._file_key: bytearray = bytearray(derive_file_key(master_key, file_id))

        self._pos = 0
        self._total_size = 0
        self._total_chunks = 0
        self._header_bytes = b""
        self._is_v2 = True

        self._cached_chunk_idx: int = -1
        self._cached_chunk_data: bytearray = bytearray()
        self._io_lock = threading.RLock()

        self._read_header()
        super().open(QIODevice.OpenModeFlag.ReadOnly)

    def _read_header(self) -> None:
        """Parse blob header to determine version, size, and chunk layout."""
        with open(self._blob_path, "rb") as fh:
            magic = fh.read(len(MAGIC))
            if magic != MAGIC:
                raise ValueError("Not a valid vault encrypted file.")
            ver = fh.read(1)
            if ver == VERSION_V2:
                self._is_v2 = True
                fid = fh.read(16)
                total_chunks_bytes = fh.read(8)
                source_size_bytes = fh.read(8)
                self._total_chunks = struct.unpack("<Q", total_chunks_bytes)[0]
                self._total_size = struct.unpack("<Q", source_size_bytes)[0]
                self._header_bytes = magic + ver + fid + total_chunks_bytes + source_size_bytes
            elif ver == VERSION_V1:
                self._is_v2 = False
                fid = fh.read(16)
                total_chunks_bytes = fh.read(8)
                self._total_chunks = struct.unpack("<Q", total_chunks_bytes)[0]
                self._header_bytes = magic + ver + fid + total_chunks_bytes
                # In v1, total size is computed from file size minus headers and tag overhead
                file_size = self._blob_path.stat().st_size
                overhead = len(self._header_bytes)
                data_len = file_size - overhead
                self._total_size = max(0, data_len - self._total_chunks * 32)
            else:
                raise ValueError(f"Unsupported vault version: {ver!r}")

    def _load_chunk(self, chunk_idx: int) -> None:
        """Decrypt chunk_idx from disk into RAM, zeroing prior cached data."""
        if self._cached_chunk_data:
            zero_bytes(self._cached_chunk_data)
            self._cached_chunk_data = bytearray()
            self._cached_chunk_idx = -1

        if chunk_idx < 0 or chunk_idx >= self._total_chunks:
            return

        header_len = len(self._header_bytes)
        chunk_offset = header_len + chunk_idx * (CHUNK_SIZE + 32)

        try:
            with open(self._blob_path, "rb") as fh:
                fh.seek(chunk_offset)
                nonce = fh.read(12)
                ct_len_bytes = fh.read(4)
                if len(nonce) < 12 or len(ct_len_bytes) < 4:
                    return
                ct_len = struct.unpack("<I", ct_len_bytes)[0]
                ciphertext = fh.read(ct_len)
                if len(ciphertext) < ct_len:
                    return

            is_final = chunk_idx == self._total_chunks - 1
            if self._is_v2:
                ad = _make_ad_v2(self._header_bytes, chunk_idx, is_final)
            else:
                ad = _make_ad_v1(self._file_id, chunk_idx)

            plain = decrypt_aes_gcm(self._file_key, nonce, ciphertext, ad)
            self._cached_chunk_data = bytearray(plain)
            self._cached_chunk_idx = chunk_idx
        except Exception as exc:
            logger.error("Failed to decrypt chunk %d: %s", chunk_idx, type(exc).__name__)

    def size(self) -> int:
        return self._total_size

    def pos(self) -> int:
        return self._pos

    def bytesAvailable(self) -> int:
        return max(0, self._total_size - self._pos) + super().bytesAvailable()

    def isSequential(self) -> bool:
        return False

    def seek(self, pos: int) -> bool:
        with self._io_lock:
            if 0 <= pos <= self._total_size:
                self._pos = pos
                return super().seek(pos)
            return False

    def atEnd(self) -> bool:
        return self._pos >= self._total_size

    def readData(self, maxlen: int) -> bytes:
        with self._io_lock:
            if self._pos >= self._total_size or maxlen <= 0 or not self._file_key:
                return b""

            remaining_in_file = self._total_size - self._pos
            to_read_total = min(maxlen, remaining_in_file)
            out = bytearray()

            while len(out) < to_read_total and self._pos < self._total_size:
                chunk_idx = self._pos // CHUNK_SIZE
                offset_in_chunk = self._pos % CHUNK_SIZE

                if chunk_idx != self._cached_chunk_idx:
                    self._load_chunk(chunk_idx)

                if not self._cached_chunk_data:
                    break

                available = len(self._cached_chunk_data) - offset_in_chunk
                if available <= 0:
                    break

                need = to_read_total - len(out)
                take = min(need, available)
                out.extend(self._cached_chunk_data[offset_in_chunk:offset_in_chunk + take])
                self._pos += take

            return bytes(out)

    def zero_and_close(self) -> None:
        """Zero cached chunk buffer, wipe file key from RAM, and close QIODevice."""
        with self._io_lock:
            if self._file_key:
                zero_bytes(self._file_key)
                self._file_key = bytearray()
            if self._cached_chunk_data:
                zero_bytes(self._cached_chunk_data)
                self._cached_chunk_data = bytearray()
            self._cached_chunk_idx = -1
            self.close()


# ─── Export Worker (User-Requested Explicit Save) ─────────────────────────────


class _VideoExportWorker(QThread):
    """Exports an encrypted video to a user-selected destination off the UI thread."""

    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, vault, file_id: str, dest_path: Path, lock=None) -> None:
        super().__init__(None)
        self._vault = vault
        self._file_id = file_id
        self._dest_path = dest_path
        self._lock = lock

    def run(self) -> None:
        try:
            if self._lock is not None:
                with self._lock:
                    out = self._vault.export_file(self._file_id, self._dest_path)
            else:
                out = self._vault.export_file(self._file_id, self._dest_path)
            self.finished_ok.emit(str(out))
        except Exception as exc:
            self.failed.emit(type(exc).__name__)


# ─── Glass Micro-Interaction Components ───────────────────────────────────────


class _GlassIconButton(QPushButton):
    """Custom frosted-glass icon button with smooth hover/press spring scale and icon shift."""

    def __init__(
        self,
        icon_name: str,
        size: int = 40,
        icon_size: int = 18,
        radius: int = 12,
        circular: bool = False,
        glass_bg: bool = True,
        hover_shift_x: float = 0.0,
        tooltip: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._btn_size = size
        self._icon_size = icon_size
        self._circular = circular
        self._radius = size / 2.0 if circular else float(radius)
        self._glass_bg = glass_bg
        self._hover_shift_x = hover_shift_x
        self._active = False
        self._hover_progress = 0.0
        self._press_progress = 0.0
        self._accent_ring = False

        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if tooltip:
            self.setToolTip(tooltip)

        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(190)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_step)

        self._press_anim = QVariantAnimation(self)
        self._press_anim.setDuration(150)
        self._press_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._press_anim.valueChanged.connect(self._on_press_step)

    def set_icon_name(self, name: str) -> None:
        if name != self._icon_name:
            self._icon_name = name
            self.update()

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def set_accent_ring(self, enabled: bool) -> None:
        self._accent_ring = enabled
        self.update()

    def set_button_geometry(self, size: int, icon_size: int, radius: Optional[float] = None) -> None:
        self._btn_size = size
        self._icon_size = icon_size
        if radius is not None:
            self._radius = radius
        elif self._radius >= size / 2.0 - 1:
            self._radius = size / 2.0
        self.setFixedSize(size, size)
        self.update()

    def _on_hover_step(self, val) -> None:
        self._hover_progress = float(val)
        self.update()

    def _on_press_step(self, val) -> None:
        self._press_progress = float(val)
        self.update()

    def enterEvent(self, event) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_progress)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_progress)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_anim.stop()
            self._press_anim.setStartValue(self._press_progress)
            self._press_anim.setEndValue(1.0)
            self._press_anim.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._press_anim.stop()
        self._press_anim.setStartValue(self._press_progress)
        self._press_anim.setEndValue(0.0)
        self._press_anim.start()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w = float(self.width())
        h = float(self.height())
        # Subtle scale 1.0 -> 1.03 on hover, 0.96 on press
        scale = 1.0 + 0.03 * self._hover_progress - 0.06 * self._press_progress
        painter.translate(w / 2.0, h / 2.0)
        painter.scale(scale, scale)
        painter.translate(-w / 2.0, -h / 2.0)

        rect = QRectF(1.0, 1.0, w - 2.0, h - 2.0)
        path = QPainterPath()
        if self._circular:
            path.addEllipse(rect)
        else:
            rad = min(self._radius, min(rect.width(), rect.height()) / 2.0)
            path.addRoundedRect(rect, rad, rad)

        if self._glass_bg:
            if self._active:
                fill_alpha = int(38 + 22 * self._hover_progress)
                fill = QColor(139, 156, 255, fill_alpha)
                border = QColor(139, 156, 255, int(115 + 55 * self._hover_progress))
            elif self._accent_ring:
                fill_alpha = int(28 + 20 * self._hover_progress)
                fill = QColor(255, 255, 255, fill_alpha)
                border = QColor(168, 178, 255, int(110 + 60 * self._hover_progress))
            else:
                # 6% glass base + 8% brightness bump on hover
                fill_alpha = int(16 + 21 * self._hover_progress)
                fill = QColor(255, 255, 255, fill_alpha)
                border = QColor(255, 255, 255, int(26 + 34 * self._hover_progress))
            painter.fillPath(path, fill)
            painter.setPen(QPen(border, 1.0))
            painter.drawPath(path)
        elif self._hover_progress > 0.01 or self._active:
            alpha = int(28 * self._hover_progress) if not self._active else int(42 + 18 * self._hover_progress)
            painter.fillPath(path, QColor(139, 156, 255, alpha) if self._active else QColor(255, 255, 255, alpha))

        icon_color = VideoColors.ACCENT_HOVER if self._active else (
            VideoColors.WHITE if self._hover_progress > 0.35 else VideoColors.TEXT_ROW
        )
        px = icons.get_pixmap(self._icon_name, color=icon_color, size=self._icon_size)
        shift_x = self._hover_shift_x * self._hover_progress
        ix = (w - self._icon_size) / 2.0 + shift_x
        iy = (h - self._icon_size) / 2.0
        painter.drawPixmap(QPointF(ix, iy), px)


# ─── Smooth Interpolated Timeline Scrubber & Preview Popup ────────────────────


class _ScrubPreviewTooltip(QFrame):
    """Floating preview badge above timeline showing timestamp and optional video frame."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._pixmap: Optional[QPixmap] = None
        self._time_text = "00:00"
        self.hide()

    def update_preview(self, time_text: str, pixmap: Optional[QPixmap]) -> None:
        self._time_text = time_text
        self._pixmap = pixmap if (pixmap is not None and not pixmap.isNull()) else None
        if self._pixmap is not None:
            self.setFixedSize(148, 108)
        else:
            fm = QFontMetrics(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
            w = max(56, fm.horizontalAdvance(time_text) + 20)
            self.setFixedSize(w, 30)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        bg_path = QPainterPath()
        rad = 12.0 if self._pixmap is not None else 9.0
        bg_path.addRoundedRect(rect, rad, rad)
        painter.fillPath(bg_path, QColor(12, 14, 21, 232))
        painter.setPen(QPen(QColor(255, 255, 255, 34), 1.0))
        painter.drawPath(bg_path)

        if self._pixmap is not None:
            img_rect = QRectF(6.0, 6.0, self.width() - 12.0, self.height() - 28.0)
            clip_path = QPainterPath()
            clip_path.addRoundedRect(img_rect, 8.0, 8.0)
            painter.save()
            painter.setClipPath(clip_path)
            painter.fillRect(img_rect, QColor(7, 8, 12))
            scaled = self._pixmap.scaled(
                img_rect.size().toSize(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            sx = img_rect.x() + (img_rect.width() - scaled.width()) / 2.0
            sy = img_rect.y() + (img_rect.height() - scaled.height()) / 2.0
            painter.drawPixmap(QPointF(sx, sy), scaled)
            painter.restore()
            text_rect = QRectF(0.0, self.height() - 22.0, float(self.width()), 18.0)
        else:
            text_rect = rect

        font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(242, 246, 255))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self._time_text)


class _TimelineScrubber(QWidget):
    """Custom 60Hz-interpolated minimal timeline bar with hover preview and glowing thumb."""

    seek_started = Signal()
    seek_moved = Signal(int)      # target ms
    seek_committed = Signal(int)  # target ms
    hover_moved = Signal(int, int)  # hover ms, local x
    hover_left = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(26)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._duration_ms: int = 0
        self._reported_pos_ms: int = 0
        self._display_pos_ms: float = 0.0
        self._buffer_progress: float = 0.0
        self._playback_rate: float = 1.0
        self._playing: bool = False
        self._dragging: bool = False
        self._hovered: bool = False
        self._hover_ratio: float = 0.0
        self._last_report_ts: float = time.monotonic()
        self._track_height: float = 4.0

        self._height_anim = QVariantAnimation(self)
        self._height_anim.setDuration(160)
        self._height_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._height_anim.valueChanged.connect(self._on_track_height_step)

        self._interp_timer = QTimer(self)
        self._interp_timer.setInterval(16)
        self._interp_timer.timeout.connect(self._tick_smooth)

    def _on_track_height_step(self, val) -> None:
        self._track_height = float(val)
        self.update()

    def set_duration(self, duration_ms: int) -> None:
        self._duration_ms = max(0, int(duration_ms))
        self.update()

    def set_buffer_progress(self, progress: float) -> None:
        self._buffer_progress = max(0.0, min(1.0, float(progress)))
        self.update()

    def set_playback_state(self, playing: bool, rate: float = 1.0) -> None:
        self._playing = playing
        self._playback_rate = max(0.1, float(rate))
        self._last_report_ts = time.monotonic()
        if playing and not self._dragging:
            if not self._interp_timer.isActive():
                self._interp_timer.start()
        else:
            self._interp_timer.stop()

    def set_position(self, pos_ms: int, force: bool = False) -> None:
        pos_ms = max(0, int(pos_ms))
        self._reported_pos_ms = pos_ms
        self._last_report_ts = time.monotonic()
        if self._dragging and not force:
            return
        if force or abs(self._display_pos_ms - pos_ms) > 450 or not self._playing:
            self._display_pos_ms = float(pos_ms)
        self.update()

    def _tick_smooth(self) -> None:
        if not self._playing or self._dragging or self._duration_ms <= 0:
            return
        now = time.monotonic()
        elapsed_ms = max(0.0, (now - self._last_report_ts) * 1000.0 * self._playback_rate)
        target = min(float(self._duration_ms), float(self._reported_pos_ms) + min(elapsed_ms, 650.0))
        # Smoothly glide display position toward extrapolated playback target
        self._display_pos_ms += (target - self._display_pos_ms) * 0.35
        self.update()

    def _pos_for_x(self, x: float) -> tuple[float, int]:
        pad = 8.0
        usable = max(1.0, self.width() - 2.0 * pad)
        ratio = max(0.0, min(1.0, (x - pad) / usable))
        ms = int(round(ratio * self._duration_ms)) if self._duration_ms > 0 else 0
        return ratio, ms

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._height_anim.stop()
        self._height_anim.setStartValue(self._track_height)
        self._height_anim.setEndValue(6.0)
        self._height_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._height_anim.stop()
        self._height_anim.setStartValue(self._track_height)
        self._height_anim.setEndValue(4.0)
        self._height_anim.start()
        self.hover_left.emit()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.seek_started.emit()
            _, target_ms = self._pos_for_x(event.position().x())
            self._display_pos_ms = float(target_ms)
            self._reported_pos_ms = target_ms
            self.seek_moved.emit(target_ms)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        ratio, target_ms = self._pos_for_x(event.position().x())
        self._hover_ratio = ratio
        if self._duration_ms > 0:
            self.hover_moved.emit(target_ms, int(event.position().x()))
        if self._dragging:
            self._display_pos_ms = float(target_ms)
            self._reported_pos_ms = target_ms
            self.seek_moved.emit(target_ms)
            self.update()
            event.accept()
            return
        self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            _, target_ms = self._pos_for_x(event.position().x())
            self._display_pos_ms = float(target_ms)
            self._reported_pos_ms = target_ms
            self._last_report_ts = time.monotonic()
            self.seek_committed.emit(target_ms)
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pad = 8.0
        usable = max(1.0, float(self.width()) - 2.0 * pad)
        cy = float(self.height()) / 2.0
        th = self._track_height
        top = cy - th / 2.0

        # Background track
        bg_rect = QRectF(pad, top, usable, th)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 42))
        painter.drawRoundedRect(bg_rect, th / 2.0, th / 2.0)

        # Buffered progress sliver
        if self._buffer_progress > 0.01:
            buf_w = usable * min(1.0, self._buffer_progress)
            painter.setBrush(QColor(255, 255, 255, 62))
            painter.drawRoundedRect(QRectF(pad, top, buf_w, th), th / 2.0, th / 2.0)

        # Hover preview subtle range highlight
        if self._hovered and self._duration_ms > 0 and not self._dragging:
            hov_w = usable * self._hover_ratio
            painter.setBrush(QColor(255, 255, 255, 48))
            painter.drawRoundedRect(QRectF(pad, top, hov_w, th), th / 2.0, th / 2.0)

        # Active played gradient fill
        ratio = 0.0
        if self._duration_ms > 0:
            ratio = max(0.0, min(1.0, self._display_pos_ms / float(self._duration_ms)))
        fill_w = usable * ratio
        thumb_x = pad + fill_w

        if fill_w > 0.5:
            painter.setBrush(QColor(VideoColors.ACCENT_MID))
            painter.drawRoundedRect(QRectF(pad, top, fill_w, th), th / 2.0, th / 2.0)

        # Crisp circular thumb (flat, no neon glow)
        thumb_r = 6.5 if (self._hovered or self._dragging) else 5.0
        painter.setBrush(QColor(VideoColors.WHITE))
        painter.drawEllipse(QPointF(thumb_x, cy), thumb_r, thumb_r)


# ─── Expanding Volume Control ─────────────────────────────────────────────────


class _VolumeSliderTrack(QWidget):
    """Minimal horizontal volume slider inside the expanding volume capsule."""

    volume_changed = Signal(int)

    def __init__(self, initial: int = 80, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._value = max(0, min(100, initial))
        self._dragging = False
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def value(self) -> int:
        return self._value

    def setValue(self, val: int) -> None:
        clamped = max(0, min(100, int(val)))
        if clamped != self._value:
            self._value = clamped
            self.update()

    def _val_for_x(self, x: float) -> int:
        pad = 6.0
        usable = max(1.0, float(self.width()) - 2.0 * pad)
        ratio = max(0.0, min(1.0, (x - pad) / usable))
        return int(round(ratio * 100))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            v = self._val_for_x(event.position().x())
            self.setValue(v)
            self.volume_changed.emit(v)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            v = self._val_for_x(event.position().x())
            if v != self._value:
                self.setValue(v)
                self.volume_changed.emit(v)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        if self.width() < 12:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pad = 6.0
        usable = max(1.0, float(self.width()) - 2.0 * pad)
        cy = float(self.height()) / 2.0
        th = 4.0
        top = cy - th / 2.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 48))
        painter.drawRoundedRect(QRectF(pad, top, usable, th), 2.0, 2.0)

        ratio = self._value / 100.0
        fill_w = usable * ratio
        if fill_w > 0.5:
            painter.setBrush(QColor(VideoColors.ACCENT_MID))
            painter.drawRoundedRect(QRectF(pad, top, fill_w, th), 2.0, 2.0)

        painter.setBrush(QColor(VideoColors.WHITE))
        painter.drawEllipse(QPointF(pad + fill_w, cy), 5.0, 5.0)


class _VolumeCluster(QWidget):
    """Speaker mute button + smoothly animated horizontal slider (0 -> 90px on hover)."""

    mute_toggled = Signal()
    volume_changed = Signal(int)

    def __init__(self, initial: int = 80, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._expanded = False
        self._muted = False
        self._slider_width = 0

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._btn = _GlassIconButton(
            "volume_high",
            size=34,
            icon_size=17,
            radius=10,
            glass_bg=False,
            tooltip="Mute / Unmute (M)",
            parent=self,
        )
        self._btn.clicked.connect(self.mute_toggled.emit)

        self._slider = _VolumeSliderTrack(initial, self)
        self._slider.setMaximumWidth(0)
        self._slider.setMinimumWidth(0)
        self._slider.volume_changed.connect(self.volume_changed.emit)

        self._slider_opacity = QGraphicsOpacityEffect(self._slider)
        self._slider_opacity.setOpacity(0.0)
        self._slider.setGraphicsEffect(self._slider_opacity)

        layout.addWidget(self._btn)
        layout.addWidget(self._slider)

        self._width_anim = QVariantAnimation(self)
        self._width_anim.setDuration(180)
        self._width_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._width_anim.valueChanged.connect(self._on_expand_step)

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(260)
        self._collapse_timer.timeout.connect(self.collapse)

    def _on_expand_step(self, val) -> None:
        w = int(round(float(val)))
        self._slider_width = w
        self._slider.setFixedWidth(w)
        self._slider_opacity.setOpacity(min(1.0, w / 90.0))
        self.updateGeometry()

    def expand(self) -> None:
        self._collapse_timer.stop()
        if self._expanded:
            return
        self._expanded = True
        self._width_anim.stop()
        self._width_anim.setStartValue(float(self._slider_width))
        self._width_anim.setEndValue(90.0)
        self._width_anim.start()

    def collapse(self) -> None:
        if self._slider._dragging:
            return
        self._expanded = False
        self._width_anim.stop()
        self._width_anim.setStartValue(float(self._slider_width))
        self._width_anim.setEndValue(0.0)
        self._width_anim.start()

    def set_volume_state(self, value: int, muted: bool) -> None:
        self._muted = muted or value <= 0
        self._btn.set_icon_name("volume_mute" if self._muted else "volume_high")
        self._slider.setValue(0 if muted else value)

    def enterEvent(self, event) -> None:
        self.expand()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._slider._dragging:
            self._collapse_timer.start()
        super().leaveEvent(event)


# ─── Video Canvas (QVideoSink with Rounded Clipping & Fallback Poster) ────────


class _VideoCanvas(QWidget):
    """Paints QVideoFrame directly with 20px rounded clipping and KeepAspectRatio.

    Using QVideoSink + QPainter eliminates Windows HWND clipping bugs so frosted
    glass controls overlay smoothly on top with clean rounded corners.
    """

    single_clicked = Signal()
    double_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_frame: Optional[QVideoFrame] = None
        self._poster_pixmap: Optional[QPixmap] = None
        self._corner_radius: float = 20.0
        self._has_video_track: bool = True
        self._audio_only: bool = False
        self._resolution: QSize = QSize(0, 0)

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self._paint_opts = QVideoFrame.PaintOptions()
        self._paint_opts.aspectRatioMode = Qt.AspectRatioMode.KeepAspectRatio
        self._paint_opts.backgroundColor = QColor(7, 8, 12)

        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(210)
        self._click_timer.timeout.connect(self.single_clicked.emit)

    def set_corner_radius(self, radius: float) -> None:
        if radius != self._corner_radius:
            self._corner_radius = radius
            self.update()

    def set_poster_b64(self, thumb_b64: Optional[str]) -> None:
        if not thumb_b64:
            self._poster_pixmap = None
            return
        try:
            raw = base64.b64decode(thumb_b64)
            px = QPixmap()
            if px.loadFromData(raw):
                self._poster_pixmap = px
                self.update()
        except Exception:
            self._poster_pixmap = None

    def set_frame(self, frame: Optional[QVideoFrame]) -> Optional[QSize]:
        if frame is not None and frame.isValid():
            self._current_frame = frame
            sz = frame.size()
            if sz.isValid() and sz.width() > 0 and sz.height() > 0:
                self._resolution = sz
            self.update()
            return self._resolution if self._resolution.isValid() else None
        return None

    def set_audio_only(self, audio_only: bool) -> None:
        self._audio_only = audio_only
        self.update()

    def clear_media(self) -> None:
        self._click_timer.stop()
        self._current_frame = None
        self._poster_pixmap = None
        self._resolution = QSize(0, 0)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.start()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        clip_path = QPainterPath()
        if self._corner_radius > 0.5:
            clip_path.addRoundedRect(rect, self._corner_radius, self._corner_radius)
        else:
            clip_path.addRect(rect)

        painter.save()
        painter.setClipPath(clip_path)
        painter.fillRect(rect, QColor(7, 8, 12))

        if self._current_frame is not None and self._current_frame.isValid():
            frame_sz = self._current_frame.size()
            target = rect
            if frame_sz.isValid() and frame_sz.width() > 0 and frame_sz.height() > 0:
                fitted = frame_sz.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
                x = (self.width() - fitted.width()) / 2.0
                y = (self.height() - fitted.height()) / 2.0
                target = QRectF(x, y, float(fitted.width()), float(fitted.height()))
            try:
                self._current_frame.paint(painter, target, self._paint_opts)
            except Exception:
                img = self._current_frame.toImage()
                if not img.isNull():
                    painter.drawImage(target, img)
        elif self._audio_only:
            # Flat calm surface for audio tracks inside video player
            painter.fillRect(rect, QColor(VideoColors.BG_SURFACE))
            icon_px = icons.get_pixmap("audio_wave", color=VideoColors.BRAND, size=54)
            painter.drawPixmap(
                QPointF((self.width() - 54) / 2.0, (self.height() - 54) / 2.0 - 16),
                icon_px,
            )
        elif self._poster_pixmap is not None and not self._poster_pixmap.isNull():
            fitted = self._poster_pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x = (self.width() - fitted.width()) / 2.0
            y = (self.height() - fitted.height()) / 2.0
            painter.setOpacity(0.55)
            painter.drawPixmap(QRectF(x, y, float(fitted.width()), float(fitted.height())), self._poster_pixmap, QRectF(self._poster_pixmap.rect()))
            painter.setOpacity(1.0)

        painter.restore()

        if self._corner_radius > 0.5:
            painter.setPen(QPen(QColor(255, 255, 255, 20), 1.0))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), self._corner_radius, self._corner_radius)


# ─── Scrim Bars (Top & Bottom Translucent Gradient Overlays) ──────────────────


class _ScrimBar(QWidget):
    """Translucent gradient bar that blends cleanly over video without hard box edges."""

    def __init__(self, from_top: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._from_top = from_top
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        h = float(self.height())
        if self._from_top:
            grad = QLinearGradient(0.0, 0.0, 0.0, h)
            grad.setColorAt(0.0, QColor(7, 8, 12, 212))
            grad.setColorAt(0.65, QColor(11, 13, 18, 132))
            grad.setColorAt(1.0, QColor(11, 13, 18, 0))
        else:
            grad = QLinearGradient(0.0, 0.0, 0.0, h)
            grad.setColorAt(0.0, QColor(11, 13, 18, 0))
            grad.setColorAt(0.35, QColor(11, 13, 18, 145))
            grad.setColorAt(1.0, QColor(7, 8, 12, 224))
        painter.fillRect(self.rect(), QBrush(grad))


# ─── Frosted Glass Popover / Drawer Containers ────────────────────────────────


class _GlassCard(QFrame):
    """Reusable frosted dark glass panel (rgba(14,16,24,0.92), 1px border, soft shadow)."""

    def __init__(self, radius: int = 16, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._radius = float(radius)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, float(self.width()) - 1.0, float(self.height()) - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.fillPath(path, QColor(15, 17, 26, 238))
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
        painter.drawPath(path)


class _RightGlassRail(QWidget):
    """Subtle vertical glass control capsule on the right side (opacity 0.65 -> 1.0 hover)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._hover_boost = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_step)

    def _on_step(self, val) -> None:
        self._hover_boost = float(val)
        self.update()

    def enterEvent(self, event) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover_boost)
        self._anim.setEndValue(1.0)
        self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover_boost)
        self._anim.setEndValue(0.0)
        self._anim.start()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, float(self.width()) - 1.0, float(self.height()) - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 20.0, 20.0)
        bg_alpha = int(120 + 62 * self._hover_boost)
        border_alpha = int(24 + 26 * self._hover_boost)
        painter.fillPath(path, QColor(18, 20, 30, bg_alpha))
        painter.setPen(QPen(QColor(255, 255, 255, border_alpha), 1.0))
        painter.drawPath(path)


# ─── Loading Ring & Transient Action HUD ──────────────────────────────────────


class _LoadingOverlay(QWidget):
    """Minimal cinematic animated arc spinner (`Loading video` / `Buffering`)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._angle = 0.0
        self._caption = "Loading video"
        self._show_caption = True
        self.setFixedSize(160, 96)

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(16)
        self._spin_timer.timeout.connect(self._step)
        self.hide()

    def start(self, caption: str = "Loading video", show_caption: bool = True) -> None:
        self._caption = caption
        self._show_caption = show_caption
        self.show()
        self.raise_()
        if not self._spin_timer.isActive():
            self._spin_timer.start()
        self.update()

    def stop(self) -> None:
        self._spin_timer.stop()
        self.hide()

    def _step(self) -> None:
        self._angle = (self._angle + 6.5) % 360.0
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = float(self.width()) / 2.0
        cy = 34.0 if self._show_caption else float(self.height()) / 2.0
        r = 16.0

        # Subtle frosted pill backing when buffering over video frame
        pill_rect = QRectF(18.0, 8.0, float(self.width()) - 36.0, float(self.height()) - 16.0)
        if not self._show_caption:
            pill_rect = QRectF(cx - 28.0, cy - 28.0, 56.0, 56.0)
        bg = QPainterPath()
        bg.addRoundedRect(pill_rect, 18.0, 18.0)
        painter.fillPath(bg, QColor(12, 14, 21, 178))
        painter.setPen(QPen(QColor(255, 255, 255, 22), 1.0))
        painter.drawPath(bg)

        # Base faint ring
        painter.setPen(QPen(QColor(255, 255, 255, 36), 2.5))
        painter.drawEllipse(QPointF(cx, cy), r, r)

        # Animated continuous arc
        pen = QPen(QColor(VideoColors.ACCENT_RING), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        arc_rect = QRectF(cx - r, cy - r, r * 2.0, r * 2.0)
        painter.drawArc(arc_rect, int(-self._angle * 16), int(115 * 16))

        if self._show_caption and self._caption:
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
            painter.setPen(QColor(220, 226, 240, 220))
            painter.drawText(
                QRectF(0.0, cy + r + 8.0, float(self.width()), 24.0),
                Qt.AlignmentFlag.AlignCenter,
                self._caption,
            )


class _TransientHUD(QWidget):
    """Brief frosted capsule for keyboard/action feedback (`10s ↷`, `85%`, `1.5×`)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._text = ""
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._fade_anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_anim.setDuration(180)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.finished.connect(self._maybe_hide)

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(780)
        self._hold_timer.timeout.connect(self._fade_out)
        self.hide()

    def flash(self, text: str) -> None:
        self._text = text
        fm = QFontMetrics(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        w = max(92, fm.horizontalAdvance(text) + 34)
        self.setFixedSize(w, 38)
        self.show()
        self.raise_()
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._opacity.opacity())
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
        self._hold_timer.start()
        self.update()

    def _fade_out(self) -> None:
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._opacity.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    def _maybe_hide(self) -> None:
        if self._opacity.opacity() <= 0.01:
            self.hide()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, float(self.width()) - 1.0, float(self.height()) - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 19.0, 19.0)
        painter.fillPath(path, QColor(16, 18, 28, 224))
        painter.setPen(QPen(QColor(255, 255, 255, 34), 1.0))
        painter.drawPath(path)

        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        painter.setPen(QColor(244, 247, 255))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)


# ─── Settings, Info, Share & Error Overlays ───────────────────────────────────


class _SettingsPopover(_GlassCard):
    """Floating glass settings panel (Speed, Quality, Subtitles, Audio Tracks)."""

    speed_selected = Signal(float)
    subtitle_selected = Signal(int)
    audio_selected = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(radius=16, parent=parent)
        self._speed_buttons: dict[float, QPushButton] = {}
        self._current_speed = 1.0
        self._current_quality = "Auto"
        self._current_sub_idx = -1
        self._current_audio_idx = 0

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(14, 14, 14, 14)
        self._root.setSpacing(10)

        # Playback Speed section
        self._root.addWidget(self._section_label("Playback Speed"))
        speed_row = QHBoxLayout()
        speed_row.setSpacing(6)
        for spd in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            txt = f"{spd:g}×"
            btn = QPushButton(txt)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda _=False, s=spd: self.speed_selected.emit(s))
            self._speed_buttons[spd] = btn
            speed_row.addWidget(btn)
        self._root.addLayout(speed_row)

        self._root.addWidget(self._divider())

        # Quality section
        self._root.addWidget(self._section_label("Quality"))
        self._quality_lbl = QLabel("Auto · Source stream")
        self._quality_lbl.setStyleSheet(
            f"color: {VideoColors.TEXT_ROW}; background: rgba(139,156,255,0.16); "
            "border: 1px solid rgba(139,156,255,0.38); border-radius: 8px; "
            "padding: 6px 10px; font-size: 12px; font-weight: 600;"
        )
        self._root.addWidget(self._quality_lbl)

        self._root.addWidget(self._divider())

        # Subtitles section
        self._root.addWidget(self._section_label("Subtitles"))
        self._sub_box = QVBoxLayout()
        self._sub_box.setSpacing(4)
        self._root.addLayout(self._sub_box)

        # Audio Tracks section (populated dynamically if tracks > 1)
        self._audio_header = self._section_label("Audio Track")
        self._audio_divider = self._divider()
        self._audio_box = QVBoxLayout()
        self._audio_box.setSpacing(4)
        self._root.addWidget(self._audio_divider)
        self._root.addWidget(self._audio_header)
        self._root.addLayout(self._audio_box)

        self._style_speed_buttons()
        self.setFixedWidth(296)
        self.hide()

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(
            "color: rgba(167,179,197,0.85); font-size: 10px; font-weight: 700; "
            "letter-spacing: 0.8px; background: transparent;"
        )
        return lbl

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(255,255,255,0.09); border: none;")
        return line

    def _style_speed_buttons(self) -> None:
        for spd, btn in self._speed_buttons.items():
            selected = abs(spd - self._current_speed) < 0.01
            if selected:
                btn.setStyleSheet(
                    f"QPushButton {{ background: rgba(139,156,255,0.28); color: {VideoColors.WHITE}; "
                    "border: 1px solid rgba(139,156,255,0.65); border-radius: 7px; "
                    "font-size: 11px; font-weight: 700; padding: 0 6px; min-height: 26px; }"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: rgba(255,255,255,0.05); color: {VideoColors.TEXT_SECONDARY}; "
                    "border: 1px solid rgba(255,255,255,0.08); border-radius: 7px; "
                    "font-size: 11px; font-weight: 600; padding: 0 6px; min-height: 26px; }"
                    f"QPushButton:hover {{ background: rgba(255,255,255,0.12); color: {VideoColors.WHITE}; }}"
                )

    def _clear_layout(self, box: QVBoxLayout) -> None:
        while box.count():
            item = box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _make_option_btn(self, label: str, selected: bool, callback) -> QPushButton:
        btn = QPushButton(f"✓  {label}" if selected else f"    {label}")
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(28)
        if selected:
            btn.setStyleSheet(
                f"QPushButton {{ text-align: left; background: rgba(139,156,255,0.20); color: {VideoColors.WHITE}; "
                "border: 1px solid rgba(139,156,255,0.45); border-radius: 8px; padding: 0 10px; "
                "font-size: 12px; font-weight: 600; min-height: 26px; }"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton {{ text-align: left; background: transparent; color: {VideoColors.TEXT_SECONDARY}; "
                "border: 1px solid transparent; border-radius: 8px; padding: 0 10px; "
                "font-size: 12px; font-weight: 500; min-height: 26px; }"
                f"QPushButton:hover {{ background: rgba(255,255,255,0.08); color: {VideoColors.WHITE}; }}"
            )
        btn.clicked.connect(callback)
        return btn

    def refresh(
        self,
        speed: float,
        quality_text: str,
        subtitle_tracks: list[QMediaMetaData],
        active_sub: int,
        audio_tracks: list[QMediaMetaData],
        active_audio: int,
    ) -> None:
        self._current_speed = speed
        self._current_quality = quality_text
        self._current_sub_idx = active_sub
        self._current_audio_idx = active_audio

        self._style_speed_buttons()
        self._quality_lbl.setText(f"Auto ({quality_text}) · Original decrypted stream")

        # Populate Subtitles
        self._clear_layout(self._sub_box)
        self._sub_box.addWidget(
            self._make_option_btn("Off", active_sub < 0, lambda: self.subtitle_selected.emit(-1))
        )
        if subtitle_tracks:
            for idx, meta in enumerate(subtitle_tracks[:6]):
                lbl = _track_display_name(meta, "Subtitle", idx)
                self._sub_box.addWidget(
                    self._make_option_btn(
                        lbl,
                        active_sub == idx,
                        lambda _=False, i=idx: self.subtitle_selected.emit(i),
                    )
                )
        else:
            note = QLabel("No embedded subtitle tracks")
            note.setStyleSheet("color: rgba(148,163,184,0.75); font-size: 11px; padding: 2px 10px; background: transparent;")
            self._sub_box.addWidget(note)

        # Populate Audio Tracks if > 1 track
        self._clear_layout(self._audio_box)
        show_audio = len(audio_tracks) > 1
        self._audio_divider.setVisible(show_audio)
        self._audio_header.setVisible(show_audio)
        if show_audio:
            for idx, meta in enumerate(audio_tracks[:6]):
                lbl = _track_display_name(meta, "Audio", idx)
                self._audio_box.addWidget(
                    self._make_option_btn(
                        lbl,
                        active_audio == idx,
                        lambda _=False, i=idx: self.audio_selected.emit(i),
                    )
                )

        self.adjustSize()


class _InfoPanel(_GlassCard):
    """Frosted metadata drawer displaying real video & vault entry properties."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(radius=16, parent=parent)
        self._rows: dict[str, QLabel] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        hdr = QLabel("FILE DETAILS")
        hdr.setStyleSheet(
            "color: rgba(167,179,197,0.85); font-size: 10px; font-weight: 700; "
            "letter-spacing: 0.9px; background: transparent;"
        )
        root.addWidget(hdr)

        for key in ("Name", "Format", "Quality", "Resolution", "Duration", "Size", "Modified"):
            row = QHBoxLayout()
            row.setSpacing(12)
            k_lbl = QLabel(key)
            k_lbl.setFixedWidth(74)
            k_lbl.setStyleSheet(f"color: {VideoColors.TEXT_MUTED}; font-size: 12px; background: transparent;")
            v_lbl = QLabel("—")
            v_lbl.setWordWrap(True)
            v_lbl.setStyleSheet(f"color: {VideoColors.TEXT_BRIGHT}; font-size: 12px; font-weight: 600; background: transparent;")
            row.addWidget(k_lbl, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(v_lbl, 1)
            root.addLayout(row)
            self._rows[key] = v_lbl

        self.setFixedWidth(308)
        self.hide()

    def set_metadata(
        self,
        name: str,
        fmt: str,
        quality: str,
        resolution: str,
        duration: str,
        size_str: str,
        modified: str,
    ) -> None:
        self._rows["Name"].setText(name)
        self._rows["Format"].setText(fmt)
        self._rows["Quality"].setText(quality)
        self._rows["Resolution"].setText(resolution)
        self._rows["Duration"].setText(duration)
        self._rows["Size"].setText(size_str)
        self._rows["Modified"].setText(modified)
        self.adjustSize()


class _ErrorCard(_GlassCard):
    """Clean frosted playback error card with Retry action (zero raw tracebacks)."""

    retry_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(radius=18, parent=parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(10)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setPixmap(icons.get_pixmap("alert_triangle", color=VideoColors.DANGER, size=34))
        icon_lbl.setStyleSheet("background: transparent;")

        self._title_lbl = QLabel("Unable to play this video")
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setStyleSheet(
            f"color: {VideoColors.TEXT_PRIMARY}; font-size: 15px; font-weight: 700; background: transparent;"
        )

        self._msg_lbl = QLabel("The file may be corrupted, unsupported, or unavailable.")
        self._msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setStyleSheet(
            f"color: {VideoColors.TEXT_MUTED}; font-size: 12px; background: transparent;"
        )

        self._retry_btn = QPushButton("Try Again")
        self._retry_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retry_btn.setFixedHeight(36)
        self._retry_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(139,156,255,0.22); color: {VideoColors.WHITE}; "
            "border: 1px solid rgba(139,156,255,0.55); border-radius: 10px; "
            "padding: 0 22px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { background: rgba(139,156,255,0.34); }"
        )
        self._retry_btn.clicked.connect(self.retry_clicked.emit)

        root.addWidget(icon_lbl)
        root.addWidget(self._title_lbl)
        root.addWidget(self._msg_lbl)
        root.addSpacing(4)
        root.addWidget(self._retry_btn, 0, Qt.AlignmentFlag.AlignCenter)

        self.setFixedWidth(340)
        self.hide()

    def show_error(self, subtitle: Optional[str] = None) -> None:
        if subtitle:
            self._msg_lbl.setText(subtitle)
        else:
            self._msg_lbl.setText("The file may be corrupted, unsupported, or unavailable.")
        self.adjustSize()
        self.show()
        self.raise_()


class _ActionPopover(_GlassCard):
    """Minimal glass action menu popover for Share & More menus."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(radius=14, parent=parent)
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(8, 8, 8, 8)
        self._box.setSpacing(4)
        self.setFixedWidth(216)
        self.hide()

    def set_items(self, items: list[tuple[str, str, object]]) -> None:
        while self._box.count():
            item = self._box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        for icon_name, label, handler in items:
            btn = QPushButton(f"  {label}")
            btn.setIcon(icons.get_icon(icon_name, VideoColors.TEXT_SECONDARY, VideoColors.WHITE, size=15))
            btn.setIconSize(QSize(15, 15))
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(32)
            btn.setStyleSheet(
                f"QPushButton {{ text-align: left; background: transparent; color: {VideoColors.TEXT_ROW}; "
                "border: none; border-radius: 9px; padding: 0 10px; font-size: 12px; font-weight: 500; }"
                f"QPushButton:hover {{ background: rgba(255,255,255,0.10); color: {VideoColors.WHITE}; }}"
            )
            btn.clicked.connect(handler)
            self._box.addWidget(btn)
        self.adjustSize()


# ─── Outer Atmospheric Dark Shell ─────────────────────────────────────────────


class _ShellContainer(QWidget):
    """Dark cinematic container frame (#07080C -> #10121A) with faint radial depth."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._fullscreen = False
        self.setMouseTracking(True)

    def set_fullscreen(self, fs: bool) -> None:
        if fs != self._fullscreen:
            self._fullscreen = fs
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        path = QPainterPath()
        radius = 0.0 if self._fullscreen else 16.0
        if radius > 0.0:
            path.addRoundedRect(rect, radius, radius)
        else:
            path.addRect(rect)

        painter.fillPath(path, QColor(VideoColors.BG_DARKEST))

        if not self._fullscreen:
            painter.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


# ─── Main VideoPlayer Dialog ──────────────────────────────────────────────────


class VideoPlayer(QDialog):
    """Premium, minimal, dark-glass encrypted video player dialog."""

    favorite_changed = Signal(str, bool)  # file_id, is_favorite

    _SEEK_STEP_MS = 10_000
    _AUTO_HIDE_MS = 2_600
    _RESIZE_EDGE = 12

    def __init__(self, file_id: str, vault, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )

        self._vault = vault
        self._file_id = file_id
        self._io_device: Optional[StreamingVaultIODevice] = None
        self._player: Optional[QMediaPlayer] = None
        self._audio_output: Optional[QAudioOutput] = None
        self._video_sink: Optional[QVideoSink] = None
        self._export_worker: Optional[_VideoExportWorker] = None

        self._cleaned_up = False
        self._seeking = False
        self._is_fullscreen = False
        self._is_pip = False
        self._loop_enabled = False
        self._controls_visible = True
        self._last_volume = 80
        self._is_muted = False
        self._playback_rate = 1.0
        self._video_size = QSize(0, 0)
        self._duration_ms = 0
        self._controls_hover = False
        self._drag_offset: Optional[QPoint] = None
        self._resizing = False
        self._resize_start_pos: Optional[QPoint] = None
        self._resize_start_size: Optional[QSize] = None
        self._normal_geometry: Optional[QRect] = None
        self._pre_pip_geometry: Optional[QRect] = None

        # Lightweight RAM-only preview thumbnails keyed by timeline bucket (0..20)
        self._thumb_cache: dict[int, QPixmap] = {}

        entry = vault._index.index.get(file_id) if vault and hasattr(vault, "_index") else None
        self._entry = entry
        self._file_size = entry.size_bytes if entry else 0
        self._file_name = entry.original_name if entry else "video.mp4"
        self._is_favorite = bool(entry.is_favorite) if entry else False
        self._modified_ts = (
            getattr(entry, "modified_time", 0) or getattr(entry, "added_time", 0)
            if entry
            else 0
        )

        self.setWindowTitle(f"Video Player — {self._file_name}")
        self.setModal(True)
        self.setMinimumSize(500, 320)
        self.resize(1120, 668)
        self.setMouseTracking(True)

        self._build_ui()
        self._sync_metadata_labels()

        # Auto-hide timer for distraction-free playback
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(self._AUTO_HIDE_MS)
        self._hide_timer.timeout.connect(self._auto_hide_controls)

        self.finished.connect(self._cleanup)
        self._load_video()

        get_lock_controller().register_viewer(self)
        apply_screenshot_protection(self, True)

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._shell = _ShellContainer(self)
        outer.addWidget(self._shell)

        shell_layout = QVBoxLayout(self._shell)
        shell_layout.setContentsMargins(10, 10, 10, 10)
        shell_layout.setSpacing(0)
        self._shell_layout = shell_layout

        # Interactive viewport container inside shell
        self._stage = QWidget(self._shell)
        self._stage.setMouseTracking(True)
        self._stage.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        shell_layout.addWidget(self._stage, 1)

        # Video render canvas (100% of stage)
        self._video_canvas = _VideoCanvas(self._stage)
        if self._entry and getattr(self._entry, "thumbnail_b64", None):
            self._video_canvas.set_poster_b64(self._entry.thumbnail_b64)
            if self._video_canvas._poster_pixmap is not None:
                self._thumb_cache[0] = self._video_canvas._poster_pixmap
        self._video_canvas.single_clicked.connect(self._toggle_play)
        self._video_canvas.double_clicked.connect(self._toggle_fullscreen)

        # Top Information Bar (floating translucent scrim)
        self._top_bar = _ScrimBar(from_top=True, parent=self._stage)
        top_layout = QHBoxLayout(self._top_bar)
        top_layout.setContentsMargins(18, 14, 18, 14)
        top_layout.setSpacing(12)
        self._top_layout = top_layout

        self._back_btn = _GlassIconButton(
            "arrow_left",
            size=40,
            icon_size=18,
            radius=12,
            hover_shift_x=-2.0,
            tooltip="Back (Esc)",
            parent=self._top_bar,
        )
        self._back_btn.clicked.connect(self.close)

        self._video_badge = _GlassIconButton(
            "video",
            size=38,
            icon_size=17,
            radius=11,
            parent=self._top_bar,
        )
        self._video_badge.setCursor(Qt.CursorShape.ArrowCursor)
        self._video_badge.setEnabled(False)

        title_stack = QVBoxLayout()
        title_stack.setContentsMargins(0, 0, 0, 0)
        title_stack.setSpacing(1)

        self._title_lbl = QLabel(self._file_name, self._top_bar)
        self._title_lbl.setStyleSheet(
            f"color: {VideoColors.TEXT_PRIMARY}; font-size: 14px; font-weight: 600; background: transparent;"
        )
        self._title_lbl.setMinimumWidth(80)

        self._subtitle_lbl = QLabel("", self._top_bar)
        self._subtitle_lbl.setStyleSheet(
            "color: rgba(182, 194, 214, 0.78); font-size: 11px; font-weight: 500; background: transparent;"
        )

        title_stack.addWidget(self._title_lbl)
        title_stack.addWidget(self._subtitle_lbl)

        self._info_btn = _GlassIconButton(
            "info", size=40, icon_size=18, radius=12, tooltip="File Info (I)", parent=self._top_bar
        )
        self._info_btn.clicked.connect(self._toggle_info_panel)

        self._bookmark_btn = _GlassIconButton(
            "bookmark_filled" if self._is_favorite else "bookmark",
            size=40,
            icon_size=18,
            radius=12,
            tooltip="Bookmark / Favorite",
            parent=self._top_bar,
        )
        self._bookmark_btn.set_active(self._is_favorite)
        self._bookmark_btn.clicked.connect(self._toggle_bookmark)

        self._download_btn = _GlassIconButton(
            "download", size=40, icon_size=18, radius=12, tooltip="Export Decrypted Copy", parent=self._top_bar
        )
        self._download_btn.clicked.connect(self._export_copy)

        self._share_btn = _GlassIconButton(
            "share", size=40, icon_size=18, radius=12, tooltip="Share / Export Options", parent=self._top_bar
        )
        self._share_btn.clicked.connect(self._toggle_share_popover)

        self._more_btn = _GlassIconButton(
            "more_horizontal", size=40, icon_size=18, radius=12, tooltip="More Options", parent=self._top_bar
        )
        self._more_btn.clicked.connect(self._toggle_more_popover)

        top_layout.addWidget(self._back_btn)
        top_layout.addWidget(self._video_badge)
        top_layout.addLayout(title_stack, 1)
        top_layout.addWidget(self._info_btn)
        top_layout.addWidget(self._bookmark_btn)
        top_layout.addWidget(self._download_btn)
        top_layout.addWidget(self._share_btn)
        top_layout.addWidget(self._more_btn)

        # Center Playback Controls (Skip -10s | Play/Pause | Skip +10s)
        self._center_controls = QWidget(self._stage)
        self._center_controls.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._center_controls.setMouseTracking(True)
        center_layout = QHBoxLayout(self._center_controls)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(22)
        center_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._skip_back_btn = _GlassIconButton(
            "skip_back",
            size=48,
            icon_size=20,
            circular=True,
            tooltip="Back 10s (←)",
            parent=self._center_controls,
        )
        self._skip_back_btn.clicked.connect(lambda: self.seek_relative(-self._SEEK_STEP_MS))

        self._center_play_btn = _GlassIconButton(
            "play",
            size=78,
            icon_size=28,
            circular=True,
            tooltip="Play / Pause (Space)",
            parent=self._center_controls,
        )
        self._center_play_btn.set_accent_ring(True)
        self._center_play_btn.clicked.connect(self._toggle_play)

        self._skip_fwd_btn = _GlassIconButton(
            "skip_forward",
            size=48,
            icon_size=20,
            circular=True,
            tooltip="Forward 10s (→)",
            parent=self._center_controls,
        )
        self._skip_fwd_btn.clicked.connect(lambda: self.seek_relative(self._SEEK_STEP_MS))

        center_layout.addWidget(self._skip_back_btn)
        center_layout.addWidget(self._center_play_btn)
        center_layout.addWidget(self._skip_fwd_btn)
        self._center_controls.setFixedSize(228, 86)

        # Floating Right Vertical Glass Control Rail
        self._right_rail = _RightGlassRail(self._stage)
        rail_layout = QVBoxLayout(self._right_rail)
        rail_layout.setContentsMargins(6, 10, 6, 10)
        rail_layout.setSpacing(8)
        rail_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._rail_pip_btn = _GlassIconButton(
            "pip", size=36, icon_size=17, radius=10, glass_bg=False, tooltip="Picture-in-Picture", parent=self._right_rail
        )
        self._rail_pip_btn.clicked.connect(self._toggle_pip)

        self._rail_cc_btn = _GlassIconButton(
            "captions", size=36, icon_size=17, radius=10, glass_bg=False, tooltip="Subtitles / Captions", parent=self._right_rail
        )
        self._rail_cc_btn.clicked.connect(self._toggle_captions_quick)

        self._rail_audio_btn = _GlassIconButton(
            "audio_wave", size=36, icon_size=17, radius=10, glass_bg=False, tooltip="Audio / Mute", parent=self._right_rail
        )
        self._rail_audio_btn.set_active(True)
        self._rail_audio_btn.clicked.connect(self._toggle_audio_quick)

        self._rail_fs_btn = _GlassIconButton(
            "maximize", size=36, icon_size=17, radius=10, glass_bg=False, tooltip="Fullscreen (F)", parent=self._right_rail
        )
        self._rail_fs_btn.clicked.connect(self._toggle_fullscreen)

        rail_layout.addWidget(self._rail_pip_btn)
        rail_layout.addWidget(self._rail_cc_btn)
        rail_layout.addWidget(self._rail_audio_btn)
        rail_layout.addWidget(self._rail_fs_btn)
        self._right_rail.setFixedSize(48, 186)

        # Bottom Control Bar (floating translucent scrim)
        self._bottom_bar = _ScrimBar(from_top=False, parent=self._stage)
        bot_layout = QHBoxLayout(self._bottom_bar)
        bot_layout.setContentsMargins(20, 14, 20, 14)
        bot_layout.setSpacing(14)
        self._bot_layout = bot_layout

        self._time_lbl = QLabel("00:00 / 00:00", self._bottom_bar)
        self._time_lbl.setStyleSheet(
            f"color: {VideoColors.TEXT_ROW}; font-family: 'Consolas', 'Cascadia Mono', 'Segoe UI'; "
            "font-size: 12px; font-weight: 600; background: transparent;"
        )
        self._time_lbl.setFixedWidth(96)

        self._scrubber = _TimelineScrubber(self._bottom_bar)
        self._scrubber.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._scrubber.seek_started.connect(self._on_seek_started)
        self._scrubber.seek_moved.connect(self._on_seek_moved)
        self._scrubber.seek_committed.connect(self._on_seek_committed)
        self._scrubber.hover_moved.connect(self._on_scrubber_hover)
        self._scrubber.hover_left.connect(self._on_scrubber_leave)

        self._vol_cluster = _VolumeCluster(initial=self._last_volume, parent=self._bottom_bar)
        self._vol_cluster.mute_toggled.connect(self._toggle_mute)
        self._vol_cluster.volume_changed.connect(self._set_volume)

        self._settings_btn = _GlassIconButton(
            "settings", size=34, icon_size=17, radius=10, glass_bg=False, tooltip="Playback Settings", parent=self._bottom_bar
        )
        self._settings_btn.clicked.connect(self._toggle_settings_popover)

        self._quality_pill = QPushButton("HD", self._bottom_bar)
        self._quality_pill.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._quality_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._quality_pill.setFixedHeight(24)
        self._quality_pill.setStyleSheet(
            f"QPushButton {{ background: rgba(255,255,255,0.06); color: {VideoColors.TEXT_ROW}; "
            "border: 1px solid rgba(255,255,255,0.22); border-radius: 6px; "
            "padding: 0 7px; font-size: 10px; font-weight: 700; min-height: 22px; }"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.14); color: {VideoColors.WHITE}; }}"
        )
        self._quality_pill.clicked.connect(self._toggle_settings_popover)

        self._fs_btn = _GlassIconButton(
            "maximize", size=34, icon_size=17, radius=10, glass_bg=False, tooltip="Fullscreen (F)", parent=self._bottom_bar
        )
        self._fs_btn.clicked.connect(self._toggle_fullscreen)

        bot_layout.addWidget(self._time_lbl)
        bot_layout.addWidget(self._scrubber, 1)
        bot_layout.addWidget(self._vol_cluster)
        bot_layout.addWidget(self._settings_btn)
        bot_layout.addWidget(self._quality_pill)
        bot_layout.addWidget(self._fs_btn)

        # Opacity animations for smooth auto-hide / wake
        self._overlay_anims: list[tuple[QWidget, QGraphicsOpacityEffect, QPropertyAnimation]] = []
        for widget in (self._top_bar, self._center_controls, self._right_rail, self._bottom_bar):
            eff = QGraphicsOpacityEffect(widget)
            eff.setOpacity(1.0)
            widget.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b"opacity", self)
            anim.setDuration(210)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda w=widget, e=eff: self._on_fade_finished(w, e))
            self._overlay_anims.append((widget, eff, anim))

        # Popovers, Drawers, Tooltip, Loading, HUD, Error Card
        self._scrub_preview = _ScrubPreviewTooltip(self._stage)
        self._loading_overlay = _LoadingOverlay(self._stage)
        self._hud = _TransientHUD(self._stage)

        self._error_card = _ErrorCard(self._stage)
        self._error_card.retry_clicked.connect(self._retry_load)

        self._settings_popover = _SettingsPopover(self._stage)
        self._settings_popover.speed_selected.connect(self._set_playback_speed)
        self._settings_popover.subtitle_selected.connect(self._set_subtitle_track)
        self._settings_popover.audio_selected.connect(self._set_audio_track)

        self._info_panel = _InfoPanel(self._stage)
        self._share_popover = _ActionPopover(self._stage)
        self._more_popover = _ActionPopover(self._stage)

        # Install event filter recursively to track hover/mouse move across child widgets
        self._install_hover_tracking(self._stage)
        self._relayout_overlays()

    def _install_hover_tracking(self, root: QWidget) -> None:
        root.installEventFilter(self)
        for child in root.findChildren(QWidget):
            child.setMouseTracking(True)
            child.installEventFilter(self)

    # ── Layout & Responsive Breakpoints ───────────────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout_overlays()

    def _relayout_overlays(self) -> None:
        w = self._stage.width()
        h = self._stage.height()
        if w <= 0 or h <= 0:
            return

        self._video_canvas.setGeometry(0, 0, w, h)
        self._video_canvas.set_corner_radius(0.0 if self._is_fullscreen else 20.0)

        # Responsive thresholds
        compact = w < 720
        ultra_compact = w < 560

        top_h = 56 if self._is_pip else (64 if compact else 72)
        bot_h = 46 if self._is_pip else (58 if compact else 64)

        self._top_bar.setGeometry(0, 0, w, min(top_h, h // 2))
        self._bottom_bar.setGeometry(0, max(0, h - bot_h), w, min(bot_h, h // 2))

        # Adapt button sizes smoothly so controls never clip or overlap
        if self._is_pip:
            self._top_layout.setContentsMargins(10, 8, 10, 8)
            self._bot_layout.setContentsMargins(10, 6, 10, 8)
            self._video_badge.hide()
            self._subtitle_lbl.hide()
            self._info_btn.hide()
            self._bookmark_btn.hide()
            self._download_btn.hide()
            self._share_btn.hide()
            self._more_btn.hide()
            self._settings_btn.hide()
            self._quality_pill.hide()
            self._vol_cluster.hide()
            self._right_rail.hide()
            self._back_btn.set_button_geometry(32, 15, 9.0)
            self._fs_btn.set_button_geometry(28, 14, 8.0)
            self._center_play_btn.set_button_geometry(52, 20)
            self._skip_back_btn.hide()
            self._skip_fwd_btn.hide()
            self._center_controls.setFixedSize(64, 58)
        else:
            self._top_layout.setContentsMargins(12 if compact else 18, 12, 12 if compact else 18, 12)
            self._bot_layout.setContentsMargins(12 if compact else 20, 10, 12 if compact else 20, 12)
            self._video_badge.setVisible(not ultra_compact)
            self._subtitle_lbl.setVisible(True)
            self._info_btn.setVisible(not ultra_compact)
            self._bookmark_btn.setVisible(True)
            self._download_btn.setVisible(not ultra_compact)
            self._share_btn.setVisible(not ultra_compact)
            self._more_btn.setVisible(True)
            self._settings_btn.setVisible(True)
            self._quality_pill.setVisible(not ultra_compact)
            self._vol_cluster.setVisible(True)
            self._right_rail.setVisible(self._controls_visible and not ultra_compact and h >= 340)

            top_btn_sz = 34 if compact else 40
            top_ico_sz = 16 if compact else 18
            for b in (
                self._back_btn,
                self._info_btn,
                self._bookmark_btn,
                self._download_btn,
                self._share_btn,
                self._more_btn,
            ):
                b.set_button_geometry(top_btn_sz, top_ico_sz, 10.0 if compact else 12.0)
            self._video_badge.set_button_geometry(32 if compact else 38, 15 if compact else 17, 10.0)
            self._fs_btn.set_button_geometry(34, 17, 10.0)

            play_sz = 62 if compact else 78
            skip_sz = 42 if compact else 48
            self._center_play_btn.set_button_geometry(play_sz, 22 if compact else 28)
            self._skip_back_btn.set_button_geometry(skip_sz, 18 if compact else 20)
            self._skip_fwd_btn.set_button_geometry(skip_sz, 18 if compact else 20)
            self._skip_back_btn.show()
            self._skip_fwd_btn.show()
            self._center_controls.setFixedSize(play_sz + 2 * skip_sz + 52, play_sz + 8)

        # Elide long filenames cleanly
        avail_title = max(80, w - (220 if self._is_pip else (240 if ultra_compact else 410)))
        fm = QFontMetrics(self._title_lbl.font())
        self._title_lbl.setText(fm.elidedText(self._file_name, Qt.TextElideMode.ElideMiddle, avail_title))

        # Safe vertical band between top & bottom scrims
        safe_top = top_h
        safe_h = max(60, h - top_h - bot_h)
        center_y = safe_top + (safe_h - self._center_controls.height()) // 2
        center_x = (w - self._center_controls.width()) // 2
        self._center_controls.move(max(0, center_x), max(0, center_y))

        # Right vertical glass rail
        rail_x = w - self._right_rail.width() - (12 if compact else 18)
        rail_y = safe_top + (safe_h - self._right_rail.height()) // 2
        self._right_rail.move(max(0, rail_x), max(safe_top, rail_y))

        # Loading / Error / HUD overlays centered
        lx = (w - self._loading_overlay.width()) // 2
        ly = safe_top + (safe_h - self._loading_overlay.height()) // 2
        self._loading_overlay.move(max(0, lx), max(0, ly))

        ex = (w - self._error_card.width()) // 2
        ey = safe_top + (safe_h - self._error_card.height()) // 2
        self._error_card.move(max(0, ex), max(0, ey))

        hx = (w - self._hud.width()) // 2
        hy = max(safe_top + 14, safe_top + int(safe_h * 0.14))
        self._hud.move(max(0, hx), hy)

        # Position any open popovers inside viewport bounds
        self._reposition_popovers()

    def _reposition_popovers(self) -> None:
        w = self._stage.width()
        h = self._stage.height()
        margin = 14
        if not self._settings_popover.isHidden():
            pw = self._settings_popover.width()
            ph = self._settings_popover.height()
            px = min(w - pw - margin, max(margin, w - pw - 22))
            py = max(margin, h - self._bottom_bar.height() - ph - 10)
            self._settings_popover.move(px, py)
        if not self._info_panel.isHidden():
            iw = self._info_panel.width()
            px = min(w - iw - margin, max(margin, w - iw - 22))
            py = min(h - self._info_panel.height() - margin, self._top_bar.height() + 8)
            self._info_panel.move(px, py)
        if not self._share_popover.isHidden():
            sw = self._share_popover.width()
            px = max(margin, min(w - sw - margin, self._share_btn.x()))
            py = self._top_bar.height() + 8
            self._share_popover.move(px, py)
        if not self._more_popover.isHidden():
            mw = self._more_popover.width()
            px = max(margin, min(w - mw - margin, self._more_btn.x() + self._more_btn.width() - mw))
            py = self._top_bar.height() + 8
            self._more_popover.move(px, py)

    # ── Video Loading & Playback Wiring ───────────────────────────────────────

    def _load_video(self) -> None:
        self._error_card.hide()
        self._loading_overlay.start("Loading video", show_caption=True)
        try:
            entry = self._vault._index.index.get(self._file_id)
            if not entry:
                raise ValueError("File entry not found in index.")

            blob_name = entry.blob_name or (self._file_id + ".blob")
            blob_path = self._vault.blobs_dir / blob_name
            if not blob_path.exists():
                raise FileNotFoundError("Encrypted video blob missing.")

            mk = get_session().get_master_key()
            file_id_bytes = bytes.fromhex(self._file_id)

            self._io_device = StreamingVaultIODevice(
                blob_path=blob_path,
                master_key=mk,
                file_id=file_id_bytes,
            )

            self._audio_output = QAudioOutput(self)
            self._player = QMediaPlayer(self)
            self._video_sink = QVideoSink(self)

            self._player.setAudioOutput(self._audio_output)
            self._player.setVideoSink(self._video_sink)

            self._video_sink.videoFrameChanged.connect(self._on_video_frame)
            self._player.playbackStateChanged.connect(self._on_state_changed)
            self._player.mediaStatusChanged.connect(self._on_media_status_changed)
            self._player.positionChanged.connect(self._on_position_changed)
            self._player.durationChanged.connect(self._on_duration_changed)
            self._player.bufferProgressChanged.connect(self._on_buffer_changed)
            self._player.metaDataChanged.connect(self._on_metadata_changed)
            self._player.tracksChanged.connect(self._on_tracks_changed)
            self._player.hasVideoChanged.connect(self._on_has_video_changed)
            self._player.errorOccurred.connect(self._on_player_error)

            self._audio_output.setVolume(self._last_volume / 100.0)
            self._audio_output.setMuted(self._is_muted)
            self._vol_cluster.set_volume_state(self._last_volume, self._is_muted)

            self._player.setSourceDevice(self._io_device, QUrl(self._file_name))
            self._player.setPlaybackRate(self._playback_rate)
            self._player.play()
        except Exception as exc:
            logger.error("Could not initialize video stream: %s", type(exc).__name__)
            self._loading_overlay.stop()
            self._error_card.show_error()
            self._relayout_overlays()

    def _retry_load(self) -> None:
        self._teardown_player_only()
        self._load_video()

    def _teardown_player_only(self) -> None:
        if self._player is not None:
            try:
                self._player.stop()
                self._player.setSourceDevice(None)
                self._player.setVideoSink(None)
            except Exception:
                pass
            self._player.deleteLater()
            self._player = None
        if self._video_sink is not None:
            self._video_sink.deleteLater()
            self._video_sink = None
        if self._audio_output is not None:
            self._audio_output.deleteLater()
            self._audio_output = None
        if self._io_device is not None:
            self._io_device.zero_and_close()
            self._io_device = None

    # ── QMediaPlayer & QVideoSink Signals ─────────────────────────────────────

    def _on_video_frame(self, frame: QVideoFrame) -> None:
        if self._cleaned_up:
            return
        res = self._video_canvas.set_frame(frame)
        if res is not None and res != self._video_size:
            self._video_size = res
            self._sync_metadata_labels()

        # Opportunistically cache lightweight preview thumbnail for current timeline bucket
        if frame.isValid() and self._duration_ms > 0 and self._player is not None:
            pos = self._player.position()
            bucket = max(0, min(20, int(pos / float(self._duration_ms) * 20)))
            if bucket not in self._thumb_cache and len(self._thumb_cache) < 24:
                try:
                    img = frame.toImage()
                    if not img.isNull():
                        small = img.scaled(
                            160,
                            90,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        px = QPixmap.fromImage(small)
                        if not px.isNull():
                            self._thumb_cache[bucket] = px
                except Exception:
                    pass

    def _on_has_video_changed(self, has_video: bool) -> None:
        self._video_canvas.set_audio_only(not has_video)

    def _on_metadata_changed(self) -> None:
        if self._player is None:
            return
        try:
            meta = self._player.metaData()
            res = meta.value(QMediaMetaData.Key.Resolution)
            if isinstance(res, QSize) and res.isValid() and res.width() > 0 and res.height() > 0:
                self._video_size = res
                self._sync_metadata_labels()
        except Exception:
            pass

    def _on_tracks_changed(self) -> None:
        if self._player is None:
            return
        try:
            sub_active = self._player.activeSubtitleTrack() >= 0
            self._rail_cc_btn.set_active(sub_active)
            if not self._settings_popover.isHidden():
                self._refresh_settings_popover()
        except Exception:
            pass

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.LoadingMedia:
            self._error_card.hide()
            self._loading_overlay.start("Loading video", show_caption=True)
            self._relayout_overlays()
        elif status in (
            QMediaPlayer.MediaStatus.BufferingMedia,
            QMediaPlayer.MediaStatus.StalledMedia,
        ):
            # Keep last rendered frame visible while showing minimal arc spinner
            self._loading_overlay.start("", show_caption=False)
            self._relayout_overlays()
        elif status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            self._loading_overlay.stop()
            self._error_card.hide()
            if self._player and not self._player.hasVideo():
                self._video_canvas.set_audio_only(True)
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._loading_overlay.stop()
            if self._loop_enabled and self._player is not None:
                self._player.setPosition(0)
                self._player.play()
            else:
                self._show_controls(reset_timer=False)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._loading_overlay.stop()
            self._error_card.show_error()
            self._show_controls(reset_timer=False)
            self._relayout_overlays()

    def _on_player_error(self, error, error_string: str) -> None:
        logger.warning("Video playback error (%s)", error)
        self._loading_overlay.stop()
        self._scrubber.set_playback_state(False, self._playback_rate)
        self._error_card.show_error()
        self._show_controls(reset_timer=False)
        self._relayout_overlays()

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._center_play_btn.set_icon_name("pause" if playing else "play")
        self._scrubber.set_playback_state(playing, self._playback_rate)
        if playing:
            self._loading_overlay.stop()
            self._schedule_auto_hide()
        else:
            self._hide_timer.stop()
            self._show_controls(reset_timer=False)

    def _on_position_changed(self, pos: int) -> None:
        if not self._seeking:
            self._scrubber.set_position(pos)
            self._time_lbl.setText(
                f"{_format_duration_ms(pos)} / {_format_duration_ms(self._duration_ms)}"
            )

    def _on_duration_changed(self, dur: int) -> None:
        self._duration_ms = max(0, int(dur))
        self._scrubber.set_duration(self._duration_ms)
        pos = self._player.position() if self._player else 0
        self._time_lbl.setText(
            f"{_format_duration_ms(pos)} / {_format_duration_ms(self._duration_ms)}"
        )
        self._sync_metadata_labels()

    def _on_buffer_changed(self, progress: float) -> None:
        self._scrubber.set_buffer_progress(progress)

    # ── Metadata Sync ─────────────────────────────────────────────────────────

    def _sync_metadata_labels(self) -> None:
        ext = (
            self._file_name.rsplit(".", 1)[-1].upper()
            if "." in self._file_name
            else "VIDEO"
        )
        size_str = _format_size(self._file_size)
        w, h = self._video_size.width(), self._video_size.height()
        if w > 0 and h > 0:
            tier = _quality_tier_from_size(w, h)
            res_str = f"{w} × {h}"
            sub = f"{ext} · {tier} · {res_str} · {size_str}"
        else:
            tier = "HD"
            res_str = "Detecting…"
            sub = f"{ext} · {size_str}"

        self._subtitle_lbl.setText(sub)
        self._quality_pill.setText(tier)

        mod_str = "—"
        if self._modified_ts and self._modified_ts > 0:
            try:
                mod_str = datetime.datetime.fromtimestamp(self._modified_ts).strftime(
                    "%Y-%m-%d %H:%M"
                )
            except Exception:
                mod_str = "—"

        dur_str = _format_duration_ms(self._duration_ms) if self._duration_ms > 0 else "—"
        self._info_panel.set_metadata(
            name=self._file_name,
            fmt=ext,
            quality=tier,
            resolution=res_str,
            duration=dur_str,
            size_str=size_str,
            modified=mod_str,
        )

    # ── Playback, Seeking & Speed Actions ─────────────────────────────────────

    def _toggle_play(self) -> None:
        if not self._player:
            return
        self._show_controls()
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            if (
                self._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia
                and self._duration_ms > 0
            ):
                self._player.setPosition(0)
            self._player.play()

    def seek_relative(self, delta_ms: int) -> None:
        if not self._player or self._duration_ms <= 0:
            return
        cur = self._player.position()
        target = max(0, min(self._duration_ms, cur + delta_ms))
        self._player.setPosition(target)
        self._scrubber.set_position(target, force=True)
        self._time_lbl.setText(
            f"{_format_duration_ms(target)} / {_format_duration_ms(self._duration_ms)}"
        )
        secs = abs(delta_ms) // 1000
        self._hud.flash(f"↶ {secs}s" if delta_ms < 0 else f"{secs}s ↷")
        self._relayout_overlays()
        self._show_controls()

    def _on_seek_started(self) -> None:
        self._seeking = True
        self._hide_timer.stop()

    def _on_seek_moved(self, target_ms: int) -> None:
        self._time_lbl.setText(
            f"{_format_duration_ms(target_ms)} / {_format_duration_ms(self._duration_ms)}"
        )
        if self._player and self._duration_ms > 0:
            self._player.setPosition(target_ms)

    def _on_seek_committed(self, target_ms: int) -> None:
        self._seeking = False
        if self._player and self._duration_ms > 0:
            self._player.setPosition(target_ms)
        self._time_lbl.setText(
            f"{_format_duration_ms(target_ms)} / {_format_duration_ms(self._duration_ms)}"
        )
        self._schedule_auto_hide()

    def _on_scrubber_hover(self, hover_ms: int, local_x: int) -> None:
        if self._duration_ms <= 0:
            return
        bucket = max(0, min(20, int(hover_ms / float(self._duration_ms) * 20)))
        thumb = self._thumb_cache.get(bucket)
        if thumb is None:
            thumb = self._thumb_cache.get(max(0, bucket - 1)) or self._thumb_cache.get(min(20, bucket + 1))
        self._scrub_preview.update_preview(_format_duration_ms(hover_ms), thumb)

        global_pos = self._scrubber.mapTo(self._stage, QPoint(local_x, 0))
        px = max(
            12,
            min(
                self._stage.width() - self._scrub_preview.width() - 12,
                global_pos.x() - self._scrub_preview.width() // 2,
            ),
        )
        py = max(12, global_pos.y() - self._scrub_preview.height() - 10)
        self._scrub_preview.move(px, py)
        self._scrub_preview.show()
        self._scrub_preview.raise_()

    def _on_scrubber_leave(self) -> None:
        self._scrub_preview.hide()

    def _set_volume(self, value: int) -> None:
        val = max(0, min(100, int(value)))
        if val > 0:
            self._last_volume = val
            self._is_muted = False
        else:
            self._is_muted = True
        if self._audio_output:
            self._audio_output.setMuted(self._is_muted)
            self._audio_output.setVolume(val / 100.0)
        self._vol_cluster.set_volume_state(val, self._is_muted)
        self._rail_audio_btn.set_active(not self._is_muted)
        self._hud.flash("🔇 Muted" if self._is_muted else f"🔊 {val}%")
        self._relayout_overlays()
        self._show_controls()

    def _toggle_mute(self) -> None:
        self._is_muted = not self._is_muted
        if not self._is_muted and self._last_volume <= 0:
            self._last_volume = 80
        vol = 0 if self._is_muted else self._last_volume
        if self._audio_output:
            self._audio_output.setMuted(self._is_muted)
            if not self._is_muted:
                self._audio_output.setVolume(vol / 100.0)
        self._vol_cluster.set_volume_state(self._last_volume, self._is_muted)
        self._rail_audio_btn.set_active(not self._is_muted)
        self._hud.flash("🔇 Muted" if self._is_muted else f"🔊 {vol}%")
        self._relayout_overlays()
        self._show_controls()

    def _set_playback_speed(self, rate: float) -> None:
        self._playback_rate = float(rate)
        if self._player:
            self._player.setPlaybackRate(self._playback_rate)
            playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            self._scrubber.set_playback_state(playing, self._playback_rate)
        self._refresh_settings_popover()
        self._hud.flash(f"{rate:g}× Speed")
        self._relayout_overlays()
        self._show_controls()

    def _set_subtitle_track(self, idx: int) -> None:
        if self._player:
            try:
                self._player.setActiveSubtitleTrack(idx)
            except Exception:
                pass
        self._rail_cc_btn.set_active(idx >= 0)
        self._refresh_settings_popover()
        self._hud.flash("Subtitles Off" if idx < 0 else "Subtitles On")
        self._relayout_overlays()

    def _set_audio_track(self, idx: int) -> None:
        if self._player:
            try:
                self._player.setActiveAudioTrack(idx)
            except Exception:
                pass
        self._refresh_settings_popover()
        self._hud.flash(f"Audio Track {idx + 1}")
        self._relayout_overlays()

    # ── Top Bar & Right Rail Actions ──────────────────────────────────────────

    def _close_all_popovers(self, keep: Optional[QWidget] = None) -> None:
        for pop in (
            self._settings_popover,
            self._info_panel,
            self._share_popover,
            self._more_popover,
        ):
            if pop is not keep:
                pop.hide()

    def _refresh_settings_popover(self) -> None:
        subs = self._player.subtitleTracks() if self._player else []
        auds = self._player.audioTracks() if self._player else []
        sub_idx = self._player.activeSubtitleTrack() if self._player else -1
        aud_idx = self._player.activeAudioTrack() if self._player else 0
        self._settings_popover.refresh(
            speed=self._playback_rate,
            quality_text=self._quality_pill.text(),
            subtitle_tracks=list(subs),
            active_sub=sub_idx,
            audio_tracks=list(auds),
            active_audio=aud_idx,
        )

    def _toggle_settings_popover(self) -> None:
        show = self._settings_popover.isHidden()
        self._close_all_popovers(keep=self._settings_popover if show else None)
        if show:
            self._refresh_settings_popover()
            self._settings_popover.show()
            self._settings_popover.raise_()
            self._reposition_popovers()
            self._hide_timer.stop()
        else:
            self._settings_popover.hide()
            self._schedule_auto_hide()

    def _toggle_info_panel(self) -> None:
        show = self._info_panel.isHidden()
        self._close_all_popovers(keep=self._info_panel if show else None)
        if show:
            self._sync_metadata_labels()
            self._info_panel.show()
            self._info_panel.raise_()
            self._reposition_popovers()
            self._hide_timer.stop()
        else:
            self._info_panel.hide()
            self._schedule_auto_hide()

    def _toggle_share_popover(self) -> None:
        show = self._share_popover.isHidden()
        self._close_all_popovers(keep=self._share_popover if show else None)
        if show:
            self._share_popover.set_items(
                [
                    ("download", "Save Decrypted Copy…", self._export_copy),
                    ("copy", "Copy File Name", self._copy_filename),
                    ("info", "View Metadata", self._toggle_info_panel),
                ]
            )
            self._share_popover.show()
            self._share_popover.raise_()
            self._reposition_popovers()
            self._hide_timer.stop()
        else:
            self._share_popover.hide()
            self._schedule_auto_hide()

    def _toggle_more_popover(self) -> None:
        show = self._more_popover.isHidden()
        self._close_all_popovers(keep=self._more_popover if show else None)
        if show:
            loop_label = "Loop Playback: On" if self._loop_enabled else "Loop Playback: Off"
            self._more_popover.set_items(
                [
                    ("refresh", loop_label, self._toggle_loop),
                    ("pip", "Picture-in-Picture", self._toggle_pip),
                    ("info", "File Information", self._toggle_info_panel),
                    ("download", "Export Decrypted Copy…", self._export_copy),
                    ("x", "Close Player", self.close),
                ]
            )
            self._more_popover.show()
            self._more_popover.raise_()
            self._reposition_popovers()
            self._hide_timer.stop()
        else:
            self._more_popover.hide()
            self._schedule_auto_hide()

    def _toggle_captions_quick(self) -> None:
        if not self._player:
            return
        tracks = list(self._player.subtitleTracks())
        if tracks:
            cur = self._player.activeSubtitleTrack()
            self._set_subtitle_track(-1 if cur >= 0 else 0)
        else:
            self._toggle_settings_popover()
            self._hud.flash("No Embedded Captions")
            self._relayout_overlays()

    def _toggle_audio_quick(self) -> None:
        if self._player:
            tracks = list(self._player.audioTracks())
            if len(tracks) > 1:
                self._toggle_settings_popover()
                return
        self._toggle_mute()

    def _toggle_loop(self) -> None:
        self._close_all_popovers()
        self._loop_enabled = not self._loop_enabled
        self._hud.flash("Loop On" if self._loop_enabled else "Loop Off")
        self._relayout_overlays()
        self._schedule_auto_hide()

    def _copy_filename(self) -> None:
        self._close_all_popovers()
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self._file_name)
        self._hud.flash("Copied Name")
        self._relayout_overlays()
        self._schedule_auto_hide()

    def _toggle_bookmark(self) -> None:
        if not self._vault or not hasattr(self._vault, "_index"):
            return
        entry = self._vault._index.index.get(self._file_id)
        if not entry:
            return

        new_state = not bool(entry.is_favorite)
        parent_obj = self.parent()
        vault_service = getattr(parent_obj, "_vault_service", None)

        try:
            if vault_service is not None and hasattr(vault_service, "_lock"):
                acquired = vault_service._lock.acquire(blocking=False)
                if not acquired:
                    self._hud.flash("Vault Busy")
                    self._relayout_overlays()
                    return
                try:
                    entry.is_favorite = new_state
                    self._vault.save_index()
                finally:
                    vault_service._lock.release()
            else:
                entry.is_favorite = new_state
                self._vault.save_index()
        except Exception as exc:
            logger.error("Failed toggling bookmark: %s", type(exc).__name__)
            self._hud.flash("Bookmark Failed")
            self._relayout_overlays()
            return

        self._is_favorite = new_state
        self._bookmark_btn.set_icon_name("bookmark_filled" if new_state else "bookmark")
        self._bookmark_btn.set_active(new_state)
        self.favorite_changed.emit(self._file_id, new_state)
        self._hud.flash("Saved to Favorites" if new_state else "Removed Bookmark")
        self._relayout_overlays()
        self._show_controls()

    def _export_copy(self) -> None:
        self._close_all_popovers()
        if not self._vault:
            return
        dest_str, _ = QFileDialog.getSaveFileName(
            self,
            "Export Decrypted Video",
            self._file_name,
        )
        if not dest_str:
            return
        dest = Path(dest_str)
        parent_obj = self.parent()
        vault_service = getattr(parent_obj, "_vault_service", None)
        lock = getattr(vault_service, "_lock", None) if vault_service is not None else None

        if self._export_worker is not None and self._export_worker.isRunning():
            self._hud.flash("Export In Progress")
            self._relayout_overlays()
            return

        self._hud.flash("Exporting…")
        self._relayout_overlays()
        worker = _VideoExportWorker(self._vault, self._file_id, dest, lock=lock)
        worker.finished_ok.connect(self._on_export_done)
        worker.failed.connect(self._on_export_failed)
        worker.finished.connect(worker.deleteLater)
        self._export_worker = worker
        worker.start()

    def _on_export_done(self, _out: str) -> None:
        self._export_worker = None
        if not self._cleaned_up:
            self._hud.flash("Exported Copy")
            self._relayout_overlays()

    def _on_export_failed(self, _err: str) -> None:
        self._export_worker = None
        if not self._cleaned_up:
            self._hud.flash("Export Failed")
            self._relayout_overlays()

    # ── Fullscreen & Picture-in-Picture Modes ─────────────────────────────────

    def _toggle_fullscreen(self) -> None:
        if self._is_pip:
            self._exit_pip()

        if not self._is_fullscreen:
            self._normal_geometry = self.geometry()
            self._is_fullscreen = True
            self._shell.set_fullscreen(True)
            self._shell_layout.setContentsMargins(0, 0, 0, 0)
            self._fs_btn.set_icon_name("minimize")
            self._rail_fs_btn.set_icon_name("minimize")
            self.showFullScreen()
        else:
            self._is_fullscreen = False
            self._shell.set_fullscreen(False)
            self._shell_layout.setContentsMargins(10, 10, 10, 10)
            self._fs_btn.set_icon_name("maximize")
            self._rail_fs_btn.set_icon_name("maximize")
            self.showNormal()
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)

        self._relayout_overlays()
        self._show_controls()

    def _toggle_pip(self) -> None:
        self._close_all_popovers()
        if self._is_pip:
            self._exit_pip()
        else:
            if self._is_fullscreen:
                self._toggle_fullscreen()
            self._pre_pip_geometry = self.geometry()
            self._is_pip = True
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.setMinimumSize(360, 210)
            self.resize(420, 250)
            self._rail_pip_btn.set_active(True)
            self.show()
            self._hud.flash("Mini Player")
        self._relayout_overlays()
        self._show_controls()

    def _exit_pip(self) -> None:
        if not self._is_pip:
            return
        self._is_pip = False
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.setMinimumSize(500, 320)
        if self._pre_pip_geometry is not None:
            self.setGeometry(self._pre_pip_geometry)
        else:
            self.resize(1120, 668)
        self._rail_pip_btn.set_active(False)
        self.show()

    # ── Auto-Hide & Cursor Visibility ─────────────────────────────────────────

    def _any_popover_open(self) -> bool:
        return any(
            not w.isHidden()
            for w in (
                self._settings_popover,
                self._info_panel,
                self._share_popover,
                self._more_popover,
            )
        )

    def _is_pointer_over_interactive_chrome(self) -> bool:
        pos = self._stage.mapFromGlobal(self.cursor().pos())
        if not self._stage.rect().contains(pos):
            return False
        for w in (
            self._top_bar,
            self._bottom_bar,
            self._center_controls,
            self._right_rail,
        ):
            if not w.isHidden() and w.geometry().contains(pos):
                return True
        return False

    def _schedule_auto_hide(self) -> None:
        if (
            self._player is not None
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and not self._seeking
            and not self._any_popover_open()
            and self._error_card.isHidden()
        ):
            self._hide_timer.start(self._AUTO_HIDE_MS)

    def _show_controls(self, reset_timer: bool = True) -> None:
        self._video_canvas.setCursor(Qt.CursorShape.ArrowCursor)
        if not self._controls_visible:
            self._controls_visible = True
            for widget, eff, anim in self._overlay_anims:
                if widget is self._right_rail and not self._right_rail.isVisible() and self._stage.width() < 560:
                    continue
                if self._is_pip and widget is self._right_rail:
                    continue
                widget.show()
                widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
                target_op = 0.72 if widget is self._right_rail else 1.0
                anim.stop()
                anim.setStartValue(eff.opacity())
                anim.setEndValue(target_op)
                anim.start()
        if reset_timer:
            self._schedule_auto_hide()
        else:
            self._hide_timer.stop()

    def _auto_hide_controls(self) -> None:
        if (
            self._cleaned_up
            or not self._player
            or self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
            or self._seeking
            or self._any_popover_open()
            or not self._error_card.isHidden()
            or self._is_pointer_over_interactive_chrome()
        ):
            if (
                self._player
                and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and not self._any_popover_open()
            ):
                self._hide_timer.start(1000)
            return

        self._controls_visible = False
        self._scrub_preview.hide()
        for _widget, eff, anim in self._overlay_anims:
            anim.stop()
            anim.setStartValue(eff.opacity())
            anim.setEndValue(0.0)
            anim.start()
        if self._stage.rect().contains(self._stage.mapFromGlobal(self.cursor().pos())):
            self._video_canvas.setCursor(Qt.CursorShape.BlankCursor)

    def _on_fade_finished(self, widget: QWidget, eff: QGraphicsOpacityEffect) -> None:
        if not self._controls_visible and eff.opacity() <= 0.02:
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            widget.hide()

    # ── Mouse Move / Drag Window / Keyboard Shortcuts ─────────────────────────

    def eventFilter(self, watched, event: QEvent) -> bool:
        etype = event.type()
        if etype in (QEvent.Type.MouseMove, QEvent.Type.HoverMove):
            self._show_controls(reset_timer=True)
        elif etype == QEvent.Type.MouseButtonPress:
            self._show_controls(reset_timer=True)
            # Close popovers when clicking outside them on video canvas
            if watched is self._video_canvas and self._any_popover_open():
                self._close_all_popovers()
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._show_controls()
        if event.button() == Qt.MouseButton.LeftButton and not self._is_fullscreen:
            pt = event.position().toPoint()
            # Bottom-right corner resize zone
            if (
                pt.x() >= self.width() - self._RESIZE_EDGE - 6
                and pt.y() >= self.height() - self._RESIZE_EDGE - 6
            ):
                self._resizing = True
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_size = self.size()
                event.accept()
                return
            # Top bar empty region drags window smoothly
            if pt.y() <= 76:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._show_controls()
        pt = event.position().toPoint()
        if not self._is_fullscreen and not self._resizing and self._drag_offset is None:
            on_corner = (
                pt.x() >= self.width() - self._RESIZE_EDGE - 6
                and pt.y() >= self.height() - self._RESIZE_EDGE - 6
            )
            self.setCursor(
                Qt.CursorShape.SizeFDiagCursor if on_corner else Qt.CursorShape.ArrowCursor
            )
        if self._resizing and self._resize_start_pos is not None and self._resize_start_size is not None:
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            new_w = max(self.minimumWidth(), self._resize_start_size.width() + delta.x())
            new_h = max(self.minimumHeight(), self._resize_start_size.height() + delta.y())
            self.resize(new_w, new_h)
            event.accept()
            return
        if self._drag_offset is not None and not self._is_fullscreen:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        self._resizing = False
        self._resize_start_pos = None
        self._resize_start_size = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        self._show_controls()
        key = event.key()
        if key == Qt.Key.Key_Space:
            self._toggle_play()
            event.accept()
            return
        if key == Qt.Key.Key_Left:
            self.seek_relative(-self._SEEK_STEP_MS)
            event.accept()
            return
        if key == Qt.Key.Key_Right:
            self.seek_relative(self._SEEK_STEP_MS)
            event.accept()
            return
        if key == Qt.Key.Key_Up:
            cur = 0 if self._is_muted else self._vol_cluster._slider.value()
            self._set_volume(cur + 5)
            event.accept()
            return
        if key == Qt.Key.Key_Down:
            cur = 0 if self._is_muted else self._vol_cluster._slider.value()
            self._set_volume(cur - 5)
            event.accept()
            return
        if key == Qt.Key.Key_M:
            self._toggle_mute()
            event.accept()
            return
        if key == Qt.Key.Key_F:
            self._toggle_fullscreen()
            event.accept()
            return
        if key == Qt.Key.Key_I:
            self._toggle_info_panel()
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            if self._any_popover_open():
                self._close_all_popovers()
                self._schedule_auto_hide()
                event.accept()
                return
            if self._is_pip:
                self._exit_pip()
                self._relayout_overlays()
                event.accept()
                return
            if self._is_fullscreen:
                self._toggle_fullscreen()
                event.accept()
                return
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    # ── Cleanup & Zeroization ─────────────────────────────────────────────────

    def _cleanup(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True

        self._hide_timer.stop()
        if hasattr(self, "_loading_overlay"):
            self._loading_overlay.stop()
        if hasattr(self, "_scrubber"):
            self._scrubber.set_playback_state(False)

        self._teardown_player_only()

        self._thumb_cache.clear()
        if hasattr(self, "_video_canvas"):
            self._video_canvas.clear_media()

        if self._export_worker is not None and self._export_worker.isRunning():
            self._export_worker.wait(1500)
            self._export_worker = None

        get_lock_controller().unregister_viewer(self)

    def closeEvent(self, event) -> None:
        """Stop player, zero all chunk buffers & preview thumbnails, unregister viewer."""
        self._cleanup()
        super().closeEvent(event)


__all__ = ["VideoPlayer", "StreamingVaultIODevice"]
