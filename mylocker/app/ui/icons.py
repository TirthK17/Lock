"""
icons.py — Unified high-DPI vector SVG icon system for Personal Vault.

One cohesive SVG stroke set:
  - 1.5px stroke width, 24x24 viewBox, round caps and joins
  - 20px default size
  - Tinted automatically to the active theme's text color when omitted
  - High-DPI crispness via devicePixelRatio scaling
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .design_system import Tokens

# ── SVG Vector Definitions (24x24 viewBox, stroke-width=1.5, round caps/joins) ─
_SVG_PATHS: Dict[str, str] = {
    "shield": """
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
    """,
    "shield_check": """
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
        <path d="m9 12 2 2 4-4"/>
    """,
    "shield_alert": """
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
        <line x1="12" y1="8" x2="12" y2="12"/>
        <line x1="12" y1="16" x2="12.01" y2="16"/>
    """,
    "lock": """
        <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
        <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
    """,
    "unlock": """
        <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
        <path d="M7 11V7a5 5 0 0 1 9.9-1"/>
    """,
    "key": """
        <circle cx="7.5" cy="15.5" r="5.5"/>
        <path d="m21 2-9.6 9.6"/>
        <path d="m15.5 7.5 2.5 2.5"/>
        <path d="m18 5 2 2"/>
    """,
    "folder": """
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
    """,
    "folder_plus": """
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
        <line x1="12" y1="11" x2="12" y2="17"/>
        <line x1="9" y1="14" x2="15" y2="14"/>
    """,
    "file": """
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
    """,
    "file_text": """
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
        <line x1="16" y1="13" x2="8" y2="13"/>
        <line x1="16" y1="17" x2="8" y2="17"/>
        <line x1="10" y1="9" x2="8" y2="9"/>
    """,
    "image": """
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
        <circle cx="8.5" cy="8.5" r="1.5"/>
        <polyline points="21 15 16 10 5 21"/>
    """,
    "video": """
        <polygon points="23 7 16 12 23 17 23 7"/>
        <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
    """,
    "play": """
        <polygon points="7 4 20 12 7 20 7 4" fill="currentColor"/>
    """,
    "music": """
        <path d="M9 18V5l12-2v13"/>
        <circle cx="6" cy="18" r="3"/>
        <circle cx="18" cy="16" r="3"/>
    """,
    "archive": """
        <polyline points="21 8 21 21 3 21 3 8"/>
        <rect x="1" y="3" width="22" height="5"/>
        <line x1="10" y1="12" x2="14" y2="12"/>
    """,
    "code": """
        <polyline points="16 18 22 12 16 6"/>
        <polyline points="8 6 2 12 8 18"/>
    """,
    "star": """
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
    """,
    "star_filled": """
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" fill="currentColor"/>
    """,
    "search": """
        <circle cx="11" cy="11" r="8"/>
        <line x1="21" y1="21" x2="16.65" y2="16.65"/>
    """,
    "plus": """
        <line x1="12" y1="5" x2="12" y2="19"/>
        <line x1="5" y1="12" x2="19" y2="12"/>
    """,
    "minus": """
        <line x1="5" y1="12" x2="19" y2="12"/>
    """,
    "trash": """
        <polyline points="3 6 5 6 21 6"/>
        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
        <line x1="10" y1="11" x2="10" y2="17"/>
        <line x1="14" y1="11" x2="14" y2="17"/>
    """,
    "download": """
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="7 10 12 15 17 10"/>
        <line x1="12" y1="15" x2="12" y2="3"/>
    """,
    "upload": """
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="17 8 12 3 7 8"/>
        <line x1="12" y1="3" x2="12" y2="15"/>
    """,
    "settings": """
        <circle cx="12" cy="12" r="3"/>
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    """,
    "camera": """
        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
        <circle cx="12" cy="13" r="4"/>
    """,
    "calculator": """
        <rect x="4" y="2" width="16" height="20" rx="2"/>
        <line x1="8" y1="6" x2="16" y2="6"/>
        <line x1="16" y1="14" x2="16" y2="18"/>
        <path d="M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01M8 18h.01M12 18h.01"/>
    """,
    "check": """
        <polyline points="20 6 9 17 4 12"/>
    """,
    "x": """
        <line x1="18" y1="6" x2="6" y2="18"/>
        <line x1="6" y1="6" x2="18" y2="18"/>
    """,
    "alert_triangle": """
        <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>
        <line x1="12" y1="9" x2="12" y2="13"/>
        <line x1="12" y1="17" x2="12.01" y2="17"/>
    """,
    "copy": """
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
    """,
    "refresh": """
        <polyline points="23 4 23 10 17 10"/>
        <polyline points="1 20 1 14 7 14"/>
        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
    """,
    "rotate_cw": """
        <polyline points="23 4 23 10 17 10"/>
        <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
    """,
    "zoom_in": """
        <circle cx="11" cy="11" r="8"/>
        <line x1="21" y1="21" x2="16.65" y2="16.65"/>
        <line x1="11" y1="8" x2="11" y2="14"/>
        <line x1="8" y1="11" x2="14" y2="11"/>
    """,
    "zoom_out": """
        <circle cx="11" cy="11" r="8"/>
        <line x1="21" y1="21" x2="16.65" y2="16.65"/>
        <line x1="8" y1="11" x2="14" y2="11"/>
    """,
    "eye": """
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
        <circle cx="12" cy="12" r="3"/>
    """,
    "eye_off": """
        <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
        <line x1="1" y1="1" x2="23" y2="23"/>
    """,
    "external_link": """
        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
        <polyline points="15 3 21 3 21 9"/>
        <line x1="10" y1="14" x2="21" y2="3"/>
    """,
    "chevron_right": """
        <polyline points="9 18 15 12 9 6"/>
    """,
    "chevron_left": """
        <polyline points="15 18 9 12 15 6"/>
    """,
    "chevron_down": """
        <polyline points="6 9 12 15 18 9"/>
    """,
    "arrow_left": """
        <line x1="19" y1="12" x2="5" y2="12"/>
        <polyline points="12 19 5 12 12 5"/>
    """,
    "arrow_right": """
        <line x1="5" y1="12" x2="19" y2="12"/>
        <polyline points="12 5 19 12 12 19"/>
    """,
    "pause": """
        <rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor"/>
        <rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor"/>
    """,
    "skip_back": """
        <polygon points="19 20 9 12 19 4 19 20" fill="currentColor"/>
        <line x1="5" y1="19" x2="5" y2="5"/>
    """,
    "skip_forward": """
        <polygon points="5 4 15 12 5 20 5 4" fill="currentColor"/>
        <line x1="19" y1="5" x2="19" y2="19"/>
    """,
    "info": """
        <circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="16" x2="12" y2="12"/>
        <line x1="12" y1="8" x2="12.01" y2="8"/>
    """,
    "bookmark": """
        <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>
    """,
    "bookmark_filled": """
        <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" fill="currentColor"/>
    """,
    "share": """
        <circle cx="18" cy="5" r="3"/>
        <circle cx="6" cy="12" r="3"/>
        <circle cx="18" cy="19" r="3"/>
        <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/>
        <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
    """,
    "more_horizontal": """
        <circle cx="5" cy="12" r="1.5" fill="currentColor"/>
        <circle cx="12" cy="12" r="1.5" fill="currentColor"/>
        <circle cx="19" cy="12" r="1.5" fill="currentColor"/>
    """,
    "more_vertical": """
        <circle cx="12" cy="12" r="1.5" fill="currentColor"/>
        <circle cx="12" cy="5" r="1.5" fill="currentColor"/>
        <circle cx="12" cy="19" r="1.5" fill="currentColor"/>
    """,
    "pip": """
        <rect x="2" y="4" width="20" height="15" rx="2"/>
        <rect x="11" y="11" width="9" height="6" rx="1" fill="currentColor"/>
    """,
    "captions": """
        <rect x="2" y="4" width="20" height="16" rx="2"/>
        <path d="M10.2 10.2c-.3-.7-1-1.2-1.8-1.2-1.2 0-2.1 1-2.1 2.3v1.4c0 1.3.9 2.3 2.1 2.3.8 0 1.5-.5 1.8-1.2"/>
        <path d="M17.7 10.2c-.3-.7-1-1.2-1.8-1.2-1.2 0-2.1 1-2.1 2.3v1.4c0 1.3.9 2.3 2.1 2.3.8 0 1.5-.5 1.8-1.2"/>
    """,
    "audio_wave": """
        <line x1="4" y1="10" x2="4" y2="14"/>
        <line x1="8" y1="6" x2="8" y2="18"/>
        <line x1="12" y1="3" x2="12" y2="21"/>
        <line x1="16" y1="7" x2="16" y2="17"/>
        <line x1="20" y1="10" x2="20" y2="14"/>
    """,
    "maximize": """
        <path d="M15 3h6v6"/>
        <path d="M9 21H3v-6"/>
        <line x1="21" y1="3" x2="14" y2="10"/>
        <line x1="3" y1="21" x2="10" y2="14"/>
    """,
    "minimize": """
        <path d="M4 14h6v6"/>
        <path d="M20 10h-6V4"/>
        <line x1="14" y1="10" x2="21" y2="3"/>
        <line x1="3" y1="21" x2="10" y2="14"/>
    """,
    "volume_high": """
        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
        <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
        <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
    """,
    "volume_mute": """
        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
        <line x1="23" y1="9" x2="17" y2="15"/>
        <line x1="17" y1="9" x2="23" y2="15"/>
    """,
    "shield_lock": """
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
        <rect x="8.5" y="10.5" width="7" height="5.5" rx="1.2"/>
        <path d="M10 10.5V8.7a2 2 0 0 1 4 0v1.8"/>
    """,
    "zap": """
        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
    """,
    "flame": """
        <path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>
    """,
    "home": """
        <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
        <polyline points="9 22 9 12 15 12 15 22"/>
    """,
    "grid": """
        <rect x="3" y="3" width="7" height="7" rx="1.5"/>
        <rect x="14" y="3" width="7" height="7" rx="1.5"/>
        <rect x="14" y="14" width="7" height="7" rx="1.5"/>
        <rect x="3" y="14" width="7" height="7" rx="1.5"/>
    """,
    "list": """
        <line x1="8" y1="6" x2="21" y2="6"/>
        <line x1="8" y1="12" x2="21" y2="12"/>
        <line x1="8" y1="18" x2="21" y2="18"/>
        <line x1="3" y1="6" x2="3.01" y2="6"/>
        <line x1="3" y1="12" x2="3.01" y2="12"/>
        <line x1="3" y1="18" x2="3.01" y2="18"/>
    """,
    "bell": """
        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
        <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
    """,
    "database": """
        <ellipse cx="12" cy="5" rx="9" ry="3"/>
        <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
        <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
    """,
    "layers": """
        <polygon points="12 2 2 7 12 12 22 7 12 2"/>
        <polyline points="2 17 12 22 22 17"/>
        <polyline points="2 12 12 17 22 12"/>
    """,
    "filter": """
        <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>
    """,
    "sort": """
        <path d="m3 16 4 4 4-4"/>
        <path d="M7 20V4"/>
        <path d="m21 8-4-4-4 4"/>
        <path d="M17 4v16"/>
    """,
    "sidebar_collapse": """
        <rect x="3" y="3" width="18" height="18" rx="2"/>
        <line x1="9" y1="3" x2="9" y2="21"/>
        <polyline points="15 15 12 12 15 9"/>
    """,
    "sidebar_expand": """
        <rect x="3" y="3" width="18" height="18" rx="2"/>
        <line x1="9" y1="3" x2="9" y2="21"/>
        <polyline points="13 9 16 12 13 15"/>
    """,
    "sun": """
        <circle cx="12" cy="12" r="4"/>
        <line x1="12" y1="2" x2="12" y2="4"/>
        <line x1="12" y1="20" x2="12" y2="22"/>
        <line x1="4.93" y1="4.93" x2="6.34" y2="6.34"/>
        <line x1="17.66" y1="17.66" x2="19.07" y2="19.07"/>
        <line x1="2" y1="12" x2="4" y2="12"/>
        <line x1="20" y1="12" x2="22" y2="12"/>
        <line x1="6.34" y1="17.66" x2="4.93" y2="19.07"/>
        <line x1="19.07" y1="4.93" x2="17.66" y2="6.34"/>
    """,
    "moon": """
        <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
    """,
    "monitor": """
        <rect x="2" y="3" width="20" height="14" rx="2"/>
        <line x1="8" y1="21" x2="16" y2="21"/>
        <line x1="12" y1="17" x2="12" y2="21"/>
    """,
}

DEFAULT_ICON_SIZE = 20
DEFAULT_STROKE_WIDTH = 1.5


def _build_svg_xml(path_content: str, color: str, size: int, stroke_width: float = DEFAULT_STROKE_WIDTH) -> str:
    """Wrap path content into a valid standalone SVG document (1.5px stroke, round caps)."""
    resolved = path_content.replace("currentColor", color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'color="{color}" fill="none" stroke="{color}" stroke-width="{stroke_width}" '
        f'stroke-linecap="round" stroke-linejoin="round">{resolved}</svg>'
    )


_PIXMAP_CACHE: Dict[tuple, QPixmap] = {}


def clear_icon_cache(*_args) -> None:
    _PIXMAP_CACHE.clear()


Tokens.subscribe(clear_icon_cache)


def get_pixmap(
    name: str,
    color: Optional[str] = None,
    size: int = DEFAULT_ICON_SIZE,
    dpr: Optional[float] = None,
) -> QPixmap:
    """Render a vector SVG icon into a high-DPI crisp QPixmap (cached)."""
    if color is None:
        color = Tokens.colors.text

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if dpr is None:
        try:
            dpr = max(1.0, float(app.devicePixelRatio())) if app is not None else 1.0
        except Exception:
            dpr = 1.0
    dpr_key = round(dpr, 2)

    cache_key = (id(app), name, color, size, dpr_key) if app is not None else None
    if cache_key is not None and cache_key in _PIXMAP_CACHE:
        return _PIXMAP_CACHE[cache_key]

    raw_path = _SVG_PATHS.get(name) or _SVG_PATHS["file"]
    px_size = max(1, int(round(size * dpr_key)))
    xml = _build_svg_xml(raw_path, color, px_size, DEFAULT_STROKE_WIDTH)
    renderer = QSvgRenderer(QByteArray(xml.encode("utf-8")))

    pixmap = QPixmap(px_size, px_size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, px_size, px_size))
    painter.end()
    pixmap.setDevicePixelRatio(dpr_key)

    if cache_key is not None:
        if len(_PIXMAP_CACHE) > 512:
            _PIXMAP_CACHE.clear()
        _PIXMAP_CACHE[cache_key] = pixmap

    return pixmap


def get_icon(
    name: str,
    color: Optional[str] = None,
    active_color: Optional[str] = None,
    size: int = DEFAULT_ICON_SIZE,
) -> QIcon:
    """Create a QIcon with normal, active/hover, and disabled states."""
    if color is None:
        color = Tokens.colors.text
    if active_color is None:
        active_color = Tokens.colors.accent

    normal_pix = get_pixmap(name, color=color, size=size)
    active_pix = get_pixmap(name, color=active_color, size=size)
    disabled_pix = get_pixmap(name, color=Tokens.colors.text_muted, size=size)

    icon = QIcon()
    icon.addPixmap(normal_pix, QIcon.Mode.Normal)
    icon.addPixmap(active_pix, QIcon.Mode.Active)
    icon.addPixmap(active_pix, QIcon.Mode.Selected)
    icon.addPixmap(disabled_pix, QIcon.Mode.Disabled)
    return icon


def get_file_type_icon_name(mime_type: Optional[str], original_name: str = "") -> str:
    """Return appropriate SVG icon key for a file based on mime type or extension."""
    m = (mime_type or "").lower()
    ext = ("." + original_name.rsplit(".", 1)[-1].lower()) if "." in original_name else ""

    if m.startswith("image/") or ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".ico", ".svg"}:
        return "image"
    if m.startswith("video/") or ext in {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"}:
        return "video"
    if m.startswith("audio/") or ext in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}:
        return "music"
    if "zip" in m or "rar" in m or "tar" in m or "7z" in m or ext in {".zip", ".rar", ".7z", ".tar", ".gz"}:
        return "archive"
    if ext in {".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".cpp", ".c", ".h", ".rs", ".go"}:
        return "code"
    return "file_text"


__all__ = [
    "DEFAULT_ICON_SIZE",
    "DEFAULT_STROKE_WIDTH",
    "get_icon",
    "get_pixmap",
    "get_file_type_icon_name",
    "clear_icon_cache",
]
