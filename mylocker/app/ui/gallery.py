"""
gallery.py — In-memory photo viewer. No plaintext ever touches disk.

Displays a full-screen-able gallery with:
  - Smooth navigation (prev/next)
  - Zoom + pan
  - Slideshow mode
  - All images loaded from encrypted vault (bytearray in RAM)
  - Zero written to disk
"""

from __future__ import annotations

import io
import logging
from collections import OrderedDict
from typing import Optional

from PySide6.QtCore import (
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QImage,
    QKeySequence,
    QPixmap,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.crypto import zero_bytes
from ..core.vault import infer_mime_type
from ..system.lock_controller import get_lock_controller
from ..system.screenshot import apply_screenshot_protection
from .theme import Colors

logger = logging.getLogger(__name__)


class _ImageLoadWorker(QThread):
    """Decrypts gallery sibling images off the UI thread."""

    loaded = Signal(str, object)   # file_id, bytearray
    failed = Signal(str)

    def __init__(self, vault, file_id: str) -> None:
        super().__init__(None)
        self._vault = vault
        self._file_id = file_id

    def run(self) -> None:
        try:
            data = self._vault.open_file_in_memory(self._file_id)
            self.loaded.emit(self._file_id, data)
        except Exception as exc:
            logger.error("Could not load image: %s", type(exc).__name__)
            self.failed.emit(self._file_id)


class ZoomableImageLabel(QLabel):
    """Image label supporting wheel zoom and click+drag scroll panning."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._zoom = 1.0
        self._original_pixmap: Optional[QPixmap] = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"background: {Colors.BG_DARKEST};")

    def set_pixmap(self, px: QPixmap) -> None:
        self._original_pixmap = px
        self._zoom = 1.0
        self._render()

    def _render(self) -> None:
        if self._original_pixmap and not self._original_pixmap.isNull():
            w = max(1, int(self._original_pixmap.width() * self._zoom))
            h = max(1, int(self._original_pixmap.height() * self._zoom))
            scaled = self._original_pixmap.scaled(
                w, h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.setPixmap(scaled)
            self.setFixedSize(scaled.size())

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        factor = 1.12 if delta > 0 else 0.9
        self._zoom = max(0.05, min(10.0, self._zoom * factor))
        self._render()

    def reset_zoom(self) -> None:
        self._zoom = 1.0
        self._render()

    def fit_to_window(self, available: QSize) -> None:
        if not self._original_pixmap or self._original_pixmap.isNull():
            return
        pw, ph = self._original_pixmap.width(), self._original_pixmap.height()
        aw = max(100, available.width() - 24)
        ah = max(100, available.height() - 24)
        if pw == 0 or ph == 0:
            return
        self._zoom = min(aw / pw, ah / ph, 1.0)
        self._render()


class GalleryViewer(QDialog):
    """In-memory photo gallery dialog.

    Args:
        file_id: The file_id of the initially displayed image.
        initial_data: Pre-loaded bytearray of the first image.
        vault: Vault instance for loading sibling images.
    """

    MAX_LRU_CACHE = 5

    def __init__(
        self,
        file_id: str,
        initial_data: bytearray,
        vault,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._vault = vault
        self._current_id = file_id
        self._slideshow_timer: Optional[QTimer] = None
        self._cleaned_up = False
        self._load_workers: set[_ImageLoadWorker] = set()
        self._cached: OrderedDict[str, bytearray] = OrderedDict()  # LRU cache: file_id → bytearray

        # Build list of all image file IDs from vault index (with extension fallback)
        self._image_ids: list[str] = [
            e.file_id for e in vault._index.index.all_files()
            if infer_mime_type(e.original_name, e.mime_type).startswith("image/")
        ]
        if file_id not in self._image_ids:
            self._image_ids.insert(0, file_id)
        self._idx = self._image_ids.index(file_id)

        # Cache initial image
        self._put_cache(file_id, initial_data)

        self.setWindowTitle("Gallery — Personal Vault")
        self.setStyleSheet(f"background: {Colors.BG_DARKEST};")
        self.setModal(True)
        self.resize(1100, 750)
        self._build_ui()
        self._show_current()

        self.finished.connect(self._cleanup)

        # Register with central LockController and apply screenshot protection
        get_lock_controller().register_viewer(self)
        apply_screenshot_protection(self, True)

    def _put_cache(self, fid: str, data: bytearray) -> None:
        """Store in LRU cache, explicitly zeroing evicted image buffers."""
        if fid in self._cached:
            self._cached.move_to_end(fid)
            return
        if len(self._cached) >= self.MAX_LRU_CACHE:
            old_fid, old_buf = self._cached.popitem(last=False)
            zero_bytes(old_buf)
        self._cached[fid] = data

    def _build_ui(self) -> None:
        from .components import IconButton, PrimaryButton, SecondaryButton
        from .design_system import Tokens

        p = Tokens.colors
        self.setMouseTracking(True)

        self._controls_hide_timer = QTimer(self)
        self._controls_hide_timer.setSingleShot(True)
        self._controls_hide_timer.setInterval(2000)
        self._controls_hide_timer.timeout.connect(self._hide_overlay_bars)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Top toolbar ──
        self._top_bar = QWidget(self)
        self._top_bar.setFixedHeight(48)
        self._top_bar.setStyleSheet(f"background-color: {p.bg_secondary}; border-bottom: 1px solid {p.border};")
        tb_layout = QHBoxLayout(self._top_bar)
        tb_layout.setContentsMargins(16, 0, 16, 0)
        tb_layout.setSpacing(6)

        self._counter_lbl = QLabel("1 / 1", self._top_bar)
        self._counter_lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL))
        self._counter_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")
        tb_layout.addWidget(self._counter_lbl)
        tb_layout.addStretch()

        zoom_out = IconButton("zoom_out", tooltip="Zoom out", size=32, icon_size=16, parent=self._top_bar)
        zoom_out.clicked.connect(lambda: self._zoom(0.9))

        reset_zoom = SecondaryButton("Fit", icon_name="maximize", parent=self._top_bar)
        reset_zoom.setFixedHeight(32)
        reset_zoom.clicked.connect(self._fit_to_window)

        zoom_in = IconButton("zoom_in", tooltip="Zoom in", size=32, icon_size=16, parent=self._top_bar)
        zoom_in.clicked.connect(lambda: self._zoom(1.1))

        rotate_btn = IconButton("rotate_cw", tooltip="Rotate 90°", size=32, icon_size=16, parent=self._top_bar)
        rotate_btn.clicked.connect(self._rotate_90)

        self._slideshow_btn = SecondaryButton("Slideshow", icon_name="play", parent=self._top_bar)
        self._slideshow_btn.setFixedHeight(32)
        self._slideshow_btn.clicked.connect(self._toggle_slideshow)

        close_btn = IconButton("x", tooltip="Close gallery (Esc)", size=32, icon_size=16, parent=self._top_bar)
        close_btn.clicked.connect(self.accept)

        for btn in (zoom_out, reset_zoom, zoom_in, rotate_btn, self._slideshow_btn, close_btn):
            tb_layout.addWidget(btn)

        # ── Image area + loading / error overlay states ──
        from .components import ErrorState, Spinner

        self._img_scroll = QScrollArea(self)
        self._img_scroll.setFrameShape(self._img_scroll.Shape.NoFrame)
        self._img_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_scroll.setStyleSheet(f"background-color: {p.bg};")
        self._img_scroll.setMouseTracking(True)
        self._img_lbl = ZoomableImageLabel()
        self._img_scroll.setWidget(self._img_lbl)
        self._img_scroll.setWidgetResizable(False)

        self._loading_state = QWidget(self)
        self._loading_state.setStyleSheet(f"background-color: {p.bg};")
        load_lay = QVBoxLayout(self._loading_state)
        load_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        load_lay.setSpacing(10)
        self._spinner = Spinner(28, parent=self._loading_state)
        self._loading_lbl = QLabel("Decrypting image…", self._loading_state)
        self._loading_lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_LABEL))
        self._loading_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")
        load_lay.addWidget(self._spinner, 0, Qt.AlignmentFlag.AlignCenter)
        load_lay.addWidget(self._loading_lbl, 0, Qt.AlignmentFlag.AlignCenter)
        self._loading_state.hide()

        self._error_state = ErrorState(
            "Unable to open image",
            "Could not decrypt or decode this image file.",
            retry_text="Retry",
            parent=self,
        )
        self._error_state.retry_clicked.connect(self._show_current)
        self._error_state.hide()

        # ── Bottom navigation ──
        self._bottom_nav = QWidget(self)
        self._bottom_nav.setFixedHeight(56)
        self._bottom_nav.setStyleSheet(f"background-color: {p.bg_secondary}; border-top: 1px solid {p.border};")
        nav_layout = QHBoxLayout(self._bottom_nav)
        nav_layout.setContentsMargins(16, 0, 16, 0)
        nav_layout.setSpacing(12)

        prev_btn = SecondaryButton("Previous", icon_name="chevron_left", parent=self._bottom_nav)
        prev_btn.setFixedHeight(34)
        prev_btn.clicked.connect(self._prev)

        next_btn = PrimaryButton("Next", icon_name="chevron_right", parent=self._bottom_nav)
        next_btn.setFixedHeight(34)
        next_btn.clicked.connect(self._next)

        self._name_lbl = QLabel("", self._bottom_nav)
        self._name_lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_LABEL))
        self._name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")

        nav_layout.addWidget(prev_btn)
        nav_layout.addStretch()
        nav_layout.addWidget(self._name_lbl)
        nav_layout.addStretch()
        nav_layout.addWidget(next_btn)

        layout.addWidget(self._top_bar)
        layout.addWidget(self._loading_state, 1)
        layout.addWidget(self._error_state, 1)
        layout.addWidget(self._img_scroll, 1)
        layout.addWidget(self._bottom_nav)

        # Keyboard shortcuts
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self._prev)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self._next)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.accept)
        QShortcut(QKeySequence("Space"), self, self._toggle_slideshow)
        self._controls_hide_timer.start()

    def _wake_overlay_bars(self) -> None:
        if hasattr(self, "_top_bar"):
            self._top_bar.show()
        if hasattr(self, "_bottom_nav"):
            self._bottom_nav.show()
        if hasattr(self, "_controls_hide_timer"):
            self._controls_hide_timer.start()

    def _hide_overlay_bars(self) -> None:
        if self._cleaned_up:
            return
        top_hover = hasattr(self, "_top_bar") and self._top_bar.underMouse()
        bot_hover = hasattr(self, "_bottom_nav") and self._bottom_nav.underMouse()
        if top_hover or bot_hover:
            self._controls_hide_timer.start()
            return
        if hasattr(self, "_top_bar"):
            self._top_bar.hide()
        if hasattr(self, "_bottom_nav"):
            self._bottom_nav.hide()

    def mouseMoveEvent(self, event) -> None:
        self._wake_overlay_bars()
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event) -> None:
        self._wake_overlay_bars()
        super().keyPressEvent(event)

    def _rotate_90(self) -> None:
        self._wake_overlay_bars()
        if self._img_lbl._original_pixmap and not self._img_lbl._original_pixmap.isNull():
            from PySide6.QtGui import QTransform
            rotated = self._img_lbl._original_pixmap.transformed(QTransform().rotate(90), Qt.TransformationMode.SmoothTransformation)
            self._img_lbl.set_pixmap(rotated)
            self._fit_to_window()

    # ── Navigation ─────────────────────────────────────────────────────────

    def _prev(self) -> None:
        if len(self._image_ids) == 0:
            return
        self._idx = (self._idx - 1) % len(self._image_ids)
        self._current_id = self._image_ids[self._idx]
        self._show_current()

    def _next(self) -> None:
        if len(self._image_ids) == 0:
            return
        self._idx = (self._idx + 1) % len(self._image_ids)
        self._current_id = self._image_ids[self._idx]
        self._show_current()

    def _decode_pixmap(self, data: bytearray) -> QPixmap:
        px = QPixmap()
        if px.loadFromData(bytes(data)) and not px.isNull():
            return px
        # Fallback via Pillow for formats not handled by Qt image plugins
        try:
            from PIL import Image
            with Image.open(io.BytesIO(bytes(data))) as im:
                rgba = im.convert("RGBA")
                raw = rgba.tobytes("raw", "RGBA")
                qimg = QImage(raw, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
                return QPixmap.fromImage(qimg.copy())
        except Exception:
            return QPixmap()

    def _show_current(self) -> None:
        fid = self._current_id
        total = len(self._image_ids)
        entry = self._vault._index.index.get(fid)
        name = entry.original_name if entry else fid
        self._counter_lbl.setText(f"{self._idx + 1} / {total}")
        self._name_lbl.setText(name)

        if fid not in self._cached:
            self._img_lbl._original_pixmap = None
            self._img_lbl.setPixmap(QPixmap())
            self._img_scroll.hide()
            self._error_state.hide()
            self._loading_state.show()
            self._spinner.start()
            worker = _ImageLoadWorker(self._vault, fid)
            self._load_workers.add(worker)

            def _on_loaded(loaded_fid: str, data: object) -> None:
                self._load_workers.discard(worker)
                worker.deleteLater()
                buf = data if isinstance(data, bytearray) else bytearray(data or b"")
                if self._cleaned_up:
                    zero_bytes(buf)
                    return
                self._put_cache(loaded_fid, buf)
                if loaded_fid == self._current_id:
                    self._render_cached_image(loaded_fid)

            def _on_failed(failed_fid: str) -> None:
                self._load_workers.discard(worker)
                worker.deleteLater()
                if not self._cleaned_up and failed_fid == self._current_id:
                    self._spinner.stop()
                    self._loading_state.hide()
                    self._img_scroll.hide()
                    self._error_state.set_error("Unable to open image", "Could not decrypt or load image.")
                    self._error_state.show()

            worker.loaded.connect(_on_loaded, Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(_on_failed, Qt.ConnectionType.QueuedConnection)
            worker.start()
            return

        self._cached.move_to_end(fid)
        self._render_cached_image(fid)

    def _render_cached_image(self, fid: str) -> None:
        if fid not in self._cached:
            return
        if hasattr(self, "_spinner"):
            self._spinner.stop()
            self._loading_state.hide()
        data = self._cached[fid]
        px = self._decode_pixmap(data)

        if px.isNull():
            self._img_lbl._original_pixmap = None
            self._img_lbl.setPixmap(QPixmap())
            self._img_scroll.hide()
            self._error_state.set_error("Unsupported image", "Could not display this image format.")
            self._error_state.show()
            return

        self._error_state.hide()
        self._img_scroll.show()
        self._img_lbl.setText("")
        self._img_lbl.setStyleSheet(f"background: {Colors.BG_DARKEST};")
        self._img_lbl.set_pixmap(px)
        self._fit_to_window()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_to_window)

    def _fit_to_window(self) -> None:
        vp = self._img_scroll.viewport()
        avail = vp.size() if (vp and vp.width() > 50 and vp.height() > 50) else self._img_scroll.size()
        self._img_lbl.fit_to_window(avail)

    def _zoom(self, factor: float) -> None:
        self._img_lbl._zoom = max(0.05, min(10.0, self._img_lbl._zoom * factor))
        self._img_lbl._render()

    # ── Slideshow ───────────────────────────────────────────────────────────

    def _toggle_slideshow(self) -> None:
        if self._slideshow_timer and self._slideshow_timer.isActive():
            self._slideshow_timer.stop()
            self._slideshow_btn.set_icon_name("play")
            self._slideshow_btn.setText("Slideshow")
        else:
            self._slideshow_timer = QTimer(self)
            self._slideshow_timer.timeout.connect(self._next)
            self._slideshow_timer.start(4000)
            self._slideshow_btn.set_icon_name("pause")
            self._slideshow_btn.setText("Pause")

    def _cleanup(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True
        if hasattr(self, "_spinner"):
            self._spinner.stop()
        if hasattr(self, "_controls_hide_timer") and self._controls_hide_timer.isActive():
            self._controls_hide_timer.stop()
        if self._slideshow_timer and self._slideshow_timer.isActive():
            self._slideshow_timer.stop()
            self._slideshow_timer = None
        for w in list(self._load_workers):
            if w.isRunning():
                w.wait(1500)
        for buf in self._cached.values():
            zero_bytes(buf)
        self._cached.clear()
        get_lock_controller().unregister_viewer(self)

    def closeEvent(self, event) -> None:
        """Zero all cached image data when gallery closes and unregister from LockController."""
        self._cleanup()
        super().closeEvent(event)

