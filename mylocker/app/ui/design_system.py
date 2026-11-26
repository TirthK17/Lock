"""
design_system.py — Single source of truth for Personal Vault's UI tokens,
typography, spacing, radii, motion durations, and global QSS builder.

Calm, precise graphite/periwinkle aesthetic inspired by macOS System Settings,
Linear, and Raycast. Supports Dark (default), Light, and Windows System modes.
Zero gradients, zero glow, hairline 1px borders, strict typographic hierarchy.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


def get_ui_resource_path(*parts: str) -> Optional[Path]:
    """Resolve UI resource file across development runs and PyInstaller bundles."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "mylocker" / "app" / "ui" / "resources"
        for part in parts:
            cand = cand / part
        if cand.exists():
            return cand
        cand_alt = Path(meipass) / "resources"
        for part in parts:
            cand_alt = cand_alt / part
        if cand_alt.exists():
            return cand_alt

    local = Path(__file__).resolve().parent / "resources"
    for part in parts:
        local = local / part
    if local.exists():
        return local
    return None


@dataclass(frozen=True)
class ThemePalette:
    """Immutable semantic + surface color set for a single theme mode."""

    name: str
    is_dark: bool

    # Surfaces
    bg: str
    bg_secondary: str
    card: str
    elevated: str
    input_bg: str
    overlay: str

    # Text
    text: str
    text_secondary: str
    text_muted: str
    on_accent: str
    on_danger: str
    white: str

    # Interactive accent
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_tint: str
    accent_border: str

    # Borders
    border: str
    border_subtle: str
    border_strong: str
    border_focus: str

    # Semantic
    success: str
    success_tint: str
    success_border: str
    warning: str
    warning_tint: str
    warning_border: str
    danger: str
    danger_hover: str
    danger_pressed: str
    danger_tint: str
    danger_border: str
    info: str

    # Subtle neutral fills & scrollbar
    hover_fill: str
    pressed_fill: str
    scrollbar_handle: str
    selection_bg: str


DARK_PALETTE = ThemePalette(
    name="dark",
    is_dark=True,
    bg="#0B0D12",
    bg_secondary="#11141B",
    card="#171B24",
    elevated="#1D2230",
    input_bg="#11141B",
    overlay="rgba(11, 13, 18, 0.88)",
    text="#F5F6F8",
    text_secondary="#969DAA",
    text_muted="#6B7280",
    on_accent="#0B0D12",
    on_danger="#0B0D12",
    white="#FFFFFF",
    accent="#8B9CFB",
    accent_hover="#A1AFFC",
    accent_pressed="#7284EE",
    accent_tint="rgba(139, 156, 251, 0.12)",
    accent_border="rgba(139, 156, 251, 0.36)",
    border="rgba(255, 255, 255, 0.07)",
    border_subtle="rgba(255, 255, 255, 0.05)",
    border_strong="rgba(255, 255, 255, 0.14)",
    border_focus="#8B9CFB",
    success="#4CC9A0",
    success_tint="rgba(76, 201, 160, 0.12)",
    success_border="rgba(76, 201, 160, 0.34)",
    warning="#E8B86A",
    warning_tint="rgba(232, 184, 106, 0.12)",
    warning_border="rgba(232, 184, 106, 0.34)",
    danger="#F0707F",
    danger_hover="#F48A97",
    danger_pressed="#DE5B6B",
    danger_tint="rgba(240, 112, 127, 0.12)",
    danger_border="rgba(240, 112, 127, 0.36)",
    info="#8B9CFB",
    hover_fill="rgba(255, 255, 255, 0.05)",
    pressed_fill="rgba(255, 255, 255, 0.09)",
    scrollbar_handle="#262C3B",
    selection_bg="rgba(139, 156, 251, 0.28)",
)

LIGHT_PALETTE = ThemePalette(
    name="light",
    is_dark=False,
    bg="#F6F7F9",
    bg_secondary="#ECEEF2",
    card="#FFFFFF",
    elevated="#F0F2F6",
    input_bg="#FFFFFF",
    overlay="rgba(23, 25, 31, 0.50)",
    text="#17191F",
    text_secondary="#747B87",
    text_muted="#8A919E",
    on_accent="#FFFFFF",
    on_danger="#FFFFFF",
    white="#FFFFFF",
    accent="#5269D8",
    accent_hover="#4359C7",
    accent_pressed="#364AB3",
    accent_tint="rgba(82, 105, 216, 0.12)",
    accent_border="rgba(82, 105, 216, 0.35)",
    border="rgba(0, 0, 0, 0.08)",
    border_subtle="rgba(0, 0, 0, 0.05)",
    border_strong="rgba(0, 0, 0, 0.16)",
    border_focus="#5269D8",
    success="#167A56",
    success_tint="rgba(22, 122, 86, 0.12)",
    success_border="rgba(22, 122, 86, 0.36)",
    warning="#8A5400",
    warning_tint="rgba(138, 84, 0, 0.12)",
    warning_border="rgba(138, 84, 0, 0.36)",
    danger="#C2273F",
    danger_hover="#A91F35",
    danger_pressed="#8F182B",
    danger_tint="rgba(194, 39, 63, 0.12)",
    danger_border="rgba(194, 39, 63, 0.36)",
    info="#5269D8",
    hover_fill="rgba(0, 0, 0, 0.04)",
    pressed_fill="rgba(0, 0, 0, 0.08)",
    scrollbar_handle="#C8CDD6",
    selection_bg="rgba(82, 105, 216, 0.22)",
)


class _Spacing:
    """Strict 4px-grid spacing scale."""

    XS_4 = 4
    SM_8 = 8
    MD_12 = 12
    LG_16 = 16
    XL_24 = 24
    XXL_32 = 32


class _Radius:
    """Consistent border-radius scale."""

    CONTROL = 8
    CARD = 12
    DIALOG = 16
    PILL = 999


class _Typography:
    """Strict 6-step type scale + weights using Inter / Segoe UI Variable."""

    FAMILY_PRIMARY = "Inter"
    FAMILY_FALLBACK = "Segoe UI Variable"
    FAMILY_SYSTEM = "Segoe UI"
    FAMILY_MONO = "Cascadia Code"

    # Strict pixel type scale: 11, 12, 13, 15, 20, 28
    META = 11
    LABEL = 12
    BODY = 13
    SECTION = 15
    PAGE_TITLE = 20
    LOGIN_TITLE = 28

    # Weights: 400 body, 500 labels/buttons, 600 titles
    WEIGHT_BODY = QFont.Weight.Normal
    WEIGHT_LABEL = QFont.Weight.Medium
    WEIGHT_TITLE = QFont.Weight.DemiBold

    _FAMILIES_LOADED = False
    _LOADED_FAMILIES: list[str] = []

    @classmethod
    def ensure_fonts_loaded(cls) -> list[str]:
        """Load bundled Inter .ttf files once into QFontDatabase."""
        if cls._FAMILIES_LOADED:
            return cls._LOADED_FAMILIES
        app = QApplication.instance()
        if app is None:
            return []

        font_dir = get_ui_resource_path("fonts")
        loaded: list[str] = []
        if font_dir and font_dir.is_dir():
            for ttf in sorted(font_dir.glob("*.ttf")):
                fid = QFontDatabase.addApplicationFont(str(ttf))
                if fid >= 0:
                    loaded.extend(QFontDatabase.applicationFontFamilies(fid))
                else:
                    logger.warning("Failed to load bundled font: %s", ttf.name)
        cls._LOADED_FAMILIES = list(dict.fromkeys(loaded))
        cls._FAMILIES_LOADED = True
        return cls._LOADED_FAMILIES

    @classmethod
    def families(cls) -> list[str]:
        return [cls.FAMILY_PRIMARY, cls.FAMILY_FALLBACK, cls.FAMILY_SYSTEM, "sans-serif"]

    @classmethod
    def font(cls, px: int, weight: QFont.Weight = QFont.Weight.Normal, mono: bool = False) -> QFont:
        """Build a pixel-sized QFont constrained to the approved scale."""
        cls.ensure_fonts_loaded()
        f = QFont()
        if mono:
            f.setFamilies([cls.FAMILY_MONO, "Consolas", "Courier New", "monospace"])
        else:
            f.setFamilies(cls.families())
        f.setPixelSize(px)
        f.setWeight(weight)
        return f

    @classmethod
    def css_family(cls, mono: bool = False) -> str:
        if mono:
            return '"Cascadia Code", "Consolas", "Courier New", monospace'
        return '"Inter", "Segoe UI Variable", "Segoe UI", sans-serif'


class _MotionDurations:
    """Snappy, restrained animation durations (ms)."""

    FAST = 120
    NORMAL = 170
    SLOW = 220


class DesignTokens:
    """Mutable singleton facade providing dark/light tokens, spacing, radius, type, motion."""

    spacing = _Spacing
    radius = _Radius
    type = _Typography
    motion = _MotionDurations
    dark = DARK_PALETTE
    light = LIGHT_PALETTE

    def __init__(self) -> None:
        self._mode: str = "dark"  # "dark" | "light"
        self._listeners: list[Callable[[ThemePalette], None]] = []

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def colors(self) -> ThemePalette:
        return self.dark if self._mode == "dark" else self.light

    def set_theme(self, mode: str) -> ThemePalette:
        resolved = resolve_theme_mode(mode)
        self._mode = resolved
        pal = self.colors
        try:
            import shiboken6
        except Exception:  # pragma: no cover
            shiboken6 = None  # type: ignore[assignment]
        stale: list[Callable[[ThemePalette], None]] = []
        for cb in list(self._listeners):
            owner = getattr(cb, "__self__", None)
            if shiboken6 is not None and owner is not None:
                try:
                    if not shiboken6.isValid(owner):
                        stale.append(cb)
                        continue
                except Exception:
                    pass
            try:
                cb(pal)
            except Exception as exc:
                logger.debug("Theme change listener error: %s", type(exc).__name__)
        for cb in stale:
            if cb in self._listeners:
                self._listeners.remove(cb)
        return pal

    def subscribe(self, cb: Callable[[ThemePalette], None]) -> None:
        if cb not in self._listeners:
            self._listeners.append(cb)

    def unsubscribe(self, cb: Callable[[ThemePalette], None]) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)


Tokens = DesignTokens()


def resolve_theme_mode(mode: str) -> str:
    """Resolve 'dark', 'light', or 'system' into concrete 'dark' | 'light'."""
    m = (mode or "dark").strip().lower()
    if m in ("dark", "light"):
        return m
    if m == "system":
        app = QApplication.instance()
        if app is not None and hasattr(app, "styleHints"):
            try:
                hints = app.styleHints()
                scheme = hints.colorScheme()
                if scheme == Qt.ColorScheme.Light:
                    return "light"
                if scheme == Qt.ColorScheme.Dark:
                    return "dark"
            except Exception:
                pass
    return "dark"


def _css_url(path: Optional[Path]) -> str:
    if path is None:
        return ""
    return path.resolve().as_posix()


def build_global_qss(palette: Optional[ThemePalette] = None) -> str:
    """Compile the complete application Qt stylesheet from Tokens."""
    p = palette or Tokens.colors
    r = Tokens.radius
    t = Tokens.type
    font_css = t.css_family()

    check_icon = _css_url(
        get_ui_resource_path("icons", "check_dark.svg" if p.is_dark else "check_light.svg")
    )
    chev_icon = _css_url(
        get_ui_resource_path("icons", "chevron_down_dark.svg" if p.is_dark else "chevron_down_light.svg")
    )

    check_rule = f"image: url({check_icon});" if check_icon else ""
    chev_rule = f"image: url({chev_icon}); width: 14px; height: 14px;" if chev_icon else ""

    # Compute high-contrast danger label for solid danger button
    danger_solid_text = p.on_danger

    return f"""
/* ── Base ───────────────────────────────────────────────────────────────── */
QWidget {{
    background-color: {p.bg};
    color: {p.text};
    font-family: {font_css};
    font-size: {t.BODY}px;
    font-weight: 400;
    border: none;
    outline: none;
}}

QMainWindow, QDialog {{
    background-color: {p.bg};
    color: {p.text};
}}

/* ── ScrollAreas & Scrollbars ───────────────────────────────────────────── */
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {p.scrollbar_handle};
    min-height: 28px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p.text_muted};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    height: 0px;
    background: transparent;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
    margin: 0 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p.scrollbar_handle};
    min-width: 28px;
    border-radius: 3px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {p.text_muted};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    width: 0px;
    background: transparent;
}}

/* ── Labels (Type Scale: 11 / 12 / 13 / 15 / 20 / 28) ───────────────────── */
QLabel {{
    background: transparent;
    color: {p.text};
    font-size: {t.BODY}px;
}}
QLabel[secondary="true"], QLabel[role="secondary"] {{
    color: {p.text_secondary};
    font-size: {t.LABEL}px;
    font-weight: 400;
}}
QLabel[role="muted"] {{
    color: {p.text_muted};
    font-size: {t.LABEL}px;
}}
QLabel[role="meta"] {{
    color: {p.text_muted};
    font-size: {t.META}px;
    font-weight: 500;
}}
QLabel[role="section"], QLabel[subtitle="true"] {{
    color: {p.text};
    font-size: {t.SECTION}px;
    font-weight: 600;
}}
QLabel[role="title"], QLabel[title="true"] {{
    color: {p.text};
    font-size: {t.PAGE_TITLE}px;
    font-weight: 600;
}}
QLabel[role="loginTitle"] {{
    color: {p.text};
    font-size: {t.LOGIN_TITLE}px;
    font-weight: 600;
}}
QLabel[role="sectionHeader"] {{
    color: {p.text_muted};
    font-size: {t.META}px;
    font-weight: 600;
    letter-spacing: 0.8px;
    background: transparent;
}}
QLabel[role="danger"] {{
    color: {p.danger};
    font-size: {t.LABEL}px;
}}
QLabel[role="success"] {{
    color: {p.success};
    font-size: {t.LABEL}px;
}}
QLabel[role="warning"] {{
    color: {p.warning};
    font-size: {t.LABEL}px;
}}

/* ── Cards & Panels ─────────────────────────────────────────────────────── */
QFrame[card="true"] {{
    background-color: {p.card};
    border: 1px solid {p.border};
    border-radius: {r.CARD}px;
}}
QFrame[card="true"][interactive="true"]:hover {{
    background-color: {p.elevated};
    border: 1px solid {p.border_strong};
}}
QFrame[card="true"][selected="true"] {{
    background-color: {p.accent_tint};
    border: 2px solid {p.accent};
}}
QFrame[card="true"][variant="elevated"] {{
    background-color: {p.elevated};
    border: 1px solid {p.border};
    border-radius: {r.CARD}px;
}}
QFrame[dialogSurface="true"] {{
    background-color: {p.card};
    border: 1px solid {p.border_strong};
    border-radius: {r.DIALOG}px;
}}
QFrame[divider="true"] {{
    background-color: {p.border};
    border: none;
}}

/* ── Buttons ────────────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {p.elevated};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: {r.CONTROL}px;
    padding: 7px 14px;
    font-size: {t.BODY}px;
    font-weight: 500;
    min-height: 34px;
}}
QPushButton:hover {{
    background-color: {p.hover_fill};
    border-color: {p.border_strong};
}}
QPushButton:pressed {{
    background-color: {p.pressed_fill};
}}
QPushButton:focus {{
    border: 2px solid {p.accent};
}}
QPushButton:disabled {{
    background-color: {p.bg_secondary};
    color: {p.text_muted};
    border-color: {p.border_subtle};
}}

QPushButton[variant="primary"], QPushButton[primary="true"] {{
    background-color: {p.accent};
    color: {p.on_accent};
    border: 1px solid {p.accent};
    font-weight: 500;
}}
QPushButton[variant="primary"]:hover, QPushButton[primary="true"]:hover {{
    background-color: {p.accent_hover};
    border-color: {p.accent_hover};
}}
QPushButton[variant="primary"]:pressed, QPushButton[primary="true"]:pressed {{
    background-color: {p.accent_pressed};
    border-color: {p.accent_pressed};
}}
QPushButton[variant="primary"]:focus, QPushButton[primary="true"]:focus {{
    border: 2px solid {p.text};
}}
QPushButton[variant="primary"]:disabled, QPushButton[primary="true"]:disabled {{
    background-color: {p.elevated};
    color: {p.text_muted};
    border-color: {p.border};
}}

QPushButton[variant="secondary"], QPushButton[ghost="true"] {{
    background-color: {p.card};
    color: {p.text};
    border: 1px solid {p.border};
    font-weight: 500;
}}
QPushButton[variant="secondary"]:hover, QPushButton[ghost="true"]:hover {{
    background-color: {p.elevated};
    border-color: {p.border_strong};
}}
QPushButton[variant="secondary"]:pressed, QPushButton[ghost="true"]:pressed {{
    background-color: {p.pressed_fill};
}}
QPushButton[variant="secondary"]:focus, QPushButton[ghost="true"]:focus {{
    border: 2px solid {p.accent};
}}

QPushButton[variant="danger"], QPushButton[danger="true"] {{
    background-color: {p.danger_tint};
    color: {p.danger};
    border: 1px solid {p.danger_border};
    font-weight: 500;
}}
QPushButton[variant="danger"]:hover, QPushButton[danger="true"]:hover {{
    background-color: {p.danger};
    color: {danger_solid_text};
    border-color: {p.danger};
}}
QPushButton[variant="danger"]:pressed, QPushButton[danger="true"]:pressed {{
    background-color: {p.danger_pressed};
    color: {danger_solid_text};
}}
QPushButton[variant="danger"]:focus, QPushButton[danger="true"]:focus {{
    border: 2px solid {p.accent};
}}

QPushButton[variant="ghost"], QPushButton[flat="true"] {{
    background-color: transparent;
    color: {p.text_secondary};
    border: 1px solid transparent;
    padding: 6px 10px;
}}
QPushButton[variant="ghost"]:hover, QPushButton[flat="true"]:hover {{
    background-color: {p.hover_fill};
    color: {p.text};
}}
QPushButton[variant="ghost"]:pressed, QPushButton[flat="true"]:pressed {{
    background-color: {p.pressed_fill};
}}
QPushButton[variant="ghost"]:focus, QPushButton[flat="true"]:focus {{
    border: 2px solid {p.accent};
}}

QPushButton[variant="icon"] {{
    background-color: transparent;
    color: {p.text_secondary};
    border: 1px solid transparent;
    border-radius: {r.CONTROL}px;
    padding: 0;
    min-height: 32px;
    min-width: 32px;
}}
QPushButton[variant="icon"]:hover {{
    background-color: {p.hover_fill};
    color: {p.text};
    border-color: {p.border};
}}
QPushButton[variant="icon"]:checked {{
    background-color: {p.accent_tint};
    color: {p.accent};
    border-color: {p.accent_border};
}}
QPushButton[variant="icon"]:focus {{
    border: 2px solid {p.accent};
}}

/* ── Inputs ─────────────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {p.input_bg};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: {r.CONTROL}px;
    padding: 6px 12px;
    font-size: {t.BODY}px;
    min-height: 36px;
    selection-background-color: {p.selection_bg};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 2px solid {p.accent};
    padding: 5px 11px;
}}
QLineEdit:disabled, QTextEdit:disabled {{
    background-color: {p.bg_secondary};
    color: {p.text_muted};
    border-color: {p.border_subtle};
}}
QLineEdit[error="true"] {{
    border: 1px solid {p.danger};
}}

QSpinBox {{
    background-color: {p.input_bg};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: {r.CONTROL}px;
    padding: 4px 8px;
    font-size: {t.BODY}px;
    min-height: 32px;
}}
QSpinBox:focus {{
    border: 2px solid {p.accent};
}}

QComboBox {{
    background-color: {p.card};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: {r.CONTROL}px;
    padding: 5px 28px 5px 12px;
    font-size: {t.BODY}px;
    font-weight: 500;
    min-height: 34px;
}}
QComboBox:hover {{
    border-color: {p.border_strong};
    background-color: {p.elevated};
}}
QComboBox:focus {{
    border: 2px solid {p.accent};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
}}
QComboBox::down-arrow {{
    {chev_rule}
}}
QComboBox QAbstractItemView {{
    background-color: {p.card};
    border: 1px solid {p.border_strong};
    border-radius: {r.CONTROL}px;
    color: {p.text};
    selection-background-color: {p.accent_tint};
    selection-color: {p.text};
    padding: 4px;
    outline: none;
}}

/* ── Checkboxes & Radios ────────────────────────────────────────────────── */
QCheckBox, QRadioButton {{
    color: {p.text};
    spacing: 8px;
    font-size: {t.BODY}px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {p.border_strong};
    border-radius: 4px;
    background-color: {p.input_bg};
}}
QCheckBox::indicator:hover {{
    border-color: {p.accent};
}}
QCheckBox::indicator:checked {{
    background-color: {p.accent};
    border-color: {p.accent};
    {check_rule}
}}
QCheckBox:focus::indicator {{
    border: 2px solid {p.accent};
}}
QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {p.border_strong};
    border-radius: 8px;
    background-color: {p.input_bg};
}}
QRadioButton::indicator:hover {{
    border-color: {p.accent};
}}
QRadioButton::indicator:checked {{
    border: 5px solid {p.accent};
    background-color: {p.on_accent};
}}
QRadioButton:focus::indicator {{
    border: 2px solid {p.accent};
}}

/* ── Progress Bars (solid accent, zero gradient) ────────────────────────── */
QProgressBar {{
    background-color: {p.elevated};
    border: 1px solid {p.border_subtle};
    border-radius: 4px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {p.accent};
    border-radius: 3px;
}}

/* ── Slider ─────────────────────────────────────────────────────────────── */
QSlider::groove:horizontal {{
    height: 4px;
    background: {p.border_strong};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {p.accent};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {p.accent};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:focus {{
    border: 2px solid {p.text};
}}

/* ── Lists, Trees, Tables ───────────────────────────────────────────────── */
QListWidget, QListView, QTreeWidget, QTableWidget {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {r.CARD}px;
    outline: none;
    color: {p.text};
}}
QListWidget:focus, QListView:focus, QTreeWidget:focus, QTableWidget:focus {{
    border: 2px solid {p.accent};
}}
QListWidget::item, QListView::item, QTreeWidget::item, QTableWidget::item {{
    padding: 6px 8px;
    border-radius: 6px;
    color: {p.text};
}}
QListWidget::item:hover, QListView::item:hover, QTreeWidget::item:hover {{
    background-color: {p.hover_fill};
}}
QListWidget::item:selected, QListView::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{
    background-color: {p.accent_tint};
    color: {p.text};
}}
QTableWidget {{
    background-color: {p.card};
    border: 1px solid {p.border};
    border-radius: {r.CARD}px;
    gridline-color: {p.border_subtle};
}}
QHeaderView::section {{
    background-color: {p.bg_secondary};
    color: {p.text_muted};
    padding: 8px 10px;
    border: none;
    border-bottom: 1px solid {p.border};
    font-size: {t.META}px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}}

/* ── Tabs ───────────────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: none;
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {p.text_secondary};
    padding: 8px 14px;
    border-bottom: 2px solid transparent;
    font-size: {t.LABEL}px;
    font-weight: 500;
}}
QTabBar::tab:selected {{
    color: {p.accent};
    border-bottom: 2px solid {p.accent};
}}
QTabBar::tab:hover:!selected {{
    color: {p.text};
}}

/* ── Menus & Tooltips ───────────────────────────────────────────────────── */
QMenu {{
    background-color: {p.card};
    border: 1px solid {p.border_strong};
    border-radius: {r.CARD}px;
    padding: 6px;
    color: {p.text};
}}
QMenu::item {{
    padding: 7px 14px;
    border-radius: 6px;
    color: {p.text};
    font-size: {t.BODY}px;
}}
QMenu::item:selected {{
    background-color: {p.accent_tint};
    color: {p.text};
}}
QMenu::separator {{
    height: 1px;
    background: {p.border};
    margin: 4px 8px;
}}

QToolTip {{
    background-color: {p.elevated};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {r.CONTROL}px;
    padding: 6px 10px;
    font-size: {t.LABEL}px;
}}

QSplitter::handle {{
    background: {p.border};
    width: 1px;
}}

QStatusBar {{
    background-color: {p.bg_secondary};
    color: {p.text_muted};
    font-size: {t.META}px;
    border-top: 1px solid {p.border};
    padding: 0 12px;
}}
"""


def build_palette(palette: Optional[ThemePalette] = None) -> QPalette:
    """Create matching QPalette for the active theme."""
    p = palette or Tokens.colors
    qpal = QPalette()
    qpal.setColor(QPalette.ColorRole.Window, QColor(p.bg))
    qpal.setColor(QPalette.ColorRole.WindowText, QColor(p.text))
    qpal.setColor(QPalette.ColorRole.Base, QColor(p.input_bg))
    qpal.setColor(QPalette.ColorRole.AlternateBase, QColor(p.card))
    qpal.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.elevated))
    qpal.setColor(QPalette.ColorRole.ToolTipText, QColor(p.text))
    qpal.setColor(QPalette.ColorRole.Text, QColor(p.text))
    qpal.setColor(QPalette.ColorRole.Button, QColor(p.elevated))
    qpal.setColor(QPalette.ColorRole.ButtonText, QColor(p.text))
    qpal.setColor(QPalette.ColorRole.BrightText, QColor(p.white))
    qpal.setColor(QPalette.ColorRole.Link, QColor(p.accent))
    qpal.setColor(QPalette.ColorRole.Highlight, QColor(p.accent))
    qpal.setColor(QPalette.ColorRole.HighlightedText, QColor(p.on_accent))
    qpal.setColor(QPalette.ColorRole.PlaceholderText, QColor(p.text_muted))
    return qpal


def apply_theme(app: Optional[QApplication], mode: str = "dark") -> ThemePalette:
    """Apply theme palette, bundled fonts, and compiled QSS to QApplication."""
    pal = Tokens.set_theme(mode)
    if app is not None:
        Tokens.type.ensure_fonts_loaded()
        app.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY))
        app.setPalette(build_palette(pal))
        app.setStyleSheet(build_global_qss(pal))
    return pal


__all__ = [
    "DARK_PALETTE",
    "LIGHT_PALETTE",
    "ThemePalette",
    "DesignTokens",
    "Tokens",
    "build_global_qss",
    "build_palette",
    "apply_theme",
    "resolve_theme_mode",
    "get_ui_resource_path",
]
