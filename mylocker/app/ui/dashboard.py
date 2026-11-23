"""
dashboard.py — Main vault window with sidebar navigation.

The window is hidden / shows nothing until session.is_unlocked is True.
Implements:
  - Sidebar with animated item selection
  - File grid/list view with thumbnails from encrypted index
  - Drag and drop to add files
  - Toolbar with search, add, lock
  - SetWindowDisplayAffinity for screenshot protection
  - Status bar showing lock state
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (
    QEvent,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..core.crypto import zero_bytes
from ..core.index import IndexCorruptError
from ..core.service import VaultService
from ..core.session import get_session
from ..core.thumbnails import (
    is_qimage_mostly_black,
    make_image_thumbnail_from_bytes_b64,
    make_video_thumbnail_from_bytes_b64,
    qimage_to_thumbnail_b64,
)
from ..core.vault import infer_mime_type
from . import icons
from .dashboard_theme import (
    CategoryPill,
    DashColors,
    DocBadgeGenerator,
    MediaBadgeGenerator,
    MetricCard,
    NotificationBellButton,
    SidebarBrandWidget,
    SidebarProtectionStatus,
    VaultSearchBar,
    VaultUnlockedBadge,
)
from .theme import Colors
from .widgets import (
    AnimatedCard,
    ProgressOverlay,
    SidebarItem,
    SmoothScrollArea,
)

logger = logging.getLogger(__name__)

WDA_EXCLUDEFROMCAPTURE = 0x11
_GRID_BATCH_SIZE = 48


def format_file_size(b: int) -> str:
    """Format byte counts into human-readable strings (e.g. '12.6 GB', '4.2 MB')."""
    val = float(b)
    for u in ["B", "KB", "MB", "GB"]:
        if val < 1024:
            return f"{int(val)} {u}" if u == "B" else f"{val:.1f} {u}"
        val /= 1024
    return f"{val:.1f} TB"

_FILECARD_STYLE = f"""
    #FileCard {{
        background-color: {DashColors.BG_CARD};
        border: 1px solid {DashColors.BORDER_SUBTLE};
        border-radius: 14px;
    }}
    #FileCard:hover {{
        border: 1px solid {DashColors.BORDER_HOVER};
        background-color: {DashColors.BG_CARD_HOVER};
    }}
    #FileCard[selected="true"] {{
        border: 2px solid {DashColors.BORDER_ACTIVE};
        background-color: rgba(59, 130, 246, 0.18);
    }}
    #FileThumb {{
        background: #0B1120;
        border-radius: 10px;
        color: {DashColors.TEXT_MUTED};
    }}
    #FileName {{
        color: {DashColors.TEXT_PRIMARY};
        background: transparent;
        font-weight: 600;
        font-size: 12px;
    }}
    #FileName[favorite="true"] {{
        color: {DashColors.ACCENT_AMBER};
    }}
    #FileSize {{
        color: {DashColors.TEXT_MUTED};
        background: transparent;
        font-size: 10px;
    }}
    #MoreBtn {{
        background: transparent;
        border: none;
        border-radius: 4px;
    }}
    #MoreBtn:hover {{
        background: rgba(255, 255, 255, 0.08);
    }}
    #SelectBadge {{
        background: rgba(15, 19, 32, 0.78);
        border: 1.5px solid rgba(255, 255, 255, 0.55);
        border-radius: 6px;
    }}
    #SelectBadge:hover {{
        border: 1.5px solid {DashColors.BORDER_ACTIVE};
        background: rgba(15, 19, 32, 0.92);
    }}
    #SelectBadge[selected="true"] {{
        background: {DashColors.BORDER_ACTIVE};
        border: 1.5px solid {DashColors.BORDER_ACTIVE};
    }}
"""


class SidebarWidget(QWidget):
    """Sidebar container with dark glass styling and subtle mountain backdrop."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(260)
        from pathlib import Path
        mount_path = Path(__file__).resolve().parent / "resources" / "sidebar_mountains.png"
        self._mount_pix = QPixmap(str(mount_path)) if mount_path.exists() else None

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(DashColors.BG_SIDEBAR))
        if self._mount_pix is not None and not self._mount_pix.isNull():
            mh = self._mount_pix.height()
            y = self.height() - mh
            p.setOpacity(0.65)
            p.drawPixmap(
                0,
                y,
                self._mount_pix.scaled(
                    260,
                    mh,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                ),
            )
        # Subtle right vertical separator border
        p.setPen(QColor(125, 160, 220, 24))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        p.end()


# ── File card widget ──────────────────────────────────────────────────────────

class FileCard(AnimatedCard):
    """Card showing a single vault file with thumbnail/vector icon, name, size."""

    open_requested = Signal(str)    # file_id
    restore_requested = Signal(str)
    delete_requested = Signal(str)
    favorite_toggled = Signal(str, bool)
    selection_requested = Signal(str, object)   # (file_id, Qt.KeyboardModifiers)
    select_toggle_requested = Signal(str)       # file_id
    batch_delete_requested = Signal()

    def __init__(
        self,
        entry,
        parent: Optional[QWidget] = None,
        *,
        thumb_cache: Optional[dict] = None,
        selected: bool = False,
        selection_mode: bool = False,
        selected_count_provider: Optional[Callable[[], int]] = None,
    ) -> None:
        super().__init__(parent)
        self._entry = entry
        self._thumb_cache = thumb_cache
        self._selected = bool(selected)
        self._selection_mode = bool(selection_mode)
        self._selected_count_provider = selected_count_provider
        self.setObjectName("FileCard")
        self._build()
        self.clicked.connect(lambda: self.open_requested.emit(entry.file_id))

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(4)
        self.setFixedSize(182, 124)
        self.setToolTip(f"{self._entry.original_name}\nSize: {self._fmt_size(self._entry.size_bytes)}")
        self.setStyleSheet(_FILECARD_STYLE)

        # Thumbnail container
        self._thumb_container = QWidget(self)
        self._thumb_container.setFixedSize(168, 70)
        thumb_lay = QVBoxLayout(self._thumb_container)
        thumb_lay.setContentsMargins(0, 0, 0, 0)

        # Thumbnail / icon
        self._thumb = QLabel(self._thumb_container)
        self._thumb.setObjectName("FileThumb")
        self._thumb.setFixedSize(168, 70)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        thumb_lay.addWidget(self._thumb)

        layout.addWidget(self._thumb_container)

        # Video play indicator overlay (centered over thumbnail)
        self._video_play_badge = QLabel(self)
        self._video_play_badge.setFixedSize(28, 28)
        self._video_play_badge.move(77, 28)
        self._video_play_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_play_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._video_play_badge.setStyleSheet(
            "background: rgba(10, 14, 26, 0.76);"
            "border: 1.5px solid rgba(255, 255, 255, 0.88);"
            "border-radius: 14px;"
        )
        self._video_play_badge.setPixmap(icons.get_pixmap("play", "#FFFFFF", size=13))
        self._video_play_badge.hide()

        # Video duration badge (bottom-right of thumbnail)
        self._video_duration_badge = QLabel(self)
        self._video_duration_badge.setFixedSize(38, 16)
        self._video_duration_badge.move(132, 57)
        self._video_duration_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_duration_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._video_duration_badge.setStyleSheet(
            "background: rgba(0, 0, 0, 0.78);"
            "color: #FFFFFF;"
            "font-size: 8px;"
            "font-weight: 600;"
            "border-radius: 3px;"
            "padding: 0 3px;"
        )
        self._video_duration_badge.setText("04:20")
        self._video_duration_badge.hide()

        self._render_thumbnail(self._entry.thumbnail_b64)

        # Bottom info row (name + details on left, 3-dots on right)
        bottom_widget = QWidget(self)
        bottom_widget.setStyleSheet("background: transparent;")
        bottom_lay = QHBoxLayout(bottom_widget)
        bottom_lay.setContentsMargins(2, 1, 0, 1)
        bottom_lay.setSpacing(4)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)

        # Name
        prefix = "★ " if self._entry.is_favorite else ""
        name_lbl = QLabel(prefix + self._truncate(self._entry.original_name, 17 if prefix else 19), bottom_widget)
        name_lbl.setObjectName("FileName")
        name_lbl.setProperty("favorite", "true" if self._entry.is_favorite else "false")
        name_lbl.setFont(QFont("Segoe UI Variable", 9, QFont.Weight.DemiBold))
        name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_col.addWidget(name_lbl)

        # Size + Type
        cat = self._infer_category_name(self._entry.original_name, self._entry.mime_type)
        size_lbl = QLabel(f"{self._fmt_size(self._entry.size_bytes)} · {cat}", bottom_widget)
        size_lbl.setObjectName("FileSize")
        size_lbl.setFont(QFont("Segoe UI Variable", 8))
        size_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_col.addWidget(size_lbl)

        bottom_lay.addLayout(text_col, 1)

        # Three dots button on right
        from PySide6.QtCore import QPoint
        self._more_btn = QPushButton(bottom_widget)
        self._more_btn.setObjectName("MoreBtn")
        self._more_btn.setFixedSize(20, 20)
        self._more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more_btn.setIcon(icons.get_icon("more_vertical", color="#6E7D96"))
        self._more_btn.clicked.connect(lambda: self._show_context(self._more_btn.mapTo(self, QPoint(0, 20))))
        bottom_lay.addWidget(self._more_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(bottom_widget)

        # Corner selection badge (top-right inside card over thumbnail)
        self._select_btn = QPushButton(self)
        self._select_btn.setObjectName("SelectBadge")
        self._select_btn.setFixedSize(20, 20)
        self._select_btn.move(150, 11)
        self._select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._select_btn.setToolTip("Select / Deselect file")
        self._select_btn.clicked.connect(lambda: self.select_toggle_requested.emit(self._entry.file_id))
        self._select_btn.raise_()
        self._apply_selection_visuals(force=True)

        # Context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context)

    @staticmethod
    def _infer_category_name(filename: str, mime_type: Optional[str]) -> str:
        mime = infer_mime_type(filename, mime_type)
        ext = Path(filename).suffix.lower()
        if mime.startswith("image/"):
            return "Image"
        if mime.startswith("video/"):
            return "Video"
        if mime.startswith("audio/"):
            return "Audio"
        if ext == ".pdf":
            return "Document"
        if ext in (".doc", ".docx", ".rtf", ".odt", ".txt", ".md"):
            return "Document"
        if ext in (".xls", ".xlsx", ".csv", ".tsv"):
            return "Spreadsheet"
        if ext in (".zip", ".rar", ".7z", ".tar", ".gz"):
            return "Archive"
        if ext in (".py", ".js", ".ts", ".html", ".css", ".cpp", ".json"):
            return "Code"
        return "File"

    @staticmethod
    def _clip_to_rounded(pix: QPixmap, radius: int) -> QPixmap:
        try:
            out = QPixmap(pix.size())
            out.fill(Qt.GlobalColor.transparent)
            p = QPainter(out)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(0, 0, pix.width(), pix.height(), radius, radius)
            p.setClipPath(path)
            p.drawPixmap(0, 0, pix)
            p.end()
            return out
        except Exception:
            return pix

    def _render_thumbnail(self, thumb_b64: Optional[str]) -> None:
        loaded_pixmap = False
        mime = infer_mime_type(self._entry.original_name, self._entry.mime_type)
        ext = Path(self._entry.original_name).suffix.lower()

        if thumb_b64:
            cache_key = (
                self._entry.file_id,
                self._entry.modified_time,
                len(thumb_b64),
            )
            cached = self._thumb_cache.get(cache_key) if self._thumb_cache is not None else None
            if cached is not None and not cached.isNull():
                self._thumb.setPixmap(cached)
                loaded_pixmap = True
            else:
                import base64
                try:
                    raw = base64.b64decode(thumb_b64)
                    px = QPixmap()
                    if px.loadFromData(raw):
                        scaled = px.scaled(
                            168,
                            70,
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        cw = max(0, (scaled.width() - 168) // 2)
                        ch = max(0, (scaled.height() - 70) // 2)
                        cropped = scaled.copy(cw, ch, 168, 70)
                        rounded = self._clip_to_rounded(cropped, 8)
                        if self._thumb_cache is not None:
                            if len(self._thumb_cache) > 1000:
                                self._thumb_cache.clear()
                            self._thumb_cache[cache_key] = rounded
                        self._thumb.setPixmap(rounded)
                        loaded_pixmap = True
                except Exception:
                    pass

        if not loaded_pixmap:
            if mime.startswith("image/"):
                badge_pix = MediaBadgeGenerator.get_image_placeholder(168, 70)
            elif mime.startswith("video/"):
                badge_pix = MediaBadgeGenerator.get_video_placeholder(168, 70)
            else:
                badge_pix = DocBadgeGenerator.get_badge(ext, 168, 70)
            self._thumb.setPixmap(badge_pix)

        is_video = mime.startswith("video/")
        if hasattr(self, "_video_play_badge"):
            self._video_play_badge.setVisible(is_video)
            if is_video:
                self._video_play_badge.raise_()
        if hasattr(self, "_video_duration_badge"):
            self._video_duration_badge.setVisible(is_video)
            if is_video:
                self._video_duration_badge.raise_()
        if hasattr(self, "_select_btn"):
            self._select_btn.raise_()

    def set_thumbnail_b64(self, thumb_b64: str) -> None:
        """Update this card's thumbnail pixmap in place once background backfill finishes."""
        self._entry.thumbnail_b64 = thumb_b64
        self._render_thumbnail(thumb_b64)

    def _apply_selection_visuals(self, force: bool = False) -> None:
        sel_val = "true" if self._selected else "false"
        if force or self.property("selected") != sel_val:
            self.setProperty("selected", sel_val)
            self._select_btn.setProperty("selected", sel_val)
            if self._selected:
                self._select_btn.setIcon(icons.get_icon("check", "#FFFFFF", "#FFFFFF", size=14))
            else:
                self._select_btn.setIcon(QIcon())
            self.style().unpolish(self)
            self.style().polish(self)
            self._select_btn.style().unpolish(self._select_btn)
            self._select_btn.style().polish(self._select_btn)
        self._select_btn.setVisible(self._selection_mode or self._selected or self.underMouse())

    def set_selection_state(self, selected: bool, selection_mode: bool) -> None:
        changed = (bool(selected) != self._selected) or (bool(selection_mode) != self._selection_mode)
        self._selected = bool(selected)
        self._selection_mode = bool(selection_mode)
        if changed:
            self._apply_selection_visuals(force=False)
        else:
            self._select_btn.setVisible(self._selection_mode or self._selected or self.underMouse())

    def enterEvent(self, event) -> None:
        self._select_btn.show()
        self._select_btn.raise_()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not (self._selection_mode or self._selected):
            self._select_btn.hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            mods = event.modifiers()
            if self._selection_mode or bool(
                mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
            ):
                self.selection_requested.emit(self._entry.file_id, mods)
                event.accept()
                return
            self.clicked.emit()
            event.accept()
            return
        QFrame.mousePressEvent(self, event)

    def _show_context(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction(icons.get_icon("eye", Colors.PRIMARY), "Open / View", lambda: self.open_requested.emit(self._entry.file_id))
        menu.addAction(icons.get_icon("download", Colors.TEXT_PRIMARY), "Restore / Export", lambda: self.restore_requested.emit(self._entry.file_id))
        fav_label = "Remove Favorite" if self._entry.is_favorite else "Add to Favorites"
        fav_icon = "star_filled" if self._entry.is_favorite else "star"
        menu.addAction(icons.get_icon(fav_icon, Colors.WARNING), fav_label, lambda: self.favorite_toggled.emit(
            self._entry.file_id, not self._entry.is_favorite
        ))
        sel_label = "Deselect" if self._selected else "Select Multiple"
        menu.addAction(icons.get_icon("check", Colors.PRIMARY), sel_label, lambda: self.select_toggle_requested.emit(self._entry.file_id))
        menu.addSeparator()
        sel_count = self._selected_count_provider() if self._selected_count_provider else 0
        if sel_count > 1 or (self._selected and sel_count >= 1):
            menu.addAction(
                icons.get_icon("trash", Colors.DANGER),
                f"Delete Selected ({sel_count})",
                self.batch_delete_requested.emit,
            )
        menu.addAction(icons.get_icon("trash", Colors.DANGER), "Delete from Vault", lambda: self.delete_requested.emit(self._entry.file_id))
        menu.exec(self.mapToGlobal(pos))

    _fmt_size = staticmethod(format_file_size)

    def _truncate(self, s: str, n: int) -> str:
        return s if len(s) <= n else s[:n-1] + "…"


class _VaultLoadWorker(QThread):
    """Loads or refreshes the encrypted vault index off the GUI thread."""

    loaded = Signal(object)
    corrupt = Signal()
    failed = Signal(str)

    def __init__(self, vault, vault_service: Optional[VaultService], initial: bool) -> None:
        super().__init__(None)
        self._vault = vault
        self._vault_service = vault_service
        self._initial = initial

    def run(self) -> None:
        try:
            if not get_session().is_unlocked:
                return

            def _do_load():
                return self._vault.load() if self._initial else self._vault.refresh()

            if self._vault_service is not None:
                with self._vault_service._lock:
                    if not get_session().is_unlocked:
                        return
                    idx = _do_load()
            else:
                idx = _do_load()
            self.loaded.emit(idx)
        except IndexCorruptError as ice:
            logger.error("Index corrupted: %s", type(ice).__name__)
            self.corrupt.emit()
        except Exception as exc:
            if not get_session().is_unlocked:
                return
            logger.error("Failed to load vault files: %s", type(exc).__name__)
            self.failed.emit(type(exc).__name__)


class _AsyncFnWorker(QThread):
    """Runs a one-shot blocking vault/system action off the GUI thread."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable) -> None:
        super().__init__(None)
        self._fn = fn

    def run(self) -> None:
        try:
            res = self._fn()
            self.succeeded.emit(res)
        except Exception as exc:
            logger.error("Dashboard background action failed: %s", type(exc).__name__)
            self.failed.emit(type(exc).__name__)


class _MediaThumbBackfillWorker(QThread):
    """Generates missing image and video thumbnails purely in memory off the GUI thread."""

    thumb_ready = Signal(str, str)    # (file_id, thumb_b64)
    thumb_failed = Signal(str, str)   # (file_id, mime_type)

    def __init__(self, vault, jobs: list[tuple[str, str, str]]) -> None:
        super().__init__(None)
        self._vault = vault
        self._jobs = list(jobs)

    def run(self) -> None:
        for file_id, orig_name, mime_type in self._jobs:
            if self.isInterruptionRequested() or not get_session().is_unlocked:
                break
            data: Optional[bytearray] = None
            try:
                data = self._vault.open_file_in_memory(file_id)
                b64: Optional[str] = None
                if mime_type.startswith("image/"):
                    b64 = make_image_thumbnail_from_bytes_b64(data, orig_name)
                elif mime_type.startswith("video/"):
                    b64 = make_video_thumbnail_from_bytes_b64(data, orig_name)
                if b64:
                    self.thumb_ready.emit(file_id, b64)
                else:
                    self.thumb_failed.emit(file_id, mime_type)
            except Exception:
                self.thumb_failed.emit(file_id, mime_type)
            finally:
                if isinstance(data, bytearray):
                    zero_bytes(data)


_ImageThumbBackfillWorker = _MediaThumbBackfillWorker


# ── Main dashboard window ─────────────────────────────────────────────────────

class _AutoDismissBanner(QFrame):
    """Notification banner with 90 FPS smooth fade/collapse and 5s idle auto-dismiss."""

    auto_dismissed = Signal()

    _FRAME_INTERVAL_MS = 11  # ~90.9 FPS
    _IN_DURATION_S = 0.24
    _OUT_DURATION_S = 0.36
    _DEFAULT_TIMEOUT_MS = 5000

    def __init__(self, timeout_ms: int = _DEFAULT_TIMEOUT_MS, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._timeout_ms = timeout_ms
        self._remaining_ms = float(timeout_ms)
        self._countdown_started_ts = 0.0
        self._hovered = False
        self._paused_externally = False

        self._anim_mode: Optional[str] = None  # "in" | "out" | None
        self._anim_start_ts = 0.0
        self._anim_start_h = 0
        self._anim_target_h = 0

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)

        # Precise ~90 FPS transition driver (11ms ticks)
        self._anim_timer = QTimer(self)
        self._anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._anim_timer.setInterval(self._FRAME_INTERVAL_MS)
        self._anim_timer.timeout.connect(self._tick_animation)

        # Countdown progress & auto-hide timer
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._countdown_timer.setInterval(self._FRAME_INTERVAL_MS)
        self._countdown_timer.timeout.connect(self._tick_countdown)

    @staticmethod
    def _ease_out_cubic(t: float) -> float:
        t = max(0.0, min(1.0, t))
        return 1.0 - (1.0 - t) ** 3

    def show_animated(self) -> None:
        self._anim_timer.stop()
        self._countdown_timer.stop()
        self.setMaximumHeight(16777215)
        self.show()
        self.adjustSize()
        natural_h = max(48, self.sizeHint().height())

        self._anim_mode = "in"
        self._anim_start_ts = time.perf_counter()
        self._anim_start_h = 0
        self._anim_target_h = natural_h
        self._opacity.setOpacity(0.02)
        self.setMaximumHeight(0)

        self._remaining_ms = float(self._timeout_ms)
        self._countdown_started_ts = time.perf_counter()
        self._anim_timer.start()
        if not self._hovered and not self._paused_externally:
            self._countdown_timer.start()

    def dismiss_animated(self) -> None:
        if not self.isVisible() or self._anim_mode == "out":
            return
        self._countdown_timer.stop()
        self._anim_timer.stop()
        self._anim_mode = "out"
        self._anim_start_ts = time.perf_counter()
        self._anim_start_h = max(1, self.height() or self.sizeHint().height())
        self._anim_target_h = 0
        self._anim_timer.start()

    def dismiss_immediately(self) -> None:
        self._countdown_timer.stop()
        self._anim_timer.stop()
        self._anim_mode = None
        self.hide()
        self._opacity.setOpacity(1.0)
        self.setMaximumHeight(16777215)

    def pause_countdown(self, paused: bool) -> None:
        self._paused_externally = paused
        self._sync_countdown_state()

    def stop_all(self) -> None:
        self._countdown_timer.stop()
        self._anim_timer.stop()
        self._anim_mode = None

    def _sync_countdown_state(self) -> None:
        if self._anim_mode == "out" or not self.isVisible():
            self._countdown_timer.stop()
            return
        if self._hovered or self._paused_externally:
            if self._countdown_timer.isActive():
                elapsed_ms = (time.perf_counter() - self._countdown_started_ts) * 1000.0
                self._remaining_ms = max(0.0, self._remaining_ms - elapsed_ms)
                self._countdown_timer.stop()
                self.update()
        else:
            if not self._countdown_timer.isActive() and self._remaining_ms > 0:
                self._countdown_started_ts = time.perf_counter()
                self._countdown_timer.start()

    def _tick_countdown(self) -> None:
        if self._hovered or self._paused_externally or not self.isVisible():
            self._countdown_timer.stop()
            return
        elapsed_ms = (time.perf_counter() - self._countdown_started_ts) * 1000.0
        rem = self._remaining_ms - elapsed_ms
        self.update()
        if rem <= 0.0:
            self._remaining_ms = 0.0
            self._countdown_timer.stop()
            self.auto_dismissed.emit()
            self.dismiss_animated()

    def _tick_animation(self) -> None:
        now = time.perf_counter()
        if self._anim_mode == "in":
            t = min(1.0, (now - self._anim_start_ts) / self._IN_DURATION_S)
            e = self._ease_out_cubic(t)
            self._opacity.setOpacity(e)
            h = max(0, int(round(self._anim_target_h * e)))
            self.setMaximumHeight(h if t < 1.0 else 16777215)
            if t >= 1.0:
                self._anim_mode = None
                self._anim_timer.stop()
        elif self._anim_mode == "out":
            t = min(1.0, (now - self._anim_start_ts) / self._OUT_DURATION_S)
            e = self._ease_out_cubic(t)
            self._opacity.setOpacity(max(0.0, 1.0 - e))
            h = max(0, int(round(self._anim_start_h * (1.0 - e))))
            self.setMaximumHeight(h)
            if t >= 1.0:
                self._anim_mode = None
                self._anim_timer.stop()
                self.hide()
                self._opacity.setOpacity(1.0)
                self.setMaximumHeight(16777215)
        else:
            self._anim_timer.stop()

    def _restore_on_hover(self) -> None:
        if self._anim_mode == "out":
            self._anim_mode = None
            self._anim_timer.stop()
            self._opacity.setOpacity(1.0)
            self.setMaximumHeight(16777215)
            self._remaining_ms = max(1200.0, self._remaining_ms)
            self.update()

    def register_hover_targets(self) -> None:
        for child in self.findChildren(QWidget):
            child.setMouseTracking(True)
            child.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        etype = event.type()
        if etype in (QEvent.Type.Enter, QEvent.Type.HoverEnter, QEvent.Type.MouseMove):
            self._hovered = True
            self._restore_on_hover()
            self._sync_countdown_state()
        elif etype in (QEvent.Type.Leave, QEvent.Type.HoverLeave):
            if not self.underMouse():
                self._hovered = False
                self._sync_countdown_state()
        return super().eventFilter(watched, event)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._restore_on_hover()
        self._sync_countdown_state()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._sync_countdown_state()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.isVisible() or self._anim_mode == "out" or self._timeout_ms <= 0:
            return
        if self._countdown_timer.isActive():
            elapsed_ms = (time.perf_counter() - self._countdown_started_ts) * 1000.0
            rem = max(0.0, self._remaining_ms - elapsed_ms)
        else:
            rem = max(0.0, self._remaining_ms)
        ratio = max(0.0, min(1.0, rem / float(self._timeout_ms)))
        if ratio <= 0.0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pad_x = 12
        usable_w = max(0, self.width() - 2 * pad_x)
        bar_w = usable_w * ratio
        if bar_w > 1.0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(139, 156, 255, 115 if self._hovered else 155))
            painter.drawRoundedRect(pad_x, self.height() - 3, int(bar_w), 2, 1.0, 1.0)


class Dashboard(QMainWindow):
    """Main vault window, shown only when unlocked."""

    lock_requested = Signal()
    panic_requested = Signal()
    credentials_changed = Signal()
    protected_apps_changed = Signal(set)
    settings_updated = Signal()
    files_loaded = Signal()

    _fmt_size = staticmethod(format_file_size)

    def __init__(
        self,
        vault=None,
        session=None,
        keystore=None,
        lockout=None,
        settings_store=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._vault = vault
        self._session = session or get_session()
        self._keystore = keystore
        self._lockout = lockout
        self._settings_store = settings_store
        self._screenshot_blocked = bool(settings_store.get("screenshot_protection", True)) if settings_store else True
        self._current_section = "all"
        self._current_entries = []
        self._current_cols = 5
        self._thumb_cache: dict[tuple, QPixmap] = {}
        self._rendered_cards: list[FileCard] = []
        self._cards_by_id: dict[str, FileCard] = {}
        self._render_offset = 0
        self._selection_mode = False
        self._selected_ids: set[str] = set()
        self._last_selected_index: Optional[int] = None

        self._active_overlay: Optional[ProgressOverlay] = None
        self._cleaned_up = False
        self._files_loaded = False
        self._speed_banner_dismissed = False
        self._opening_file = False
        self._registered_top_window: Optional[QWidget] = None

        self._intruder_ui = None
        self._applock_ui = None
        self._settings_ui = None
        self._load_worker: Optional[_VaultLoadWorker] = None
        self._action_workers: set[_AsyncFnWorker] = set()

        # Non-blocking background thumbnail backfill (images + videos, purely in-memory)
        self._thumb_backfill_attempted: set[str] = set()
        self._pending_thumb_saves: dict[str, str] = {}
        self._media_backfill_worker: Optional[_MediaThumbBackfillWorker] = None
        self._img_backfill_worker: Optional[_MediaThumbBackfillWorker] = None
        self._vid_backfill_queue: list[object] = []
        self._vid_backfill_active_id: Optional[str] = None
        self._vid_backfill_player = None
        self._vid_backfill_sink = None
        self._vid_backfill_device = None
        self._vid_backfill_candidate_b64: Optional[str] = None
        self._vid_backfill_sought = False

        self._vid_backfill_timer = QTimer(self)
        self._vid_backfill_timer.setSingleShot(True)
        self._vid_backfill_timer.setInterval(2500)
        self._vid_backfill_timer.timeout.connect(self._finish_current_video_thumb)

        self._thumb_flush_timer = QTimer(self)
        self._thumb_flush_timer.setSingleShot(True)
        self._thumb_flush_timer.setInterval(700)
        self._thumb_flush_timer.timeout.connect(self._flush_pending_thumb_index)

        self._grid_render_timer = QTimer(self)
        self._grid_render_timer.setSingleShot(True)
        self._grid_render_timer.setInterval(0)
        self._grid_render_timer.timeout.connect(self._render_next_grid_batch)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(35)
        self._resize_timer.timeout.connect(self._on_debounced_resize)

        self._vault_service: Optional[VaultService] = VaultService(self._vault) if self._vault else None
        if self._vault_service:
            self._vault_service.task_started.connect(self._on_service_started)
            self._vault_service.progress.connect(self._on_service_progress)
            self._vault_service.file_completed.connect(self._on_service_file_done)
            self._vault_service.task_finished.connect(self._on_service_finished)
            self._vault_service.error_occurred.connect(self._on_service_error)

        self.setWindowTitle("Personal Vault — Protected")
        self.setMinimumSize(1000, 650)
        self.resize(1200, 750)
        self.setAcceptDrops(True)

        self._build_ui()
        self._setup_lock_shortcut()
        QTimer.singleShot(0, self._init_top_window_security)

    def _init_top_window_security(self) -> None:
        """Apply screenshot protection and central lock listeners once parented to top window."""
        if self._cleaned_up:
            return
        self._setup_screenshot_protection()
        self._setup_central_lock_coordination()

    def _setup_central_lock_coordination(self) -> None:
        from ..system.lock_controller import get_lock_controller
        from ..system.win32_events import install_windows_security_filter

        target = self.window() or self
        self._registered_top_window = target

        lc = get_lock_controller()
        lc.register_window(target)
        lc.register_pre_lock_hook(self._on_pre_lock)
        lc.hotkey_registration_failed.connect(self._on_hotkey_registration_failed)
        lc.start_listeners()
        if not lc.is_hotkey_registered and lc.hotkey_error_message:
            self._on_hotkey_registration_failed(lc.hotkey_error_message)

        win_filter = install_windows_security_filter()
        win_filter.register_window(target)

    def _on_hotkey_registration_failed(self, msg: str) -> None:
        """Surfaces panic hotkey registration failure visibly in the dashboard."""
        if hasattr(self, "_hotkey_lbl") and hasattr(self, "_hotkey_banner"):
            self._hotkey_lbl.setText(msg)
            self._hotkey_banner.show_animated()
        if hasattr(self, "_protection_status") and self._protection_status is not None:
            self._protection_status.setToolTip("Panic hotkey inactive (shortcut in use by another app)")
        if hasattr(self, "_notif_btn") and self._notif_btn is not None:
            self._notif_btn.setToolTip(f"Security Alert: {msg}")

    def _on_pre_lock(self) -> None:
        """Invoked before session.lock() zeroes master key: cancel all running tasks."""
        if self._vault_service is not None:
            try:
                self._vault_service.cancel_current()
            except Exception:
                pass
        for w in list(self._action_workers):
            if w.isRunning():
                try:
                    if hasattr(w, "cancel"):
                        w.cancel()
                    w.wait(500)
                except Exception:
                    pass

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        central.setStyleSheet(f"background: {DashColors.BG_DARKEST};")
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ──
        sidebar = self._build_sidebar()
        root.addWidget(sidebar)

        # ── Divider ──
        div = QFrame()
        div.setFixedWidth(1)
        div.setStyleSheet(f"background: {DashColors.BORDER_SUBTLE};")
        root.addWidget(div)

        # ── Content area ──
        content = QWidget()
        content.setStyleSheet(f"background: {DashColors.BG_CONTENT};")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Topbar
        topbar = self._build_topbar()
        content_layout.addWidget(topbar)

        # Page stack (indices 1..3 start as empty placeholders and load lazily on first visit)
        self._page_stack = QStackedWidget()
        self._files_widget = self._build_files_page()
        self._page_stack.addWidget(self._files_widget)   # 0 — Files (All, Photos, Videos, Docs, Favs)

        self._intruder_slot = QWidget()
        self._applock_slot = QWidget()
        self._settings_slot = QWidget()
        self._page_stack.addWidget(self._intruder_slot)   # 1 — Intruder Log (lazy)
        self._page_stack.addWidget(self._applock_slot)    # 2 — App Lock (lazy)
        self._page_stack.addWidget(self._settings_slot)   # 3 — Settings (lazy)

        content_layout.addWidget(self._page_stack)
        root.addWidget(content, 1)

        # Status bar
        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(f"""
            QStatusBar {{
                background: {DashColors.BG_DARKEST};
                color: {DashColors.TEXT_MUTED};
                font-size: 11px;
                border-top: 1px solid {DashColors.BORDER_SUBTLE};
                padding: 0 12px;
            }}
        """)
        self.setStatusBar(self._status_bar)
        self._update_status()

    def _replace_stack_page(self, index: int, widget: QWidget) -> None:
        old = self._page_stack.widget(index)
        self._page_stack.insertWidget(index, widget)
        if old is not None and old is not widget:
            self._page_stack.removeWidget(old)
            old.deleteLater()

    def _ensure_intruder_ui(self) -> None:
        if self._intruder_ui is not None:
            return
        if self._lockout:
            from .intruder_log import IntruderLogUI
            self._intruder_ui = IntruderLogUI(self._lockout)
        else:
            self._intruder_ui = self._build_placeholder("Intruder Log", "📸")
        self._replace_stack_page(1, self._intruder_ui)

    def _ensure_applock_ui(self) -> None:
        if self._applock_ui is not None:
            return
        from ..applock.config import AppLockConfig
        from .applock_ui import AppLockUI
        applock_cfg = AppLockConfig(self._vault.vault_dir)
        self._applock_ui = AppLockUI(applock_cfg)
        self._applock_ui.protected_apps_changed.connect(self.protected_apps_changed.emit)
        self._replace_stack_page(2, self._applock_ui)

    def _ensure_settings_ui(self) -> None:
        if self._settings_ui is not None:
            return
        if self._vault and self._keystore:
            from .settings import SettingsUI
            self._settings_ui = SettingsUI(self._vault, self._keystore, settings_store=self._settings_store)
            self._settings_ui.lock_requested.connect(self.lock_requested.emit)
            self._settings_ui.credentials_changed.connect(self.credentials_changed.emit)
            self._settings_ui.settings_updated.connect(self.settings_updated.emit)
        else:
            self._settings_ui = self._build_placeholder("Settings", "⚙️")
        self._replace_stack_page(3, self._settings_ui)

    def _build_sidebar(self) -> QWidget:
        sb = SidebarWidget()
        sb.setFixedWidth(260)

        layout = QVBoxLayout(sb)
        layout.setContentsMargins(14, 20, 14, 16)
        layout.setSpacing(4)

        # Brand widget (Shield emblem + Personal Vault + Private • Secure • Yours)
        self._brand_widget = SidebarBrandWidget()
        layout.addWidget(self._brand_widget)
        layout.addSpacing(14)

        # Active top Dashboard pill
        self._nav_dashboard = SidebarItem("home", "Dashboard")
        self._nav_dashboard.clicked.connect(self._nav_to_dashboard)
        layout.addWidget(self._nav_dashboard)
        layout.addSpacing(6)

        # Navigation items (0..4 for Vault categories)
        item_all = SidebarItem("folder", "All Files")
        item_photos = SidebarItem("image", "Photos")
        item_videos = SidebarItem("video", "Videos")
        item_docs = SidebarItem("file_text", "Documents")
        item_favs = SidebarItem("star", "Favorites")
        self._nav_bin = SidebarItem("trash", "Bin")

        item_all.clicked.connect(lambda: self._nav_to(0, item_all))
        item_photos.clicked.connect(lambda: self._nav_to(1, item_photos))
        item_videos.clicked.connect(lambda: self._nav_to(2, item_videos))
        item_docs.clicked.connect(lambda: self._nav_to(3, item_docs))
        item_favs.clicked.connect(lambda: self._nav_to(4, item_favs))
        self._nav_bin.clicked.connect(self._nav_to_bin)

        layout.addWidget(item_all)
        layout.addWidget(item_photos)
        layout.addWidget(item_videos)
        layout.addWidget(item_docs)
        layout.addWidget(item_favs)
        layout.addWidget(self._nav_bin)

        # Subtle divider line
        layout.addSpacing(8)
        nav_div = QFrame()
        nav_div.setFixedHeight(1)
        nav_div.setStyleSheet(f"background: {DashColors.BORDER_SUBTLE};")
        layout.addWidget(nav_div)
        layout.addSpacing(8)

        # Security & System items (5..7)
        item_backup = SidebarItem("layers", "Backup & Restore")
        item_security = SidebarItem("shield_check", "Security")
        item_settings = SidebarItem("settings", "Settings")

        item_backup.clicked.connect(lambda: self._nav_to(5, item_backup))
        item_security.clicked.connect(lambda: self._nav_to(6, item_security))
        item_settings.clicked.connect(lambda: self._nav_to(7, item_settings))

        layout.addWidget(item_backup)
        layout.addWidget(item_security)
        layout.addWidget(item_settings)

        if self._session.is_decoy:
            item_backup.hide()
            item_security.hide()
            item_settings.hide()
            nav_div.hide()

        self._nav_items: list[SidebarItem] = [
            item_all, item_photos, item_videos, item_docs, item_favs,
            item_backup, item_security, item_settings,
        ]

        self._nav_dashboard.set_selected(True)
        item_all.set_selected(True)

        layout.addStretch()

        # Minimal subtle lock button
        lock_btn = QPushButton(" Lock Vault")
        lock_btn.setIcon(icons.get_icon("lock", color=DashColors.TEXT_MUTED, active_color=DashColors.ACCENT_RED))
        lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lock_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {DashColors.TEXT_MUTED};
                border: 1px solid rgba(125, 160, 220, 0.12);
                border-radius: 8px;
                padding: 6px 12px;
                font-family: 'Segoe UI Variable', 'Segoe UI';
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: rgba(255, 111, 134, 0.15);
                color: {DashColors.ACCENT_RED};
                border-color: rgba(255, 111, 134, 0.35);
            }}
        """)
        lock_btn.clicked.connect(self.lock_requested.emit)
        layout.addWidget(lock_btn)
        layout.addSpacing(8)

        return sb

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("DashboardTopBar")
        bar.setFixedHeight(64)
        bar.setStyleSheet(f"""
            #DashboardTopBar {{
                background: {DashColors.BG_CONTENT};
                border-bottom: 1px solid rgba(125, 160, 220, 0.08);
            }}
        """)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(12)

        # Page title kept for tests and accessibility
        self._page_title = QLabel("All Files")
        self._page_title.setFont(QFont("Segoe UI Variable", 15, QFont.Weight.Bold))
        self._page_title.setStyleSheet(f"color: {DashColors.TEXT_PRIMARY}; background: transparent;")
        layout.addWidget(self._page_title)
        self._page_title.hide()  # Minimal topbar matching reference mockup
        layout.addStretch()

        # Search bar matching reference (420px width, Ctrl + F badge)
        self._search = VaultSearchBar("Search in your vault…")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._do_search)
        self._search.textChanged.connect(lambda: self._search_timer.start(250))
        layout.addWidget(self._search)

        # Notification bell with red unread dot
        self._notif_btn = NotificationBellButton()
        layout.addWidget(self._notif_btn)

        # Multi-select toggle button (preserves test_batch_performance_and_multiselect.py)
        self._select_btn = QPushButton(" Select")
        self._select_btn.setProperty("ghost", True)
        self._select_btn.setCheckable(True)
        self._select_btn.setFixedHeight(36)
        self._select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_btn.setIcon(icons.get_icon("check", color=DashColors.TEXT_SECONDARY))
        self._select_btn.setToolTip("Select multiple files to delete (or hold Ctrl/Shift while clicking)")
        self._select_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(24, 37, 62, 0.65);
                color: {DashColors.TEXT_SECONDARY};
                border: 1px solid {DashColors.BORDER_SUBTLE};
                border-radius: 8px;
                padding: 0 12px;
                font-family: 'Segoe UI Variable', 'Segoe UI';
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:checked {{
                background: rgba(91, 140, 255, 0.22);
                color: {DashColors.ACCENT_BLUE};
                border: 1px solid {DashColors.ACCENT_BLUE};
            }}
            QPushButton:hover {{
                border-color: rgba(125, 160, 220, 0.35);
            }}
        """)
        self._select_btn.clicked.connect(self._toggle_selection_mode)
        layout.addWidget(self._select_btn)

        # Screenshot toggle
        self._ss_btn = QPushButton(" Protected")
        self._ss_btn.setProperty("ghost", True)
        self._ss_btn.setFixedHeight(36)
        self._ss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ss_btn.setIcon(icons.get_icon("shield_check", color=DashColors.ACCENT_GREEN))
        self._ss_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(24, 37, 62, 0.65);
                color: {DashColors.ACCENT_GREEN};
                border: 1px solid rgba(57, 214, 162, 0.25);
                border-radius: 8px;
                padding: 0 12px;
                font-family: 'Segoe UI Variable', 'Segoe UI';
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: rgba(24, 37, 62, 0.85);
                border-color: {DashColors.ACCENT_GREEN};
            }}
        """)
        self._ss_btn.clicked.connect(self._toggle_screenshot_protection)
        layout.addWidget(self._ss_btn)

        return bar

    def _build_files_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {DashColors.BG_CONTENT};")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(16)

        # ── 1. Welcome Section & Vault Status ──
        welcome_row = QHBoxLayout()
        welcome_row.setContentsMargins(0, 0, 0, 0)
        welcome_row.setSpacing(16)

        welcome_text_col = QVBoxLayout()
        welcome_text_col.setContentsMargins(0, 0, 0, 0)
        welcome_text_col.setSpacing(4)

        welcome_sub = QLabel("Welcome back,")
        welcome_sub.setFont(QFont("Segoe UI Variable", 12, QFont.Weight.Medium))
        welcome_sub.setStyleSheet(f"color: {DashColors.TEXT_SECONDARY}; background: transparent;")
        welcome_text_col.addWidget(welcome_sub)

        welcome_title = QLabel("Your Personal Vault")
        welcome_title.setFont(QFont("Segoe UI Variable", 26, QFont.Weight.Bold))
        welcome_title.setStyleSheet(f"color: {DashColors.TEXT_PRIMARY}; background: transparent; letter-spacing: -0.5px;")
        welcome_text_col.addWidget(welcome_title)

        welcome_desc = QLabel("Keep your private files safe, encrypted, and always under your control.")
        welcome_desc.setFont(QFont("Segoe UI Variable", 11, QFont.Weight.Normal))
        welcome_desc.setStyleSheet(f"color: {DashColors.TEXT_MUTED}; background: transparent;")
        welcome_text_col.addWidget(welcome_desc)

        welcome_row.addLayout(welcome_text_col)
        welcome_row.addStretch()

        self._vault_unlocked_badge = VaultUnlockedBadge()
        welcome_row.addWidget(self._vault_unlocked_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(welcome_row)

        # ── 2. Statistics Row (4 Cards) ──
        stats_row = QHBoxLayout()
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(14)

        self._stat_total = MetricCard("folder", "0", "Total Files", DashColors.ACCENT_BLUE)
        self._stat_storage = MetricCard("database", "0 B", "Stored Data", DashColors.ACCENT_PURPLE)
        self._stat_favs = MetricCard("star", "0", "Favorites", DashColors.ACCENT_GOLD)
        self._stat_protection = MetricCard("shield_check", "100%", "Vault Protected", DashColors.ACCENT_CYAN)

        stats_row.addWidget(self._stat_total, 1)
        stats_row.addWidget(self._stat_storage, 1)
        stats_row.addWidget(self._stat_favs, 1)
        stats_row.addWidget(self._stat_protection, 1)
        layout.addLayout(stats_row)

        # ── 3. Category Filter Tabs & View Mode Toggles ──
        tabs_row = QHBoxLayout()
        tabs_row.setContentsMargins(0, 0, 0, 0)
        tabs_row.setSpacing(10)

        self._pill_all = CategoryPill("grid", "All Files", active=True)
        self._pill_photos = CategoryPill("image", "Photos", active=False)
        self._pill_videos = CategoryPill("video", "Videos", active=False)
        self._pill_docs = CategoryPill("file_text", "Documents", active=False)
        self._pill_favs = CategoryPill("star", "Favorites", active=False)

        self._pill_all.clicked.connect(lambda: self._select_category_pill("all"))
        self._pill_photos.clicked.connect(lambda: self._select_category_pill("photos"))
        self._pill_videos.clicked.connect(lambda: self._select_category_pill("videos"))
        self._pill_docs.clicked.connect(lambda: self._select_category_pill("documents"))
        self._pill_favs.clicked.connect(lambda: self._select_category_pill("favorites"))

        tabs_row.addWidget(self._pill_all)
        tabs_row.addWidget(self._pill_photos)
        tabs_row.addWidget(self._pill_videos)
        tabs_row.addWidget(self._pill_docs)
        tabs_row.addWidget(self._pill_favs)
        tabs_row.addStretch()

        # View Mode Toggle (Grid vs List)
        self._btn_grid_mode = QPushButton()
        self._btn_grid_mode.setFixedSize(36, 36)
        self._btn_grid_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_grid_mode.setIcon(icons.get_icon("grid", color="#FFFFFF"))
        self._btn_grid_mode.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(125, 160, 220, 0.25);
                border-radius: 10px;
            }
        """)

        self._btn_list_mode = QPushButton()
        self._btn_list_mode.setFixedSize(36, 36)
        self._btn_list_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_list_mode.setIcon(icons.get_icon("list", color=DashColors.TEXT_MUTED))
        self._btn_list_mode.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 10px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.06);
            }
        """)

        tabs_row.addWidget(self._btn_grid_mode)
        tabs_row.addWidget(self._btn_list_mode)
        layout.addLayout(tabs_row)

        # ── 4. Section Title & Sort/Filter Controls ──
        sec_header_row = QHBoxLayout()
        sec_header_row.setContentsMargins(0, 4, 0, 0)
        sec_header_row.setSpacing(12)

        sec_text_col = QVBoxLayout()
        sec_text_col.setContentsMargins(0, 0, 0, 0)
        sec_text_col.setSpacing(2)

        self._section_title_lbl = QLabel("Recent Files")
        self._section_title_lbl.setFont(QFont("Segoe UI Variable", 14, QFont.Weight.Bold))
        self._section_title_lbl.setStyleSheet(f"color: {DashColors.TEXT_PRIMARY}; background: transparent;")
        sec_text_col.addWidget(self._section_title_lbl)

        self._section_sub_lbl = QLabel("Your recently added or accessed files.")
        self._section_sub_lbl.setFont(QFont("Segoe UI Variable", 10))
        self._section_sub_lbl.setStyleSheet(f"color: {DashColors.TEXT_MUTED}; background: transparent;")
        sec_text_col.addWidget(self._section_sub_lbl)

        sec_header_row.addLayout(sec_text_col)
        sec_header_row.addStretch()

        # Sort Dropdown
        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["Sort by: Recent", "Sort by: Name", "Sort by: Size", "Sort by: Type"])
        self._sort_combo.setFixedHeight(34)
        self._sort_combo.setStyleSheet(f"""
            QComboBox {{
                background: rgba(24, 37, 62, 0.65);
                color: {DashColors.TEXT_SECONDARY};
                border: 1px solid {DashColors.BORDER_SUBTLE};
                border-radius: 8px;
                padding: 4px 12px;
                font-family: 'Segoe UI Variable', 'Segoe UI';
                font-size: 12px;
                font-weight: 500;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background: #0E1626;
                color: #F4F7FF;
                border: 1px solid rgba(125, 160, 220, 0.25);
                selection-background-color: rgba(91, 140, 255, 0.35);
                border-radius: 8px;
                padding: 4px;
            }}
        """)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        sec_header_row.addWidget(self._sort_combo)

        # Filter Button
        self._filter_btn = QPushButton()
        self._filter_btn.setFixedSize(34, 34)
        self._filter_btn.setIcon(icons.get_icon("filter", color=DashColors.TEXT_SECONDARY))
        self._filter_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(24, 37, 62, 0.65);
                border: 1px solid {DashColors.BORDER_SUBTLE};
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background: rgba(36, 54, 90, 0.85);
                border-color: {DashColors.ACCENT_BLUE};
            }}
        """)
        sec_header_row.addWidget(self._filter_btn)
        layout.addLayout(sec_header_row)

        # Optional unlock speed optimization banner (shown when KDF is slow, auto-dismisses after 5s @ 90fps)
        self._speed_banner = _AutoDismissBanner(timeout_ms=5000)
        self._speed_banner.setObjectName("SpeedBanner")
        self._speed_banner.auto_dismissed.connect(lambda: setattr(self, "_speed_banner_dismissed", True))
        self._speed_banner.setStyleSheet("""
            #SpeedBanner {
                background: rgba(139, 156, 255, 0.10);
                border: 1px solid rgba(139, 156, 255, 0.35);
                border-radius: 12px;
                padding: 10px 14px;
            }
        """)
        sb_lay = QHBoxLayout(self._speed_banner)
        sb_lay.setContentsMargins(6, 2, 6, 2)
        sb_lay.setSpacing(12)

        sb_icon = QLabel()
        sb_icon.setPixmap(icons.get_pixmap("shield_check", size=20, color=Colors.PRIMARY))
        sb_icon.setStyleSheet("background: transparent;")
        sb_lay.addWidget(sb_icon)

        self._speed_lbl = QLabel(
            "Slow unlock detected — optimize key derivation to ~0.25s with full OWASP-safe security."
        )
        self._speed_lbl.setWordWrap(True)
        self._speed_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 12px; background: transparent;")
        sb_lay.addWidget(self._speed_lbl, 1)

        # Never nest GlowButton (QGraphicsDropShadowEffect) inside a parent QGraphicsOpacityEffect,
        # which can clip/blank the banner on hover in Qt6 software effect composition.
        self._optimize_btn = QPushButton("Optimize Speed (~0.25s)")
        self._optimize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._optimize_btn.setFixedHeight(32)
        self._optimize_btn.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.ACCENT};
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.22);
                border-radius: 8px;
                padding: 0 14px;
                font-size: 12px;
                font-weight: 600;
                min-height: 30px;
            }}
            QPushButton:hover {{
                background: {Colors.ACCENT_HOVER};
            }}
            QPushButton:pressed {{
                background: {Colors.ACCENT_DARK};
            }}
        """)
        self._optimize_btn.clicked.connect(self._open_optimize_speed_dialog)
        sb_lay.addWidget(self._optimize_btn)

        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.setProperty("ghost", True)
        dismiss_btn.setFixedHeight(30)
        dismiss_btn.clicked.connect(lambda: self._dismiss_speed_banner(animate=True))
        sb_lay.addWidget(dismiss_btn)

        self._speed_banner.register_hover_targets()
        self._speed_banner.hide()
        layout.addWidget(self._speed_banner)

        # Panic hotkey registration failure banner (shown if hotkey is already registered by another process)
        self._hotkey_banner = _AutoDismissBanner(timeout_ms=7000)
        self._hotkey_banner.setObjectName("HotkeyBanner")
        self._hotkey_banner.setStyleSheet("""
            #HotkeyBanner {
                background: rgba(239, 68, 68, 0.12);
                border: 1px solid rgba(239, 68, 68, 0.35);
                border-radius: 12px;
                padding: 10px 14px;
            }
        """)
        hb_lay = QHBoxLayout(self._hotkey_banner)
        hb_lay.setContentsMargins(6, 2, 6, 2)
        hb_lay.setSpacing(12)

        hb_icon = QLabel()
        hb_icon.setPixmap(icons.get_pixmap("alert_triangle", size=20, color=DashColors.ACCENT_RED))
        hb_icon.setStyleSheet("background: transparent;")
        hb_lay.addWidget(hb_icon)

        self._hotkey_lbl = QLabel("Panic hotkey (Ctrl+Alt+Shift+L) could not be registered.")
        self._hotkey_lbl.setWordWrap(True)
        self._hotkey_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 12px; background: transparent;")
        hb_lay.addWidget(self._hotkey_lbl, 1)

        hb_dismiss_btn = QPushButton("Dismiss")
        hb_dismiss_btn.setProperty("ghost", True)
        hb_dismiss_btn.setFixedHeight(30)
        hb_dismiss_btn.clicked.connect(self._hotkey_banner.dismiss_animated)
        hb_lay.addWidget(hb_dismiss_btn)

        self._hotkey_banner.register_hover_targets()
        self._hotkey_banner.hide()
        layout.addWidget(self._hotkey_banner)

        # Contextual Multi-Select Action Bar (shown during selection mode / active selection)
        self._selection_bar = QFrame()
        self._selection_bar.setObjectName("SelectionBar")
        self._selection_bar.setStyleSheet("""
            #SelectionBar {
                background: rgba(139, 156, 255, 0.12);
                border: 1px solid rgba(139, 156, 255, 0.35);
                border-radius: 12px;
                padding: 8px 14px;
            }
        """)
        sel_lay = QHBoxLayout(self._selection_bar)
        sel_lay.setContentsMargins(6, 2, 6, 2)
        sel_lay.setSpacing(10)

        self._sel_count_lbl = QLabel("0 selected")
        self._sel_count_lbl.setFont(QFont("Segoe UI Variable", 12, QFont.Weight.DemiBold))
        self._sel_count_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        sel_lay.addWidget(self._sel_count_lbl)
        sel_lay.addStretch()

        self._sel_all_btn = QPushButton("Select All")
        self._sel_all_btn.setProperty("ghost", True)
        self._sel_all_btn.setFixedHeight(32)
        self._sel_all_btn.clicked.connect(self._select_all_visible)
        sel_lay.addWidget(self._sel_all_btn)

        self._sel_clear_btn = QPushButton("Clear")
        self._sel_clear_btn.setProperty("ghost", True)
        self._sel_clear_btn.setFixedHeight(32)
        self._sel_clear_btn.clicked.connect(lambda: self._clear_selection(exit_mode=False))
        sel_lay.addWidget(self._sel_clear_btn)

        self._sel_delete_btn = QPushButton(" Delete Selected")
        self._sel_delete_btn.setIcon(icons.get_icon("trash", color=Colors.DANGER, active_color="#FFFFFF"))
        self._sel_delete_btn.setFixedHeight(32)
        self._sel_delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sel_delete_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 113, 131, 0.18);
                color: {Colors.DANGER};
                border: 1px solid rgba(255, 113, 131, 0.45);
                border-radius: 8px;
                padding: 0 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255, 113, 131, 0.30);
            }}
            QPushButton:disabled {{
                background: rgba(255, 255, 255, 0.04);
                color: {Colors.TEXT_MUTED};
                border: 1px solid {Colors.BORDER};
            }}
        """)
        self._sel_delete_btn.setEnabled(False)
        self._sel_delete_btn.clicked.connect(self._delete_selected_files)
        sel_lay.addWidget(self._sel_delete_btn)

        self._sel_done_btn = QPushButton("Done")
        self._sel_done_btn.setProperty("ghost", True)
        self._sel_done_btn.setFixedHeight(32)
        self._sel_done_btn.clicked.connect(lambda: self._toggle_selection_mode(False))
        sel_lay.addWidget(self._sel_done_btn)

        self._selection_bar.hide()
        layout.addWidget(self._selection_bar)

        # Drop zone hint (shown when empty)
        self._drop_hint = QFrame()
        self._drop_hint.setObjectName("DropHintCard")
        self._drop_hint.setStyleSheet("""
            #DropHintCard {
                background: rgba(16, 27, 48, 0.50);
                border: 2px dashed rgba(125, 160, 220, 0.22);
                border-radius: 16px;
                padding: 48px;
            }
        """)
        drop_layout = QVBoxLayout(self._drop_hint)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.setSpacing(12)

        drop_icon = QLabel()
        drop_icon.setPixmap(icons.get_pixmap("folder_plus", size=56, color=DashColors.TEXT_MUTED))
        drop_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_icon.setStyleSheet("background: transparent;")

        drop_text = QLabel("Drag files or folders here to encrypt them\nor click '+ Add Files' below")
        drop_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_text.setFont(QFont("Segoe UI Variable", 13))
        drop_text.setStyleSheet(f"color: {DashColors.TEXT_MUTED}; background: transparent; line-height: 1.6;")
        self._drop_text = drop_text

        drop_layout.addWidget(drop_icon)
        drop_layout.addWidget(drop_text)

        # File grid scroll area
        self._scroll = SmoothScrollArea()
        self._scroll.setAcceptDrops(False)
        if self._scroll.viewport():
            self._scroll.viewport().setAcceptDrops(False)
        self._grid_container = QWidget()
        self._grid_container.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(self._grid_container)
        self._grid.setSpacing(12)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._scroll.setWidget(self._grid_container)

        layout.addWidget(self._drop_hint)
        layout.addWidget(self._scroll, 1)

        self._drop_hint.show()
        self._scroll.hide()

        # 5. Bottom Footer Row (Vault protection active on left, + Add Files ∨ on right)
        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(4, 6, 4, 0)
        footer_row.setSpacing(12)

        self._protection_status = SidebarProtectionStatus()
        footer_row.addWidget(self._protection_status, 0, Qt.AlignmentFlag.AlignVCenter)

        footer_row.addStretch()

        self._fab_add = QPushButton("+ Add Files  ∨")
        self._fab_add.setFixedSize(142, 42)
        self._fab_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fab_add.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4C65E8, stop:1 #637EFF);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.28);
                border-radius: 21px;
                font-family: 'Segoe UI Variable', 'Segoe UI';
                font-size: 13px;
                font-weight: 600;
                padding: 0 16px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5C75F8, stop:1 #738EFF);
                border-color: rgba(255, 255, 255, 0.45);
            }
        """)
        self._fab_add.clicked.connect(self._show_fab_menu)
        footer_row.addWidget(self._fab_add, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addLayout(footer_row)

        return page

    def _dismiss_speed_banner(self, animate: bool = False) -> None:
        self._speed_banner_dismissed = True
        if animate:
            self._speed_banner.dismiss_animated()
        else:
            self._speed_banner.dismiss_immediately()

    def _is_kdf_slow(self) -> bool:
        try:
            if not self._keystore:
                return False
            kp = self._keystore.config.kdf_params
            if kp is None and self._keystore.config.exists():
                self._keystore.config.load()
                kp = self._keystore.config.kdf_params
            if kp is None:
                return False
            return bool(kp.memory_kib > 98304 or kp.time_cost > 18)
        except Exception:
            return False

    def _open_optimize_speed_dialog(self) -> None:
        self._speed_banner.pause_countdown(True)
        try:
            self._ensure_settings_ui()
            if self._settings_ui is not None and hasattr(self._settings_ui, "_optimize_unlock_speed_dialog"):
                self._settings_ui._optimize_unlock_speed_dialog()
                if not self._is_kdf_slow():
                    self._dismiss_speed_banner(animate=True)
                    return
        finally:
            if not self._speed_banner_dismissed:
                self._speed_banner.pause_countdown(False)

    def _build_category_page(self, mime_prefix: str, title: str) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {Colors.BG_DARK};")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        # Simple placeholder for now — full impl in Phase 5
        lbl = QLabel(f"{title}\n\nOpen vault and navigate to this section.")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 14px; background: transparent;")
        layout.addWidget(lbl)
        return page

    def _build_favorites_page(self) -> QWidget:
        return self._build_category_page("", "⭐ Favorites")

    def _build_placeholder(self, name: str, icon: str) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {Colors.BG_DARK};")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(f"{icon}\n{name}")
        lbl.setFont(QFont("Segoe UI Emoji, Segoe UI", 36))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {Colors.TEXT_MUTED}; background: transparent;")
        layout.addWidget(lbl)
        return page

    # ── Navigation ────────────────────────────────────────────────────────────

    def _unselect_all_nav(self) -> None:
        if hasattr(self, "_nav_dashboard") and self._nav_dashboard:
            self._nav_dashboard.set_selected(False)
        if hasattr(self, "_nav_bin") and self._nav_bin:
            self._nav_bin.set_selected(False)
        for nav in getattr(self, "_nav_items", []):
            nav.set_selected(False)

    def _select_category_pill(self, cat: str) -> None:
        if hasattr(self, "_pill_all"):
            self._pill_all.set_active(cat == "all")
            self._pill_photos.set_active(cat == "photos")
            self._pill_videos.set_active(cat == "videos")
            self._pill_docs.set_active(cat == "documents")
            self._pill_favs.set_active(cat == "favorites")

        self._current_section = cat

        titles = {
            "all": ("Recent Files", "Your recently added or accessed files.", "All Files"),
            "photos": ("Photos", "Your encrypted photos and images.", "Photos"),
            "videos": ("Videos", "Your private video clips.", "Videos"),
            "documents": ("Documents", "Your documents, spreadsheets and archives.", "Documents"),
            "favorites": ("Favorites", "Your starred and favorite items.", "Favorites"),
            "bin": ("Bin", "Deleted files in your vault.", "Bin"),
        }
        title, sub, legacy_title = titles.get(cat, ("Recent Files", "Your recently added or accessed files.", "All Files"))
        if hasattr(self, "_section_title_lbl"):
            self._section_title_lbl.setText(title)
            self._section_sub_lbl.setText(sub)
        if hasattr(self, "_page_title"):
            self._page_title.setText(legacy_title)

        cat_to_nav_idx = {"all": 0, "photos": 1, "videos": 2, "documents": 3, "favorites": 4}
        if cat in cat_to_nav_idx and hasattr(self, "_nav_items"):
            idx = cat_to_nav_idx[cat]
            if idx < len(self._nav_items):
                self._unselect_all_nav()
                self._nav_items[idx].set_selected(True)
                if cat == "all" and hasattr(self, "_nav_dashboard") and self._nav_dashboard:
                    self._nav_dashboard.set_selected(True)

        self._page_stack.setCurrentIndex(0)
        self.load_files()

    def _nav_to_dashboard(self) -> None:
        self._unselect_all_nav()
        if hasattr(self, "_nav_dashboard") and self._nav_dashboard:
            self._nav_dashboard.set_selected(True)
        if hasattr(self, "_nav_items") and self._nav_items:
            self._nav_items[0].set_selected(True)
        self._select_category_pill("all")

    def _nav_to_bin(self) -> None:
        self._unselect_all_nav()
        if hasattr(self, "_nav_bin") and self._nav_bin:
            self._nav_bin.set_selected(True)
        self._select_category_pill("bin")

    def _nav_to(self, page: int, item: SidebarItem) -> None:
        self._unselect_all_nav()
        item.set_selected(True)

        titles = ["All Files", "Photos", "Videos", "Documents",
                  "Favorites", "Intruder Log", "App Lock", "Settings"]
        if page < len(titles):
            self._page_title.setText(titles[page])

        if page == 0:
            self._select_category_pill("all")
        elif page == 1:
            self._select_category_pill("photos")
        elif page == 2:
            self._select_category_pill("videos")
        elif page == 3:
            self._select_category_pill("documents")
        elif page == 4:
            self._select_category_pill("favorites")
        elif page == 5:
            if self._selection_mode or self._selected_ids:
                self._clear_selection(exit_mode=True)
            self._ensure_intruder_ui()
            self._page_stack.setCurrentIndex(1)
            if hasattr(self._intruder_ui, "refresh_log"):
                self._intruder_ui.refresh_log()
        elif page == 6:
            if self._selection_mode or self._selected_ids:
                self._clear_selection(exit_mode=True)
            self._ensure_applock_ui()
            self._page_stack.setCurrentIndex(2)
        elif page == 7:
            if self._selection_mode or self._selected_ids:
                self._clear_selection(exit_mode=True)
            self._ensure_settings_ui()
            self._page_stack.setCurrentIndex(3)

    # ── File operations ───────────────────────────────────────────────────────

    def _filter_entries(self, entries: list) -> list:
        if self._current_section == "photos":
            return [e for e in entries if infer_mime_type(e.original_name, e.mime_type).startswith("image/")]
        if self._current_section == "videos":
            return [e for e in entries if infer_mime_type(e.original_name, e.mime_type).startswith("video/")]
        if self._current_section == "documents":
            return [e for e in entries if not infer_mime_type(e.original_name, e.mime_type).startswith(("image/", "video/"))]
        if self._current_section == "favorites":
            return [e for e in entries if e.is_favorite]
        if self._current_section == "bin":
            return [e for e in entries if getattr(e, "deleted_at", None) or getattr(e, "is_deleted", False)]
        return list(entries)

    def _on_sort_changed(self, index: int) -> None:
        if not self._current_entries:
            return
        if index == 0:  # Recent
            self._current_entries.sort(
                key=lambda e: getattr(e, "added_at", 0) or getattr(e, "created_at", 0),
                reverse=True,
            )
        elif index == 1:  # Name
            self._current_entries.sort(key=lambda e: e.original_name.lower())
        elif index == 2:  # Size
            self._current_entries.sort(key=lambda e: e.size_bytes, reverse=True)
        elif index == 3:  # Type
            self._current_entries.sort(key=lambda e: Path(e.original_name).suffix.lower())
        self._relayout_grid()

    def _show_fab_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #0E1626;
                border: 1px solid rgba(125, 160, 220, 0.25);
                border-radius: 10px;
                padding: 6px;
                color: #F4F7FF;
                font-family: 'Segoe UI Variable', 'Segoe UI';
                font-size: 12px;
            }
            QMenu::item {
                padding: 8px 22px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background: rgba(91, 140, 255, 0.30);
            }
        """)
        act_files = menu.addAction("Add Files…")
        act_folder = menu.addAction("Add Folder…")
        act_files.setIcon(icons.get_icon("plus", color=DashColors.TEXT_PRIMARY))
        act_folder.setIcon(icons.get_icon("folder_plus", color=DashColors.TEXT_PRIMARY))
        act_files.triggered.connect(self._add_files_dialog)
        act_folder.triggered.connect(self._add_folder_dialog)

        from PySide6.QtCore import QPoint
        pos = self._fab_add.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 6))
        menu.exec(pos)

    def _apply_loaded_index(self, index) -> None:
        all_files = index.all_files()
        valid_ids = {e.file_id for e in all_files}
        if self._selected_ids:
            self._selected_ids &= valid_ids
            self._sync_selection_ui()

        # Update real metric cards
        total_count = len(all_files)
        total_bytes = sum(f.size_bytes for f in all_files)
        favs_count = sum(1 for f in all_files if f.is_favorite)

        if hasattr(self, "_stat_total"):
            self._stat_total.set_value(str(total_count))
        if hasattr(self, "_stat_storage"):
            self._stat_storage.set_value(self._fmt_size(total_bytes))
        if hasattr(self, "_stat_favs"):
            self._stat_favs.set_value(str(favs_count))
        if hasattr(self, "_stat_protection"):
            self._stat_protection.set_value("100%")

        entries = self._filter_entries(all_files)

        search_text = self._search.text().strip() if hasattr(self, "_search") else ""
        if search_text:
            q = search_text.lower()
            entries = [
                e for e in entries
                if q in e.original_name.lower() or any(q in t.lower() for t in (e.tags or []))
            ]

        # Apply active sort
        if hasattr(self, "_sort_combo"):
            sort_idx = self._sort_combo.currentIndex()
            if sort_idx == 0:
                entries.sort(key=lambda e: getattr(e, "added_at", 0) or getattr(e, "created_at", 0), reverse=True)
            elif sort_idx == 1:
                entries.sort(key=lambda e: e.original_name.lower())
            elif sort_idx == 2:
                entries.sort(key=lambda e: e.size_bytes, reverse=True)
            elif sort_idx == 3:
                entries.sort(key=lambda e: Path(e.original_name).suffix.lower())

        self._populate_grid(entries)
        self._schedule_thumbnail_backfill(all_files)

    def load_files(self, initial: bool = False) -> None:
        """Call after unlock to populate the file grid synchronously (used by tests & refreshes)."""
        if not self._vault:
            return
        try:
            if initial or not getattr(self, "_files_loaded", False):
                index = self._vault.load()
                self._files_loaded = True
            else:
                index = self._vault.refresh()
            self._apply_loaded_index(index)
            self._update_status()
            self.files_loaded.emit()
        except IndexCorruptError as ice:
            logger.error("Index corrupted: %s", type(ice).__name__)
            self._handle_corrupt_index()
        except Exception as exc:
            logger.error("Failed to load vault files: %s", type(exc).__name__)
            self._status_bar.showMessage("Could not load vault index [ERR-IDX-01]", 5000)

    def load_files_async(self, initial: bool = True, unlock_elapsed_s: float = 0.0) -> None:
        """Non-blocking file index load after unlock so Dashboard transitions without freezing."""
        if not self._vault or self._cleaned_up:
            return

        if (
            not getattr(self._session, "is_decoy", False)
            and not self._speed_banner_dismissed
            and (unlock_elapsed_s > 0.55 or self._is_kdf_slow())
        ):
            if unlock_elapsed_s > 0.55:
                self._speed_lbl.setText(
                    f"Unlock took {unlock_elapsed_s:.1f}s due to heavy legacy key settings — "
                    "optimize it to ~0.25s with full OWASP-safe security."
                )
            self._speed_banner.show_animated()

        if self._load_worker is not None and self._load_worker.isRunning():
            return

        self._status_bar.showMessage("Loading encrypted vault…")
        do_initial = bool(initial or not getattr(self, "_files_loaded", False))
        worker = _VaultLoadWorker(self._vault, self._vault_service, do_initial)
        self._load_worker = worker

        def _on_loaded(idx: object) -> None:
            self._load_worker = None
            worker.deleteLater()
            if self._cleaned_up or not getattr(self._session, "is_unlocked", False):
                return
            self._files_loaded = True
            self._apply_loaded_index(idx)
            self._update_status()
            self.files_loaded.emit()

        def _on_corrupt() -> None:
            self._load_worker = None
            worker.deleteLater()
            if not self._cleaned_up and getattr(self._session, "is_unlocked", False):
                self._handle_corrupt_index()

        def _on_failed(_err: str) -> None:
            self._load_worker = None
            worker.deleteLater()
            if not self._cleaned_up:
                self._status_bar.showMessage("Could not load vault index [ERR-IDX-01]", 5000)

        worker.loaded.connect(_on_loaded, Qt.ConnectionType.QueuedConnection)
        worker.corrupt.connect(_on_corrupt, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(_on_failed, Qt.ConnectionType.QueuedConnection)
        worker.start()

    def _handle_corrupt_index(self) -> None:
        """Offer recovery from index.bak1-3 when primary index.dat is corrupted."""
        backups = [self._vault.vault_dir / f"index.bak{i}" for i in range(1, 4)]
        available = [b for b in backups if b.exists()]
        if not available:
            QMessageBox.critical(
                self,
                "Vault Index Corrupted",
                "The vault index could not be verified and no backups were found. [ERR-IDX-02]",
            )
            return

        from PySide6.QtWidgets import QInputDialog
        names = [b.name for b in available]
        choice, ok = QInputDialog.getItem(
            self,
            "Recover Vault Index",
            "Primary index.dat is corrupted. Choose an encrypted backup to restore:",
            names,
            0,
            False,
        )
        if ok and choice:
            chosen_path = self._vault.vault_dir / choice
            try:
                mk = get_session().get_master_key()
                idx = self._vault._index.try_load_backup(chosen_path, mk)
                self._vault._index.save(mk)
                self._vault._loaded = True
                self._files_loaded = True
                self._populate_grid(self._filter_entries(idx.all_files()))
                QMessageBox.information(self, "Recovered", f"Vault index successfully recovered from {choice}.")
            except Exception:
                QMessageBox.critical(
                    self,
                    "Recovery Failed",
                    "Selected backup could not be decrypted or verified. [ERR-IDX-03]",
                )

    def _toggle_favorite(self, file_id: str, is_fav: bool) -> None:
        def _apply() -> None:
            entry = self._vault._index.index.get(file_id)
            if entry:
                entry.is_favorite = is_fav
                self._vault._save_index()

        if self._vault_service:
            acquired = self._vault_service._lock.acquire(blocking=False)
            if not acquired:
                self._status_bar.showMessage("Vault is busy with an active operation — try again shortly.", 3000)
                return
            try:
                _apply()
            finally:
                self._vault_service._lock.release()
        else:
            _apply()
        self.load_files()

    def _populate_grid(self, entries) -> None:
        self._current_entries = list(entries)
        self._relayout_grid()

    def _create_file_card(self, entry) -> FileCard:
        card = FileCard(
            entry,
            thumb_cache=self._thumb_cache,
            selected=(entry.file_id in self._selected_ids),
            selection_mode=self._selection_mode,
            selected_count_provider=lambda: len(self._selected_ids),
        )
        card.open_requested.connect(self._open_file)
        card.restore_requested.connect(self._restore_file)
        card.delete_requested.connect(self._delete_file)
        card.favorite_toggled.connect(self._toggle_favorite)
        card.selection_requested.connect(self._handle_card_selection)
        card.select_toggle_requested.connect(self._on_card_select_toggled)
        card.batch_delete_requested.connect(self._delete_selected_files)
        return card

    def _render_next_grid_batch(self) -> None:
        if self._cleaned_up or not self._current_entries:
            return
        total = len(self._current_entries)
        if self._render_offset >= total:
            return
        col_count = max(1, self._current_cols)
        end = min(total, self._render_offset + _GRID_BATCH_SIZE)

        self._grid_container.setUpdatesEnabled(False)
        try:
            for i in range(self._render_offset, end):
                entry = self._current_entries[i]
                card = self._create_file_card(entry)
                self._rendered_cards.append(card)
                self._cards_by_id[entry.file_id] = card
                self._grid.addWidget(card, i // col_count, i % col_count)
        finally:
            self._grid_container.setUpdatesEnabled(True)

        self._render_offset = end
        if self._render_offset < total and not self._cleaned_up:
            self._grid_render_timer.start(0)

    def _reposition_rendered_cards(self, col_count: int) -> None:
        col_count = max(1, col_count)
        self._current_cols = col_count
        self._grid_container.setUpdatesEnabled(False)
        try:
            while self._grid.count():
                self._grid.takeAt(0)
            for i, card in enumerate(self._rendered_cards):
                self._grid.addWidget(card, i // col_count, i % col_count)
        finally:
            self._grid_container.setUpdatesEnabled(True)

    def _relayout_grid(self) -> None:
        if hasattr(self, "_grid_render_timer") and self._grid_render_timer.isActive():
            self._grid_render_timer.stop()

        self._grid_container.setUpdatesEnabled(False)
        try:
            while self._grid.count():
                item = self._grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._rendered_cards.clear()
            self._cards_by_id.clear()
            self._render_offset = 0

            if not self._current_entries:
                if hasattr(self, "_drop_text"):
                    search_text = self._search.text().strip() if hasattr(self, "_search") else ""
                    if search_text:
                        self._drop_text.setText("No encrypted files match your search.")
                    elif self._current_section == "photos":
                        self._drop_text.setText("No photos in this vault yet.\nDrag images here or click '+ Add Files' above.")
                    elif self._current_section == "videos":
                        self._drop_text.setText("No videos in this vault yet.\nDrag videos here or click '+ Add Files' above.")
                    elif self._current_section == "documents":
                        self._drop_text.setText("No documents or other files in this vault yet.\nDrag files here or click '+ Add Files' above.")
                    elif self._current_section == "favorites":
                        self._drop_text.setText("No favorite files yet.\nRight-click any file card and choose 'Add to Favorites'.")
                    else:
                        self._drop_text.setText("Drag files or folders here to encrypt them\nor click '+ Add Files' above")
                self._drop_hint.show()
                self._scroll.hide()
                self._sync_selection_ui()
                return

            self._drop_hint.hide()
            self._scroll.show()

            vp = self._scroll.viewport()
            avail = vp.width() if (vp and vp.width() > 100) else self._scroll.width()
            col_count = max(1, (avail - 24) // 172)
            self._current_cols = col_count
        finally:
            self._grid_container.setUpdatesEnabled(True)

        self._render_next_grid_batch()
        self._sync_selection_ui()

    def _on_debounced_resize(self) -> None:
        if self._cleaned_up or not (hasattr(self, "_scroll") and hasattr(self, "_grid") and self._current_entries):
            return
        vp = self._scroll.viewport()
        avail = vp.width() if (vp and vp.width() > 100) else self._scroll.width()
        col_count = max(1, (avail - 24) // 172)
        if col_count != getattr(self, "_current_cols", 5):
            if self._rendered_cards:
                self._reposition_rendered_cards(col_count)
            else:
                self._relayout_grid()

    # ── Multi-select & batch deletion ─────────────────────────────────────────

    def _toggle_selection_mode(self, enabled: Optional[bool] = None) -> None:
        target = (not self._selection_mode) if enabled is None else bool(enabled)
        if target and hasattr(self, "_page_stack") and self._page_stack.currentIndex() != 0 and self._nav_items:
            self._nav_to(0, self._nav_items[0])
        self._selection_mode = target
        if not self._selection_mode:
            self._selected_ids.clear()
            self._last_selected_index = None
        self._sync_selection_ui()

    def _handle_card_selection(self, file_id: str, modifiers: object = None) -> None:
        if not self._selection_mode:
            self._selection_mode = True
        mods = modifiers if modifiers is not None else Qt.KeyboardModifier.NoModifier

        clicked_idx: Optional[int] = None
        for idx, entry in enumerate(self._current_entries):
            if entry.file_id == file_id:
                clicked_idx = idx
                break

        if (
            bool(mods & Qt.KeyboardModifier.ShiftModifier)
            and self._last_selected_index is not None
            and clicked_idx is not None
            and 0 <= self._last_selected_index < len(self._current_entries)
        ):
            lo, hi = sorted((self._last_selected_index, clicked_idx))
            for k in range(lo, hi + 1):
                self._selected_ids.add(self._current_entries[k].file_id)
        else:
            if file_id in self._selected_ids:
                self._selected_ids.discard(file_id)
            else:
                self._selected_ids.add(file_id)
            self._last_selected_index = clicked_idx

        self._sync_selection_ui()

    def _on_card_select_toggled(self, file_id: str) -> None:
        self._handle_card_selection(file_id, Qt.KeyboardModifier.NoModifier)

    def _select_all_visible(self) -> None:
        if hasattr(self, "_page_stack") and self._page_stack.currentIndex() != 0:
            return
        self._selection_mode = True
        for entry in self._current_entries:
            self._selected_ids.add(entry.file_id)
        self._last_selected_index = len(self._current_entries) - 1 if self._current_entries else None
        self._sync_selection_ui()

    def _clear_selection(self, exit_mode: bool = False) -> None:
        self._selected_ids.clear()
        self._last_selected_index = None
        if exit_mode:
            self._selection_mode = False
        self._sync_selection_ui()

    def _sync_selection_ui(self) -> None:
        count = len(self._selected_ids)
        if hasattr(self, "_select_btn"):
            self._select_btn.blockSignals(True)
            self._select_btn.setChecked(self._selection_mode)
            self._select_btn.blockSignals(False)

        if hasattr(self, "_selection_bar"):
            self._selection_bar.setVisible(self._selection_mode or count > 0)
            self._sel_count_lbl.setText(f"{count} selected" if count else "Select files to delete")
            self._sel_clear_btn.setEnabled(count > 0)
            self._sel_all_btn.setEnabled(bool(self._current_entries) and count < len(self._current_entries))
            self._sel_delete_btn.setEnabled(count > 0)
            self._sel_delete_btn.setText(f" Delete ({count})" if count else " Delete Selected")

        for fid, card in self._cards_by_id.items():
            card.set_selection_state(
                selected=(fid in self._selected_ids),
                selection_mode=self._selection_mode,
            )

    def _delete_selected_files(self) -> None:
        if not self._vault or not self._selected_ids:
            return
        if self._vault_service:
            target_ids = [
                fid for fid in self._selected_ids
                if self._vault._index.index.get(fid) is not None
            ]
        else:
            target_ids = list(self._selected_ids)
        if not target_ids:
            self._clear_selection(exit_mode=True)
            return

        n = len(target_ids)
        reply = QMessageBox.question(
            self,
            "Delete Selected Files",
            f"Permanently remove {n} selected file(s) from the vault?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._clear_selection(exit_mode=True)
        self._stop_thumbnail_backfill()
        if self._vault_service:
            if not self._active_overlay:
                self._active_overlay = ProgressOverlay("Deleting Selected Files", self)
                self._active_overlay.cancelled.connect(self._vault_service.cancel_current)
                self._active_overlay.show()
            self._vault_service.delete_files(target_ids)
        else:
            try:
                self._vault.delete_many_from_vault(target_ids)
                self.load_files()
            except Exception:
                QMessageBox.critical(self, "Error", "Failed to delete selected files. [ERR-DEL-02]")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_resize_timer"):
            self._resize_timer.start()

    # ── Service callbacks ─────────────────────────────────────────────────────

    def _on_service_started(self, task_id: str, task_desc: str) -> None:
        if not self._active_overlay:
            self._active_overlay = ProgressOverlay(task_desc or "Working…", self)
            if self._vault_service:
                self._active_overlay.cancelled.connect(self._vault_service.cancel_current)
            self._active_overlay.show()

    def _on_service_progress(self, current: int, total: int, step: str) -> None:
        if self._active_overlay:
            self._active_overlay.set_progress(current, total, step)

    def _on_service_file_done(self, file_id: str, result_meta: object) -> None:
        if self._active_overlay is None:
            self._status_bar.showMessage("Vault operation step completed.", 2500)

    def _on_service_finished(self, task_id: str, success: bool, message: str) -> None:
        if self._active_overlay:
            self._active_overlay.accept()
            self._active_overlay = None
        self.load_files()
        self._status_bar.showMessage(message or "Vault task completed.", 4000)

    def _on_service_error(self, task_id: str, err_code: str, error_msg: str) -> None:
        if self._active_overlay:
            self._active_overlay.accept()
            self._active_overlay = None
        QMessageBox.critical(self, "Vault Error", f"Operation failed [{err_code}]:\n{error_msg}")

    def _add_files_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select Files to Encrypt")
        if paths:
            self._add_files(paths)

    def _add_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Encrypt")
        if folder:
            self._add_files([folder])

    def _add_files(self, paths: list[str]) -> None:
        if not self._vault or not self._vault_service:
            return
        if not self._active_overlay:
            self._active_overlay = ProgressOverlay("Encrypting Files", self)
            self._active_overlay.cancelled.connect(self._vault_service.cancel_current)
            self._active_overlay.show()
        self._vault_service.add_batch([Path(p) for p in paths])

    def _restore_file(self, file_id: str) -> None:
        dest = QFileDialog.getExistingDirectory(self, "Restore to…")
        if not dest or not self._vault_service:
            return
        if not self._active_overlay:
            self._active_overlay = ProgressOverlay("Restoring File", self)
            self._active_overlay.cancelled.connect(self._vault_service.cancel_current)
            self._active_overlay.show()
        self._vault_service.restore_file(file_id, Path(dest), remove=False)

    def _delete_file(self, file_id: str) -> None:
        reply = QMessageBox.question(
            self, "Delete from Vault",
            "Permanently remove this file from the vault?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._stop_thumbnail_backfill()
            if self._vault_service:
                self._vault_service.delete_file(file_id)
            else:
                try:
                    self._vault.delete_from_vault(file_id)
                    self.load_files()
                except Exception:
                    QMessageBox.critical(self, "Error", "Failed to delete file from vault. [ERR-DEL-01]")

    def _open_file(self, file_id: str) -> None:
        """Open a file in the built-in viewer."""
        if not self._vault:
            return
        entry = self._vault._index.index.get(file_id)
        if not entry:
            return
        mime = infer_mime_type(entry.original_name, entry.mime_type)
        if mime.startswith("image/"):
            self._open_in_gallery(file_id)
        elif mime.startswith("video/"):
            self._open_in_player(file_id)
        else:
            self._open_external(file_id)

    def _run_action_worker(self, fn: Callable, on_ok: Callable, on_err: Callable) -> None:
        worker = _AsyncFnWorker(fn)
        self._action_workers.add(worker)

        def _done(res: object) -> None:
            self._action_workers.discard(worker)
            worker.deleteLater()
            on_ok(res)

        def _fail(err: str) -> None:
            self._action_workers.discard(worker)
            worker.deleteLater()
            on_err(err)

        worker.succeeded.connect(_done, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(_fail, Qt.ConnectionType.QueuedConnection)
        worker.start()

    def _open_in_gallery(self, file_id: str) -> None:
        if self._opening_file or not self._vault:
            return
        self._opening_file = True
        self._status_bar.showMessage("Decrypting image…")

        def _load_bytes():
            return self._vault.open_file_in_memory(file_id)

        def _on_ok(res: object) -> None:
            self._opening_file = False
            self._update_status()
            data = res if isinstance(res, bytearray) else bytearray(res or b"")
            if self._cleaned_up or not getattr(self._session, "is_unlocked", False):
                zero_bytes(data)
                return
            try:
                from .gallery import GalleryViewer
                viewer = GalleryViewer(file_id, data, self._vault, self)
                viewer.exec()
            except Exception as exc:
                zero_bytes(data)
                logger.error("Gallery open failed: %s", type(exc).__name__)
                QMessageBox.critical(self, "Error", "Could not open image in viewer. [ERR-GAL-01]")

        def _on_err(_err: str) -> None:
            self._opening_file = False
            self._update_status()
            if not self._cleaned_up:
                QMessageBox.critical(self, "Error", "Could not open image in viewer. [ERR-GAL-01]")

        self._run_action_worker(_load_bytes, _on_ok, _on_err)

    def _open_in_player(self, file_id: str) -> None:
        try:
            from .video_player import VideoPlayer
            player = VideoPlayer(file_id, self._vault, self)
            player.favorite_changed.connect(lambda _fid, _fav: self.load_files())
            player.exec()
        except Exception as exc:
            logger.error("Video player open failed: %s", type(exc).__name__)
            QMessageBox.critical(self, "Error", "Could not open video in player. [ERR-VID-01]")

    def _open_external(self, file_id: str) -> None:
        if self._opening_file or not self._vault:
            return
        from ..system.external_opener import open_external_securely
        self._opening_file = True
        self._status_bar.showMessage("Decrypting and opening file…")

        def _launch():
            return open_external_securely(self._vault, file_id, on_modified=self._on_external_file_modified)

        def _on_ok(ok: object) -> None:
            self._opening_file = False
            self._update_status()
            if not self._cleaned_up and not ok:
                QMessageBox.warning(self, "Cannot Open File", "Unable to open this file with its default application. [ERR-EXT-01]")

        def _on_err(_err: str) -> None:
            self._opening_file = False
            self._update_status()
            if not self._cleaned_up:
                QMessageBox.critical(self, "Error", "Failed to open external file. [ERR-EXT-02]")

        self._run_action_worker(_launch, _on_ok, _on_err)

    def _on_external_file_modified(self, file_id: str, file_path: Path) -> None:
        """Prompt user to save modified external file back into vault (Audit Phase 6)."""
        entry = self._vault._index.index.get(file_id) if (self._vault and hasattr(self._vault, "_index")) else None
        name = entry.original_name if entry else "file"

        def _prompt_user() -> None:
            if self._cleaned_up or not get_session().is_unlocked or not self._vault:
                try:
                    if file_path.exists():
                        from ..core.secure_delete import secure_delete_file
                        secure_delete_file(file_path)
                except Exception:
                    pass
                return
            reply = QMessageBox.question(
                self,
                "File Modified Externally",
                f"Changes detected in '{name}'.\n\nWould you like to save the updated version back to your vault?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes and file_path.exists():
                try:
                    if self._vault_service:
                        with self._vault_service._lock:
                            self._vault.update_file(file_id, file_path)
                    else:
                        self._vault.update_file(file_id, file_path)
                    self.load_files()
                    QMessageBox.information(self, "Updated", f"'{name}' successfully updated in vault.")
                except Exception:
                    QMessageBox.warning(self, "Save Failed", "Could not update file in vault. [ERR-UPD-01]")
            else:
                try:
                    if file_path.exists():
                        from ..core.secure_delete import secure_delete_file
                        secure_delete_file(file_path)
                except Exception:
                    pass

        QTimer.singleShot(0, _prompt_user)

    def _wipe_temp(self, tmp_dir: Path) -> None:
        from ..core.secure_delete import wipe_temp_directory
        wipe_temp_directory(tmp_dir)

    # ── Drag and drop ─────────────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drop_hint.setStyleSheet(f"""
                QFrame {{
                    background: rgba(139, 156, 255, 0.08);
                    border: 2px dashed {Colors.PRIMARY};
                    border-radius: 16px;
                    padding: 48px;
                }}
            """)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._drop_hint.setStyleSheet(f"""
            QFrame {{
                background: {Colors.BG_INPUT};
                border: 2px dashed {Colors.BORDER};
                border-radius: 16px;
                padding: 48px;
            }}
        """)

    def dropEvent(self, event: QDropEvent) -> None:
        self.dragLeaveEvent(event)
        urls = event.mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        if paths:
            self._add_files(paths)

    # ── Search ────────────────────────────────────────────────────────────────

    def _do_search(self) -> None:
        text = self._search.text().strip()
        self._on_search(text)

    def _on_search(self, text: str) -> None:
        if text and self._page_stack.currentIndex() != 0 and self._nav_items:
            self._nav_to(0, self._nav_items[0])
            return
        self.load_files()

    # ── Screenshot protection ─────────────────────────────────────────────────

    def _setup_screenshot_protection(self) -> None:
        self.set_screenshot_protection(self._screenshot_blocked)

    def set_screenshot_protection(self, enabled: bool) -> None:
        from ..system.screenshot import apply_screenshot_protection
        target = self.window() or self
        ok = apply_screenshot_protection(target, enabled)
        self._screenshot_blocked = enabled if ok else False
        self._update_ss_btn_ui()

    def _toggle_screenshot_protection(self) -> None:
        from ..system.lock_controller import get_lock_controller
        target = not self._screenshot_blocked
        self.set_screenshot_protection(target)
        get_lock_controller().set_screenshot_protection(self._screenshot_blocked)
        if self._settings_store is not None:
            self._settings_store.set("screenshot_protection", self._screenshot_blocked)
        if self._settings_ui is not None and hasattr(self._settings_ui, "screen_protect_check"):
            self._settings_ui.screen_protect_check.blockSignals(True)
            self._settings_ui.screen_protect_check.setChecked(self._screenshot_blocked)
            self._settings_ui.screen_protect_check.blockSignals(False)

    def _update_ss_btn_ui(self) -> None:
        if self._screenshot_blocked:
            self._ss_btn.setText(" Protected")
            self._ss_btn.setIcon(icons.get_icon("shield_check", Colors.SUCCESS))
        else:
            self._ss_btn.setText(" Unprotected")
            self._ss_btn.setIcon(icons.get_icon("shield_alert", Colors.WARNING))

    # ── Lock shortcut ─────────────────────────────────────────────────────────

    def _setup_lock_shortcut(self) -> None:
        from PySide6.QtGui import QShortcut
        sc = QShortcut(QKeySequence("Ctrl+Alt+L"), self)
        sc.activated.connect(self.lock_requested.emit)

        # Ctrl+F to focus search input (Item 51)
        search_sc = QShortcut(QKeySequence("Ctrl+F"), self)
        search_sc.activated.connect(self._search.setFocus)

        sel_all_sc = QShortcut(QKeySequence("Ctrl+A"), self)
        sel_all_sc.activated.connect(self._on_ctrl_a_shortcut)

        del_sc = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        del_sc.activated.connect(self._on_delete_shortcut)

        esc_sc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc_sc.activated.connect(self._on_escape_shortcut)

    def _on_ctrl_a_shortcut(self) -> None:
        if self._page_stack.currentIndex() == 0 and not self._search.hasFocus() and self._current_entries:
            self._select_all_visible()

    def _on_delete_shortcut(self) -> None:
        if self._page_stack.currentIndex() == 0 and self._selected_ids and not self._search.hasFocus():
            self._delete_selected_files()

    def _on_escape_shortcut(self) -> None:
        if self._selection_mode or self._selected_ids:
            self._clear_selection(exit_mode=True)

    # ── Status & Background Thumbnail Backfill ────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if (
            not self._cleaned_up
            and self._vault
            and getattr(self._vault, "_loaded", False)
            and hasattr(self._vault, "_index")
            and getattr(self._vault._index, "_index", None) is not None
            and get_session().is_unlocked
        ):
            try:
                all_f = self._vault._index.index.all_files()
                if any(not f.thumbnail_b64 for f in all_f):
                    self._schedule_thumbnail_backfill(all_f)
            except Exception:
                pass

    def _schedule_thumbnail_backfill(self, entries: list) -> None:
        """Populate thumbnails for existing vault images/videos that lack cached previews."""
        if (
            self._cleaned_up
            or not self._vault
            or not get_session().is_unlocked
        ):
            return

        media_jobs: list[tuple[str, str, str]] = []

        for e in entries:
            if e.thumbnail_b64 or e.file_id in self._thumb_backfill_attempted:
                continue
            mime = infer_mime_type(e.original_name, e.mime_type)
            if mime.startswith("image/") or mime.startswith("video/"):
                media_jobs.append((e.file_id, e.original_name, mime))

        if media_jobs and self._media_backfill_worker is None:
            worker = _MediaThumbBackfillWorker(self._vault, media_jobs)
            self._media_backfill_worker = worker
            self._img_backfill_worker = worker

            def _on_media_ready(fid: str, b64: str) -> None:
                self._thumb_backfill_attempted.add(fid)
                if not self._cleaned_up:
                    self._on_backfill_thumb_ready(fid, b64)

            def _on_media_failed(fid: str, mime: str) -> None:
                if mime.startswith("video/") and not self._cleaned_up and self._vault:
                    entry = self._vault._index.index.get(fid)
                    if entry and not entry.thumbnail_b64 and entry not in self._vid_backfill_queue:
                        self._vid_backfill_queue.append(entry)
                        if self._vid_backfill_active_id is None:
                            QTimer.singleShot(60, self._process_next_video_thumb)
                        return
                self._thumb_backfill_attempted.add(fid)

            def _on_media_done() -> None:
                if self._media_backfill_worker is worker:
                    self._media_backfill_worker = None
                    self._img_backfill_worker = None
                worker.deleteLater()
                if not self._cleaned_up and self._pending_thumb_saves:
                    self._thumb_flush_timer.start(250)
                if not self._cleaned_up and self._vault and hasattr(self._vault, "_index"):
                    all_f = self._vault._index.index.all_files()
                    if any((not f.thumbnail_b64 and f.file_id not in self._thumb_backfill_attempted) for f in all_f):
                        self._schedule_thumbnail_backfill(all_f)

            worker.thumb_ready.connect(_on_media_ready, Qt.ConnectionType.QueuedConnection)
            worker.thumb_failed.connect(_on_media_failed, Qt.ConnectionType.QueuedConnection)
            worker.finished.connect(_on_media_done, Qt.ConnectionType.QueuedConnection)
            worker.start()

    def _ensure_video_thumb_extractor(self) -> bool:
        if self._vid_backfill_player is not None and self._vid_backfill_sink is not None:
            return True
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

            player = QMediaPlayer(self)
            sink = QVideoSink(self)
            player.setVideoSink(sink)
            sink.videoFrameChanged.connect(self._on_video_thumb_frame)
            player.errorOccurred.connect(self._on_video_thumb_error)
            self._vid_backfill_player = player
            self._vid_backfill_sink = sink
            return True
        except Exception:
            return False

    def _process_next_video_thumb(self) -> None:
        if (
            self._cleaned_up
            or self._vid_backfill_active_id is not None
            or not self._vault
            or not get_session().is_unlocked
        ):
            return

        while self._vid_backfill_queue:
            entry = self._vid_backfill_queue.pop(0)
            if entry.thumbnail_b64:
                continue
            live_entry = self._vault._index.index.get(entry.file_id)
            if live_entry is None or live_entry.thumbnail_b64:
                continue
            try:
                blob_path = self._vault._get_blob_path(entry.file_id)
                if not blob_path.exists() or entry.size_bytes <= 0:
                    continue
                if not self._ensure_video_thumb_extractor():
                    self._vid_backfill_queue.clear()
                    return

                from .video_player import StreamingVaultIODevice

                mk = get_session().get_master_key()
                dev = StreamingVaultIODevice(
                    blob_path=blob_path,
                    master_key=mk,
                    file_id=bytes.fromhex(entry.file_id),
                )
                self._vid_backfill_device = dev
                self._vid_backfill_active_id = entry.file_id
                self._vid_backfill_candidate_b64 = None
                self._vid_backfill_sought = False

                self._vid_backfill_player.setSourceDevice(
                    dev, QUrl(entry.original_name or "video.mp4")
                )
                self._vid_backfill_player.play()
                self._vid_backfill_timer.start(2500)
                return
            except Exception:
                self._close_video_backfill_device()
                self._vid_backfill_active_id = None
                continue

        if self._pending_thumb_saves and not self._cleaned_up:
            self._thumb_flush_timer.start(200)

    def _on_video_thumb_frame(self, frame) -> None:
        if self._cleaned_up or self._vid_backfill_active_id is None:
            return
        try:
            if not frame.isValid():
                return
            qimg = frame.toImage()
            if qimg.isNull():
                return
            b64 = qimage_to_thumbnail_b64(qimg)
            if not b64:
                return
            player = self._vid_backfill_player
            if (
                is_qimage_mostly_black(qimg)
                and not self._vid_backfill_sought
                and player is not None
                and player.duration() > 800
            ):
                self._vid_backfill_candidate_b64 = b64
                self._vid_backfill_sought = True
                player.setPosition(min(1500, player.duration() // 4))
                return

            self._vid_backfill_candidate_b64 = b64
            self._finish_current_video_thumb()
        except Exception:
            self._finish_current_video_thumb()

    def _on_video_thumb_error(self, *_args) -> None:
        if self._vid_backfill_active_id is not None:
            self._finish_current_video_thumb()

    def _close_video_backfill_device(self) -> None:
        if self._vid_backfill_player is not None:
            try:
                self._vid_backfill_player.stop()
                self._vid_backfill_player.setSource(QUrl())
                self._vid_backfill_player.setSourceDevice(None)
            except Exception:
                pass
        if self._vid_backfill_device is not None:
            try:
                self._vid_backfill_device.zero_and_close()
            except Exception:
                pass
            self._vid_backfill_device = None

    def _finish_current_video_thumb(self) -> None:
        if self._vid_backfill_timer.isActive():
            self._vid_backfill_timer.stop()
        fid = self._vid_backfill_active_id
        b64 = self._vid_backfill_candidate_b64
        self._vid_backfill_active_id = None
        self._vid_backfill_candidate_b64 = None
        self._vid_backfill_sought = False

        self._close_video_backfill_device()

        if fid:
            self._thumb_backfill_attempted.add(fid)
        if fid and b64 and not self._cleaned_up:
            self._on_backfill_thumb_ready(fid, b64)

        if not self._cleaned_up and self._vid_backfill_queue:
            QTimer.singleShot(15, self._process_next_video_thumb)
        elif not self._cleaned_up and self._pending_thumb_saves:
            self._thumb_flush_timer.start(200)

    def _on_backfill_thumb_ready(self, file_id: str, b64: str) -> None:
        if self._cleaned_up or not self._vault:
            return
        entry = self._vault._index.index.get(file_id)
        if entry is not None:
            entry.thumbnail_b64 = b64
        card = self._cards_by_id.get(file_id)
        if card is not None:
            card.set_thumbnail_b64(b64)
        self._pending_thumb_saves[file_id] = b64
        if len(self._pending_thumb_saves) >= 8:
            self._flush_pending_thumb_index()
        else:
            self._thumb_flush_timer.start(700)

    def _flush_pending_thumb_index(self) -> None:
        if not self._pending_thumb_saves or not self._vault or not get_session().is_unlocked:
            return
        if self._vault_service is not None:
            acquired = self._vault_service._lock.acquire(blocking=False)
            if not acquired:
                if not self._cleaned_up:
                    self._thumb_flush_timer.start(1000)
                return
            try:
                self._apply_and_save_pending_thumbs()
            finally:
                self._vault_service._lock.release()
        else:
            self._apply_and_save_pending_thumbs()

    def _apply_and_save_pending_thumbs(self) -> None:
        changed = False
        for fid, b64 in list(self._pending_thumb_saves.items()):
            entry = self._vault._index.index.get(fid)
            if entry is not None and entry.thumbnail_b64 != b64:
                entry.thumbnail_b64 = b64
                changed = True
            elif entry is not None and entry.thumbnail_b64 == b64:
                changed = True
        self._pending_thumb_saves.clear()
        if changed and get_session().is_unlocked:
            try:
                self._vault._save_index()
            except Exception as exc:
                logger.warning("Failed to save backfilled thumbnails: %s", type(exc).__name__)

    def _stop_thumbnail_backfill(self, flush_pending: bool = False) -> None:
        if flush_pending and self._pending_thumb_saves:
            self._flush_pending_thumb_index()
        if hasattr(self, "_vid_backfill_timer") and self._vid_backfill_timer.isActive():
            self._vid_backfill_timer.stop()
        if hasattr(self, "_thumb_flush_timer") and self._thumb_flush_timer.isActive():
            self._thumb_flush_timer.stop()
        self._vid_backfill_queue.clear()
        self._vid_backfill_active_id = None
        self._vid_backfill_candidate_b64 = None
        self._close_video_backfill_device()
        if self._vid_backfill_player is not None:
            try:
                self._vid_backfill_player.deleteLater()
            except Exception:
                pass
            self._vid_backfill_player = None
        if self._vid_backfill_sink is not None:
            try:
                self._vid_backfill_sink.deleteLater()
            except Exception:
                pass
            self._vid_backfill_sink = None
        if self._media_backfill_worker is not None:
            try:
                self._media_backfill_worker.requestInterruption()
                if self._media_backfill_worker.isRunning():
                    self._media_backfill_worker.wait(1500)
            except Exception:
                pass
            self._media_backfill_worker = None
        self._img_backfill_worker = None

    def _update_status(self) -> None:
        self._status_bar.showMessage(
            "Vault unlocked  •  Ctrl+Alt+L to lock  •  Protected by AES-256-GCM"
        )

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Cleanly shut down background threads, viewers, and unregister handlers."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        if hasattr(self, "_speed_banner") and hasattr(self._speed_banner, "stop_all"):
            self._speed_banner.stop_all()
        if hasattr(self, "_hotkey_banner") and hasattr(self._hotkey_banner, "stop_all"):
            self._hotkey_banner.stop_all()
        self._stop_thumbnail_backfill(flush_pending=True)
        if hasattr(self, "_grid_render_timer") and self._grid_render_timer.isActive():
            self._grid_render_timer.stop()
        if hasattr(self, "_resize_timer") and self._resize_timer.isActive():
            self._resize_timer.stop()
        if hasattr(self, "_search_timer") and self._search_timer.isActive():
            self._search_timer.stop()
        self._thumb_cache.clear()
        if self._active_overlay:
            try:
                self._active_overlay.accept()
            except Exception:
                pass
            self._active_overlay = None
        if self._load_worker is not None and self._load_worker.isRunning():
            self._load_worker.wait(2000)
        for w in list(self._action_workers):
            if w.isRunning():
                w.wait(1500)
        if self._applock_ui is not None and hasattr(self._applock_ui, "cleanup"):
            try:
                self._applock_ui.cleanup()
            except Exception:
                pass
        if self._vault_service:
            try:
                self._vault_service.shutdown()
            except Exception:
                pass
        reg_win = self._registered_top_window or self
        try:
            from ..system.win32_events import install_windows_security_filter
            install_windows_security_filter().unregister_window(reg_win)
        except Exception:
            pass
        try:
            from ..system.lock_controller import get_lock_controller
            get_lock_controller().unregister_pre_lock_hook(self._on_pre_lock)
            get_lock_controller().unregister_window(reg_win)
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        self.cleanup()
        try:
            from ..system.lock_controller import get_lock_controller
            if get_session().is_unlocked:
                get_lock_controller().lock("window_close")
        except Exception:
            pass
        event.accept()

