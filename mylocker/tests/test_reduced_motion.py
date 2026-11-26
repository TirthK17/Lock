"""
test_reduced_motion.py — Verification of centralized motion helpers (U5) and reduced motion support.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QDialog, QFrame, QLabel, QPushButton, QStackedWidget, QWidget

from mylocker.app.ui.components import Toast
from mylocker.app.ui.dashboard import _AutoDismissBanner
from mylocker.app.ui.login_theme import is_reduced_motion
from mylocker.app.ui.motion import (
    animate_indicator_slide,
    crossfade_stack_page,
    dropdown_open,
    fade_in,
    modal_rise,
    slide_fade,
    stop_all_animations,
    toast_in,
    toast_out,
)
from mylocker.app.ui.widgets import PasswordStrengthBar


def test_is_reduced_motion_settings_store(monkeypatch):
    monkeypatch.delenv("MYLOCKER_FORCE_REDUCED_MOTION", raising=False)
    res_none = is_reduced_motion(None)
    assert isinstance(res_none, bool)

    mock_store_true = MagicMock()
    mock_store_true.get.side_effect = lambda k, default=None: True if k == "reduced_motion" else default
    assert is_reduced_motion(mock_store_true) is True

    mock_store_false = MagicMock()
    mock_store_false.get.side_effect = lambda k, default=None: False if k == "reduced_motion" else default
    with patch("ctypes.windll.user32.SystemParametersInfoW", return_value=1):
        assert isinstance(is_reduced_motion(mock_store_false), bool)


def test_is_reduced_motion_system_call(monkeypatch):
    monkeypatch.delenv("MYLOCKER_FORCE_REDUCED_MOTION", raising=False)
    with patch("ctypes.windll.user32.SystemParametersInfoW") as mock_spi:
        def set_val_zero(action, param, ref, winini):
            ref._obj.value = 0
            return 1

        mock_spi.side_effect = set_val_zero
        assert is_reduced_motion(None) is True

        def set_val_one(action, param, ref, winini):
            ref._obj.value = 1
            return 1

        mock_spi.side_effect = set_val_one
        assert is_reduced_motion(None) is False


def test_password_strength_bar_reduced_motion(qtbot, monkeypatch):
    monkeypatch.setattr("mylocker.app.ui.login_theme.is_reduced_motion", lambda: True)
    bar = PasswordStrengthBar()
    qtbot.addWidget(bar)
    score = bar.update_password("StrongPass123!@#")
    assert score > 0
    assert bar._bar.value() == score


def test_auto_dismiss_banner_reduced_motion(qtbot, monkeypatch):
    monkeypatch.setattr("mylocker.app.ui.login_theme.is_reduced_motion", lambda: True)
    banner = _AutoDismissBanner(timeout_ms=3000)
    qtbot.addWidget(banner)
    banner.show_animated()
    assert not banner.isHidden()
    assert banner.maximumHeight() == 16777215

    banner.dismiss_animated()
    assert banner.isHidden()


def test_motion_helpers_reduced_and_active(qtbot, monkeypatch):
    container = QWidget()
    container.resize(300, 200)
    qtbot.addWidget(container)
    child = QLabel("Hello", container)
    child.setGeometry(10, 10, 100, 30)
    target_btn = QPushButton("Nav", container)
    target_btn.setGeometry(10, 60, 120, 36)
    indicator = QFrame(container)
    indicator.setGeometry(10, 10, 120, 36)

    stack = QStackedWidget(container)
    stack.addWidget(QWidget())
    stack.addWidget(QWidget())

    # 1. With reduced motion active: zero running animations, immediate snap
    monkeypatch.setenv("MYLOCKER_FORCE_REDUCED_MOTION", "1")
    assert fade_in(child) is None
    assert slide_fade(child) is None
    assert animate_indicator_slide(indicator, target_btn) is None
    assert indicator.geometry() == target_btn.geometry()
    crossfade_stack_page(stack, 1)
    assert stack.currentIndex() == 1

    # 2. With normal motion active: parented OutCubic animations 120-220ms
    monkeypatch.delenv("MYLOCKER_FORCE_REDUCED_MOTION", raising=False)
    monkeypatch.setattr("mylocker.app.ui.motion.is_reduced_motion", lambda _s=None: False)
    monkeypatch.setattr("mylocker.app.ui.login_theme.is_reduced_motion", lambda _s=None: False)

    container.show()
    anim_fade = fade_in(child)
    assert anim_fade is not None
    assert 120 <= anim_fade.duration() <= 220

    anim_grp = slide_fade(child)
    assert anim_grp is not None

    dlg = QDialog()
    qtbot.addWidget(dlg)
    assert modal_rise(dlg) is not None
    assert dropdown_open(dlg) is not None

    toast = Toast("Saved", duration_ms=1200)
    qtbot.addWidget(toast)
    assert toast_in(toast) is not None
    closed: list[bool] = []
    toast_out(toast, on_finished=lambda: closed.append(True))

    stop_all_animations(container)
    stop_all_animations(dlg)
    stop_all_animations(toast)
