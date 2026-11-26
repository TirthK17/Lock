"""Tests for U1 Design System tokens, contrast helpers, bundled Inter fonts, and icon SVG stroke."""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from mylocker.app.ui import icons
from mylocker.app.ui.dashboard_theme import DashColors
from mylocker.app.ui.design_system import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    Tokens,
    apply_theme,
    build_global_qss,
)
from mylocker.app.ui.login_theme import LoginColors
from mylocker.app.ui.theme import Colors


def _hex_to_rgb(hx: str) -> tuple[float, float, float]:
    hx = hx.lstrip("#")
    return tuple(int(hx[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _rel_lum(hx: str) -> float:
    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (_lin(c) for c in _hex_to_rgb(hx))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    l1, l2 = sorted((_rel_lum(a), _rel_lum(b)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def test_tokens_palettes_and_scale_match_spec():
    assert DARK_PALETTE.bg == "#0B0D12"
    assert DARK_PALETTE.bg_secondary == "#11141B"
    assert DARK_PALETTE.card == "#171B24"
    assert DARK_PALETTE.elevated == "#1D2230"
    assert DARK_PALETTE.text == "#F5F6F8"
    assert DARK_PALETTE.text_secondary == "#969DAA"
    assert DARK_PALETTE.text_muted == "#6B7280"
    assert DARK_PALETTE.accent == "#8B9CFB"
    assert DARK_PALETTE.accent_hover == "#A1AFFC"
    assert DARK_PALETTE.accent_pressed == "#7284EE"

    assert LIGHT_PALETTE.bg == "#F6F7F9"
    assert LIGHT_PALETTE.card == "#FFFFFF"
    assert LIGHT_PALETTE.text == "#17191F"
    assert LIGHT_PALETTE.text_secondary == "#747B87"
    assert LIGHT_PALETTE.accent == "#5269D8"

    assert (Tokens.type.META, Tokens.type.LABEL, Tokens.type.BODY, Tokens.type.SECTION, Tokens.type.PAGE_TITLE, Tokens.type.LOGIN_TITLE) == (11, 12, 13, 15, 20, 28)
    assert (Tokens.radius.CONTROL, Tokens.radius.CARD, Tokens.radius.DIALOG) == (8, 12, 16)
    assert (Tokens.spacing.XS_4, Tokens.spacing.SM_8, Tokens.spacing.MD_12, Tokens.spacing.LG_16, Tokens.spacing.XL_24, Tokens.spacing.XXL_32) == (4, 8, 12, 16, 24, 32)


def test_theme_aliases_track_active_mode(qapp):
    apply_theme(qapp, "dark")
    assert Colors.BG_DARKEST == "#0B0D12"
    assert DashColors.ACCENT_BLUE == "#8B9CFB"
    assert LoginColors.BG_PRIMARY == "#0B0D12"

    apply_theme(qapp, "light")
    assert Colors.BG_DARKEST == "#F6F7F9"
    assert DashColors.BG_CARD == "#FFFFFF"
    assert LoginColors.TEXT_PRIMARY == "#17191F"

    # Restore default dark theme
    apply_theme(qapp, "dark")


def test_inter_fonts_loaded_and_global_qss_clean(qapp):
    apply_theme(qapp, "dark")
    loaded = Tokens.type.ensure_fonts_loaded()
    assert "Inter" in loaded

    qss = build_global_qss(DARK_PALETTE)
    assert "qlineargradient" not in qss
    assert ":/icons/check.svg" not in qss
    assert "#0B0D12" in qss


def test_icon_svg_defaults_1_5px_and_20px(qapp):
    assert icons.DEFAULT_ICON_SIZE == 20
    assert icons.DEFAULT_STROKE_WIDTH == 1.5
    xml = icons._build_svg_xml("<path d='M0 0'/>", "#F5F6F8", 20)
    assert 'stroke-width="1.5"' in xml
    px = icons.get_pixmap("shield")
    assert not px.isNull()
    assert px.deviceIndependentSize().width() == 20


def test_contrast_on_primary_buttons_and_surfaces():
    assert _contrast(DARK_PALETTE.text, DARK_PALETTE.bg) >= 4.5
    assert _contrast(DARK_PALETTE.text_secondary, DARK_PALETTE.card) >= 4.5
    assert _contrast(DARK_PALETTE.on_accent, DARK_PALETTE.accent) >= 4.5
    assert _contrast(LIGHT_PALETTE.text, LIGHT_PALETTE.bg) >= 4.5
    assert _contrast(LIGHT_PALETTE.on_accent, LIGHT_PALETTE.accent) >= 4.5


def test_system_theme_resolution_uses_qt_color_scheme(qapp):
    from PySide6.QtCore import Qt

    with patch.object(qapp.styleHints(), "colorScheme", return_value=Qt.ColorScheme.Light):
        pal = apply_theme(qapp, "system")
        assert pal.name == "light"

    with patch.object(qapp.styleHints(), "colorScheme", return_value=Qt.ColorScheme.Dark):
        pal = apply_theme(qapp, "system")
        assert pal.name == "dark"
