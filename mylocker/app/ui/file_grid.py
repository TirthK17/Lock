"""
file_grid.py — FileCard tiles, context menu, virtualized FileListModel / QListView,
and thumbnail decoding helpers for Personal Vault.

Preserves all FileCard signals, attributes (`_select_btn`, `_video_play_badge`,
objectNames `#FileCard`, `#FileThumb`, `#FileName`, `#FileSize`, `#MoreBtn`,
`#SelectBadge`), zero graphics effects (`graphicsEffect() is None`), and
hover highlight by color only.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..core.vault import infer_mime_type
from . import icons
from .components import _apply_dialog_shadow
from .dashboard_theme import DocBadgeGenerator, MediaBadgeGenerator
from .design_system import Tokens
from .widgets import AnimatedCard


def format_file_size(b: int) -> str:
    """Format byte counts into human-readable strings (e.g. '12.6 GB', '4.2 MB')."""
    val = float(b)
    for u in ["B", "KB", "MB", "GB"]:
        if val < 1024:
            return f"{int(val)} {u}" if u == "B" else f"{val:.1f} {u}"
        val /= 1024
    return f"{val:.1f} TB"


def infer_category_name(filename: str, mime_type: Optional[str]) -> str:
    mime = infer_mime_type(filename, mime_type)
    ext = Path(filename).suffix.lower()
    if mime.startswith("image/"):
        return "Image"
    if mime.startswith("video/"):
        return "Video"
    if mime.startswith("audio/"):
        return "Audio"
    if ext in (".pdf", ".doc", ".docx", ".rtf", ".odt", ".txt", ".md"):
        return "Document"
    if ext in (".xls", ".xlsx", ".csv", ".tsv"):
        return "Spreadsheet"
    if ext in (".zip", ".rar", ".7z", ".tar", ".gz"):
        return "Archive"
    if ext in (".py", ".js", ".ts", ".html", ".css", ".cpp", ".json"):
        return "Code"
    return "File"


def clip_to_rounded(pix: QPixmap, radius: int) -> QPixmap:
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


def build_file_context_menu(
    parent: QWidget,
    entry,
    *,
    selected: bool,
    selected_count: int,
    on_open: Callable[[str], None],
    on_restore: Callable[[str], None],
    on_favorite: Callable[[str, bool], None],
    on_toggle_select: Callable[[str], None],
    on_batch_delete: Callable[[], None],
    on_delete: Callable[[str], None],
) -> QMenu:
    """Create a calm, theme-consistent context menu with single soft drop shadow."""
    p = Tokens.colors
    menu = QMenu(parent)
    _apply_dialog_shadow(menu)

    menu.addAction(icons.get_icon("eye", p.text, p.accent, 16), "Open / View", lambda: on_open(entry.file_id))
    menu.addAction(icons.get_icon("download", p.text, p.accent, 16), "Restore / Export", lambda: on_restore(entry.file_id))

    fav_label = "Remove Favorite" if entry.is_favorite else "Add to Favorites"
    fav_icon = "star_filled" if entry.is_favorite else "star"
    menu.addAction(
        icons.get_icon(fav_icon, p.warning, p.warning, 16),
        fav_label,
        lambda: on_favorite(entry.file_id, not entry.is_favorite),
    )

    sel_label = "Deselect" if selected else "Select Multiple"
    menu.addAction(
        icons.get_icon("check", p.accent, p.accent, 16),
        sel_label,
        lambda: on_toggle_select(entry.file_id),
    )
    menu.addSeparator()
    if selected_count > 1 or (selected and selected_count >= 1):
        menu.addAction(
            icons.get_icon("trash", p.danger, p.danger, 16),
            f"Delete Selected ({selected_count})",
            on_batch_delete,
        )
    menu.addAction(
        icons.get_icon("trash", p.danger, p.danger, 16),
        "Delete from Vault",
        lambda: on_delete(entry.file_id),
    )
    return menu


# ─── Grid Tile Card ───────────────────────────────────────────────────────────


class FileCard(AnimatedCard):
    """Minimal 12px-radius tile showing thumbnail, middle-elided name, size/type, and check."""

    open_requested = Signal(str)
    restore_requested = Signal(str)
    delete_requested = Signal(str)
    favorite_toggled = Signal(str, bool)
    selection_requested = Signal(str, object)
    select_toggle_requested = Signal(str)
    batch_delete_requested = Signal()

    CARD_WIDTH = 176
    CARD_HEIGHT = 132
    THUMB_WIDTH = 160
    THUMB_HEIGHT = 76

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

    def _card_qss(self) -> str:
        p = Tokens.colors
        return f"""
            #FileCard {{
                background-color: {p.card};
                border: 1px solid {p.border};
                border-radius: {Tokens.radius.CARD}px;
            }}
            #FileCard:hover {{
                border: 1px solid {p.border_strong};
                background-color: {p.elevated};
            }}
            #FileCard[selected="true"] {{
                border: 2px solid {p.accent};
                background-color: {p.accent_tint};
            }}
            #FileThumb {{
                background-color: {p.bg_secondary};
                border-radius: {Tokens.radius.CONTROL}px;
                color: {p.text_muted};
            }}
            #FileName {{
                color: {p.text};
                background: transparent;
                font-weight: 500;
                font-size: {Tokens.type.LABEL}px;
            }}
            #FileName[favorite="true"] {{
                color: {p.warning};
            }}
            #FileSize {{
                color: {p.text_muted};
                background: transparent;
                font-size: {Tokens.type.META}px;
            }}
            #MoreBtn {{
                background: transparent;
                border: none;
                border-radius: 6px;
                min-height: 22px;
                padding: 0;
            }}
            #MoreBtn:hover {{
                background: {p.hover_fill};
            }}
            #SelectBadge {{
                background-color: {p.overlay};
                border: 1px solid {p.border_strong};
                border-radius: 6px;
                min-height: 20px;
                padding: 0;
            }}
            #SelectBadge:hover {{
                border: 1px solid {p.accent};
            }}
            #SelectBadge[selected="true"] {{
                background-color: {p.accent};
                border: 1px solid {p.accent};
            }}
        """

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.setFixedSize(self.CARD_WIDTH, self.CARD_HEIGHT)

        cat = infer_category_name(self._entry.original_name, self._entry.mime_type)
        size_str = format_file_size(self._entry.size_bytes)
        self.setToolTip(f"{self._entry.original_name}\n{size_str} · {cat}")
        self.setAccessibleName(f"{self._entry.original_name}, {size_str}, {cat}")
        self.setStyleSheet(self._card_qss())

        self._thumb_container = QWidget(self)
        self._thumb_container.setFixedSize(self.THUMB_WIDTH, self.THUMB_HEIGHT)
        thumb_lay = QVBoxLayout(self._thumb_container)
        thumb_lay.setContentsMargins(0, 0, 0, 0)

        self._thumb = QLabel(self._thumb_container)
        self._thumb.setObjectName("FileThumb")
        self._thumb.setFixedSize(self.THUMB_WIDTH, self.THUMB_HEIGHT)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        thumb_lay.addWidget(self._thumb)
        layout.addWidget(self._thumb_container)

        # Video badges
        self._video_play_badge = QLabel(self)
        self._video_play_badge.setFixedSize(28, 28)
        self._video_play_badge.move(74, 32)
        self._video_play_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_play_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._video_play_badge.setStyleSheet(
            f"background: {Tokens.colors.overlay}; border: 1px solid {Tokens.colors.border_strong}; border-radius: 14px;"
        )
        self._video_play_badge.setPixmap(icons.get_pixmap("play", Tokens.colors.text, size=14))
        self._video_play_badge.hide()

        self._video_duration_badge = QLabel(self)
        self._video_duration_badge.setFixedSize(36, 16)
        self._video_duration_badge.move(126, 62)
        self._video_duration_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_duration_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._video_duration_badge.setStyleSheet(
            f"background: {Tokens.colors.overlay}; color: {Tokens.colors.text}; "
            f"font-size: {Tokens.type.META}px; border-radius: 4px; padding: 0 3px;"
        )
        self._video_duration_badge.setText("Video")
        self._video_duration_badge.hide()

        self._render_thumbnail(self._entry.thumbnail_b64)

        # Bottom info row
        bottom_widget = QWidget(self)
        bottom_widget.setStyleSheet("background: transparent;")
        bottom_lay = QHBoxLayout(bottom_widget)
        bottom_lay.setContentsMargins(2, 0, 0, 0)
        bottom_lay.setSpacing(4)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)

        prefix = "★ " if self._entry.is_favorite else ""
        name_font = Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL)
        fm = QFontMetrics(name_font)
        elided_name = fm.elidedText(prefix + self._entry.original_name, Qt.TextElideMode.ElideMiddle, 130)

        self._name_lbl = QLabel(elided_name, bottom_widget)
        self._name_lbl.setObjectName("FileName")
        self._name_lbl.setProperty("favorite", "true" if self._entry.is_favorite else "false")
        self._name_lbl.setFont(name_font)
        self._name_lbl.setToolTip(self._entry.original_name)
        self._name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_col.addWidget(self._name_lbl)

        size_lbl = QLabel(f"{size_str} · {cat}", bottom_widget)
        size_lbl.setObjectName("FileSize")
        size_lbl.setFont(Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_BODY))
        size_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_col.addWidget(size_lbl)

        bottom_lay.addLayout(text_col, 1)

        self._more_btn = QPushButton(bottom_widget)
        self._more_btn.setObjectName("MoreBtn")
        self._more_btn.setFixedSize(22, 22)
        self._more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more_btn.setToolTip("File actions")
        self._more_btn.setAccessibleName(f"Actions for {self._entry.original_name}")
        self._more_btn.setIcon(icons.get_icon("more_vertical", color=Tokens.colors.text_muted, size=14))
        self._more_btn.clicked.connect(lambda: self._show_context(self._more_btn.mapTo(self, QPoint(0, 22))))
        bottom_lay.addWidget(self._more_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(bottom_widget)

        self._select_btn = QPushButton(self)
        self._select_btn.setObjectName("SelectBadge")
        self._select_btn.setFixedSize(20, 20)
        self._select_btn.move(self.CARD_WIDTH - 30, 12)
        self._select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._select_btn.setToolTip("Select / Deselect file")
        self._select_btn.setAccessibleName(f"Select {self._entry.original_name}")
        self._select_btn.clicked.connect(lambda: self.select_toggle_requested.emit(self._entry.file_id))
        self._select_btn.raise_()
        self._apply_selection_visuals(force=True)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context)

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
                if isinstance(self._thumb_cache, OrderedDict):
                    self._thumb_cache.move_to_end(cache_key)
                self._thumb.setPixmap(cached)
                loaded_pixmap = True
            else:
                try:
                    raw = base64.b64decode(thumb_b64)
                    px = QPixmap()
                    if px.loadFromData(raw):
                        scaled = px.scaled(
                            self.THUMB_WIDTH,
                            self.THUMB_HEIGHT,
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        cw = max(0, (scaled.width() - self.THUMB_WIDTH) // 2)
                        ch = max(0, (scaled.height() - self.THUMB_HEIGHT) // 2)
                        cropped = scaled.copy(cw, ch, self.THUMB_WIDTH, self.THUMB_HEIGHT)
                        rounded = clip_to_rounded(cropped, 8)
                        if self._thumb_cache is not None:
                            if isinstance(self._thumb_cache, OrderedDict):
                                self._thumb_cache[cache_key] = rounded
                                self._thumb_cache.move_to_end(cache_key)
                                while len(self._thumb_cache) > 512:
                                    self._thumb_cache.popitem(last=False)
                            else:
                                if len(self._thumb_cache) > 512:
                                    self._thumb_cache.clear()
                                self._thumb_cache[cache_key] = rounded
                        self._thumb.setPixmap(rounded)
                        loaded_pixmap = True
                except Exception:
                    pass

        if not loaded_pixmap:
            if mime.startswith("image/"):
                badge_pix = MediaBadgeGenerator.get_image_placeholder(self.THUMB_WIDTH, self.THUMB_HEIGHT)
            elif mime.startswith("video/"):
                badge_pix = MediaBadgeGenerator.get_video_placeholder(self.THUMB_WIDTH, self.THUMB_HEIGHT)
            else:
                badge_pix = DocBadgeGenerator.get_badge(ext, self.THUMB_WIDTH, self.THUMB_HEIGHT)
            self._thumb.setPixmap(badge_pix)

        is_video = mime.startswith("video/")
        if hasattr(self, "_video_play_badge"):
            self._video_play_badge.setVisible(is_video)
            if is_video:
                self._video_play_badge.raise_()
        if hasattr(self, "_video_duration_badge"):
            self._video_duration_badge.setVisible(False)
        if hasattr(self, "_select_btn"):
            self._select_btn.raise_()

    def set_thumbnail_b64(self, thumb_b64: str) -> None:
        self._entry.thumbnail_b64 = thumb_b64
        self._render_thumbnail(thumb_b64)

    def refresh_theme(self) -> None:
        """Update card styles and vector badges when global Dark/Light theme changes."""
        p = Tokens.colors
        self.setStyleSheet(self._card_qss())
        if hasattr(self, "_more_btn"):
            self._more_btn.setIcon(icons.get_icon("more_vertical", color=p.text_muted, size=14))
        if hasattr(self, "_video_play_badge"):
            self._video_play_badge.setStyleSheet(
                f"background: {p.overlay}; border: 1px solid {p.border_strong}; border-radius: 14px;"
            )
            self._video_play_badge.setPixmap(icons.get_pixmap("play", p.text, size=14))
        if not self._entry.thumbnail_b64:
            self._render_thumbnail(None)
        self._apply_selection_visuals(force=True)

    def _apply_selection_visuals(self, force: bool = False) -> None:
        sel_val = "true" if self._selected else "false"
        if force or self.property("selected") != sel_val:
            self.setProperty("selected", sel_val)
            self._select_btn.setProperty("selected", sel_val)
            if self._selected:
                col = Tokens.colors.on_accent
                self._select_btn.setIcon(icons.get_icon("check", col, col, size=14))
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
        sel_count = self._selected_count_provider() if self._selected_count_provider else 0
        menu = build_file_context_menu(
            self,
            self._entry,
            selected=self._selected,
            selected_count=sel_count,
            on_open=self.open_requested.emit,
            on_restore=self.restore_requested.emit,
            on_favorite=self.favorite_toggled.emit,
            on_toggle_select=self.select_toggle_requested.emit,
            on_batch_delete=self.batch_delete_requested.emit,
            on_delete=self.delete_requested.emit,
        )
        from .motion import dropdown_open
        dropdown_open(menu)
        menu.exec(self.mapToGlobal(pos))

    _fmt_size = staticmethod(format_file_size)

    def _truncate(self, s: str, n: int) -> str:
        return s if len(s) <= n else s[:n - 1] + "…"


# ─── Virtualized Model + View (List Mode & >500-file Scaling) ──────────────────


class FileListModel(QAbstractListModel):
    """Lightweight model over vault entries for zero-lag list & large-vault rendering."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._entries: list = []
        self._selected_ids: set[str] = set()

    def set_entries(self, entries: list, selected_ids: Optional[set[str]] = None) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        if selected_ids is not None:
            self._selected_ids = set(selected_ids)
        self.endResetModel()

    def set_selected_ids(self, selected_ids: set[str]) -> None:
        self._selected_ids = set(selected_ids)
        if self._entries:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._entries) - 1, 0))

    def notify_entry_updated(self, file_id: str) -> None:
        for row, e in enumerate(self._entries):
            if e.file_id == file_id:
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx)
                break

    def entry_at(self, row: int):
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._entries)):
            return None
        e = self._entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return e.original_name
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{e.original_name}\n{format_file_size(e.size_bytes)} · {infer_category_name(e.original_name, e.mime_type)}"
        if role == Qt.ItemDataRole.UserRole:
            return e
        if role == Qt.ItemDataRole.CheckStateRole:
            return e.file_id in self._selected_ids
        return None


class FileListDelegate(QStyledItemDelegate):
    """Clean 48px hairline row delegate for List view & >500-file virtualization."""

    ROW_HEIGHT = 48

    def __init__(self, thumb_cache: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._thumb_cache = thumb_cache

    def sizeHint(self, option, index: QModelIndex) -> QSize:
        return QSize(max(200, option.rect.width()), self.ROW_HEIGHT)

    def _get_row_icon(self, entry) -> QPixmap:
        mime = infer_mime_type(entry.original_name, entry.mime_type)
        key = ("row", entry.file_id, entry.modified_time, Tokens.colors.name)
        cached = self._thumb_cache.get(key)
        if cached is not None and not cached.isNull():
            if isinstance(self._thumb_cache, OrderedDict):
                self._thumb_cache.move_to_end(key)
            return cached
        if entry.thumbnail_b64:
            try:
                raw = base64.b64decode(entry.thumbnail_b64)
                px = QPixmap()
                if px.loadFromData(raw):
                    scaled = px.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                    cw = max(0, (scaled.width() - 32) // 2)
                    ch = max(0, (scaled.height() - 32) // 2)
                    out = clip_to_rounded(scaled.copy(cw, ch, 32, 32), 6)
                    if isinstance(self._thumb_cache, OrderedDict):
                        self._thumb_cache[key] = out
                        self._thumb_cache.move_to_end(key)
                        while len(self._thumb_cache) > 512:
                            self._thumb_cache.popitem(last=False)
                    else:
                        if len(self._thumb_cache) > 512:
                            self._thumb_cache.clear()
                        self._thumb_cache[key] = out
                    return out
            except Exception:
                pass
        ico_name = icons.get_file_type_icon_name(mime, entry.original_name)
        return icons.get_pixmap(ico_name, color=Tokens.colors.text_secondary, size=18)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        entry = index.data(Qt.ItemDataRole.UserRole)
        if entry is None:
            return
        is_checked = bool(index.data(Qt.ItemDataRole.CheckStateRole))
        is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p = Tokens.colors

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect.adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)

        if is_checked:
            painter.fillPath(path, QColor(139, 156, 251, 30) if p.is_dark else QColor(82, 105, 216, 28))
            painter.setPen(QPen(QColor(p.accent), 1.0))
            painter.drawPath(path)
        elif is_hover:
            painter.fillPath(path, QColor(p.elevated))
            painter.setPen(QPen(QColor(255, 255, 255, 18) if p.is_dark else QColor(0, 0, 0, 18), 1.0))
            painter.drawPath(path)

        # Icon / thumbnail box
        ico = self._get_row_icon(entry)
        ix = rect.left() + 12
        iy = rect.center().y() - ico.deviceIndependentSize().height() / 2
        painter.drawPixmap(int(ix), int(iy), ico)

        # Metadata text on right
        cat = infer_category_name(entry.original_name, entry.mime_type)
        meta_txt = f"{format_file_size(entry.size_bytes)} · {cat}"
        meta_font = Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_BODY)
        meta_fm = QFontMetrics(meta_font)
        meta_w = meta_fm.horizontalAdvance(meta_txt) + 12

        # Name on left (middle elided)
        name_x = rect.left() + 56
        name_w = max(80, rect.right() - name_x - meta_w - 32)
        name_font = Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_LABEL)
        painter.setFont(name_font)
        painter.setPen(QColor(p.warning if entry.is_favorite else p.text))
        prefix = "★ " if entry.is_favorite else ""
        elided = QFontMetrics(name_font).elidedText(prefix + entry.original_name, Qt.TextElideMode.ElideMiddle, name_w)
        painter.drawText(QRect(name_x, rect.top(), name_w, rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

        # Size/type on right
        painter.setFont(meta_font)
        painter.setPen(QColor(p.text_muted))
        painter.drawText(
            QRect(rect.right() - meta_w - 28, rect.top(), meta_w, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            meta_txt,
        )

        # Trailing selection indicator
        if is_checked:
            chk_px = icons.get_pixmap("check", color=p.accent, size=16)
            painter.drawPixmap(rect.right() - 24, int(rect.center().y() - 8), chk_px)

        painter.restore()


class VaultFileListView(QListView):
    """Virtualized QListView used for List view and >500-file vaults."""

    open_requested = Signal(str)
    restore_requested = Signal(str)
    delete_requested = Signal(str)
    favorite_toggled = Signal(str, bool)
    selection_requested = Signal(str, object)
    select_toggle_requested = Signal(str)
    batch_delete_requested = Signal()

    def __init__(
        self,
        thumb_cache: dict,
        selected_count_provider: Optional[Callable[[], int]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._thumb_cache = thumb_cache
        self._selection_mode = False
        self._selected_ids: set[str] = set()
        self._selected_count_provider = selected_count_provider

        self.file_model = FileListModel(self)
        self.setModel(self.file_model)
        self.setItemDelegate(FileListDelegate(thumb_cache, self))
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context)
        self.doubleClicked.connect(self._on_double_click)

    def set_data(self, entries: list, selected_ids: set[str], selection_mode: bool) -> None:
        self._selected_ids = set(selected_ids)
        self._selection_mode = bool(selection_mode)
        self.file_model.set_entries(entries, self._selected_ids)

    def update_selection(self, selected_ids: set[str], selection_mode: bool) -> None:
        self._selected_ids = set(selected_ids)
        self._selection_mode = bool(selection_mode)
        self.file_model.set_selected_ids(self._selected_ids)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(event.position().toPoint())
            if idx.isValid():
                entry = self.file_model.entry_at(idx.row())
                if entry is not None:
                    mods = event.modifiers()
                    if self._selection_mode or bool(
                        mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
                    ):
                        self.selection_requested.emit(entry.file_id, mods)
                        event.accept()
                        return
        super().mousePressEvent(event)

    def _on_double_click(self, index: QModelIndex) -> None:
        entry = self.file_model.entry_at(index.row())
        if entry is not None and not self._selection_mode:
            self.open_requested.emit(entry.file_id)

    def _show_context(self, pos: QPoint) -> None:
        idx = self.indexAt(pos)
        if not idx.isValid():
            return
        entry = self.file_model.entry_at(idx.row())
        if entry is None:
            return
        sel_count = self._selected_count_provider() if self._selected_count_provider else len(self._selected_ids)
        menu = build_file_context_menu(
            self,
            entry,
            selected=(entry.file_id in self._selected_ids),
            selected_count=sel_count,
            on_open=self.open_requested.emit,
            on_restore=self.restore_requested.emit,
            on_favorite=self.favorite_toggled.emit,
            on_toggle_select=self.select_toggle_requested.emit,
            on_batch_delete=self.batch_delete_requested.emit,
            on_delete=self.delete_requested.emit,
        )
        from .motion import dropdown_open
        dropdown_open(menu)
        menu.exec(self.viewport().mapToGlobal(pos))


__all__ = [
    "FileCard",
    "FileListDelegate",
    "FileListModel",
    "VaultFileListView",
    "build_file_context_menu",
    "clip_to_rounded",
    "format_file_size",
    "infer_category_name",
]
