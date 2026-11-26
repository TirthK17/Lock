"""
theme.py — Thin compatibility facade over design_system.Tokens.

Keeps `Colors`, `GLOBAL_QSS`, and `apply_dark_theme(app)` working seamlessly
for any legacy imports while resolving all colors dynamically from `Tokens`.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from .design_system import Tokens, apply_theme, build_global_qss


def _color_attr_map() -> dict[str, str]:
    p = Tokens.colors
    return {
        "BG_DARKEST": p.bg,
        "BG_DARK": p.bg_secondary,
        "BG_CARD": p.card,
        "BG_CARD_HOVER": p.elevated,
        "BG_INPUT": p.input_bg,
        "BG_SIDEBAR": p.bg_secondary,
        "ACCENT": p.accent,
        "PRIMARY": p.accent,
        "ACCENT_HOVER": p.accent_hover,
        "ACCENT_DARK": p.accent_pressed,
        "SUCCESS": p.success,
        "WARNING": p.warning,
        "DANGER": p.danger,
        "DANGER_DARK": p.danger_pressed,
        "WHITE": p.white,
        "TEXT_PRIMARY": p.text,
        "TEXT_SECONDARY": p.text_secondary,
        "TEXT_MUTED": p.text_muted,
        "TEXT_ACCENT": p.accent,
        "BORDER": p.border,
        "BORDER_FOCUS": p.border_focus,
        "BORDER_SUBTLE": p.border_subtle,
        "LOCKED_RED": p.danger,
        "UNLOCKED_GREEN": p.success,
        "ICON_BG": p.elevated,
        "GLASS": p.overlay,
    }


class _ColorsAliasMeta(type):
    def __getattr__(cls, name: str) -> str:
        mapping = _color_attr_map()
        if name in mapping:
            return mapping[name]
        raise AttributeError(f"Colors has no attribute {name!r}")


class Colors(metaclass=_ColorsAliasMeta):
    """Backward-compatible dynamic alias to Tokens.colors."""


GLOBAL_QSS: str = build_global_qss(Tokens.colors)


def apply_dark_theme(app: QApplication, mode: str = "dark") -> None:
    """Apply the design system theme palette + stylesheet to QApplication."""
    global GLOBAL_QSS
    pal = apply_theme(app, mode=mode)
    GLOBAL_QSS = build_global_qss(pal)


__all__ = ["Colors", "GLOBAL_QSS", "apply_dark_theme"]
