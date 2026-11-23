"""
theme.py — Design system for Personal Vault (dark modern UI).

All colours, fonts, radii, and animation durations live here so the whole
app shares a consistent look.  PySide6 QSS + QPalette approach.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


# ─── Colour palette ─────────────────────────────────────────────────────────
class Colors:
    # Backgrounds (Phase 12.A Refined Palette)
    BG_DARKEST   = "#0B1020"      # Main application background
    BG_DARK      = "#111A2A"      # Main surface
    BG_CARD      = "#172235"      # Secondary surface
    BG_CARD_HOVER= "#1B2940"      # Elevated card
    BG_INPUT     = "#111A2A"      # Input fields
    BG_SIDEBAR   = "#0B1020"      # Navigation sidebar

    # Accents
    ACCENT       = "#8B9CFF"      # Modern soft indigo
    PRIMARY      = "#8B9CFF"      # Primary brand alias
    ACCENT_HOVER = "#A2B0FF"
    ACCENT_DARK  = "#7385E8"
    SUCCESS      = "#55C7A4"      # Mint green
    WARNING      = "#F4BD68"      # Amber
    DANGER       = "#FF7183"      # Coral red
    DANGER_DARK  = "#D95365"

    # Text
    WHITE        = "#FFFFFF"
    TEXT_PRIMARY = "#F1F5FB"      # High contrast clean white
    TEXT_SECONDARY = "#A7B3C5"    # Clean slate gray
    TEXT_MUTED   = "#748298"      # Muted subtext
    TEXT_ACCENT  = "#8B9CFF"

    # Borders
    BORDER       = "#28364B"      # Restrained border
    BORDER_FOCUS = "#8B9CFF"
    BORDER_SUBTLE= "#1B2940"

    # Special
    LOCKED_RED   = "#FF7183"
    UNLOCKED_GREEN = "#55C7A4"
    ICON_BG      = "#1B2940"
    GLASS        = "rgba(17, 26, 42, 0.92)"

# ─── Global stylesheet ────────────────────────────────────────────────────────
GLOBAL_QSS = f"""
/* ── Global ─────────────────────────────────────────────────────────────── */
QWidget {{
    background-color: {Colors.BG_DARK};
    color: {Colors.TEXT_PRIMARY};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
    border: none;
    outline: none;
}}

QMainWindow {{
    background-color: {Colors.BG_DARKEST};
}}

/* ── Scrollbars ──────────────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {Colors.BG_DARKEST};
    width: 6px;
    margin: 0;
    border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {Colors.BG_CARD};
    min-height: 30px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{
    background: {Colors.ACCENT};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: {Colors.BG_DARKEST};
    height: 6px;
    border-radius: 3px;
}}
QScrollBar::handle:horizontal {{
    background: {Colors.BG_CARD};
    min-width: 30px;
    border-radius: 3px;
}}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {Colors.ACCENT};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 20px;
    font-weight: 600;
    font-size: 13px;
    min-height: 36px;
}}
QPushButton:hover {{
    background-color: {Colors.ACCENT_HOVER};
}}
QPushButton:pressed {{
    background-color: {Colors.ACCENT_DARK};
}}
QPushButton:disabled {{
    background-color: {Colors.BG_CARD};
    color: {Colors.TEXT_MUTED};
}}

QPushButton[flat="true"] {{
    background: transparent;
    color: {Colors.TEXT_SECONDARY};
    padding: 6px 12px;
}}
QPushButton[flat="true"]:hover {{
    background: {Colors.BG_CARD};
    color: {Colors.TEXT_PRIMARY};
}}

QPushButton[danger="true"] {{
    background-color: {Colors.DANGER};
}}
QPushButton[danger="true"]:hover {{
    background-color: #F87171;
}}

QPushButton[success="true"] {{
    background-color: {Colors.SUCCESS};
}}

QPushButton[ghost="true"] {{
    background: transparent;
    color: {Colors.ACCENT};
    border: 1px solid {Colors.ACCENT};
}}
QPushButton[ghost="true"]:hover {{
    background: rgba(108, 99, 255, 0.1);
}}

/* ── Inputs ──────────────────────────────────────────────────────────────── */
QLineEdit {{
    background-color: {Colors.BG_INPUT};
    color: {Colors.TEXT_PRIMARY};
    border: 1.5px solid {Colors.BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    min-height: 38px;
    selection-background-color: {Colors.ACCENT};
}}
QLineEdit:focus {{
    border-color: {Colors.BORDER_FOCUS};
    background-color: #131D2D;
}}
QLineEdit:disabled {{
    color: {Colors.TEXT_MUTED};
    border-color: {Colors.BORDER_SUBTLE};
}}
QLineEdit::placeholder {{
    color: {Colors.TEXT_MUTED};
}}

QTextEdit {{
    background-color: {Colors.BG_INPUT};
    color: {Colors.TEXT_PRIMARY};
    border: 1.5px solid {Colors.BORDER};
    border-radius: 8px;
    padding: 8px;
    selection-background-color: {Colors.ACCENT};
}}

QSpinBox {{
    background-color: {Colors.BG_INPUT};
    color: {Colors.TEXT_PRIMARY};
    border: 1.5px solid {Colors.BORDER};
    border-radius: 8px;
    padding: 4px 8px;
    font-size: 13px;
    min-height: 32px;
}}
QSpinBox:focus {{
    border-color: {Colors.BORDER_FOCUS};
}}

/* ── Labels ──────────────────────────────────────────────────────────────── */
QLabel {{
    background: transparent;
    color: {Colors.TEXT_PRIMARY};
}}
QLabel[secondary="true"] {{
    color: {Colors.TEXT_SECONDARY};
    font-size: 12px;
}}
QLabel[title="true"] {{
    font-size: 22px;
    font-weight: 700;
    color: {Colors.TEXT_PRIMARY};
}}
QLabel[subtitle="true"] {{
    font-size: 15px;
    font-weight: 600;
    color: {Colors.TEXT_PRIMARY};
}}

/* ── Progress bar ────────────────────────────────────────────────────────── */
QProgressBar {{
    background-color: {Colors.BG_INPUT};
    border-radius: 4px;
    border: none;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {Colors.ACCENT}, stop:1 #A78BFA);
    border-radius: 4px;
}}

/* ── Combo box ───────────────────────────────────────────────────────────── */
QComboBox {{
    background-color: {Colors.BG_INPUT};
    color: {Colors.TEXT_PRIMARY};
    border: 1.5px solid {Colors.BORDER};
    border-radius: 8px;
    padding: 6px 12px;
    min-height: 36px;
}}
QComboBox:focus {{
    border-color: {Colors.BORDER_FOCUS};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {Colors.BG_CARD};
    border: 1px solid {Colors.BORDER};
    border-radius: 8px;
    color: {Colors.TEXT_PRIMARY};
    selection-background-color: {Colors.ACCENT};
    padding: 4px;
}}

/* ── Checkbox / Radio ────────────────────────────────────────────────────── */
QCheckBox, QRadioButton {{
    color: {Colors.TEXT_PRIMARY};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border: 2px solid {Colors.BORDER};
    border-radius: 4px;
    background: {Colors.BG_INPUT};
}}
QCheckBox::indicator:checked {{
    background: {Colors.ACCENT};
    border-color: {Colors.ACCENT};
    image: url(:/icons/check.svg);
}}

/* ── Slider ──────────────────────────────────────────────────────────────── */
QSlider::groove:horizontal {{
    height: 4px;
    background: {Colors.BORDER};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {Colors.ACCENT};
    width: 16px; height: 16px;
    margin: -6px 0;
    border-radius: 8px;
}}
QSlider::sub-page:horizontal {{
    background: {Colors.ACCENT};
    border-radius: 2px;
}}

/* ── Tab widget ──────────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: none;
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {Colors.TEXT_MUTED};
    padding: 8px 20px;
    border-bottom: 2px solid transparent;
    font-weight: 500;
}}
QTabBar::tab:selected {{
    color: {Colors.ACCENT};
    border-bottom: 2px solid {Colors.ACCENT};
}}
QTabBar::tab:hover:!selected {{
    color: {Colors.TEXT_SECONDARY};
}}

/* ── List / Tree ─────────────────────────────────────────────────────────── */
QListWidget, QTreeWidget, QTableWidget {{
    background-color: transparent;
    border: none;
    outline: none;
}}
QListWidget::item, QTreeWidget::item, QTableWidget::item {{
    padding: 6px 8px;
    border-radius: 6px;
    color: {Colors.TEXT_PRIMARY};
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background-color: rgba(108, 99, 255, 0.2);
    color: {Colors.ACCENT};
}}
QListWidget::item:hover, QTreeWidget::item:hover {{
    background-color: {Colors.BG_CARD_HOVER};
}}

/* ── Header ──────────────────────────────────────────────────────────────── */
QHeaderView::section {{
    background-color: {Colors.BG_DARKEST};
    color: {Colors.TEXT_MUTED};
    padding: 6px 10px;
    border: none;
    border-bottom: 1px solid {Colors.BORDER};
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

/* ── Tooltip ─────────────────────────────────────────────────────────────── */
QToolTip {{
    background-color: {Colors.BG_CARD};
    color: {Colors.TEXT_PRIMARY};
    border: 1px solid {Colors.BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}}

/* ── Menu ────────────────────────────────────────────────────────────────── */
QMenu {{
    background-color: {Colors.BG_CARD};
    border: 1px solid {Colors.BORDER};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 16px;
    border-radius: 6px;
    color: {Colors.TEXT_PRIMARY};
}}
QMenu::item:selected {{
    background-color: rgba(108, 99, 255, 0.2);
    color: {Colors.TEXT_PRIMARY};
}}
QMenu::separator {{
    height: 1px;
    background: {Colors.BORDER};
    margin: 4px 8px;
}}

/* ── Splitter ────────────────────────────────────────────────────────────── */
QSplitter::handle {{
    background: {Colors.BORDER};
    width: 1px;
}}

/* ── Status bar ──────────────────────────────────────────────────────────── */
QStatusBar {{
    background: {Colors.BG_DARKEST};
    color: {Colors.TEXT_MUTED};
    font-size: 11px;
    border-top: 1px solid {Colors.BORDER};
}}
"""


def apply_dark_theme(app: QApplication) -> None:
    """Apply the dark theme palette + stylesheet to the application."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(Colors.BG_DARK))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(Colors.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base,            QColor(Colors.BG_INPUT))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(Colors.BG_CARD))
    palette.setColor(QPalette.ColorRole.ToolTipBase,     QColor(Colors.BG_CARD))
    palette.setColor(QPalette.ColorRole.ToolTipText,     QColor(Colors.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Text,            QColor(Colors.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button,          QColor(Colors.BG_CARD))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(Colors.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.BrightText,      QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Link,            QColor(Colors.ACCENT))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor(Colors.ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(Colors.TEXT_MUTED))

    app.setPalette(palette)
    app.setStyleSheet(GLOBAL_QSS)

