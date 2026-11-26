"""U10 Dark / Light / System appearance setting, persistence (SettingsStore HMAC), and live re-theming tests."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mylocker.app.core.index import FileEntry
from mylocker.app.core.settings_store import SettingsStore
from mylocker.app.ui.dashboard import Dashboard
from mylocker.app.ui.design_system import Tokens, apply_theme
from mylocker.app.ui.login import LoginScreen
from mylocker.app.ui.settings import SettingsUI


@pytest.fixture(autouse=True)
def _u10_env(qtbot):
    os.environ["MYLOCKER_FORCE_REDUCED_MOTION"] = "1"
    app = QApplication.instance()
    if app is not None:
        apply_theme(app, "dark")
    yield
    os.environ.pop("MYLOCKER_FORCE_REDUCED_MOTION", None)
    if app is not None:
        apply_theme(app, "dark")


def test_settings_appearance_defaults_dark_switches_themes_and_verifies_hmac(qtbot, tmp_path):
    app = QApplication.instance()
    assert app is not None

    signing_key = b"0123456789abcdef0123456789abcdef"
    store = SettingsStore(tmp_path, signing_key, auto_create=True)

    vault = MagicMock()
    vault.vault_dir = tmp_path
    ks = MagicMock()
    ks.config.exists.return_value = False

    settings_ui = SettingsUI(vault, ks, settings_store=store)
    qtbot.addWidget(settings_ui)
    settings_ui.show()

    # Default is dark
    assert settings_ui.appearance_switch.current() == "dark"
    assert Tokens.mode == "dark"

    # Click Light in Settings -> persists cleanly in SettingsStore & switches active palette
    settings_ui.appearance_switch._buttons["light"].click()
    assert Tokens.mode == "light"
    assert Tokens.colors.bg == "#F6F7F9"
    assert store.get("appearance") == "light"

    # Reload SettingsStore from disk to confirm HMAC-SHA256 verification succeeds (did NOT reset to defaults!)
    reloaded = SettingsStore(tmp_path, signing_key, auto_create=False)
    assert reloaded.get("appearance") == "light"

    # Switch to System via button click and test Qt colorScheme resolution
    with patch.object(app.styleHints(), "colorScheme", return_value=Qt.ColorScheme.Dark):
        settings_ui.appearance_switch._buttons["system"].click()
        assert Tokens.mode == "dark"
        reloaded_sys = SettingsStore(tmp_path, signing_key, auto_create=False)
        assert reloaded_sys.get("appearance") == "system"

    settings_ui.close()


def test_all_primary_screens_live_retheme_between_dark_and_light(qtbot, tmp_path):
    app = QApplication.instance()
    assert app is not None

    ks = MagicMock()
    ks.vault_exists.return_value = True
    ks.has_keyfile.return_value = False
    lm = MagicMock()
    lm.is_locked_out.return_value = False
    lm.remaining_seconds.return_value = 0
    lm.get_lockout_seconds.return_value = 0
    ss = MagicMock()
    ss.get.side_effect = lambda _k, default=None: default

    vault = MagicMock()
    vault.is_unlocked = True
    vault.vault_dir = tmp_path
    vault.list_entries.return_value = []
    vault.list_trash.return_value = []
    vault.get_total_size.return_value = 0

    session = MagicMock()
    session.is_unlocked = True
    session.key = b"\x00" * 32

    login = LoginScreen(keystore=ks, lockout_manager=lm, settings_store=ss)
    qtbot.addWidget(login)
    login.show()

    dash = Dashboard(vault=vault, session=session, keystore=ks, lockout=lm, settings_store=ss)
    qtbot.addWidget(dash)
    dash.show()
    entry = FileEntry(
        file_id="f1",
        original_name="sample.pdf",
        original_path="C:/tmp/sample.pdf",
        vault_path="",
        size_bytes=512,
        modified_time=1700000000.0,
        mime_type="application/pdf",
    )
    dash._populate_grid([entry])
    QApplication.processEvents()

    apply_theme(app, "light")
    QApplication.processEvents()
    assert "#F6F7F9" in login.styleSheet()
    assert "#F6F7F9" in dash._central.styleSheet()
    assert "#FFFFFF" in dash._cards_by_id["f1"].styleSheet()
    assert "#F6F7F9" in app.styleSheet()

    apply_theme(app, "dark")
    QApplication.processEvents()
    assert "#0B0D12" in login.styleSheet()
    assert "#0B0D12" in dash._central.styleSheet()
    assert "#171B24" in dash._cards_by_id["f1"].styleSheet()

    login.close()
    dash.close()
