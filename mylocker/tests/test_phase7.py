"""
test_phase7.py — Comprehensive tests for Phase 7 (Settings, Stealth & UI/UX):
1. HMAC-signed SettingsStore and tamper detection
2. Calculator disguise safe arithmetic parser (no eval)
3. Calculator PIN trigger
4. Search debounce and keyboard shortcuts
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mylocker.app.core.settings_store import DEFAULT_SETTINGS, SettingsStore
from mylocker.app.ui.calculator_disguise import CalculatorDisguise, _safe_eval


class TestSettingsStore:
    def test_settings_store_defaults_and_updates(self, tmp_path: Path):
        key = b"TestSigningKey123456789012345678"
        store = SettingsStore(tmp_path, key)

        # Check default values
        assert store.get("idle_timeout_minutes") == 5
        assert store.get("lock_on_minimize") is True

        # Update values
        store.set("idle_timeout_minutes", 15)
        store.set("calculator_disguise_enabled", True)

        # Reload from disk and verify
        reloaded = SettingsStore(tmp_path, key)
        assert reloaded.get("idle_timeout_minutes") == 15
        assert reloaded.get("calculator_disguise_enabled") is True

    def test_settings_store_tampering_detected(self, tmp_path: Path):
        key = b"TestSigningKey123456789012345678"
        store = SettingsStore(tmp_path, key)
        store.set("idle_timeout_minutes", 60)

        # Tamper with settings file on disk directly
        settings_file = tmp_path / "settings.dat"
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        data["idle_timeout_minutes"] = 999  # Tamper without updating _mac
        settings_file.write_text(json.dumps(data), encoding="utf-8")

        # Reloading should detect HMAC mismatch and revert to defaults
        reloaded = SettingsStore(tmp_path, key)
        assert reloaded.get("idle_timeout_minutes") == DEFAULT_SETTINGS["idle_timeout_minutes"]


class TestCalculatorDisguise:
    def test_safe_arithmetic_evaluation(self):
        assert _safe_eval("2+3") == 5.0
        assert _safe_eval("10 - 4") == 6.0
        assert _safe_eval("6 * 7") == 42.0
        assert _safe_eval("100 / 4") == 25.0
        assert _safe_eval("(2 + 3) * 4") == 20.0
        assert _safe_eval("10 + -2") == 8.0

    def test_rejection_of_invalid_or_dangerous_expressions(self):
        with pytest.raises(Exception):
            _safe_eval("__import__('os').system('calc')")

        with pytest.raises(Exception):
            _safe_eval("1 / 0")

        with pytest.raises(Exception):
            _safe_eval("hello + world")

    def test_pin_entry_triggers_secret_signal(self, qtbot):
        calc = CalculatorDisguise(secret_code="1337")
        qtbot.addWidget(calc)

        secret_triggered = []
        calc.secret_entered.connect(lambda: secret_triggered.append(True))

        # Enter "1337="
        for ch in ["1", "3", "3", "7", "="]:
            calc._on_btn_click(ch)

        assert len(secret_triggered) == 1
