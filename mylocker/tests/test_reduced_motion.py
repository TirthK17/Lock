"""
test_reduced_motion.py — Verification of Phase 5 subtle motion and reduced motion support.
"""

from unittest.mock import MagicMock, patch

from mylocker.app.ui.dashboard import _AutoDismissBanner
from mylocker.app.ui.login_theme import is_reduced_motion
from mylocker.app.ui.widgets import PasswordStrengthBar


def test_is_reduced_motion_settings_store():
    # When settings_store is None, default reflects system setting
    res_none = is_reduced_motion(None)
    assert isinstance(res_none, bool)

    # When settings_store has reduced_motion=True
    mock_store_true = MagicMock()
    mock_store_true.get.side_effect = lambda k, default=None: True if k == "reduced_motion" else default
    assert is_reduced_motion(mock_store_true) is True

    # When settings_store has reduced_motion=False
    mock_store_false = MagicMock()
    mock_store_false.get.side_effect = lambda k, default=None: False if k == "reduced_motion" else default
    with patch("ctypes.windll.user32.SystemParametersInfoW", return_value=1):
        with patch("ctypes.byref"):
            pass


def test_is_reduced_motion_system_call():
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
