"""
chunked_io.py — Streaming AES-256-GCM chunked encryption/decryption (v1 and v2 formats).

Phase 2 Security Hardening:
- File format v2:
    Header (41 bytes):
      [MAGIC 8B "PVAULT01"][VERSION 1B \x02][FILE_ID 16B][TOTAL_CHUNKS 8B uint64 LE][ORIG_SIZE 8B uint64 LE]
    For each chunk i:
      [NONCE 12B][CHUNK_LEN 4B uint32 LE][CIPHERTEXT + 16B TAG]
      Associated Data:
        b"chunk-v2" + header_bytes + struct.pack("<QB", chunk_index, 1 if is_final else 0)
      Guards:
        - chunk_len <= CHUNK_SIZE + 16
        - Final chunk flag authenticated
        - Header tampering invalidates all chunks
    Trailer (32 bytes):
      [TRAILER_MAGIC 4B "PVTR"][NONCE 12B][ENCRYPTED_LEN 16B]
- Robust against: truncation, extension, reordering, chunk swapping, header editing.
- Full backwards-compatibility for v1 reading.
- Crash-safe decrypt_file writes to dest.part first, renames on success, wipes on error.
- Real cancellation via threading.Event.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import secrets
import struct
import threading
from pathlib import Path
from typing import Callable, Iterator, Optional

from mylocker.app.core.crypto import (
    NONCE_SIZE,
    CryptoError,
    decrypt_aes_gcm,
    encrypt_aes_gcm,
    zero_bytes,
)
from mylocker.app.core.kdf import derive_file_key

logger = logging.getLogger(__name__)

# ─── File format constants ────────────────────────────────────────────────────
MAGIC            = b"PVAULT01"   # 8 bytes signature
VERSION_V1       = b"\x01"       # Legacy v1
VERSION_V2       = b"\x02"       # Enhanced v2
CURRENT_VERSION  = VERSION_V2

FILE_ID_LEN      = 16
TOTAL_CHUNKS_LEN = 8             # uint64 LE
ORIG_SIZE_LEN    = 8             # uint64 LE (v2)

HEADER_V1_LEN    = len(MAGIC) + 1 + FILE_ID_LEN + TOTAL_CHUNKS_LEN  # 33 bytes
HEADER_V2_LEN    = HEADER_V1_LEN + ORIG_SIZE_LEN                    # 41 bytes
HEADER_LEN       = HEADER_V2_LEN                                     # Default alias

TRAILER_MAGIC    = b"PVTR"       # 4 bytes trailer marker (v2)

CHUNK_SIZE       = 4 * 1024 * 1024    # 4 MiB plaintext chunk
CHUNK_NONCE_LEN  = NONCE_SIZE         # 12 bytes
CHUNK_LEN_FIELD  = 4                  # uint32 LE — ciphertext length incl. tag
TAG_SIZE         = 16                 # AES-GCM tag

ProgressCallback = Callable[[int, int], None]  # (bytes_done, bytes_total)


class ChunkError(CryptoError):
    """Raised for chunk-level integrity failures with contextual info."""


class OperationCancelledError(CryptoError):
    """Raised when an operation is cancelled via threading.Event."""


def generate_file_id() -> bytes:
    """Generate a fresh 16-byte random file identifier."""
    return secrets.token_bytes(FILE_ID_LEN)


def _make_ad_v1(file_id: bytes, chunk_index: int) -> bytes:
    """Legacy v1 associated data."""
    return b"chunk" + file_id + struct.pack("<Q", chunk_index)


def _make_ad_v2(header_bytes: bytes, chunk_index: int, is_final: bool) -> bytes:
    """v2 associated data authenticating the full header + position + finality flag."""
    return b"chunk-v2" + header_bytes + struct.pack("<QB", chunk_index, 1 if is_final else 0)


def encrypt_file(
    source_path: Path,
    dest_path: Path,
    master_key: bytes | bytearray,
    file_id: Optional[bytes] = None,
    progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> bytes:
    """Encrypt *source_path* → *dest_path* using v2 format.

    Returns the 16-byte file_id.
    """
    if file_id is None:
        file_id = generate_file_id()

    file_key = derive_file_key(master_key, file_id)
    source_size = source_path.stat().st_size
    total_chunks = max(0, (source_size + CHUNK_SIZE - 1) // CHUNK_SIZE)

    # Build v2 header
    header_bytes = (
        MAGIC +
        VERSION_V2 +
        file_id +
        struct.pack("<Q", total_chunks) +
        struct.pack("<Q", source_size)
    )

    bytes_done = 0
    try:
        with open(source_path, "rb") as src, open(dest_path, "wb") as dst:
            dst.write(header_bytes)

            chunk_index = 0
            while True:
                if cancel_event and cancel_event.is_set():
                    raise OperationCancelledError("Operation cancelled.")

                plaintext_chunk = src.read(CHUNK_SIZE)
                if not plaintext_chunk:
                    break

                is_final = (chunk_index == total_chunks - 1)
                ad = _make_ad_v2(header_bytes, chunk_index, is_final)
                nonce, ciphertext = encrypt_aes_gcm(file_key, plaintext_chunk, ad)

                dst.write(nonce)
                dst.write(struct.pack("<I", len(ciphertext)))
                dst.write(ciphertext)

                bytes_done += len(plaintext_chunk)
                chunk_index += 1
                if progress:
                    progress(bytes_done, source_size)

            # Write authenticated trailer (v2)
            # Authenticates the final size and serves as an unambiguous EOF marker
            trailer_payload = struct.pack("<Q", source_size)
            trailer_ad = b"trailer-v2" + header_bytes
            t_nonce, t_ct = encrypt_aes_gcm(file_key, trailer_payload, trailer_ad)
            dst.write(TRAILER_MAGIC)
            dst.write(t_nonce)
            dst.write(t_ct)

            dst.flush()
            os.fsync(dst.fileno())

    except Exception:
        dest_path.unlink(missing_ok=True)
        raise
    finally:
        zero_bytes(file_key)

    return file_id


def decrypt_file(
    source_path: Path,
    dest_path: Path,
    master_key: bytes | bytearray,
    expected_file_id: Optional[bytes] = None,
    progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> None:
    """Decrypt *source_path* → *dest_path*, verifying every chunk's tag.

    Audit Requirement: Decrypts to a unique dest.part first, renames on success,
    and removes the partial file on error.
    """
    part_path = dest_path.with_name(
        f"{dest_path.name}.{secrets.token_hex(4)}.part"
    )
    try:
        with open(part_path, "wb") as dst:
            for chunk in decrypt_file_streaming(
                source_path, master_key, expected_file_id, progress, cancel_event
            ):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())

        # Atomic rename to final destination on success
        part_path.replace(dest_path)
    except Exception:
        part_path.unlink(missing_ok=True)
        raise


def decrypt_file_streaming(
    source_path: Path,
    master_key: bytes | bytearray,
    expected_file_id: Optional[bytes] = None,
    progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Iterator[bytes]:
    """Yield decrypted chunks one at a time (generator) for v1 or v2 format.

    Validates:
      - Valid magic & supported version (v1 or v2)
      - expected_file_id (if supplied)
      - chunk length <= CHUNK_SIZE + 16
      - chunk order, final chunk flag, and authenticated trailer (v2)
      - no trailing bytes after EOF
    """
    source_size = source_path.stat().st_size

    with open(source_path, "rb") as src:
        magic = src.read(len(MAGIC))
        if magic != MAGIC:
            raise ValueError("Not a vault file (bad magic).")

        version = src.read(1)
        if version not in (VERSION_V1, VERSION_V2):
            raise ValueError(f"Unsupported vault file version: {version!r}")

        file_id = src.read(FILE_ID_LEN)
        if len(file_id) < FILE_ID_LEN:
            raise ChunkError("Corrupt header: truncated file ID.")
        if expected_file_id is not None and file_id != expected_file_id:
            raise ChunkError("Authentication failed.")

        total_chunks_raw = src.read(TOTAL_CHUNKS_LEN)
        if len(total_chunks_raw) < TOTAL_CHUNKS_LEN:
            raise ChunkError("Corrupt header: truncated chunk count.")
        total_chunks = struct.unpack("<Q", total_chunks_raw)[0]

        header_bytes = MAGIC + version + file_id + total_chunks_raw

        orig_size = None
        if version == VERSION_V2:
            orig_size_raw = src.read(ORIG_SIZE_LEN)
            if len(orig_size_raw) < ORIG_SIZE_LEN:
                raise ChunkError("Corrupt header: truncated original size.")
            orig_size = struct.unpack("<Q", orig_size_raw)[0]
            header_bytes += orig_size_raw

        file_key = derive_file_key(master_key, file_id)
        bytes_done = 0

        try:
            if total_chunks == 0:
                # Empty file
                if version == VERSION_V2:
                    _verify_trailer(src, file_key, header_bytes, orig_size or 0)
                return

            for chunk_index in range(total_chunks):
                if cancel_event and cancel_event.is_set():
                    raise OperationCancelledError("Operation cancelled.")

                nonce = src.read(CHUNK_NONCE_LEN)
                if len(nonce) < CHUNK_NONCE_LEN:
                    raise ChunkError("Truncated file: missing chunk nonce.")

                chunk_len_raw = src.read(CHUNK_LEN_FIELD)
                if len(chunk_len_raw) < CHUNK_LEN_FIELD:
                    raise ChunkError("Truncated file: missing chunk length.")
                chunk_len = struct.unpack("<I", chunk_len_raw)[0]

                # Security check: chunk_len must never exceed chunk_size + 16-byte tag
                if chunk_len > CHUNK_SIZE + TAG_SIZE:
                    raise ChunkError(f"Invalid chunk length: {chunk_len} exceeds max size.")

                ciphertext = src.read(chunk_len)
                if len(ciphertext) < chunk_len:
                    raise ChunkError("Truncated chunk data.")

                is_final = (chunk_index == total_chunks - 1)
                if version == VERSION_V2:
                    ad = _make_ad_v2(header_bytes, chunk_index, is_final)
                else:
                    ad = _make_ad_v1(file_id, chunk_index)

                plaintext = decrypt_aes_gcm(file_key, nonce, ciphertext, ad)
                bytes_done += len(plaintext)
                if progress:
                    progress(bytes_done, orig_size or source_size)

                yield plaintext

            # If v2, verify authenticated trailer and ensure no trailing data
            if version == VERSION_V2:
                _verify_trailer(src, file_key, header_bytes, orig_size or 0)

            # Ensure no trailing garbage
            remaining = src.read(1)
            if remaining:
                raise ChunkError("Extraneous data detected after final chunk.")

        finally:
            zero_bytes(file_key)


def _verify_trailer(
    src, file_key: bytearray, header_bytes: bytes, expected_orig_size: int
) -> None:
    """Verify the authenticated trailer at the end of a v2 vault file."""
    t_magic = src.read(len(TRAILER_MAGIC))
    if t_magic != TRAILER_MAGIC:
        raise ChunkError("Truncated or tampered file: missing authenticated trailer.")

    t_nonce = src.read(CHUNK_NONCE_LEN)
    if len(t_nonce) < CHUNK_NONCE_LEN:
        raise ChunkError("Truncated file: missing trailer nonce.")

    # Encrypted 8-byte uint64 + 16B GCM tag = 24 bytes
    t_ct = src.read(8 + TAG_SIZE)
    if len(t_ct) < 8 + TAG_SIZE:
        raise ChunkError("Truncated file: missing trailer data.")

    trailer_ad = b"trailer-v2" + header_bytes
    pt = decrypt_aes_gcm(file_key, t_nonce, t_ct, trailer_ad)
    dec_size = struct.unpack("<Q", pt)[0]
    if dec_size != expected_orig_size:
        raise ChunkError("Trailer original size mismatch.")


def decrypt_file_to_memory(
    source_path: Path,
    master_key: bytes | bytearray,
    expected_file_id: Optional[bytes] = None,
    max_bytes: int = 100 * 1024 * 1024,  # 100 MiB safety cap
) -> bytearray:
    """Decrypt an entire vault file into RAM (for photos, small documents)."""
    buf = io.BytesIO()
    total_written = 0
    for chunk in decrypt_file_streaming(source_path, master_key, expected_file_id):
        total_written += len(chunk)
        if total_written > max_bytes:
            raise ValueError(f"File exceeds maximum in-memory limit ({max_bytes} bytes).")
        buf.write(chunk)
    return bytearray(buf.getvalue())


def compute_decrypted_sha256(
    source_path: Path,
    master_key: bytes | bytearray,
    expected_file_id: Optional[bytes] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """Stream decrypted chunks and compute full-file SHA-256 with bounded memory (Audit Phase 4.B)."""
    hasher = hashlib.sha256()
    for chunk in decrypt_file_streaming(
        source_path, master_key, expected_file_id, cancel_event=cancel_event
    ):
        hasher.update(chunk)
    return hasher.hexdigest()


def verify_file_integrity(
    source_path: Path,
    master_key: bytes | bytearray,
    cancel_event: Optional[threading.Event] = None,
) -> tuple[bool, str]:
    """Authenticate every chunk and the trailer without writing output."""
    try:
        for _ in decrypt_file_streaming(source_path, master_key, cancel_event=cancel_event):
            pass
        return True, "OK"
    except OperationCancelledError:
        raise
    except ChunkError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"Error: {exc}"


def read_file_id(source_path: Path) -> bytes:
    """Read the file_id from a vault file header (no decryption)."""
    with open(source_path, "rb") as fh:
        magic = fh.read(len(MAGIC))
        if magic != MAGIC:
            raise ValueError("Not a vault file.")
        version = fh.read(1)
        if version not in (VERSION_V1, VERSION_V2):
            raise ValueError("Unsupported version.")
        return fh.read(FILE_ID_LEN)


__all__ = [
    "ChunkError",
    "OperationCancelledError",
    "CHUNK_SIZE",
    "HEADER_LEN",
    "HEADER_V1_LEN",
    "HEADER_V2_LEN",
    "FILE_ID_LEN",
    "VERSION_V1",
    "VERSION_V2",
    "CURRENT_VERSION",
    "generate_file_id",
    "encrypt_file",
    "decrypt_file",
    "decrypt_file_to_memory",
    "decrypt_file_streaming",
    "compute_decrypted_sha256",
    "verify_file_integrity",
    "read_file_id",
]
