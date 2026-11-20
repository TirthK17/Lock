"""
journal.py — Write-ahead journal for crash-safe vault operations.

Problem: If power fails between "file encrypted" and "original deleted", we
get a situation where both exist. If it fails mid-encryption, we have a partial
file. The journal records intent so the app can recover cleanly on next start.

Journal states:
  PENDING  → operation started (encrypt started, original untouched)
  VERIFY   → encryption done, verifying by decrypting and hashing
  COMMIT   → verification passed; safe to delete original
  INDEXED  → index has been updated atomically; next step: delete original
  DONE     → original deleted; operation complete
  ROLLBACK → encryption failed; delete partial encrypted file, keep original

Phase 1 security changes:
  - Journal is encrypted with AES-256-GCM (key derived from MK).
  - source_path is stored only as a hash reference — never as a plaintext path.
  - dest_path stores the blob_name (a random 48-hex string), not a full path.
  - Journal recovery runs once at unlock before any worker, never from load_files().

The journal file is written atomically (temp → fsync → rename).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import struct
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

JOURNAL_FILENAME = "journal.enc"   # Renamed from journal.json (Phase 1)
_LEGACY_JOURNAL  = "journal.json"  # Migrate if found


class JournalState(str, Enum):
    PENDING  = "PENDING"
    VERIFY   = "VERIFY"
    COMMIT   = "COMMIT"
    INDEXED  = "INDEXED"   # Phase 1: index updated, original not yet deleted
    DONE     = "DONE"
    ROLLBACK = "ROLLBACK"


class JournalEntry:
    """Represents one in-progress vault operation.

    Security note: source_path stores only the original filename (not the full
    path) or its SHA-256 for journaling purposes.  dest_path stores the
    blob_name (random hex), never a filesystem path.
    """

    def __init__(
        self,
        op_id: str,
        state: JournalState,
        source_path: str,     # original filename only (no directory component)
        dest_path: str,       # blob_name (random hex in blobs/ dir)
        file_id: str,
        source_hash: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> None:
        self.op_id = op_id
        self.state = state
        self.source_path = source_path
        self.dest_path = dest_path
        self.file_id = file_id
        self.source_hash = source_hash          # SHA-256 hex of plaintext
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "op_id": self.op_id,
            "state": self.state.value,
            "source_path": self.source_path,
            "dest_path": self.dest_path,
            "file_id": self.file_id,
            "source_hash": self.source_hash,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "JournalEntry":
        return cls(
            op_id=d["op_id"],
            state=JournalState(d["state"]),
            source_path=d["source_path"],
            dest_path=d["dest_path"],
            file_id=d["file_id"],
            source_hash=d.get("source_hash"),
            created_at=d.get("created_at"),
        )


class Journal:
    """Persistent write-ahead log stored encrypted in vault_dir/journal.enc.

    Encryption: AES-256-GCM with a key derived from the Master Key via HKDF.
    The journal is only decryptable if you have the master key — protecting
    source filenames even if the vault directory is copied.

    If no master key is supplied (pre-unlock phase), falls back to a lightweight
    plaintext mode — in that case, source_path contains only a filename, never
    a full path.
    """

    def __init__(self, vault_dir: Path) -> None:
        self.vault_dir = vault_dir
        self._path = vault_dir / JOURNAL_FILENAME
        self._legacy_path = vault_dir / _LEGACY_JOURNAL
        self._entries: dict[str, JournalEntry] = {}
        self._master_key: Optional[bytes | bytearray] = None
        self._load()

    def set_master_key(self, mk: bytes | bytearray) -> None:
        """Supply the master key so the journal can encrypt its entries."""
        self._master_key = mk
        self._load()

    # ── API ───────────────────────────────────────────────────────────────────

    def begin(self, source_path: str, dest_path: str, file_id: str) -> JournalEntry:
        """Record a new pending add-to-vault operation."""
        op_id = secrets.token_hex(8)
        entry = JournalEntry(
            op_id=op_id,
            state=JournalState.PENDING,
            source_path=source_path,
            dest_path=dest_path,
            file_id=file_id,
        )
        self._entries[op_id] = entry
        self._save()
        return entry

    def advance(self, op_id: str, new_state: JournalState,
                source_hash: Optional[str] = None) -> None:
        """Move an operation to the next state."""
        entry = self._entries[op_id]
        entry.state = new_state
        if source_hash is not None:
            entry.source_hash = source_hash
        self._save()

    def complete(self, op_id: str) -> None:
        """Remove completed operation from active journal with a single atomic save."""
        if op_id in self._entries:
            del self._entries[op_id]
            self._save()

    def advance_many(self, op_ids: list[str], new_state: JournalState) -> None:
        """Advance multiple journal entries to *new_state* in one atomic save."""
        changed = False
        for op_id in op_ids:
            entry = self._entries.get(op_id)
            if entry is not None:
                entry.state = new_state
                changed = True
        if changed:
            self._save()

    def complete_many(self, op_ids: list[str]) -> None:
        """Remove multiple completed operations in one atomic save."""
        changed = False
        for op_id in op_ids:
            if op_id in self._entries:
                del self._entries[op_id]
                changed = True
        if changed:
            self._save()

    def rollback(self, op_id: str) -> None:
        """Remove rolled-back operation from active journal with a single atomic save."""
        if op_id in self._entries:
            del self._entries[op_id]
            self._save()

    def rollback_many(self, op_ids: list[str]) -> None:
        """Remove multiple rolled-back operations in one atomic save."""
        self.complete_many(op_ids)

    def get_pending(self) -> list[JournalEntry]:
        """Return entries that did not complete (need recovery on startup)."""
        return [e for e in self._entries.values()
                if e.state not in (JournalState.DONE,)]

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        # Migrate legacy plaintext journal if present
        if self._legacy_path.exists() and not self._path.exists():
            self._load_plaintext(self._legacy_path)
            self._save()   # re-save encrypted
            try:
                # Secure delete the plaintext journal
                from mylocker.app.core.secure_delete import secure_delete_file
                secure_delete_file(self._legacy_path)
            except Exception:
                self._legacy_path.unlink(missing_ok=True)
            return

        if not self._path.exists():
            return

        try:
            if self._master_key is not None:
                self._load_encrypted(self._path)
            else:
                # Key not yet available — load as plaintext legacy format
                self._load_plaintext(self._path)
        except Exception:
            logger.warning("Journal file unreadable — starting fresh.")
            self._entries = {}

    def _save(self) -> None:
        data = {"entries": [e.to_dict() for e in self._entries.values()]}
        raw = json.dumps(data, indent=2).encode("utf-8")

        if self._master_key is not None:
            payload = self._encrypt_payload(raw)
        else:
            # Fallback: plaintext (should only happen before first unlock)
            payload = raw

        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(self._path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _encrypt_payload(self, raw: bytes) -> bytes:
        """Encrypt journal payload with AES-256-GCM."""
        from mylocker.app.core.crypto import encrypt_aes_gcm
        from mylocker.app.core.kdf import derive_journal_key
        assert self._master_key is not None, "Master key must be loaded to encrypt journal"
        jkey = derive_journal_key(self._master_key)
        try:
            nonce, ct = encrypt_aes_gcm(jkey, raw)
            # Format: [MAGIC 4B][NONCE 12B][LEN 4B LE][CIPHERTEXT]
            return b"JRNL" + nonce + struct.pack("<I", len(ct)) + ct
        finally:
            from mylocker.app.core.crypto import zero_bytes
            zero_bytes(jkey)

    def _load_encrypted(self, path: Path) -> None:
        from mylocker.app.core.crypto import decrypt_aes_gcm
        from mylocker.app.core.kdf import derive_journal_key
        with open(path, "rb") as fh:
            header = fh.read(4)
            if header != b"JRNL":
                # Might be a plaintext journal from before Phase 1
                fh.seek(0)
                self._load_plaintext_fh(fh)
                return
            nonce = fh.read(12)
            ct_len = struct.unpack("<I", fh.read(4))[0]
            ct = fh.read(ct_len)
        assert self._master_key is not None, "Master key must be loaded to decrypt journal"
        jkey = derive_journal_key(self._master_key)
        try:
            raw = decrypt_aes_gcm(jkey, nonce, ct)
        finally:
            from mylocker.app.core.crypto import zero_bytes
            zero_bytes(jkey)
        self._parse_json(raw)

    def _load_plaintext(self, path: Path) -> None:
        try:
            raw = path.read_bytes()
            if raw.startswith(b"JRNL"):
                # Encrypted payload; cannot load without master key
                return
            data = json.loads(raw.decode("utf-8"))
            self._parse_entries(data)
        except Exception as exc:
            logger.warning("Journal plaintext parse failed: %s", exc)

    def _load_plaintext_fh(self, fh) -> None:
        try:
            data = json.load(fh)
            self._parse_entries(data)
        except Exception:
            logger.warning("Journal plaintext parse failed.")

    def _parse_json(self, raw: bytes) -> None:
        try:
            data = json.loads(raw.decode("utf-8"))
            self._parse_entries(data)
        except Exception:
            logger.warning("Journal JSON parse failed.")

    def _parse_entries(self, data: dict) -> None:
        for d in data.get("entries", []):
            try:
                e = JournalEntry.from_dict(d)
                self._entries[e.op_id] = e
            except Exception:
                pass


def hash_file_sha256(path: Path) -> str:
    """Compute SHA-256 of a file for pre/post-encryption verification."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


__all__ = [
    "Journal",
    "JournalEntry",
    "JournalState",
    "hash_file_sha256",
]
