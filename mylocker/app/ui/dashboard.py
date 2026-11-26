"""
dashboard.py — Main vault window with collapsible sidebar navigation.

The window is hidden / shows nothing until session.is_unlocked is True.
Implements:
  - Collapsible 232px <-> 64px sidebar with sliding active indicator
  - Custom 36px title area + workspace toolbar (search, grid/list toggle, sort, add)
  - File grid / virtualized list view with encrypted index thumbnails
  - Drag and drop to add files
  - SetWindowDisplayAffinity for screenshot protection
  - Status bar showing lock state and inline task progress
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (
    QEvent,
    QPoint,
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
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QProgressBar,
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
from .components import (
    DangerButton,
    DialogShell,
    EmptyState,
    ErrorState,
    FlowLayout,
    PrimaryButton,
    SecondaryButton,
    SkeletonRow,
    Spinner,
    _apply_dialog_shadow,
)
from .dashboard_theme import (
    CategoryPill,
    MetricCard,
    SidebarProtectionStatus,
)
from .design_system import ThemePalette, Tokens
from .file_grid import FileCard, VaultFileListView, format_file_size
from .motion import crossfade_stack_page, dropdown_open, stop_all_animations
from .sidebar import SidebarWidget
from .toolbar import TitleAreaBar, WorkspaceToolbar
from .widgets import (
    ProgressOverlay,
    SidebarItem,
    SmoothScrollArea,
)

logger = logging.getLogger(__name__)

WDA_EXCLUDEFROMCAPTURE = 0x11
_GRID_BATCH_SIZE = 48
_VIRTUAL_LIST_THRESHOLD = 500


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

        from .login_theme import is_reduced_motion
        if is_reduced_motion():
            self._opacity.setOpacity(1.0)
            self._anim_mode = None
            self._remaining_ms = float(self._timeout_ms)
            self._countdown_started_ts = time.perf_counter()
            if not self._hovered and not self._paused_externally:
                self._countdown_timer.start()
            return

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
        from .login_theme import is_reduced_motion
        if is_reduced_motion():
            self.dismiss_immediately()
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
        self._view_mode = "grid"
        self._current_entries = []
        self._current_cols = 5
        self._thumb_cache: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._rendered_cards: list[FileCard] = []
        self._cards_by_id: dict[str, FileCard] = {}
        self._render_offset = 0
        self._selection_mode = False
        self._selected_ids: set[str] = set()
        self._last_selected_index: Optional[int] = None

        self._active_overlay: Optional[ProgressOverlay] = None
        self._status_progress: Optional[QProgressBar] = None
        self._last_hotkey_error: str = ""
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
        self.setMinimumSize(900, 600)
        self.resize(1200, 750)
        self.setAcceptDrops(True)

        self._build_ui()
        Tokens.subscribe(self._on_theme_changed)
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
        self._hotkey_connected = True
        lc.start_listeners()
        if not lc.is_hotkey_registered and lc.hotkey_error_message:
            self._on_hotkey_registration_failed(lc.hotkey_error_message)

        win_filter = install_windows_security_filter()
        win_filter.register_window(target)

    def _on_hotkey_registration_failed(self, msg: str) -> None:
        """Surfaces panic hotkey registration failure visibly in the dashboard."""
        self._last_hotkey_error = msg
        if hasattr(self, "_hotkey_lbl") and hasattr(self, "_hotkey_banner"):
            self._hotkey_lbl.setText(msg)
            self._hotkey_banner.show_animated()
        if hasattr(self, "_protection_status") and self._protection_status is not None:
            self._protection_status.setToolTip("Panic hotkey inactive (shortcut in use by another app)")
        if hasattr(self, "_notif_btn") and self._notif_btn is not None:
            self._notif_btn.setToolTip(f"Security Alert: {msg}")
            self._notif_btn.set_unread(True)

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

    def _on_theme_changed(self, _pal: ThemePalette) -> None:
        if self._cleaned_up:
            return
        self._apply_dashboard_theme()

    def _apply_dashboard_theme(self) -> None:
        p = Tokens.colors
        if hasattr(self, "_central") and self._central is not None:
            self._central.setStyleSheet(f"background-color: {p.bg};")
        if hasattr(self, "_content_widget") and self._content_widget is not None:
            self._content_widget.setStyleSheet(f"background-color: {p.bg};")
        if hasattr(self, "_files_widget") and self._files_widget is not None:
            self._files_widget.setStyleSheet(f"background-color: {p.bg};")
        if hasattr(self, "_status_bar") and self._status_bar is not None:
            self._status_bar.setStyleSheet(f"""
                QStatusBar {{
                    background-color: {p.bg};
                    color: {p.text_muted};
                    font-size: {Tokens.type.META}px;
                    border-top: 1px solid {p.border};
                    padding: 0 12px;
                }}
                QStatusBar::item {{
                    border: none;
                }}
            """)
        if hasattr(self, "_speed_banner") and self._speed_banner is not None:
            self._speed_banner.setStyleSheet(f"""
                #SpeedBanner {{
                    background-color: {p.accent_tint};
                    border: 1px solid {p.accent_border};
                    border-radius: {Tokens.radius.CARD}px;
                    padding: 8px 12px;
                }}
            """)
            self._speed_lbl.setStyleSheet(f"color: {p.text}; font-size: {Tokens.type.LABEL}px; background: transparent;")
        if hasattr(self, "_hotkey_banner") and self._hotkey_banner is not None:
            self._hotkey_banner.setStyleSheet(f"""
                #HotkeyBanner {{
                    background-color: {p.danger_tint};
                    border: 1px solid {p.danger_border};
                    border-radius: {Tokens.radius.CARD}px;
                    padding: 8px 12px;
                }}
            """)
            self._hotkey_lbl.setStyleSheet(f"color: {p.text}; font-size: {Tokens.type.LABEL}px; background: transparent;")
        if hasattr(self, "_selection_bar") and self._selection_bar is not None:
            self._selection_bar.setStyleSheet(f"""
                #SelectionBar {{
                    background-color: {p.accent_tint};
                    border: 1px solid {p.accent_border};
                    border-radius: {Tokens.radius.CARD}px;
                    padding: 6px 12px;
                }}
            """)
            self._sel_count_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")
        for card_stat in getattr(self, "_stat_cards", ()):
            card_stat._apply_palette()
        for pill in getattr(self, "_category_pills", ()):
            pill._update_appearance()
        if hasattr(self, "_protection_status") and self._protection_status is not None:
            self._protection_status.refresh_theme()
        for card in self._cards_by_id.values():
            card.setStyleSheet(card._card_qss())

    def _build_ui(self) -> None:
        self._central = QWidget()
        self.setCentralWidget(self._central)

        root = QHBoxLayout(self._central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar rail (232px <-> 64px collapsible) ──
        self._sidebar = self._build_sidebar()
        root.addWidget(self._sidebar)

        # ── Content area ──
        self._content_widget = QWidget()
        content_layout = QVBoxLayout(self._content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # 36px custom-feeling title area
        self._title_bar = TitleAreaBar(self._content_widget)
        self._title_bar.screenshot_toggled.connect(self._toggle_screenshot_protection)
        self._title_bar.notifications_clicked.connect(self._show_notifications_menu)
        self._page_title = self._title_bar.page_title_lbl
        self._ss_btn = self._title_bar.ss_btn
        self._notif_btn = self._title_bar.notif_btn
        self._vault_unlocked_badge = self._title_bar.unlocked_badge
        content_layout.addWidget(self._title_bar)

        # Page stack (indices 1..3 load lazily on first visit)
        self._page_stack = QStackedWidget()
        self._files_widget = self._build_files_page()
        self._page_stack.addWidget(self._files_widget)   # 0 — Files

        self._intruder_slot = QWidget()
        self._applock_slot = QWidget()
        self._settings_slot = QWidget()
        self._page_stack.addWidget(self._intruder_slot)   # 1 — Intruder Log (lazy)
        self._page_stack.addWidget(self._applock_slot)    # 2 — App Lock (lazy)
        self._page_stack.addWidget(self._settings_slot)   # 3 — Settings (lazy)

        content_layout.addWidget(self._page_stack, 1)
        root.addWidget(self._content_widget, 1)

        # ── Status bar with vault state & inline task progress ──
        self._status_bar = QStatusBar()
        self._status_progress = QProgressBar(self._status_bar)
        self._status_progress.setFixedSize(120, 6)
        self._status_progress.setTextVisible(False)
        self._status_progress.hide()
        self._status_bar.addPermanentWidget(self._status_progress)

        self._protection_status = SidebarProtectionStatus(self._status_bar)
        self._status_bar.addPermanentWidget(self._protection_status)
        self.setStatusBar(self._status_bar)

        self._apply_dashboard_theme()
        self._setup_tab_order()
        self._update_status()

    def _setup_tab_order(self) -> None:
        """Establish a logical keyboard Tab order across title bar, workspace toolbar, pills, and sidebar."""
        chain: list[QWidget] = [
            self._title_bar.ss_btn,
            self._title_bar.notif_btn,
            self._toolbar.search_bar._input,
            self._toolbar.btn_grid_mode,
            self._toolbar.btn_list_mode,
            self._toolbar.sort_combo,
            self._toolbar.select_btn,
            self._toolbar.add_btn,
            *self._category_pills,
            *self._sidebar.nav_items,
            self._sidebar.nav_bin,
            self._sidebar.lock_btn,
        ]
        visible_chain = [w for w in chain if w is not None]
        for a, b in zip(visible_chain, visible_chain[1:], strict=False):
            QWidget.setTabOrder(a, b)

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
            self._intruder_ui = self._build_placeholder("Intruder Log", "shield_alert")
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
            self._settings_ui = self._build_placeholder("Settings", "settings")
        self._replace_stack_page(3, self._settings_ui)

    def _build_sidebar(self) -> SidebarWidget:
        sb = SidebarWidget(is_decoy=self._session.is_decoy, parent=self)
        self._brand_widget = sb.brand_widget
        self._nav_items: list[SidebarItem] = sb.nav_items
        self._nav_bin: SidebarItem = sb.nav_bin

        sb.nav_clicked.connect(self._nav_to)
        sb.bin_clicked.connect(self._nav_to_bin)
        sb.lock_clicked.connect(self.lock_requested.emit)
        sb.collapsed_changed.connect(
            lambda _collapsed: QTimer.singleShot(Tokens.motion.NORMAL + 20, self._on_debounced_resize)
        )
        return sb

    def _build_files_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── 1. Workspace Toolbar (search, grid/list toggle, sort, select, + Add) ──
        self._toolbar = WorkspaceToolbar(page)
        self._search = self._toolbar.search_bar
        self._btn_grid_mode = self._toolbar.btn_grid_mode
        self._btn_list_mode = self._toolbar.btn_list_mode
        self._sort_combo = self._toolbar.sort_combo
        self._select_btn = self._toolbar.select_btn
        self._fab_add = self._toolbar.add_btn

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._do_search)
        self._toolbar.search_changed.connect(lambda _txt: self._search_timer.start(250))
        self._toolbar.view_mode_changed.connect(self._set_view_mode)
        self._toolbar.sort_changed.connect(self._on_sort_changed)
        self._toolbar.selection_toggled.connect(self._toggle_selection_mode)
        self._toolbar.add_clicked.connect(self._show_fab_menu)
        layout.addWidget(self._toolbar)

        # ── 2. Compact Summary Metrics Strip ──
        stats_row = QHBoxLayout()
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(12)

        p = Tokens.colors
        self._stat_total = MetricCard("folder", "0", "Total Files", p.accent, parent=page)
        self._stat_storage = MetricCard("database", "0 B", "Stored Data", p.accent, parent=page)
        self._stat_favs = MetricCard("star", "0", "Favorites", p.warning, parent=page)
        self._stat_protection = MetricCard("shield_check", "100%", "Vault Protected", p.success, parent=page)
        self._stat_cards = (self._stat_total, self._stat_storage, self._stat_favs, self._stat_protection)
        for mc in self._stat_cards:
            mc.setMinimumWidth(116)
            mc.setMinimumHeight(68)
            stats_row.addWidget(mc, 1)
        layout.addLayout(stats_row)

        # ── 3. Quick Category Filter Strip + Section Heading ──
        tabs_row = FlowLayout(spacing=8)

        self._pill_all = CategoryPill("grid", "All Files", active=True, parent=page)
        self._pill_photos = CategoryPill("image", "Photos", active=False, parent=page)
        self._pill_videos = CategoryPill("video", "Videos", active=False, parent=page)
        self._pill_docs = CategoryPill("file_text", "Documents", active=False, parent=page)
        self._pill_favs = CategoryPill("star", "Favorites", active=False, parent=page)
        self._category_pills = (
            self._pill_all,
            self._pill_photos,
            self._pill_videos,
            self._pill_docs,
            self._pill_favs,
        )

        self._pill_all.clicked.connect(lambda: self._select_category_pill("all"))
        self._pill_photos.clicked.connect(lambda: self._select_category_pill("photos"))
        self._pill_videos.clicked.connect(lambda: self._select_category_pill("videos"))
        self._pill_docs.clicked.connect(lambda: self._select_category_pill("documents"))
        self._pill_favs.clicked.connect(lambda: self._select_category_pill("favorites"))

        for pill in self._category_pills:
            tabs_row.addWidget(pill)

        self._section_title_lbl = QLabel("All Files", page)
        self._section_title_lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL))
        self._section_title_lbl.setProperty("role", "muted")
        self._section_title_lbl.hide()
        self._section_sub_lbl = QLabel("", page)
        self._section_sub_lbl.hide()
        layout.addLayout(tabs_row)

        # ── 4. Optional Banners & Multi-Select Bar ──
        self._speed_banner = _AutoDismissBanner(timeout_ms=5000)
        self._speed_banner.setObjectName("SpeedBanner")
        self._speed_banner.auto_dismissed.connect(lambda: setattr(self, "_speed_banner_dismissed", True))
        sb_lay = QHBoxLayout(self._speed_banner)
        sb_lay.setContentsMargins(6, 2, 6, 2)
        sb_lay.setSpacing(12)

        sb_icon = QLabel()
        sb_icon.setPixmap(icons.get_pixmap("shield_check", size=18, color=p.accent))
        sb_icon.setStyleSheet("background: transparent;")
        sb_lay.addWidget(sb_icon)

        self._speed_lbl = QLabel(
            "Slow unlock detected — optimize key derivation to ~0.25s with full OWASP-safe security."
        )
        self._speed_lbl.setWordWrap(True)
        sb_lay.addWidget(self._speed_lbl, 1)

        # Avoid QGraphicsDropShadowEffect inside animated opacity banner
        self._optimize_btn = PrimaryButton("Optimize Speed (~0.25s)", parent=self._speed_banner)
        self._optimize_btn.setMinimumHeight(32)
        self._optimize_btn.clicked.connect(self._open_optimize_speed_dialog)
        sb_lay.addWidget(self._optimize_btn)

        dismiss_btn = SecondaryButton("Dismiss", parent=self._speed_banner)
        dismiss_btn.setMinimumHeight(32)
        dismiss_btn.clicked.connect(lambda: self._dismiss_speed_banner(animate=True))
        sb_lay.addWidget(dismiss_btn)

        self._speed_banner.register_hover_targets()
        self._speed_banner.hide()
        layout.addWidget(self._speed_banner)

        self._hotkey_banner = _AutoDismissBanner(timeout_ms=7000)
        self._hotkey_banner.setObjectName("HotkeyBanner")
        hb_lay = QHBoxLayout(self._hotkey_banner)
        hb_lay.setContentsMargins(6, 2, 6, 2)
        hb_lay.setSpacing(12)

        hb_icon = QLabel()
        hb_icon.setPixmap(icons.get_pixmap("alert_triangle", size=18, color=p.danger))
        hb_icon.setStyleSheet("background: transparent;")
        hb_lay.addWidget(hb_icon)

        self._hotkey_lbl = QLabel("Panic hotkey (Ctrl+Alt+Shift+L) could not be registered.")
        self._hotkey_lbl.setWordWrap(True)
        hb_lay.addWidget(self._hotkey_lbl, 1)

        hb_dismiss_btn = SecondaryButton("Dismiss", parent=self._hotkey_banner)
        hb_dismiss_btn.setMinimumHeight(32)
        hb_dismiss_btn.clicked.connect(self._hotkey_banner.dismiss_animated)
        hb_lay.addWidget(hb_dismiss_btn)

        self._hotkey_banner.register_hover_targets()
        self._hotkey_banner.hide()
        layout.addWidget(self._hotkey_banner)

        self._selection_bar = QFrame(page)
        self._selection_bar.setObjectName("SelectionBar")
        sel_lay = QHBoxLayout(self._selection_bar)
        sel_lay.setContentsMargins(6, 2, 6, 2)
        sel_lay.setSpacing(8)

        self._sel_count_lbl = QLabel("0 selected", self._selection_bar)
        self._sel_count_lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_TITLE))
        sel_lay.addWidget(self._sel_count_lbl)
        sel_lay.addStretch()

        self._sel_all_btn = SecondaryButton("Select All", parent=self._selection_bar)
        self._sel_all_btn.setMinimumHeight(30)
        self._sel_all_btn.clicked.connect(self._select_all_visible)
        sel_lay.addWidget(self._sel_all_btn)

        self._sel_clear_btn = SecondaryButton("Clear", parent=self._selection_bar)
        self._sel_clear_btn.setMinimumHeight(30)
        self._sel_clear_btn.clicked.connect(lambda: self._clear_selection(exit_mode=False))
        sel_lay.addWidget(self._sel_clear_btn)

        self._sel_delete_btn = DangerButton("Delete Selected", icon_name="trash", parent=self._selection_bar)
        self._sel_delete_btn.setMinimumHeight(30)
        self._sel_delete_btn.setEnabled(False)
        self._sel_delete_btn.clicked.connect(self._delete_selected_files)
        sel_lay.addWidget(self._sel_delete_btn)

        self._sel_done_btn = SecondaryButton("Done", parent=self._selection_bar)
        self._sel_done_btn.setMinimumHeight(30)
        self._sel_done_btn.clicked.connect(lambda: self._toggle_selection_mode(False))
        sel_lay.addWidget(self._sel_done_btn)

        self._selection_bar.hide()
        layout.addWidget(self._selection_bar)

        # ── 5. Empty State, Error State, Grid & Virtualized List View ──
        self._drop_hint = EmptyState(
            "folder_plus",
            "No encrypted files here yet",
            "Drag files or folders here to encrypt them, or click Add above.",
            action_text="Add Files",
            parent=page,
        )
        self._drop_text = self._drop_hint._desc_lbl
        self._drop_hint.action_clicked.connect(self._show_fab_menu)

        self._error_state = ErrorState(
            "Could not load vault",
            "The encrypted vault index could not be read.",
            retry_text="Retry",
            parent=page,
        )
        self._error_state.retry_clicked.connect(lambda: self.load_files(initial=True))
        self._error_state.hide()

        self._loading_box = QWidget(page)
        load_lay = QVBoxLayout(self._loading_box)
        load_lay.setContentsMargins(0, 8, 0, 8)
        load_lay.setSpacing(10)
        load_hdr = QHBoxLayout()
        load_hdr.setSpacing(8)
        self._loading_spinner = Spinner(18, parent=self._loading_box)
        load_hdr.addWidget(self._loading_spinner)
        load_lbl = QLabel("Loading encrypted files…", self._loading_box)
        load_lbl.setProperty("role", "secondary")
        load_hdr.addWidget(load_lbl)
        load_hdr.addStretch()
        load_lay.addLayout(load_hdr)
        for _ in range(4):
            load_lay.addWidget(SkeletonRow(self._loading_box))
        load_lay.addStretch()
        self._loading_box.hide()

        self._scroll = SmoothScrollArea()
        self._scroll.setAcceptDrops(False)
        if self._scroll.viewport():
            self._scroll.viewport().setAcceptDrops(False)
        self._grid_container = QWidget()
        self._grid_container.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(self._grid_container)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(12)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._scroll.setWidget(self._grid_container)

        self._list_view = VaultFileListView(
            self._thumb_cache,
            selected_count_provider=lambda: len(self._selected_ids),
            parent=page,
        )
        self._list_view.open_requested.connect(self._open_file)
        self._list_view.restore_requested.connect(self._restore_file)
        self._list_view.delete_requested.connect(self._delete_file)
        self._list_view.favorite_toggled.connect(self._toggle_favorite)
        self._list_view.selection_requested.connect(self._handle_card_selection)
        self._list_view.select_toggle_requested.connect(self._on_card_select_toggled)
        self._list_view.batch_delete_requested.connect(self._delete_selected_files)
        self._list_view.hide()

        layout.addWidget(self._loading_box, 1)
        layout.addWidget(self._error_state)
        layout.addWidget(self._drop_hint, 1)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._list_view, 1)

        self._drop_hint.show()
        self._scroll.hide()

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
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(
            EmptyState(
                "folder",
                title,
                "Open vault and navigate to this section.",
                parent=page,
            )
        )
        return page

    def _build_favorites_page(self) -> QWidget:
        return self._build_category_page("", "Favorites")

    def _build_placeholder(self, name: str, icon_name: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        ico = icon_name if icon_name.isascii() else "folder"
        layout.addWidget(
            EmptyState(
                ico,
                name,
                f"{name} is unavailable for this session.",
                parent=page,
            )
        )
        return page

    # ── Navigation & view mode ────────────────────────────────────────────────

    def _show_notifications_menu(self) -> None:
        if hasattr(self, "_notif_btn") and self._notif_btn is not None:
            self._notif_btn.set_unread(False)
        msg = (
            self._last_hotkey_error
            if self._last_hotkey_error
            else "All vault protections are active. Zero pending security alerts."
        )
        DialogShell.inform(self, "Security Status", msg)

    def _set_view_mode(self, mode: str) -> None:
        target = "list" if mode == "list" else "grid"
        if target == self._view_mode:
            return
        self._view_mode = target
        if hasattr(self, "_toolbar"):
            self._toolbar.view_switch.set_current(target, emit_signal=False)
        self._relayout_grid()

    def _unselect_all_nav(self) -> None:
        if hasattr(self, "_sidebar") and self._sidebar is not None:
            self._sidebar.set_active_item(None, animate=False)

    def _select_category_pill(self, cat: str) -> None:
        if hasattr(self, "_pill_all"):
            self._pill_all.set_active(cat == "all")
            self._pill_photos.set_active(cat == "photos")
            self._pill_videos.set_active(cat == "videos")
            self._pill_docs.set_active(cat == "documents")
            self._pill_favs.set_active(cat == "favorites")

        self._current_section = cat

        titles = {
            "all": ("All Files", "Your encrypted files.", "All Files"),
            "photos": ("Photos", "Your encrypted photos and images.", "Photos"),
            "videos": ("Videos", "Your private video clips.", "Videos"),
            "documents": ("Documents", "Your documents, spreadsheets and archives.", "Documents"),
            "favorites": ("Favorites", "Your starred and favorite items.", "Favorites"),
            "bin": ("Bin", "Deleted files in your vault.", "Bin"),
        }
        title, sub, page_title = titles.get(cat, ("All Files", "Your encrypted files.", "All Files"))
        if hasattr(self, "_section_title_lbl"):
            self._section_title_lbl.setText(title)
            self._section_sub_lbl.setText(sub)
        if hasattr(self, "_page_title"):
            self._page_title.setText(page_title)

        cat_to_nav_idx = {"all": 0, "photos": 1, "videos": 2, "documents": 3, "favorites": 4}
        if hasattr(self, "_sidebar") and self._sidebar is not None:
            if cat == "bin":
                self._sidebar.set_active_item(self._nav_bin)
            elif cat in cat_to_nav_idx and cat_to_nav_idx[cat] < len(self._nav_items):
                self._sidebar.set_active_item(self._nav_items[cat_to_nav_idx[cat]])

        if hasattr(self, "_page_stack"):
            crossfade_stack_page(self._page_stack, 0)
        self.load_files()

    def _nav_to_dashboard(self) -> None:
        self._select_category_pill("all")

    def _nav_to_bin(self) -> None:
        self._select_category_pill("bin")

    def _nav_to(self, page: int, item: SidebarItem) -> None:
        if hasattr(self, "_sidebar") and self._sidebar is not None:
            self._sidebar.set_active_item(item)

        titles = [
            "All Files",
            "Photos",
            "Videos",
            "Documents",
            "Favorites",
            "Intruder Log",
            "App Lock",
            "Settings",
        ]
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
            crossfade_stack_page(self._page_stack, 1)
            if hasattr(self._intruder_ui, "refresh_log"):
                self._intruder_ui.refresh_log()
        elif page == 6:
            if self._selection_mode or self._selected_ids:
                self._clear_selection(exit_mode=True)
            self._ensure_applock_ui()
            crossfade_stack_page(self._page_stack, 2)
        elif page == 7:
            if self._selection_mode or self._selected_ids:
                self._clear_selection(exit_mode=True)
            self._ensure_settings_ui()
            crossfade_stack_page(self._page_stack, 3)

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
        p = Tokens.colors
        menu = QMenu(self)
        _apply_dialog_shadow(menu)
        act_files = menu.addAction("Add Files…")
        act_folder = menu.addAction("Add Folder…")
        act_files.setIcon(icons.get_icon("plus", color=p.text, active_color=p.accent, size=16))
        act_folder.setIcon(icons.get_icon("folder_plus", color=p.text, active_color=p.accent, size=16))
        act_files.triggered.connect(self._add_files_dialog)
        act_folder.triggered.connect(self._add_folder_dialog)

        pos = self._fab_add.mapToGlobal(QPoint(0, self._fab_add.height() + 6))
        dropdown_open(menu)
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

    def _set_status_busy(self, busy: bool, current: int = 0, total: int = 0) -> None:
        bar = getattr(self, "_status_progress", None)
        if bar is None:
            return
        if not busy:
            bar.hide()
            return
        if total > 0:
            bar.setRange(0, total)
            bar.setValue(min(current, total))
        else:
            bar.setRange(0, 0)
        bar.show()

    def _set_loading_state(self, loading: bool) -> None:
        if not hasattr(self, "_loading_box"):
            return
        if loading:
            self._loading_box.show()
            if hasattr(self, "_loading_spinner"):
                self._loading_spinner.start()
            if hasattr(self, "_drop_hint"):
                self._drop_hint.hide()
            if hasattr(self, "_error_state"):
                self._error_state.hide()
            if hasattr(self, "_scroll"):
                self._scroll.hide()
            if hasattr(self, "_list_view"):
                self._list_view.hide()
        else:
            if hasattr(self, "_loading_spinner"):
                self._loading_spinner.stop()
            self._loading_box.hide()

    def load_files(self, initial: bool = False) -> None:
        """Call after unlock to populate the file grid synchronously (used by tests & refreshes)."""
        if not self._vault:
            return
        self._set_loading_state(False)
        if hasattr(self, "_error_state"):
            self._error_state.hide()
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
            if hasattr(self, "_error_state"):
                self._error_state.set_error("Could not load vault", "The vault index could not be read [ERR-IDX-01].")
                self._error_state.show()
                if hasattr(self, "_drop_hint"):
                    self._drop_hint.hide()

    def load_files_async(self, initial: bool = True, unlock_elapsed_s: float = 0.0) -> None:
        """Non-blocking file index load after unlock so Dashboard transitions without freezing."""
        if not self._vault or self._cleaned_up:
            return
        if hasattr(self, "_error_state"):
            self._error_state.hide()

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
        self._set_status_busy(True, 0, 0)
        do_initial = bool(initial or not getattr(self, "_files_loaded", False))
        if do_initial:
            self._set_loading_state(True)
        worker = _VaultLoadWorker(self._vault, self._vault_service, do_initial)
        self._load_worker = worker

        def _on_loaded(idx: object) -> None:
            self._load_worker = None
            worker.deleteLater()
            self._set_status_busy(False)
            self._set_loading_state(False)
            if self._cleaned_up or not getattr(self._session, "is_unlocked", False):
                return
            self._files_loaded = True
            self._apply_loaded_index(idx)
            self._update_status()
            self.files_loaded.emit()

        def _on_corrupt() -> None:
            self._load_worker = None
            worker.deleteLater()
            self._set_status_busy(False)
            self._set_loading_state(False)
            if not self._cleaned_up and getattr(self._session, "is_unlocked", False):
                self._handle_corrupt_index()

        def _on_failed(_err: str) -> None:
            self._load_worker = None
            worker.deleteLater()
            self._set_status_busy(False)
            self._set_loading_state(False)
            if not self._cleaned_up:
                self._status_bar.showMessage("Could not load vault index [ERR-IDX-01]", 5000)
                if hasattr(self, "_error_state"):
                    self._error_state.set_error(
                        "Could not load vault",
                        "The encrypted vault index could not be verified [ERR-IDX-01].",
                    )
                    self._error_state.show()
                    if hasattr(self, "_drop_hint"):
                        self._drop_hint.hide()

        worker.loaded.connect(_on_loaded, Qt.ConnectionType.QueuedConnection)
        worker.corrupt.connect(_on_corrupt, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(_on_failed, Qt.ConnectionType.QueuedConnection)
        worker.start()

    def _handle_corrupt_index(self) -> None:
        """Offer recovery from index.bak1-3 when primary index.dat is corrupted."""
        backups = [self._vault.vault_dir / f"index.bak{i}" for i in range(1, 4)]
        available = [b for b in backups if b.exists()]
        if not available:
            DialogShell.error(
                self,
                "Vault Index Corrupted",
                "The vault index could not be verified and no backups were found. [ERR-IDX-02]",
            )
            return

        names = [b.name for b in available]
        choice, ok = DialogShell.choose_item(
            self,
            "Recover Vault Index",
            "Primary index.dat is corrupted. Choose an encrypted backup to restore:",
            names,
            confirm_text="Restore Backup",
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
                DialogShell.inform(self, "Recovered", f"Vault index successfully recovered from {choice}.")
            except Exception:
                DialogShell.error(
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

    def _use_virtual_list_view(self) -> bool:
        return self._view_mode == "list" or len(self._current_entries) > _VIRTUAL_LIST_THRESHOLD

    def _grid_col_count(self) -> int:
        vp = self._scroll.viewport()
        avail = vp.width() if (vp and vp.width() > 100) else max(240, self._scroll.width())
        return max(1, (avail + 12) // (FileCard.CARD_WIDTH + 12))

    def _relayout_grid(self) -> None:
        if hasattr(self, "_grid_render_timer") and self._grid_render_timer.isActive():
            self._grid_render_timer.stop()
        if hasattr(self, "_error_state"):
            self._error_state.hide()

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
                if hasattr(self, "_drop_hint"):
                    search_text = self._search.text().strip() if hasattr(self, "_search") else ""
                    if search_text:
                        self._drop_hint.set_content(
                            "No matching files",
                            "No encrypted files match your search.",
                            action_text="",
                        )
                    elif self._current_section == "photos":
                        self._drop_hint.set_content(
                            "No photos yet",
                            "Drag images here or click Add above to encrypt photos.",
                            action_text="Add Files",
                        )
                    elif self._current_section == "videos":
                        self._drop_hint.set_content(
                            "No videos yet",
                            "Drag video clips here or click Add above to encrypt them.",
                            action_text="Add Files",
                        )
                    elif self._current_section == "documents":
                        self._drop_hint.set_content(
                            "No documents yet",
                            "Drag documents or archives here or click Add above.",
                            action_text="Add Files",
                        )
                    elif self._current_section == "favorites":
                        self._drop_hint.set_content(
                            "No favorites yet",
                            "Right-click any file and choose 'Add to Favorites' to pin it here.",
                            action_text="",
                        )
                    elif self._current_section == "bin":
                        self._drop_hint.set_content(
                            "Bin is empty",
                            "Deleted items will appear here when removed.",
                            action_text="",
                        )
                    else:
                        self._drop_hint.set_content(
                            "No encrypted files here yet",
                            "Drag files or folders here to encrypt them, or click Add above.",
                            action_text="Add Files",
                        )
                self._drop_hint.show()
                self._scroll.hide()
                if hasattr(self, "_list_view"):
                    self._list_view.hide()
                    self._list_view.set_data([], self._selected_ids, self._selection_mode)
                self._sync_selection_ui()
                return

            self._drop_hint.hide()
            if self._use_virtual_list_view() and hasattr(self, "_list_view"):
                self._scroll.hide()
                self._list_view.show()
                self._list_view.set_data(self._current_entries, self._selected_ids, self._selection_mode)
                self._sync_selection_ui()
                return

            if hasattr(self, "_list_view"):
                self._list_view.hide()
            self._scroll.show()
            self._current_cols = self._grid_col_count()
        finally:
            self._grid_container.setUpdatesEnabled(True)

        self._render_next_grid_batch()
        self._sync_selection_ui()

    def _on_debounced_resize(self) -> None:
        if self._cleaned_up or not (hasattr(self, "_scroll") and hasattr(self, "_grid") and self._current_entries):
            return
        if self._use_virtual_list_view():
            return
        col_count = self._grid_col_count()
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
        if hasattr(self, "_list_view") and self._list_view.isVisible():
            self._list_view.update_selection(self._selected_ids, self._selection_mode)

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
        confirmed = DialogShell.confirm(
            self,
            "Delete Selected Files",
            f"Permanently remove {n} selected file(s) from the vault?\n\nThis cannot be undone.",
            confirm_text=f"Delete ({n})",
            danger=True,
        )
        if not confirmed:
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
                DialogShell.error(self, "Error", "Failed to delete selected files. [ERR-DEL-02]")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_resize_timer"):
            self._resize_timer.start()

    # ── Service callbacks ─────────────────────────────────────────────────────

    def _on_service_started(self, task_id: str, task_desc: str) -> None:
        self._set_status_busy(True, 0, 0)
        if not self._active_overlay:
            self._active_overlay = ProgressOverlay(task_desc or "Working…", self)
            if self._vault_service:
                self._active_overlay.cancelled.connect(self._vault_service.cancel_current)
            self._active_overlay.show()

    def _on_service_progress(self, current: int, total: int, step: str) -> None:
        self._set_status_busy(True, current, max(1, total))
        if self._active_overlay:
            self._active_overlay.set_progress(current, total, step)

    def _on_service_file_done(self, file_id: str, result_meta: object) -> None:
        if self._active_overlay is None:
            self._status_bar.showMessage("Vault operation step completed.", 2500)

    def _on_service_finished(self, task_id: str, success: bool, message: str) -> None:
        self._set_status_busy(False)
        if self._active_overlay:
            self._active_overlay.accept()
            self._active_overlay = None
        self.load_files()
        self._status_bar.showMessage(message or "Vault task completed.", 4000)

    def _on_service_error(self, task_id: str, err_code: str, error_msg: str) -> None:
        self._set_status_busy(False)
        if self._active_overlay:
            self._active_overlay.accept()
            self._active_overlay = None
        DialogShell.error(self, "Vault Error", f"Operation failed [{err_code}]:\n{error_msg}")

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
        confirmed = DialogShell.confirm(
            self,
            "Delete from Vault",
            "Permanently remove this file from the vault?\n\nThis cannot be undone.",
            confirm_text="Delete",
            danger=True,
        )
        if confirmed:
            self._stop_thumbnail_backfill()
            if self._vault_service:
                self._vault_service.delete_file(file_id)
            else:
                try:
                    self._vault.delete_from_vault(file_id)
                    self.load_files()
                except Exception:
                    DialogShell.error(self, "Error", "Failed to delete file from vault. [ERR-DEL-01]")

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
                DialogShell.error(self, "Error", "Could not open image in viewer. [ERR-GAL-01]")

        def _on_err(_err: str) -> None:
            self._opening_file = False
            self._update_status()
            if not self._cleaned_up:
                DialogShell.error(self, "Error", "Could not open image in viewer. [ERR-GAL-01]")

        self._run_action_worker(_load_bytes, _on_ok, _on_err)

    def _open_in_player(self, file_id: str) -> None:
        try:
            from .video_player import VideoPlayer
            player = VideoPlayer(file_id, self._vault, self)
            player.favorite_changed.connect(lambda _fid, _fav: self.load_files())
            player.exec()
        except Exception as exc:
            logger.error("Video player open failed: %s", type(exc).__name__)
            DialogShell.error(self, "Error", "Could not open video in player. [ERR-VID-01]")

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
                DialogShell.warn(self, "Cannot Open File", "Unable to open this file with its default application. [ERR-EXT-01]")

        def _on_err(_err: str) -> None:
            self._opening_file = False
            self._update_status()
            if not self._cleaned_up:
                DialogShell.error(self, "Error", "Failed to open external file. [ERR-EXT-02]")

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
            confirmed = DialogShell.confirm(
                self,
                "File Modified Externally",
                f"Changes detected in '{name}'.\n\nWould you like to save the updated version back to your vault?",
                confirm_text="Save Changes",
            )
            if confirmed and file_path.exists():
                try:
                    if self._vault_service:
                        with self._vault_service._lock:
                            self._vault.update_file(file_id, file_path)
                    else:
                        self._vault.update_file(file_id, file_path)
                    self.load_files()
                    DialogShell.inform(self, "Updated", f"'{name}' successfully updated in vault.")
                except Exception:
                    DialogShell.warn(self, "Save Failed", "Could not update file in vault. [ERR-UPD-01]")
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
            p = Tokens.colors
            self._drop_hint.setStyleSheet(f"""
                #EmptyStateCard {{
                    background-color: {p.accent_tint};
                    border: 2px dashed {p.accent};
                    border-radius: {Tokens.radius.CARD}px;
                }}
            """)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        if hasattr(self._drop_hint, "_apply_theme"):
            self._drop_hint._apply_theme()

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

    def _focus_search(self) -> None:
        if self._page_stack.currentIndex() != 0 and self._nav_items:
            self._nav_to(0, self._nav_items[0])
        self._search.setFocus()

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
        if hasattr(self, "_title_bar") and self._title_bar is not None:
            self._title_bar.update_screenshot_state(self._screenshot_blocked)

    # ── Lock shortcut ─────────────────────────────────────────────────────────

    def _setup_lock_shortcut(self) -> None:
        from PySide6.QtGui import QShortcut
        sc = QShortcut(QKeySequence("Ctrl+Alt+L"), self)
        sc.activated.connect(self.lock_requested.emit)

        search_sc = QShortcut(QKeySequence("Ctrl+F"), self)
        search_sc.activated.connect(self._focus_search)

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
        if hasattr(self, "_list_view"):
            self._list_view.file_model.notify_entry_updated(file_id)
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
        n = len(self._current_entries)
        noun = "file" if n == 1 else "files"
        self._status_bar.showMessage(
            f"Vault unlocked · {n} {noun} · Ctrl+Alt+L to lock"
        )

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Cleanly shut down background threads, viewers, and unregister handlers."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        stop_all_animations(self)
        Tokens.unsubscribe(self._on_theme_changed)
        if getattr(self, "_hotkey_connected", False):
            self._hotkey_connected = False
            try:
                from ..system.lock_controller import get_lock_controller
                lc = get_lock_controller()
                lc.hotkey_registration_failed.disconnect(self._on_hotkey_registration_failed)
            except Exception:
                pass
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

