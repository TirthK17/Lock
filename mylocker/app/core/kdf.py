"""
kdf.py — Key Derivation Functions for Personal Vault.

Design:
- Argon2id for password → KEK: memory-hard, side-channel resistant.
- HKDF-SHA256 for sub-key derivation from Master Key.
- Auto-benchmark: adjusts time_cost so derivation takes ~1 second on this
  machine (run once at first setup, stored in vault.config).
- Optional USB key-file: KEK = HKDF(Argon2id(password) || SHA3-256(keyfile)).
  This requires the physical file to open the vault.
- Never stores or returns the password; only derived key material.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .crypto import KEY_SIZE, generate_salt, zero_bytes

logger = logging.getLogger(__name__)

# ─── Argon2id defaults (safe OWASP minimums; fast ~0.25s tuning at setup) ───
ARGON2_MIN_MEMORY_KIB = 64 * 1024    # 64 MiB (>3x OWASP Argon2id minimum)
ARGON2_MIN_TIME_COST   = 3
ARGON2_PARALLELISM     = 4
ARGON2_HASH_LEN        = 32           # 256-bit output = AES-256 key size
ARGON2_SALT_LEN        = 16           # 128-bit salt

# Target derivation time for auto-benchmark (seconds)
BENCHMARK_TARGET_SECONDS = 0.25


class KDFParams:
    """Serialisable container for all KDF parameters stored in vault.config."""

    __slots__ = ("memory_kib", "time_cost", "parallelism", "salt", "hash_len")

    def __init__(
        self,
        memory_kib: int,
        time_cost: int,
        parallelism: int,
        salt: bytes,
        hash_len: int = ARGON2_HASH_LEN,
    ) -> None:
        self.memory_kib = memory_kib
        self.time_cost = time_cost
        self.parallelism = parallelism
        self.salt = salt
        self.hash_len = hash_len

    def to_dict(self) -> dict:
        import base64
        return {
            "memory_kib": self.memory_kib,
            "time_cost": self.time_cost,
            "parallelism": self.parallelism,
            "salt": base64.b64encode(self.salt).decode(),
            "hash_len": self.hash_len,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KDFParams":
        import base64
        return cls(
            memory_kib=int(d["memory_kib"]),
            time_cost=int(d["time_cost"]),
            parallelism=int(d["parallelism"]),
            salt=base64.b64decode(d["salt"]),
            hash_len=int(d.get("hash_len", ARGON2_HASH_LEN)),
        )

    @classmethod
    def new_with_fresh_salt(cls, memory_kib: int = ARGON2_MIN_MEMORY_KIB,
                            time_cost: int = ARGON2_MIN_TIME_COST) -> "KDFParams":
        return cls(
            memory_kib=memory_kib,
            time_cost=time_cost,
            parallelism=ARGON2_PARALLELISM,
            salt=generate_salt(ARGON2_SALT_LEN),
        )


def _derive_argon2id(password: str | bytes, params: KDFParams) -> bytearray:
    """Run Argon2id and return the derived bytes as a *mutable* bytearray.

    The mutable return lets callers zero the memory after use.
    """
    if isinstance(password, str):
        password_bytes = password.encode("utf-8")
    else:
        password_bytes = bytes(password)

    raw = hash_secret_raw(
        secret=password_bytes,
        salt=params.salt,
        time_cost=params.time_cost,
        memory_cost=params.memory_kib,
        parallelism=params.parallelism,
        hash_len=params.hash_len,
        type=Type.ID,
    )
    # hash_secret_raw returns bytes; copy to bytearray for zeroing
    return bytearray(raw)


def derive_kek(
    password: str,
    params: KDFParams,
    keyfile_path: Optional[str] = None,
) -> bytearray:
    """Derive the Key-Encryption-Key (KEK) from a password (+ optional keyfile).

    If *keyfile_path* is supplied the KEK becomes:
        HKDF(IKM = Argon2id(password) || SHA3-256(keyfile_bytes))

    This means the vault cannot be opened without the physical key file,
    providing a second factor independent of the password.

    Returns:
        32-byte KEK as a *mutable* bytearray.  Caller must zero after use.
    """
    argon_key = _derive_argon2id(password, params)

    if keyfile_path is None:
        return argon_key  # KEK = Argon2id output directly

    # Second factor: blend in the key-file hash
    try:
        keyfile_hash = _hash_keyfile(keyfile_path)
    except OSError as exc:
        zero_bytes(argon_key)
        raise RuntimeError(
            "Key file could not be read. Ensure your USB drive is plugged in."
        ) from exc

    ikm = bytes(argon_key) + keyfile_hash
    zero_bytes(argon_key)

    kek = bytearray(_hkdf_derive(ikm, length=KEY_SIZE, info=b"PersonalVault-KEK-v1"))
    del ikm  # ikm is immutable bytes; cannot memset in-place, dereference immediately
    return kek


def derive_file_key(master_key: bytes | bytearray, file_id: bytes) -> bytearray:
    """Derive a unique per-file encryption key from the Master Key.

    Args:
        master_key: The 32-byte vault Master Key.
        file_id: 16-byte random file identifier.

    Returns:
        32-byte file key as a mutable bytearray.
    """
    info = b"PersonalVault-FileKey-v1" + file_id
    raw = _hkdf_derive(bytes(master_key), length=KEY_SIZE, info=info)
    return bytearray(raw)


def derive_index_key(master_key: bytes | bytearray) -> bytearray:
    """Derive the index encryption key from the Master Key."""
    raw = _hkdf_derive(bytes(master_key), length=KEY_SIZE,
                       info=b"PersonalVault-IndexKey-v1")
    return bytearray(raw)


def derive_blob_name_key(master_key: bytes | bytearray, file_id: bytes) -> bytes:
    """Derive a secret blob filename from the Master Key and file_id.

    Returns 24 raw bytes (48 hex chars) — stored in FileEntry.blob_name.
    Using HKDF ensures blob names leak no information about file content or ID
    without knowledge of the master key.
    """
    info = b"blobname-v1" + file_id
    return _hkdf_derive(bytes(master_key), length=24, info=info)


def derive_journal_key(master_key: bytes | bytearray) -> bytearray:
    """Derive the journal encryption key from the Master Key."""
    raw = _hkdf_derive(bytes(master_key), length=KEY_SIZE,
                       info=b"PersonalVault-JournalKey-v1")
    return bytearray(raw)


def derive_hmac_key(master_key: bytes | bytearray, purpose: bytes = b"lockout") -> bytes:
    """Derive a sub-key for HMAC purposes (e.g. lockout counter signing)."""
    return _hkdf_derive(bytes(master_key), length=32,
                        info=b"PersonalVault-HMAC-v1-" + purpose)


def _hkdf_derive(ikm: bytes, length: int, info: bytes, salt: Optional[bytes] = None) -> bytes:
    """HKDF-SHA256 extraction + expansion."""
    hkdf = HKDF(
        algorithm=SHA256(),
        length=length,
        salt=salt,
        info=info,
    )
    return hkdf.derive(ikm)


def _hash_keyfile(path: str) -> bytes:
    """Return SHA3-256 hash of a keyfile.  Reads in chunks to handle large USB files."""
    h = hashlib.sha3_256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):  # 1 MiB chunks
            h.update(chunk)
    return h.digest()


def benchmark_argon2id(
    target_seconds: float = BENCHMARK_TARGET_SECONDS,
    memory_kib: int = ARGON2_MIN_MEMORY_KIB,
    parallelism: int = ARGON2_PARALLELISM,
) -> KDFParams:
    """Auto-tune Argon2id time_cost so that derivation takes ~*target_seconds*.

    Starts at ARGON2_MIN_TIME_COST and calibrates smoothly in a bounded number
    of steps so setup completes quickly without sluggish login delays.
    Returns a KDFParams with a fresh random salt ready for first-time setup.
    """
    logger.info("Benchmarking Argon2id (target ~%.2fs)…", target_seconds)
    dummy_salt = generate_salt(ARGON2_SALT_LEN)
    dummy_password = b"benchmark_dummy_password"

    time_cost = ARGON2_MIN_TIME_COST

    for _ in range(6):
        t0 = time.perf_counter()
        hash_secret_raw(
            secret=dummy_password,
            salt=dummy_salt,
            time_cost=time_cost,
            memory_cost=memory_kib,
            parallelism=parallelism,
            hash_len=ARGON2_HASH_LEN,
            type=Type.ID,
        )
        elapsed = time.perf_counter() - t0

        logger.info("Argon2id t=%d -> %.3fs", time_cost, elapsed)

        if elapsed >= target_seconds * 0.85 or time_cost >= 24:
            break
        if elapsed <= 0:
            elapsed = 0.001

        ratio = target_seconds / elapsed
        increment = max(1, min(8, round(time_cost * (ratio - 1))))
        time_cost = min(24, time_cost + increment)

    return KDFParams(
        memory_kib=memory_kib,
        time_cost=time_cost,
        parallelism=parallelism,
        salt=generate_salt(ARGON2_SALT_LEN),
    )


__all__ = [
    "KDFParams",
    "ARGON2_MIN_MEMORY_KIB",
    "ARGON2_MIN_TIME_COST",
    "ARGON2_PARALLELISM",
    "ARGON2_SALT_LEN",
    "derive_kek",
    "derive_file_key",
    "derive_index_key",
    "derive_blob_name_key",
    "derive_journal_key",
    "derive_hmac_key",
    "benchmark_argon2id",
]

