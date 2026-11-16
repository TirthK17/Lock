"""
config.py — Dedicated configuration and Argon2id verifier for App Lock.

Audit Requirement (Phase 6, Item 43):
  - Dedicated applock.config separate from the vault Master Key and vault.config.
  - Dedicated Argon2id password verifier (64 MiB, t=2, p=1).
  - Unlocking an app does NOT require loading or unwrapping the vault Master Key.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Set

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .process_utils import resolve_shortcut_target

logger = logging.getLogger(__name__)

CONFIG_NAME = "applock.config"
MEMORY_COST_KIB = 65536   # 64 MiB
TIME_COST = 2
PARALLELISM = 1


class AppLockConfig:
    """Manages applock.config, protected application list, and separate password verifier."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.config_path = config_dir / CONFIG_NAME
        self._hasher = PasswordHasher(
            time_cost=TIME_COST,
            memory_cost=MEMORY_COST_KIB,
            parallelism=PARALLELISM,
            hash_len=32,
            salt_len=16,
        )
        self._data: dict = {
            "version": 1,
            "enabled": True,
            "argon2id_hash": "",
            "locked_apps": [],
            "grace_period_minutes": 5,
        }
        self.load()

    def exists(self) -> bool:
        return self.config_path.exists()

    def load(self) -> None:
        """Load configuration from disk if it exists."""
        if not self.config_path.exists():
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                self._data = json.load(fh)
        except Exception as exc:
            logger.warning("Could not read applock.config: %s", exc)

    def save(self) -> None:
        """Atomically persist configuration."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(self.config_path)
            logger.debug("applock.config saved successfully.")
        except Exception as exc:
            logger.error("Failed to save applock.config: %s", exc)
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    # ── Password Verification ─────────────────────────────────────────────────

    def set_password(self, password: str) -> None:
        """Hash and store the app lock password using Argon2id (64 MiB, t=2)."""
        hashed = self._hasher.hash(password)
        self._data["argon2id_hash"] = hashed
        self.save()
        logger.info("App Lock password updated.")

    def verify_password(self, password: str) -> bool:
        """Verify an entered password against the Argon2id hash.

        Does not require or access the vault Master Key.
        """
        hash_val = self._data.get("argon2id_hash", "")
        if not hash_val:
            return False
        try:
            return self._hasher.verify(hash_val, password)
        except VerifyMismatchError:
            return False
        except Exception as exc:
            logger.warning("Error verifying app lock password: %s", exc)
            return False

    def is_configured(self) -> bool:
        """Return True if an app lock password has been configured."""
        return bool(self._data.get("argon2id_hash"))

    # ── Application Management ────────────────────────────────────────────────

    def add_locked_app(self, path_or_name: str) -> str:
        """Add an executable to the locked list, resolving .lnk shortcuts (Item 45).

        Returns:
            The normalized executable name (e.g. 'chrome.exe').
        """
        target = resolve_shortcut_target(path_or_name)
        p = Path(target)
        exe_name = p.name.lower()
        if not exe_name.endswith(".exe"):
            exe_name += ".exe"

        current = set(self._data.get("locked_apps", []))
        current.add(exe_name)
        self._data["locked_apps"] = sorted(current)
        self.save()
        logger.info("Added %s to App Lock list.", exe_name)
        return exe_name

    def remove_locked_app(self, exe_name: str) -> None:
        """Remove an executable from the locked list."""
        clean_name = exe_name.lower().strip()
        current = set(self._data.get("locked_apps", []))
        current.discard(clean_name)
        self._data["locked_apps"] = sorted(current)
        self.save()
        logger.info("Removed %s from App Lock list.", clean_name)

    def get_locked_apps(self) -> Set[str]:
        """Return the set of protected executable names in lowercase."""
        return set(self._data.get("locked_apps", []))

    def get_grace_minutes(self) -> int:
        return int(self._data.get("grace_period_minutes", 5))

    def set_grace_minutes(self, minutes: int) -> None:
        self._data["grace_period_minutes"] = max(0, int(minutes))
        self.save()


__all__ = ["AppLockConfig"]
