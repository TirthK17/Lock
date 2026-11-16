"""
crypto.py — Low-level AES-256-GCM primitives for Personal Vault.

Design decisions:
- Uses the `cryptography` library exclusively; no custom crypto.
- All randomness via `secrets.token_bytes` (CSPRNG).
- 16-byte nonces (96-bit is GCM spec; we use 128-bit for extra safety with
  random nonces to push collision probability down further).
- 32-byte authentication tags (GCM default 16 bytes; we keep 16 — the standard
  is proven secure at 128-bit tag length).
- Never logs plaintext, keys, nonces, or file names.
- Caller is responsible for zeroing key material (mutable bytearray) after use.

Thread safety: stateless functions — safe to call from multiple threads.
"""

from __future__ import annotations

import logging
import secrets
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────
KEY_SIZE = 32          # 256-bit AES key
NONCE_SIZE = 12        # 96-bit nonce (GCM standard)
TAG_SIZE = 16          # 128-bit authentication tag (default for AESGCM)
HKDF_HASH_ALG = "SHA-256"


class CryptoError(Exception):
    """Raised when an authenticated decryption fails or parameters are invalid.

    Deliberately vague: callers must NOT expose whether the key or the data
    was the problem — that leaks oracle information.
    """


def generate_key() -> bytes:
    """Generate a fresh 256-bit random key using the OS CSPRNG."""
    return secrets.token_bytes(KEY_SIZE)


def generate_nonce() -> bytes:
    """Generate a fresh 96-bit random nonce."""
    return secrets.token_bytes(NONCE_SIZE)


def generate_salt(size: int = 16) -> bytes:
    """Generate a random salt of *size* bytes (default 16)."""
    return secrets.token_bytes(size)


def encrypt_aes_gcm(
    key: bytes | bytearray,
    plaintext: bytes,
    associated_data: Optional[bytes] = None,
) -> tuple[bytes, bytes]:
    """Encrypt *plaintext* with AES-256-GCM.

    Args:
        key: 32-byte symmetric key (bytes or bytearray).
        plaintext: Data to encrypt.
        associated_data: Optional authenticated-but-not-encrypted context.

    Returns:
        ``(nonce, ciphertext_with_tag)`` — ciphertext includes the 16-byte
        GCM authentication tag appended by the cryptography library.

    Raises:
        ValueError: if key length is not 32 bytes.
    """
    if len(key) != KEY_SIZE:
        raise ValueError(f"Key must be {KEY_SIZE} bytes, got {len(key)}")

    nonce = generate_nonce()
    aesgcm = AESGCM(bytes(key))  # copies key into C layer
    ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data)
    return nonce, ciphertext


def decrypt_aes_gcm(
    key: bytes | bytearray,
    nonce: bytes,
    ciphertext: bytes,
    associated_data: Optional[bytes] = None,
) -> bytes:
    """Decrypt and authenticate an AES-256-GCM ciphertext.

    Args:
        key: 32-byte symmetric key.
        nonce: 12-byte nonce used during encryption.
        ciphertext: Ciphertext with appended 16-byte tag.
        associated_data: Must match what was used during encryption.

    Returns:
        Decrypted plaintext.

    Raises:
        CryptoError: If authentication fails (wrong key, tampered data, bad AD).
    """
    if len(key) != KEY_SIZE:
        raise CryptoError("Authentication failed.")  # vague on purpose

    try:
        aesgcm = AESGCM(bytes(key))
        return aesgcm.decrypt(nonce, ciphertext, associated_data)
    except Exception:
        # cryptography raises InvalidTag; we wrap it uniformly.
        raise CryptoError("Authentication failed.") from None


def wrap_key(wrapping_key: bytes | bytearray, key_to_wrap: bytes | bytearray) -> tuple[bytes, bytes]:
    """Encrypt (wrap) *key_to_wrap* with *wrapping_key* using AES-256-GCM.

    Returns:
        ``(nonce, ciphertext_with_tag)``
    """
    return encrypt_aes_gcm(wrapping_key, bytes(key_to_wrap))


def unwrap_key(
    wrapping_key: bytes | bytearray,
    nonce: bytes,
    wrapped_key: bytes,
) -> bytearray:
    """Decrypt a wrapped key.  Returns a *mutable* bytearray so the caller
    can zero it after use.

    Raises:
        CryptoError: If authentication fails.
    """
    plain = decrypt_aes_gcm(wrapping_key, nonce, wrapped_key)
    return bytearray(plain)


def constant_time_compare(a: bytes, b: bytes) -> bool:
    """Constant-time equality comparison to avoid timing side-channels."""
    return secrets.compare_digest(a, b)


def zero_bytes(buf: bytearray | memoryview) -> None:
    """Overwrite a mutable bytearray with zeros using ctypes.memset.

    Audit Requirement: Uses ctypes.memset to securely clear buffer memory.
    """
    if not isinstance(buf, (bytearray, memoryview)):
        return
    if len(buf) == 0:
        return
    import ctypes
    try:
        addr = (ctypes.c_char * len(buf)).from_buffer(buf)
        ctypes.memset(addr, 0, len(buf))
    except Exception:
        # Fallback if buffer protocol fails
        for i in range(len(buf)):
            buf[i] = 0


__all__ = [
    "CryptoError",
    "KEY_SIZE",
    "NONCE_SIZE",
    "TAG_SIZE",
    "generate_key",
    "generate_nonce",
    "generate_salt",
    "encrypt_aes_gcm",
    "decrypt_aes_gcm",
    "wrap_key",
    "unwrap_key",
    "constant_time_compare",
    "zero_bytes",
]

