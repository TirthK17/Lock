"""
index.py — Encrypted vault file index.

The index stores everything about vault contents EXCEPT the actual file data:
  - Original file name and path
  - Encrypted file's internal ID (random, used to find the blob on disk)
  - File size, modification time, MIME type
  - Thumbnail (small JPEG, stored as base64 inside the index — never on disk)
  - Tags, favorites flag
  - Folder hierarchy (virtual, stored as path string)
  - blob_name: the random on-disk filename inside blobs/ (no blobmap.dat needed)

The index is encrypted with its own key derived from MK:
    index_key = HKDF(MK, info="PersonalVault-IndexKey-v1")

The index is serialised as JSON → gzip-compressed → AES-256-GCM encrypted.

Atomic write: write temp → fsync → rename → fsync parent dir.
Rolling backup: last 3 good indices kept as index.bak1, .bak2, .bak3.

IMPORTANT: If the primary index AND all backups fail to decrypt, IndexCorruptError
is raised.  The UI must show a recovery screen; never fall back to an empty index
silently (data loss risk).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import struct
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from mylocker.app.core.crypto import CryptoError, decrypt_aes_gcm, encrypt_aes_gcm, zero_bytes
from mylocker.app.core.kdf import derive_index_key

logger = logging.getLogger(__name__)

INDEX_FILENAME   = "index.dat"
INDEX_BACKUP_FMT = "index.bak{n}"
MAX_BACKUPS      = 3
INDEX_VERSION    = 1


class IndexCorruptError(Exception):
    """Raised when the primary index and all backups fail to decrypt.

    Attributes:
        available_backups: list of Path objects for backup files that exist on disk
                           (may be corrupt themselves, but are available to present
                           to the user for attempted recovery).
    """

    def __init__(self, vault_dir: Path, message: str = "") -> None:
        self.vault_dir = vault_dir
        self.available_backups: list[Path] = []
        for n in range(1, MAX_BACKUPS + 1):
            bak = vault_dir / INDEX_BACKUP_FMT.format(n=n)
            if bak.exists():
                self.available_backups.append(bak)
        super().__init__(
            message or (
                "Vault index is corrupt and no backup could be decrypted. "
                f"Available backup files: {[str(b) for b in self.available_backups]}"
            )
        )


@dataclass
class FileEntry:
    """Metadata for one encrypted file in the vault."""
    file_id: str            # 32-char hex (16 raw bytes)
    original_name: str      # original filename (kept only in encrypted index)
    original_path: str      # original full path (encrypted, for reference)
    vault_path: str         # virtual path inside vault (e.g. "Photos/Summer/")
    size_bytes: int
    modified_time: float    # original mtime as Unix timestamp
    mime_type: str          # e.g. "image/jpeg"
    blob_name: str = ""     # random on-disk filename in blobs/ dir (Phase 1)
    added_time: float = field(default_factory=time.time)
    thumbnail_b64: Optional[str] = None   # base64-encoded JPEG thumbnail
    tags: list[str] = field(default_factory=list)
    is_favorite: bool = False
    is_folder: bool = False   # True for virtual folder entries
    source_hash: str = ""   # SHA-256 of original plaintext (for crash recovery)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FileEntry":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class VaultIndex:
    """The complete vault index."""
    version: int = INDEX_VERSION
    entries: dict[str, FileEntry] = field(default_factory=dict)  # file_id → entry

    def add(self, entry: FileEntry) -> None:
        self.entries[entry.file_id] = entry

    def remove(self, file_id: str) -> Optional[FileEntry]:
        return self.entries.pop(file_id, None)

    def get(self, file_id: str) -> Optional[FileEntry]:
        return self.entries.get(file_id)

    def all_files(self) -> list[FileEntry]:
        return [e for e in self.entries.values() if not e.is_folder]

    def search(self, query: str) -> list[FileEntry]:
        """Case-insensitive substring search across name, path, and tags."""
        q = query.lower()
        results = []
        for entry in self.entries.values():
            if (q in entry.original_name.lower()
                    or q in entry.vault_path.lower()
                    or any(q in t.lower() for t in entry.tags)):
                results.append(entry)
        return results

    def by_mime_prefix(self, prefix: str) -> list[FileEntry]:
        """Return entries whose MIME type starts with *prefix* (e.g. 'image/')."""
        return [e for e in self.all_files() if e.mime_type.startswith(prefix)]

    def favorites(self) -> list[FileEntry]:
        return [e for e in self.all_files() if e.is_favorite]

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "entries": {fid: e.to_dict() for fid, e in self.entries.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VaultIndex":
        idx = cls(version=d.get("version", INDEX_VERSION))
        for fid, ed in d.get("entries", {}).items():
            idx.entries[fid] = FileEntry.from_dict(ed)
        return idx


class EncryptedIndex:
    """Loads, saves, and manages encrypted vault index with rolling backups.

    Key behaviour change (Phase 1 audit fix):
      - If the primary index AND all backups fail, raises IndexCorruptError.
      - Never silently falls back to an empty index.
    """

    def __init__(self, vault_dir: Path) -> None:
        self.vault_dir = vault_dir
        self._path = vault_dir / INDEX_FILENAME
        self._index: Optional[VaultIndex] = None
        # Track whether this instance loaded/created the index in this session
        # (guards against saving an index that was never properly loaded)
        self._session_loaded = False

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        if name == "_index" and value is not None:
            super().__setattr__("_session_loaded", True)

    # ── Load / Save ───────────────────────────────────────────────────────────

    def load(self, master_key: bytes | bytearray) -> VaultIndex:
        """Decrypt and load the index.

        Raises:
            IndexCorruptError: If primary AND all backups fail to decrypt.
                               The UI must present backup choices to the user.
        """
        index_key = derive_index_key(master_key)
        try:
            # Try primary
            try:
                self._index = self._decrypt_index(self._path, index_key)
                self._session_loaded = True
                return self._index
            except (CryptoError, FileNotFoundError, ValueError, OSError) as primary_exc:
                logger.warning("Primary index failed: %s", type(primary_exc).__name__)

            # Try each backup in order
            for n in range(1, MAX_BACKUPS + 1):
                bak = self.vault_dir / INDEX_BACKUP_FMT.format(n=n)
                try:
                    self._index = self._decrypt_index(bak, index_key)
                    logger.info("Recovered index from backup %d.", n)
                    self._session_loaded = True
                    return self._index
                except (CryptoError, FileNotFoundError, ValueError, OSError):
                    continue

            # All failed — raise, do NOT create empty index silently
            raise IndexCorruptError(self.vault_dir)
        finally:
            zero_bytes(index_key)

    def load_new_empty(self) -> VaultIndex:
        """Create a brand-new empty index for a freshly created vault."""
        self._index = VaultIndex()
        self._session_loaded = True
        return self._index

    def try_load_backup(self, backup_path: Path,
                        master_key: bytes | bytearray) -> VaultIndex:
        """Attempt to load a specific backup file (for user-driven recovery)."""
        index_key = derive_index_key(master_key)
        try:
            self._index = self._decrypt_index(backup_path, index_key)
            self._session_loaded = True
            return self._index
        finally:
            zero_bytes(index_key)

    def save(self, master_key: bytes | bytearray) -> None:
        """Encrypt and atomically save the index, rotating backups.

        Audit Rule: Only rotate backups after the new file is written and verified.

        Raises:
            RuntimeError: If no index was loaded or created this session
                          (guards against overwriting a good index with garbage).
        """
        if self._index is None:
            raise RuntimeError("No index loaded.")
        if not self._session_loaded:
            raise RuntimeError(
                "Refusing to save index that was not loaded or created in this session."
            )
        index_key = derive_index_key(master_key)
        tmp = self._path.with_suffix(".tmp")
        try:
            # 1. Write to temp file
            raw_json = json.dumps(self._index.to_dict(), ensure_ascii=False).encode("utf-8")
            compressed = gzip.compress(raw_json, compresslevel=6)
            nonce, ciphertext = encrypt_aes_gcm(index_key, compressed)
            payload = nonce + struct.pack("<I", len(ciphertext)) + ciphertext

            with open(tmp, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())

            # 2. Verify temp file by reloading and checking keys
            reloaded = self._decrypt_index(tmp, index_key)
            if set(reloaded.entries.keys()) != set(self._index.entries.keys()):
                raise RuntimeError("Index verification after save failed.")

            # 3. Only rotate backups after the new file is written and verified!
            self._rotate_backups()

            # 4. Atomic rename temp file to primary index
            tmp.replace(self._path)
            try:
                dir_fd = os.open(str(self._path.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            zero_bytes(index_key)

    @property
    def index(self) -> VaultIndex:
        if self._index is None:
            raise RuntimeError("Index not loaded yet — call load() or load_new_empty() first.")
        return self._index

    # ── Internal ──────────────────────────────────────────────────────────────

    def _encrypt_index(self, path: Path, idx: VaultIndex,
                       index_key: bytearray) -> None:
        """Serialise, compress, encrypt, and atomically write the index."""
        raw_json = json.dumps(idx.to_dict(), ensure_ascii=False).encode("utf-8")
        compressed = gzip.compress(raw_json, compresslevel=6)

        nonce, ciphertext = encrypt_aes_gcm(index_key, compressed)

        # Format: [NONCE 12B][LEN 4B LE][CIPHERTEXT]
        payload = nonce + struct.pack("<I", len(ciphertext)) + ciphertext

        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(path)
            # fsync the directory so the rename is durable
            try:
                dir_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass  # Windows does not support directory fsync; non-fatal
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _decrypt_index(self, path: Path, index_key: bytearray) -> VaultIndex:
        """Read, decrypt, decompress, and parse the index."""
        with open(path, "rb") as fh:
            nonce = fh.read(12)
            if len(nonce) < 12:
                raise ValueError("Index file too short.")
            ct_len = struct.unpack("<I", fh.read(4))[0]
            ciphertext = fh.read(ct_len)

        compressed = decrypt_aes_gcm(index_key, nonce, ciphertext)
        raw_json = gzip.decompress(compressed)
        data = json.loads(raw_json.decode("utf-8"))
        return VaultIndex.from_dict(data)

    def _rotate_backups(self) -> None:
        """Shift existing backups: bak2→bak3, bak1→bak2, current→bak1."""
        for n in range(MAX_BACKUPS, 0, -1):
            src = self.vault_dir / INDEX_BACKUP_FMT.format(n=n)
            if n == MAX_BACKUPS:
                if src.exists():
                    src.unlink()
            else:
                dst = self.vault_dir / INDEX_BACKUP_FMT.format(n=n + 1)
                if src.exists():
                    src.rename(dst)

        if self._path.exists():
            import shutil
            bak1 = self.vault_dir / INDEX_BACKUP_FMT.format(n=1)
            shutil.copy2(self._path, bak1)


__all__ = [
    "FileEntry",
    "VaultIndex",
    "EncryptedIndex",
    "IndexCorruptError",
]
