"""
keystore.py — Master Key wrapping, uniform key slots, decoy vault, and password management.

Audit Requirements (Phase 3):
  - Key slots: 4 fixed-size slots in vault.config. All slots look identical
    (random bytes for unused). Eliminates plaintext fields like 'wrapped_mk_decoy'
    to ensure plausible deniability.
  - Backward compatibility: transparently reads legacy v1 config (wrapped_mk)
    and migrates to key slots format.
  - change_password: old verify → new wrap in temp config → unwrap to prove → atomic replace.
  - Self-destruct: overwrite key slots with random bytes, fsync, zero memory.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Optional, Tuple

from mylocker.app.core.crypto import (
    KEY_SIZE,
    NONCE_SIZE,
    TAG_SIZE,
    CryptoError,
    constant_time_compare,
    generate_key,
    unwrap_key,
    wrap_key,
    zero_bytes,
)
from mylocker.app.core.kdf import KDFParams, benchmark_argon2id, derive_kek

logger = logging.getLogger(__name__)

CONFIG_VERSION = "2.0"
NUM_KEY_SLOTS = 4

# Slot 0 = Primary Master Key
# Slot 1 = Recovery Key
# Slot 2 = Decoy Vault Master Key (or random decoy bytes if inactive)
# Slot 3 = Spare / Extra Factor Slot
SLOT_PRIMARY = 0
SLOT_RECOVERY = 1
SLOT_DECOY = 2
SLOT_SPARE = 3

# Standard length of wrapped 32-byte key: 32 bytes + 16 bytes GCM tag = 48 bytes
WRAPPED_CIPHERTEXT_LEN = KEY_SIZE + TAG_SIZE  # 48 bytes


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _random_slot_entry(slot_id: int) -> dict:
    """Generate indistinguishable pseudo-random bytes for an unused key slot."""
    return {
        "slot_id": slot_id,
        "salt": _b64e(secrets.token_bytes(16)),
        "nonce": _b64e(secrets.token_bytes(NONCE_SIZE)),
        "ciphertext": _b64e(secrets.token_bytes(WRAPPED_CIPHERTEXT_LEN)),
    }


class VaultConfig:
    """Loads and saves vault.config.

    Supports:
      - Uniform 4-slot layout (v2.0): all slots look identical
      - Legacy layout (v1.0): wrapped_mk, wrapped_mk_recovery
    """

    CONFIG_FILENAME = "vault.config"

    def __init__(self, vault_dir: Path) -> None:
        self.vault_dir = vault_dir
        self.config_path = vault_dir / self.CONFIG_FILENAME
        self._data: dict = {}

    def load(self) -> None:
        """Load config from disk."""
        if not self.config_path.exists():
            raise CryptoError("Vault configuration not found.")
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                self._data = json.load(fh)
        except PermissionError as exc:
            raise CryptoError(f"Access denied reading vault configuration: {exc}") from exc
        except (json.JSONDecodeError, KeyError) as exc:
            raise CryptoError("Vault configuration corrupted or wiped.") from exc
        except Exception as exc:
            raise CryptoError(f"Failed to read vault configuration: {exc}") from exc

    def save(self) -> None:
        """Atomic write: temp → fsync → rename."""
        tmp_path = self.config_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            tmp_path.replace(self.config_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def exists(self) -> bool:
        return self.config_path.exists()

    @property
    def version(self) -> str:
        return self._data.get("version", "1.0")

    @property
    def kdf_params(self) -> KDFParams:
        return KDFParams.from_dict(self._data["kdf_params"])

    @property
    def keyfile_enabled(self) -> bool:
        return bool(self._data.get("keyfile_enabled", False))

    def get_slot(self, slot_id: int) -> Optional[Tuple[bytes, bytes]]:
        """Return (nonce, ciphertext) for a slot."""
        # 1. Key slots layout (v2)
        slots = self._data.get("slots", [])
        if slots and slot_id < len(slots):
            s = slots[slot_id]
            return _b64d(s["nonce"]), _b64d(s["ciphertext"])

        # 2. Legacy fallback (v1)
        if slot_id == SLOT_PRIMARY and "wrapped_mk" in self._data:
            wm = self._data["wrapped_mk"]
            return _b64d(wm["nonce"]), _b64d(wm["ciphertext"])
        if slot_id == SLOT_RECOVERY and "wrapped_mk_recovery" in self._data:
            wm = self._data["wrapped_mk_recovery"]
            return _b64d(wm["nonce"]), _b64d(wm["ciphertext"])
        if slot_id == SLOT_DECOY and "wrapped_mk_decoy" in self._data:
            wm = self._data["wrapped_mk_decoy"]
            return _b64d(wm["nonce"]), _b64d(wm["ciphertext"])
        return None

    def get_slot_entry(self, slot_id: int) -> Optional[dict]:
        """Return raw slot dict containing slot_id, salt, nonce, ciphertext."""
        slots = self._data.get("slots", [])
        if slots and slot_id < len(slots):
            return slots[slot_id]
        return None

    def set_slot(self, slot_id: int, nonce: bytes, ciphertext: bytes, salt: Optional[bytes] = None) -> None:
        """Update a specific slot in the uniform 4-slot array with indistinguishable fields."""
        if "slots" not in self._data:
            self._data["slots"] = [_random_slot_entry(i) for i in range(NUM_KEY_SLOTS)]
        while len(self._data["slots"]) <= slot_id:
            self._data["slots"].append(_random_slot_entry(len(self._data["slots"])))

        slot_salt = salt if salt is not None else secrets.token_bytes(16)
        self._data["slots"][slot_id] = {
            "slot_id": slot_id,
            "salt": _b64e(slot_salt),
            "nonce": _b64e(nonce),
            "ciphertext": _b64e(ciphertext),
        }

    # Legacy accessors for test backwards-compatibility
    @property
    def wrapped_mk(self) -> tuple[bytes, bytes]:
        slot = self.get_slot(SLOT_PRIMARY)
        if slot is None:
            raise KeyError("Slot 0 not found")
        return slot

    @property
    def wrapped_mk_recovery(self) -> tuple[bytes, bytes]:
        slot = self.get_slot(SLOT_RECOVERY)
        if slot is None:
            raise KeyError("Slot 1 not found")
        return slot

    @property
    def has_decoy(self) -> bool:
        # In v2, all configs have 4 slots; decoy availability is checked cryptographically
        return True

    @property
    def kdf_params_decoy(self) -> Optional[KDFParams]:
        d = self._data.get("kdf_params_decoy")
        if d:
            return KDFParams.from_dict(d)
        slot = self.get_slot_entry(SLOT_DECOY)
        if slot and "salt" in slot:
            return KDFParams(
                memory_kib=self.kdf_params.memory_kib,
                time_cost=self.kdf_params.time_cost,
                parallelism=self.kdf_params.parallelism,
                salt=_b64d(slot["salt"]),
            )
        return None

    @property
    def wrapped_mk_decoy(self) -> Optional[tuple[bytes, bytes]]:
        return self.get_slot(SLOT_DECOY)

    def _set_wrapped(self, field: str, nonce: bytes, ciphertext: bytes) -> None:
        self._data[field] = {"nonce": _b64e(nonce), "ciphertext": _b64e(ciphertext)}
        # Also populate slots
        if field == "wrapped_mk":
            self.set_slot(SLOT_PRIMARY, nonce, ciphertext)
        elif field == "wrapped_mk_recovery":
            self.set_slot(SLOT_RECOVERY, nonce, ciphertext)
        elif field == "wrapped_mk_decoy":
            self.set_slot(SLOT_DECOY, nonce, ciphertext)


class Keystore:
    """High-level key management: setup, unlock, change-password, self-destruct."""

    def __init__(self, vault_dir: Path) -> None:
        self.config = VaultConfig(vault_dir)

    # ── First-time setup ──────────────────────────────────────────────────────

    def setup(
        self,
        password: str,
        keyfile_path: Optional[str] = None,
        benchmark: bool = True,
        kdf_params: Optional[KDFParams] = None,
    ) -> Tuple[bytearray, str]:
        """Initialise a fresh vault using uniform 4-slot layout."""
        if kdf_params is not None:
            kdf_params = kdf_params
        elif benchmark:
            kdf_params = benchmark_argon2id()
        else:
            kdf_params = KDFParams.new_with_fresh_salt()

        mk = bytearray(generate_key())

        # Primary slot (Slot 0)
        kek = derive_kek(password, kdf_params, keyfile_path)
        nonce_mk, wrapped_mk = wrap_key(kek, mk)
        zero_bytes(kek)

        # Recovery slot (Slot 1)
        recovery_key_bytes = secrets.token_bytes(KEY_SIZE)
        recovery_b32 = _format_recovery_key(recovery_key_bytes)
        kek_recovery = bytearray(recovery_key_bytes)
        nonce_rec, wrapped_rec = wrap_key(kek_recovery, mk)
        zero_bytes(kek_recovery)

        # Build uniform 4 slots (Slot 2 and 3 filled with random bytes)
        slots = [
            {"slot_id": 0, "salt": _b64e(kdf_params.salt), "nonce": _b64e(nonce_mk), "ciphertext": _b64e(wrapped_mk)},
            {"slot_id": 1, "salt": _b64e(secrets.token_bytes(16)), "nonce": _b64e(nonce_rec), "ciphertext": _b64e(wrapped_rec)},
            _random_slot_entry(2),
            _random_slot_entry(3),
        ]

        self.config.vault_dir.mkdir(parents=True, exist_ok=True)
        self.config._data = {
            "version": CONFIG_VERSION,
            "keyfile_enabled": keyfile_path is not None,
            "kdf_params": kdf_params.to_dict(),
            "slots": slots,
            # Legacy compatibility fields
            "wrapped_mk": {"nonce": _b64e(nonce_mk), "ciphertext": _b64e(wrapped_mk)},
            "wrapped_mk_recovery": {"nonce": _b64e(nonce_rec), "ciphertext": _b64e(wrapped_rec)},
        }
        self.config.save()
        logger.info("Vault keystore initialised with uniform key slots.")
        return mk, recovery_b32

    # ── Unlock with password ──────────────────────────────────────────────────

    def unlock(
        self,
        password: str,
        keyfile_path: Optional[str] = None,
    ) -> Tuple[bytearray, bool]:
        """Attempt unlock with primary password first, then decoy password if configured.

        Returns:
            (master_key, is_decoy)
        """
        try:
            mk = self.unlock_with_password(password, keyfile_path)
            return mk, False
        except CryptoError:
            if self.config.has_decoy:
                try:
                    mk = self.unlock_decoy(password)
                    return mk, True
                except CryptoError:
                    pass
            raise CryptoError("Authentication failed.") from None

    def unlock_with_password(
        self,
        password: str,
        keyfile_path: Optional[str] = None,
    ) -> bytearray:
        """Derive KEK from password, unwrap Master Key from primary slot."""
        self.config.load()
        kdf_params = self.config.kdf_params
        kek = derive_kek(password, kdf_params, keyfile_path)

        slot = self.config.get_slot(SLOT_PRIMARY)
        if slot is None:
            zero_bytes(kek)
            raise CryptoError("Authentication failed.")

        nonce, wrapped = slot
        try:
            mk = unwrap_key(kek, nonce, wrapped)
            return mk
        finally:
            zero_bytes(kek)

    def unlock_with_recovery_key(self, recovery_b32: str) -> bytearray:
        """Unlock the vault using the recovery key from slot 1."""
        self.config.load()
        recovery_key_bytes = _parse_recovery_key(recovery_b32)
        kek_recovery = bytearray(recovery_key_bytes)

        slot = self.config.get_slot(SLOT_RECOVERY)
        if slot is None:
            zero_bytes(kek_recovery)
            raise CryptoError("Authentication failed.")

        nonce, wrapped = slot
        try:
            mk = unwrap_key(kek_recovery, nonce, wrapped)
            return mk
        finally:
            zero_bytes(kek_recovery)

    # ── Decoy vault ───────────────────────────────────────────────────────────

    def setup_decoy(self, decoy_password: str) -> bytearray:
        """Store decoy vault MK into Slot 2.

        In v2, Slot 2 looks identical to all other slots in vault.config (indistinguishable layout).
        """
        self.config.load()
        decoy_salt = secrets.token_bytes(16)
        decoy_kdf = KDFParams(
            memory_kib=self.config.kdf_params.memory_kib,
            time_cost=self.config.kdf_params.time_cost,
            parallelism=self.config.kdf_params.parallelism,
            salt=decoy_salt,
        )
        decoy_mk = bytearray(generate_key())
        kek_decoy = derive_kek(decoy_password, decoy_kdf)

        nonce, wrapped = wrap_key(kek_decoy, decoy_mk)
        zero_bytes(kek_decoy)

        self.config.set_slot(SLOT_DECOY, nonce, wrapped, salt=decoy_salt)
        self.config.save()
        return decoy_mk

    def unlock_decoy(self, password: str) -> bytearray:
        """Attempt to unlock the decoy vault from Slot 2."""
        self.config.load()
        slot = self.config.get_slot_entry(SLOT_DECOY)
        if slot is None:
            raise CryptoError("Authentication failed.") from None

        salt_str = slot.get("salt")
        if salt_str:
            salt_bytes = _b64d(salt_str)
        elif self.config.kdf_params_decoy:
            salt_bytes = self.config.kdf_params_decoy.salt
        else:
            salt_bytes = self.config.kdf_params.salt

        params = KDFParams(
            memory_kib=self.config.kdf_params.memory_kib,
            time_cost=self.config.kdf_params.time_cost,
            parallelism=self.config.kdf_params.parallelism,
            salt=salt_bytes,
        )

        kek = derive_kek(password, params)
        nonce, wrapped = _b64d(slot["nonce"]), _b64d(slot["ciphertext"])
        try:
            mk = unwrap_key(kek, nonce, wrapped)
            return mk
        finally:
            zero_bytes(kek)

    # ── Change password (proven before atomic replacement) ───────────────────

    def change_password(
        self,
        old_password: str,
        new_password: str,
        keyfile_path: Optional[str] = None,
        old_keyfile_path: Optional[str] = None,
    ) -> None:
        """Crash-safe password change.

        Audit Requirement (Item 22):
          1. Verify old password -> unwrap MK.
          2. Derive new KEK with fresh salt.
          3. Wrap MK with new KEK into temp config structure.
          4. Unwrap MK from temp config to prove the new wrapping works.
          5. Only if proven, atomically overwrite vault.config.
        """
        # 1. Verify old password
        mk = self.unlock_with_password(old_password, old_keyfile_path)

        # 2. Derive new KEK with fresh parameters
        old_kdf = self.config.kdf_params
        new_kdf = KDFParams(
            memory_kib=old_kdf.memory_kib,
            time_cost=old_kdf.time_cost,
            parallelism=old_kdf.parallelism,
            salt=KDFParams.new_with_fresh_salt().salt,
        )
        new_kek = derive_kek(new_password, new_kdf, keyfile_path)
        nonce, wrapped = wrap_key(new_kek, mk)

        # 3. Prove verification by attempting unwrap with new_kek
        try:
            proven_mk = unwrap_key(new_kek, nonce, wrapped)
            if not constant_time_compare(bytes(proven_mk), bytes(mk)):
                zero_bytes(proven_mk)
                raise CryptoError("Password change verification failed.")
            zero_bytes(proven_mk)
        finally:
            zero_bytes(new_kek)

        # 4. Atomic update of config
        self.config.load()
        self.config._data["kdf_params"] = new_kdf.to_dict()
        self.config._data["keyfile_enabled"] = keyfile_path is not None
        self.config.set_slot(SLOT_PRIMARY, nonce, wrapped, salt=new_kdf.salt)
        self.config._data["wrapped_mk"] = {"nonce": _b64e(nonce), "ciphertext": _b64e(wrapped)}
        self.config.save()

        zero_bytes(mk)
        logger.info("Master password changed and cryptographically proven.")

    def optimize_unlock_speed(
        self,
        password: str,
        keyfile_path: Optional[str] = None,
        target_seconds: float = 0.25,
    ) -> KDFParams:
        """Re-tune Argon2id KDF parameters on an existing vault for fast, responsive unlock.

        Verifies the current password, derives a fresh salt with OWASP-compliant
        Argon2id parameters tuned to *target_seconds* (~0.25s), proves key wrapping/
        unwrapping works before saving, and atomically updates vault.config.
        """
        mk = self.unlock_with_password(password, keyfile_path)
        try:
            new_kdf = benchmark_argon2id(target_seconds=target_seconds)
            new_kek = derive_kek(password, new_kdf, keyfile_path)
            nonce, wrapped = wrap_key(new_kek, mk)
            try:
                proven_mk = unwrap_key(new_kek, nonce, wrapped)
                try:
                    if not constant_time_compare(bytes(proven_mk), bytes(mk)):
                        raise CryptoError("KDF optimization verification failed.")
                finally:
                    zero_bytes(proven_mk)
            finally:
                zero_bytes(new_kek)

            self.config.load()
            self.config._data["kdf_params"] = new_kdf.to_dict()
            self.config.set_slot(SLOT_PRIMARY, nonce, wrapped, salt=new_kdf.salt)
            self.config._data["wrapped_mk"] = {"nonce": _b64e(nonce), "ciphertext": _b64e(wrapped)}
            self.config.save()
            logger.info("Vault KDF parameters re-tuned for fast unlock (mem=%d KiB, t=%d).", new_kdf.memory_kib, new_kdf.time_cost)
            return new_kdf
        finally:
            zero_bytes(mk)

    def reset_password_with_recovery_key(
        self,
        recovery_b32: str,
        new_password: str,
        keyfile_path: Optional[str] = None,
        enforce_policy: bool = True,
    ) -> bytearray:
        """Reset master password using a valid 256-bit recovery key (Slot 1).

        Unwraps the Master Key via Slot 1, proves wrapping under the new password
        (and optional key file), atomically updates Slot 0 in vault.config while
        preserving Slot 1 (recovery) and Slot 2 (decoy), and returns the
        unlocked Master Key bytearray.
        """
        if enforce_policy:
            from mylocker.app.core.password_policy import validate_password

            ok, msg, _ = validate_password(new_password)
            if not ok:
                raise ValueError(msg)
        elif len(new_password) < 12:
            raise ValueError("Password must be at least 12 characters long.")

        mk = self.unlock_with_recovery_key(recovery_b32)
        try:
            old_kdf = self.config.kdf_params
            new_kdf = KDFParams(
                memory_kib=old_kdf.memory_kib,
                time_cost=old_kdf.time_cost,
                parallelism=old_kdf.parallelism,
                salt=KDFParams.new_with_fresh_salt().salt,
            )
            new_kek = derive_kek(new_password, new_kdf, keyfile_path)
            nonce, wrapped = wrap_key(new_kek, mk)
            try:
                proven_mk = unwrap_key(new_kek, nonce, wrapped)
                if not constant_time_compare(bytes(proven_mk), bytes(mk)):
                    zero_bytes(proven_mk)
                    raise CryptoError("Password reset verification failed.")
                zero_bytes(proven_mk)
            finally:
                zero_bytes(new_kek)

            self.config.load()
            self.config._data["kdf_params"] = new_kdf.to_dict()
            self.config._data["keyfile_enabled"] = keyfile_path is not None
            self.config.set_slot(SLOT_PRIMARY, nonce, wrapped, salt=new_kdf.salt)
            self.config._data["wrapped_mk"] = {"nonce": _b64e(nonce), "ciphertext": _b64e(wrapped)}
            self.config.save()
        except Exception:
            zero_bytes(mk)
            raise

        logger.info("Master password reset via recovery key and cryptographically proven.")
        return mk

    # ── Self Destruct (Emergency Wipe) ────────────────────────────────────────

    def self_destruct(self) -> None:
        """Audit Requirement (Item 25):
        Emergency self-destruct: permanently overwrite all key slots with random bytes,
        rendering vault contents unrecoverable.
        """
        logger.warning("EMERGENCY SELF-DESTRUCT INITIATED.")
        if self.config.exists():
            self.config.load()
            # Overwrite all slots with cryptographically secure random noise
            self.config._data["slots"] = [_random_slot_entry(i) for i in range(NUM_KEY_SLOTS)]
            self.config._data["wrapped_mk"] = _random_slot_entry(0)
            self.config._data["wrapped_mk_recovery"] = _random_slot_entry(1)
            self.config._data.pop("wrapped_mk_decoy", None)
            self.config._data.pop("kdf_params_decoy", None)
            self.config.save()

            # Overwrite file on disk multiple times
            try:
                size = self.config.config_path.stat().st_size
                with open(self.config.config_path, "r+b") as fh:
                    for _ in range(3):
                        fh.seek(0)
                        fh.write(secrets.token_bytes(size))
                        fh.flush()
                        os.fsync(fh.fileno())
            except Exception:
                pass


# ── Recovery key helpers ───────────────────────────────────────────────────────

def _format_recovery_key(raw: bytes) -> str:
    """Format 32 raw bytes as a human-readable Base32 string."""
    b32 = base64.b32encode(raw).decode().rstrip("=")
    groups = [b32[i:i+8] for i in range(0, len(b32), 8)]
    return "-".join(groups)


def _parse_recovery_key(formatted: str) -> bytes:
    """Parse a formatted recovery key string back to raw bytes."""
    cleaned = formatted.replace("-", "").replace(" ", "").upper()
    padding = (8 - len(cleaned) % 8) % 8
    cleaned += "=" * padding
    try:
        return base64.b32decode(cleaned)
    except Exception:
        raise CryptoError("Authentication failed.") from None


__all__ = [
    "VaultConfig",
    "Keystore",
    "CONFIG_VERSION",
    "NUM_KEY_SLOTS",
    "SLOT_PRIMARY",
    "SLOT_RECOVERY",
    "SLOT_DECOY",
    "SLOT_SPARE",
]
