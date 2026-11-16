"""
lockout.py — Failed-attempt counter with DPAPI protection, exponential backoff, and rollback detection.

Audit Requirements (Phase 3 item 24 & 26):
  - Lockout with DPAPI: CryptProtectData for lockout state.
  - Fail-closed: Tampering or corruption triggers security lockout in check_lockout.
  - Exponential backoff schedule.
  - Monotonic clock + wall-clock rollback detection (prevents clock tampering).
  - Encrypted intruder log with local time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LOCKOUT_FILENAME = "lockout.dat"
INTRUDER_LOG_FILENAME = "intruder.dat"

try:
    import win32crypt
    HAS_DPAPI = True
except ImportError:
    HAS_DPAPI = False

# Lockout schedule: (min_fails, lockout_seconds)
LOCKOUT_SCHEDULE = [
    (20, 3600),
    (15, 1800),
    (10,  300),
    ( 5,   60),
    ( 3,   30),
]


class LockoutError(Exception):
    """Raised when the vault is in a lockout period."""

    def __init__(self, remaining_seconds: float) -> None:
        super().__init__(f"Too many failed attempts. Wait {remaining_seconds:.0f}s.")
        self.remaining_seconds = remaining_seconds


class LockoutManager:
    """Manages the failed-attempt counter with DPAPI encryption and HMAC integrity protection."""

    def __init__(self, vault_dir: Path, signing_secret: bytes) -> None:
        self.vault_dir = vault_dir
        self._signing_secret = signing_secret
        self._path = vault_dir / LOCKOUT_FILENAME
        self._intruder_path = vault_dir / INTRUDER_LOG_FILENAME

    # ── Main API ──────────────────────────────────────────────────────────────

    def check_lockout(self) -> None:
        """Raise LockoutError if currently in a lockout period or if tampering was detected."""
        state = self._load_state()
        if state.get("tampered"):
            raise LockoutError(3600.0)
        remaining = self._lockout_remaining(state)
        if remaining > 0:
            raise LockoutError(remaining)

    def record_failure(self) -> int:
        """Increment failure count, update timestamps, and record intruder log entry."""
        state = self._load_state()
        state["fails"] = state.get("fails", 0) + 1
        state["last_fail_time"] = time.time()
        state["last_fail_mono"] = time.monotonic()
        state.pop("tampered", None)
        self._save_state(state)
        self._append_intruder_log(state["fails"])
        logger.warning("Failed login attempt #%d recorded.", state["fails"])
        return state["fails"]

    def record_success(self) -> None:
        """Reset the failure counter on successful login."""
        state = self._load_state()
        previous_fails = state.get("fails", 0)
        state["fails"] = 0
        state["last_fail_time"] = None
        state["last_fail_mono"] = None
        state.pop("tampered", None)
        self._save_state(state)
        if previous_fails > 0:
            logger.info("Successful login after %d failed attempt(s).", previous_fails)

    def get_fail_count(self) -> int:
        return self._load_state().get("fails", 0)

    def get_lockout_seconds(self) -> float:
        state = self._load_state()
        if state.get("tampered"):
            return 3600.0
        return self._lockout_remaining(state)

    def clear(self) -> None:
        """Wipe the lockout state (call after change-password, etc.)."""
        self._save_state({"fails": 0, "last_fail_time": None, "last_fail_mono": None})

    def rekey(self, new_signing_secret: bytes, reset_failures: bool = True) -> None:
        """Update the HMAC signing secret (e.g. after KDF salt change) without causing a tamper lockout."""
        if reset_failures or not self._path.exists():
            self._signing_secret = new_signing_secret
            self._save_state({"fails": 0, "last_fail_time": None, "last_fail_mono": None})
            return
        state = self._load_state()
        if state.get("tampered"):
            state = {"fails": 0, "last_fail_time": None, "last_fail_mono": None}
        self._signing_secret = new_signing_secret
        self._save_state(state)

    # ── Intruder log (local time, encrypted via DPAPI) ────────────────────────

    def get_intruder_log(self) -> list[dict]:
        """Return a list of intruder log entries."""
        if not self._intruder_path.exists():
            return []
        try:
            with open(self._intruder_path, "r", encoding="utf-8") as fh:
                raw_json = json.load(fh)
            if isinstance(raw_json, dict) and "_dpapi" in raw_json and HAS_DPAPI:
                protected = base64.b64decode(raw_json["_dpapi"])
                _, raw = win32crypt.CryptUnprotectData(protected, None, None, None, 0)
                return json.loads(raw.decode("utf-8"))
            if isinstance(raw_json, list):
                return raw_json
            return []
        except Exception:
            return []

    def clear_intruder_log(self) -> None:
        """Wipe the intruder log entries."""
        self._write_intruder_log([])

    def attach_photo_to_latest(self, photo_rel: str) -> None:
        """Attach a captured encrypted webcam photo path to the latest intruder entry."""
        log = self.get_intruder_log()
        if log:
            log[-1]["has_photo"] = True
            log[-1]["photo_file"] = photo_rel
            self._write_intruder_log(log)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _lockout_remaining(self, state: dict) -> float:
        """How many seconds remain in the current lockout period (0 = not locked)."""
        fails = state.get("fails", 0)
        last_fail = state.get("last_fail_time")
        if last_fail is None or fails == 0:
            return 0.0

        # Rollback detection: if current wall clock is earlier than last_fail, clock was tampered!
        now_wall = time.time()
        if now_wall < (last_fail - 5.0):
            logger.warning("Clock rollback detected! Enforcing fail-closed lockout.")
            return 3600.0

        lockout_duration = 0.0
        for threshold, duration in LOCKOUT_SCHEDULE:
            if fails >= threshold:
                lockout_duration = float(duration)
                break

        if lockout_duration == 0:
            return 0.0

        elapsed = now_wall - last_fail

        # Monotonic verification within the same system boot (Audit Phase 14)
        last_mono = state.get("last_fail_mono")
        now_mono = time.monotonic()
        if last_mono is not None and now_mono >= last_mono:
            elapsed_mono = now_mono - last_mono
            # If wall-clock was artificially pushed forward, clamp to true monotonic elapsed time
            if elapsed > (elapsed_mono + 5.0):
                logger.warning("Clock fast-forward detected! Clamping to monotonic elapsed time.")
                elapsed = elapsed_mono
            else:
                elapsed = min(elapsed, elapsed_mono)

        remaining = lockout_duration - elapsed
        return max(0.0, remaining)

    def _load_state(self) -> dict:
        """Load and verify the lockout state from disk using DPAPI + HMAC."""
        if not self._path.exists():
            return {"fails": 0, "last_fail_time": None}

        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw_json = json.load(fh)

            if isinstance(raw_json, dict) and "_dpapi" in raw_json and HAS_DPAPI:
                outer_mac = raw_json.get("_mac")
                try:
                    protected = base64.b64decode(raw_json["_dpapi"])
                    _, raw = win32crypt.CryptUnprotectData(protected, None, None, None, 0)
                    data = json.loads(raw.decode("utf-8"))
                    if outer_mac and outer_mac != data.get("_mac"):
                        logger.warning("Outer MAC mismatch — tampering detected.")
                        return {"fails": 0, "last_fail_time": time.time(), "tampered": True}
                except Exception:
                    logger.warning("DPAPI unprotect failed — tampering detected.")
                    return {"fails": 0, "last_fail_time": time.time(), "tampered": True}
            else:
                data = raw_json

            stored_mac = bytes.fromhex(data.pop("_mac", ""))
            payload = json.dumps(data, sort_keys=True).encode()
            expected_mac = self._compute_mac(payload)

            if not hmac.compare_digest(stored_mac, expected_mac):
                logger.warning("Lockout file HMAC mismatch — tampering detected.")
                return {"fails": 0, "last_fail_time": time.time(), "tampered": True}

            return data
        except Exception:
            logger.warning("Lockout file corrupt — fail closed.")
            return {"fails": 0, "last_fail_time": time.time(), "tampered": True}

    def _save_state(self, state: dict) -> None:
        """Sign with HMAC, optionally DPAPI protect, and atomically save."""
        clean = {k: v for k, v in state.items() if k not in ("_mac", "tampered")}
        payload = json.dumps(clean, sort_keys=True).encode("utf-8")
        mac = self._compute_mac(payload)
        clean["_mac"] = mac.hex()

        if HAS_DPAPI:
            try:
                serialized = json.dumps(clean).encode("utf-8")
                protected = win32crypt.CryptProtectData(serialized, "PersonalVaultLockout", None, None, None, 0)
                file_dict = {
                    "_dpapi": base64.b64encode(protected).decode("ascii"),
                    "_mac": mac.hex(),
                    "fails": clean.get("fails", 0),
                    "last_fail_time": clean.get("last_fail_time"),
                }
            except Exception:
                file_dict = clean
        else:
            file_dict = clean

        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(file_dict, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(self._path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _compute_mac(self, payload: bytes) -> bytes:
        return hmac.new(self._signing_secret, payload, hashlib.sha256).digest()

    def _append_intruder_log(self, fail_count: int) -> None:
        log = self.get_intruder_log()
        # Local time with timezone offset
        log.append({
            "timestamp": datetime.now().astimezone().isoformat(),
            "fail_count": fail_count,
            "has_photo": False,
        })
        self._write_intruder_log(log)

    def _write_intruder_log(self, log: list) -> None:
        serialized = json.dumps(log, indent=2).encode("utf-8")
        if HAS_DPAPI:
            try:
                protected = win32crypt.CryptProtectData(serialized, "PersonalVaultIntruderLog", None, None, None, 0)
                file_dict = {
                    "_dpapi": base64.b64encode(protected).decode("ascii")
                }
            except Exception:
                file_dict = log
        else:
            file_dict = log

        tmp = self._intruder_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(file_dict, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(self._intruder_path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise


def make_lockout_signing_key(kdf_salt: bytes) -> bytes:
    """Derive a stable signing key from the KDF salt (no MK needed)."""
    return hmac.new(kdf_salt, b"PersonalVault-LockoutHMAC-v1", hashlib.sha256).digest()


__all__ = [
    "LockoutError",
    "LockoutManager",
    "LOCKOUT_SCHEDULE",
    "make_lockout_signing_key",
]
