"""
settings_store.py — HMAC-signed per-vault settings store.

Audit Requirement (Phase 7, Item 48):
  - HMAC-SHA256 integrity protection on settings.dat.
  - Detects tampering, resets to secure defaults if modified outside app.
  - Controls auto-lock, screenshot protection, disguise mode, and clipboard wiping.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

SETTINGS_FILENAME = "settings.dat"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "idle_timeout_minutes": 5,
    "lock_on_minimize": True,
    "screenshot_protection": True,
    "clipboard_wipe_seconds": 30,
    "calculator_disguise_enabled": False,
    "calculator_disguise_pin": "1337",
    "tray_only_mode": False,
    "intruder_webcam_enabled": True,
    "self_destruct_enabled": False,
}


class SettingsStore:
    """HMAC-signed persistent settings store."""

    def __init__(self, vault_dir: Path, signing_key: bytes, auto_create: bool = True) -> None:
        self.vault_dir = vault_dir
        self.settings_path = vault_dir / SETTINGS_FILENAME
        self.signing_key = signing_key
        self._auto_create = auto_create
        self._data: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.load()

    def _compute_mac(self, payload: Dict[str, Any]) -> str:
        """Compute HMAC-SHA256 hex string over sorted JSON payload excluding _mac."""
        clean = {k: v for k, v in payload.items() if k != "_mac"}
        raw = json.dumps(clean, sort_keys=True).encode("utf-8")
        return hmac.new(self.signing_key, raw, hashlib.sha256).hexdigest()

    def load(self) -> None:
        """Load settings from disk and verify HMAC signature."""
        if not self.settings_path.exists():
            self._data = dict(DEFAULT_SETTINGS)
            if self._auto_create:
                self.save()
            return

        try:
            with open(self.settings_path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)

            stored_mac = stored.get("_mac", "")
            expected_mac = self._compute_mac(stored)

            if not hmac.compare_digest(stored_mac, expected_mac):
                logger.warning("Settings MAC mismatch — tampering detected! Reverting to secure defaults.")
                self._data = dict(DEFAULT_SETTINGS)
                self.save()
                return

            # Merge with defaults for forward compatibility
            self._data = dict(DEFAULT_SETTINGS)
            for k, v in stored.items():
                if k != "_mac":
                    self._data[k] = v

        except Exception as exc:
            logger.warning("Error reading settings (%s). Reverting to defaults.", exc)
            self._data = dict(DEFAULT_SETTINGS)
            self.save()

    def save(self) -> None:
        """Persist settings with HMAC signature."""
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        to_save = dict(self._data)
        to_save["_mac"] = self._compute_mac(to_save)

        tmp = self.settings_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(to_save, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(self.settings_path)
        except Exception as exc:
            logger.error("Failed to save settings: %s", exc)
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def rekey(self, new_signing_key: bytes) -> None:
        """Update the HMAC signing key (e.g. after KDF salt change) and re-sign existing settings."""
        self.signing_key = new_signing_key
        self.save()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()


__all__ = ["SettingsStore", "DEFAULT_SETTINGS"]
