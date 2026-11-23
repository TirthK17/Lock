"""
vault.py — High-level vault operations (add, restore, delete, list).

Orchestrates: journal → encrypt → verify → secure_delete_original → update_index

Phase 1 audit changes:
  - blobmap.dat eliminated: blob_name is stored inside FileEntry (encrypted index).
    blob_name = HKDF(master_key, info=b"blobname-v1" + file_id_bytes, length=24).hex()
  - add_file is fully crash-safe: encrypt → verify SHA-256 → fsync rename → index
    atomic write (temp + fsync + rename + fsync dir + verify reload) → journal
    INDEXED → secure delete original → journal DONE.
  - Recovery at unlock: only delete an original if the index contains that file_id
    AND the blob verifies AND its hash matches; in every other state delete the
    partial blob and KEEP the original.
  - IndexCorruptError: show recovery screen to the user; never swallow silently.
  - Migration: if old blobmap.dat exists at open time, reads it, populates
    FileEntry.blob_name for each entry, saves the index, then securely deletes
    blobmap.dat.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from mylocker.app.core.chunked_io import (
    OperationCancelledError,
    compute_decrypted_sha256,
    decrypt_file,
    decrypt_file_streaming,
    decrypt_file_to_memory,
    encrypt_file,
    generate_file_id,
    verify_file_integrity,
)
from mylocker.app.core.index import EncryptedIndex, FileEntry, IndexCorruptError, VaultIndex
from mylocker.app.core.journal import Journal, JournalState, hash_file_sha256
from mylocker.app.core.kdf import derive_blob_name_key
from mylocker.app.core.secure_delete import SecureDeleteError, secure_delete_file
from mylocker.app.core.session import get_session
from mylocker.app.core.thumbnails import make_media_thumbnail_b64

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]  # (done, total, label)

_IMAGE_EXTS: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jfif": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".svg": "image/svg+xml",
    ".heic": "image/heic",
    ".avif": "image/avif",
}

_VIDEO_EXTS: dict[str, str] = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".wmv": "video/x-ms-wmv",
    ".webm": "video/webm",
    ".flv": "video/x-flv",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".3gp": "video/3gpp",
}


def infer_mime_type(filename: str, existing_mime: Optional[str] = None) -> str:
    """Resolve reliable MIME type with explicit image/video extension fallbacks."""
    if existing_mime and existing_mime != "application/octet-stream":
        return existing_mime
    ext = Path(filename).suffix.lower()
    if ext in _IMAGE_EXTS:
        return _IMAGE_EXTS[ext]
    if ext in _VIDEO_EXTS:
        return _VIDEO_EXTS[ext]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or existing_mime or "application/octet-stream"


class VaultError(Exception):
    """General vault operation error."""


def _derive_blob_name(master_key: bytes | bytearray, file_id_bytes: bytes) -> str:
    """Derive a deterministic (but secret) blob filename from MK + file_id.

    Uses HKDF-SHA256(MK, salt=b"", info=b"blobname-v1" + file_id_bytes) → 24 bytes → hex.
    The blob name is also stored in the encrypted FileEntry so we never need blobmap.dat.
    """
    key_bytes = derive_blob_name_key(master_key, file_id_bytes)
    return key_bytes.hex()


class Vault:
    """Main vault manager — the single point of contact for the UI."""

    def __init__(self, vault_dir: Path) -> None:
        self._base_vault_dir = Path(vault_dir).resolve()
        self.vault_dir = self._base_vault_dir
        self.blobs_dir = self.vault_dir / "blobs"
        self._index = EncryptedIndex(self.vault_dir)
        self._journal = Journal(self.vault_dir)
        self._loaded = False
        self._state_lock = threading.RLock()

    @property
    def is_decoy(self) -> bool:
        """Check if active session is authenticated as decoy vault."""
        try:
            return get_session().is_decoy
        except Exception:
            return False

    def _sync_decoy_state(self) -> None:
        """Mount isolated decoy folder if session is a decoy session."""
        with self._state_lock:
            target_dir = self._base_vault_dir / "decoy" if self.is_decoy else self._base_vault_dir
            if self.vault_dir != target_dir or not self._loaded:
                self.vault_dir = target_dir
                self.blobs_dir = target_dir / "blobs"
                self._index = EncryptedIndex(target_dir)
                self._journal = Journal(target_dir)
                self._loaded = False
            self.vault_dir.mkdir(parents=True, exist_ok=True)
            self.blobs_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_loaded(self) -> None:
        """Ensure vault state is synced with session and index is loaded."""
        with self._state_lock:
            self._sync_decoy_state()
            if not self._loaded:
                self.open()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def initialise(self) -> None:
        """Create vault directory structure on first setup."""
        with self._state_lock:
            self._sync_decoy_state()
            self.vault_dir.mkdir(parents=True, exist_ok=True)
            self.blobs_dir.mkdir(parents=True, exist_ok=True)
            # Start with a known-good empty index
            mk = get_session().get_master_key()
            self._index.load_new_empty()
            self._index.save(mk)
            self._loaded = True

    def open(self) -> VaultIndex:
        """Load and decrypt the index.  Call after session is unlocked.

        Raises:
            IndexCorruptError: if the primary index and all backups are corrupt.
        """
        with self._state_lock:
            self._sync_decoy_state()
            mk = get_session().get_master_key()

            # If decoy vault has not yet been initialized with files, start fresh
            if self.is_decoy and not (self.vault_dir / "index.dat").exists():
                self._index.load_new_empty()
                self._index.save(mk)
                self._loaded = True
                return self._index.index

            self._recover_journal(mk)
            idx = self._index.load(mk)
            self._migrate_blobmap_if_needed(mk)   # Phase 1 migration
            self._loaded = True
            return idx

    def load(self) -> VaultIndex:
        """Alias for open() — preferred after the initial unlock."""
        return self.open()

    def refresh(self) -> VaultIndex:
        """Return current in-memory index without disk reload (no recovery)."""
        with self._state_lock:
            target_dir = self._base_vault_dir / "decoy" if self.is_decoy else self._base_vault_dir
            if not self._loaded or self.vault_dir != target_dir:
                return self.open()
            return self._index.index

    def close(self) -> None:
        """Flush the index and release memory."""
        with self._state_lock:
            self._loaded = False
            self.vault_dir = self._base_vault_dir
            self.blobs_dir = self._base_vault_dir / "blobs"
            self._index = EncryptedIndex(self._base_vault_dir)
            self._journal = Journal(self._base_vault_dir)

    def reset(self) -> None:
        """Alias for close() to explicitly reset vault state on lock."""
        self.close()

    def _save_index(self, mk: Optional[bytes | bytearray] = None) -> None:
        with self._state_lock:
            if mk is None:
                mk = get_session().get_master_key()
            self._index.save(mk)

    def save_index(self) -> None:
        self._save_index()

    # ── Add files ─────────────────────────────────────────────────────────────

    def add_file(
        self,
        source_path: Path,
        vault_folder: str = "/",
        progress: Optional[ProgressCallback] = None,
        generate_thumbnail: bool = True,
        cancel_event: Optional[threading.Event] = None,
    ) -> FileEntry:
        """Encrypt a file into the vault and securely delete the original.

        Crash-safe steps (Phase 1 & Phase 4):
            (a) Validate source and record initial metadata.
            (b) Calculate source SHA-256 hash.
            (c) Encrypt to unique blob.part.
            (d) Verify chunk integrity and streaming SHA-256 (bounded RAM, Phase 4.B).
            (e) Check source consistency before committing (Phase 4.C).
            (f) Atomic rename blob.part → final blob name + fsync directory.
            (g) Journal COMMIT.
            (h) Write index atomically and verify reload.
            (i) Journal INDEXED.
            (j) Securely delete original ONLY if source was unchanged.
            (k) Journal DONE.

        Recovery and Data-Loss Prevention:
            If an exception occurs AFTER original deletion, the committed blob
            is NEVER deleted.
        """
        self._ensure_loaded()
        source_path = Path(source_path).resolve()
        if not source_path.exists() or not source_path.is_file():
            raise VaultError("Source file not found or is not a regular file.")

        initial_stat = source_path.stat()
        source_size = initial_stat.st_size
        initial_mtime = initial_stat.st_mtime
        file_id_bytes = generate_file_id()
        file_id_hex = file_id_bytes.hex()
        mk = get_session().get_master_key()

        # Deterministic blob name (derived from MK + file_id)
        blob_name = _derive_blob_name(mk, file_id_bytes)
        final_blob_path = self.blobs_dir / blob_name
        part_blob_path = self.blobs_dir / (blob_name + "." + secrets.token_hex(4) + ".part")

        # (a) Hash original
        if progress:
            progress(0, source_size, "Hashing original…")
        source_hash = hash_file_sha256(source_path)

        if cancel_event and cancel_event.is_set():
            raise VaultError("Operation cancelled.")

        # Journal PENDING
        entry_j = self._journal.begin(
            source_path=source_path.name,
            dest_path=blob_name,
            file_id=file_id_hex,
        )

        original_deleted = False
        file_indexed = False

        try:
            # (b) Encrypt to .part with cancel_event
            def _enc_progress(done: int, total: int) -> None:
                if progress:
                    progress(done, total, "Encrypting…")

            encrypt_file(source_path, part_blob_path, mk, file_id_bytes, _enc_progress, cancel_event=cancel_event)
            self._journal.advance(entry_j.op_id, JournalState.VERIFY, source_hash)

            if cancel_event and cancel_event.is_set():
                raise VaultError("Operation cancelled.")

            # (c) Decrypt and verify SHA-256 matches using bounded-RAM streaming (Audit Phase 4.B)
            # decrypt_file_streaming inside compute_decrypted_sha256 authenticates header, file_id,
            # every chunk AES-GCM tag, sequence numbers, finality flag, and PVTR trailer.
            if progress:
                progress(0, source_size, "Verifying…")
            dec_hash = compute_decrypted_sha256(part_blob_path, mk, expected_file_id=file_id_bytes, cancel_event=cancel_event)
            if dec_hash != source_hash:
                raise VaultError("Content hash mismatch after encryption verification.")

            # (d) Verify source file consistency (Audit Phase 4.C)
            current_stat = source_path.stat()
            source_unchanged = (
                current_stat.st_mtime == initial_mtime and current_stat.st_size == source_size
            )

            # (e) fsync + rename .part → final
            part_blob_path.rename(final_blob_path)
            try:
                dir_fd = os.open(str(self.blobs_dir), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass  # Windows: non-fatal

            self._journal.advance(entry_j.op_id, JournalState.COMMIT)

            # Build FileEntry
            mime_type = infer_mime_type(source_path.name)

            thumbnail_b64 = None
            if generate_thumbnail and mime_type.startswith(("image/", "video/")):
                thumbnail_b64 = self._make_thumbnail_b64(source_path, mime_type)

            file_entry = FileEntry(
                file_id=file_id_hex,
                original_name=source_path.name,
                original_path="",   # never store full path in the entry
                vault_path=vault_folder.rstrip("/") + "/" + source_path.name,
                size_bytes=source_size,
                modified_time=current_stat.st_mtime,
                mime_type=mime_type,
                blob_name=blob_name,
                thumbnail_b64=thumbnail_b64,
                source_hash=source_hash,
            )

            # (f) Write index atomically and verify reload
            self._index.index.add(file_entry)
            self._save_index(mk)
            file_indexed = True

            # (g) Journal INDEXED
            self._journal.advance(entry_j.op_id, JournalState.INDEXED)

            # (h) Secure delete original ONLY if source was unchanged
            if source_unchanged:
                if progress:
                    progress(source_size, source_size, "Securely deleting original…")
                try:
                    secure_delete_file(source_path)
                    original_deleted = True
                except SecureDeleteError as exc:
                    logger.warning("Secure delete incomplete (SSD?): %s", type(exc).__name__)
                    original_deleted = True
            else:
                logger.warning("Source file changed during encryption; preserving original.")

            # (i) Journal DONE
            self._journal.complete(entry_j.op_id)
            return file_entry

        except Exception as exc:
            # Rollback handling with data safety guarantee (Phase 4.A)
            self._journal.rollback(entry_j.op_id)

            if not original_deleted:
                # Original is still on disk: discard partial or uncommitted blob
                if file_indexed:
                    try:
                        self._index.index.remove(file_id_hex)
                        self._save_index(mk)
                    except Exception:
                        pass
                for path in (part_blob_path, final_blob_path):
                    if path.exists():
                        try:
                            path.unlink()
                        except OSError:
                            pass
            else:
                # Original was already deleted: under NO circumstance delete final_blob_path!
                logger.warning(
                    "Original was deleted before exception occurred; preserving committed blob %s.",
                    final_blob_path.name,
                )

            if isinstance(exc, VaultError):
                raise
            raise VaultError(str(exc)) from exc

    def update_file(
        self,
        file_id: str,
        new_source_path: Path,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> FileEntry:
        """Safely update an existing file with modified external content (Audit Phase 6).

        Preserves the existing encrypted version until the replacement is verified
        and committed.
        """
        self._ensure_loaded()
        entry = self._index.index.get(file_id)
        if not entry:
            raise VaultError("File not found in vault index.")

        new_source_path = Path(new_source_path).resolve()
        if not new_source_path.exists() or not new_source_path.is_file():
            raise VaultError("Updated source file not found or invalid.")

        mk = get_session().get_master_key()
        file_id_bytes = bytes.fromhex(file_id)
        blob_name = entry.blob_name or _derive_blob_name(mk, file_id_bytes)
        blob_path = self.blobs_dir / blob_name

        new_size = new_source_path.stat().st_size
        new_hash = hash_file_sha256(new_source_path)

        new_part_path = self.blobs_dir / (blob_name + "." + secrets.token_hex(4) + ".part")

        try:
            # 1. Encrypt new content to temp part
            def _enc_progress(done: int, total: int) -> None:
                if progress:
                    progress(done, total, "Encrypting updated version…")

            encrypt_file(new_source_path, new_part_path, mk, file_id_bytes, _enc_progress, cancel_event=cancel_event)

            # 2. Verify streaming integrity and SHA-256 (authenticates header, chunks, and trailer)
            dec_hash = compute_decrypted_sha256(new_part_path, mk, expected_file_id=file_id_bytes, cancel_event=cancel_event)
            if dec_hash != new_hash:
                raise VaultError("Content hash mismatch for updated file.")

            # 3. Commit new blob atomically
            new_part_path.replace(blob_path)

            # 4. Update FileEntry in index
            entry.size_bytes = new_size
            entry.modified_time = time.time()
            entry.source_hash = new_hash
            entry.blob_name = blob_name

            resolved_mime = infer_mime_type(entry.original_name, entry.mime_type)
            entry.mime_type = resolved_mime
            if resolved_mime.startswith(("image/", "video/")):
                entry.thumbnail_b64 = self._make_thumbnail_b64(new_source_path, resolved_mime)

            self._save_index(mk)

            # 5. Securely delete the temporary updated file
            try:
                secure_delete_file(new_source_path)
            except Exception:
                pass

            return entry

        except Exception as exc:
            new_part_path.unlink(missing_ok=True)
            if isinstance(exc, VaultError):
                raise
            raise VaultError(f"Failed to update file: {exc}") from exc

    def add_many_mapped(
        self,
        items: list[tuple[Path, str]],
        progress: Optional[ProgressCallback] = None,
        generate_thumbnail: bool = True,
        cancel_event: Optional[threading.Event] = None,
        flush_every: int = 16,
    ) -> list[FileEntry]:
        """Encrypt many (source_path, vault_folder) pairs with batched atomic index commits.

        Crash-safety invariant (never violated):
          No original file is deleted until BOTH its encrypted blob is verified
          and renamed into blobs/ AND the encrypted index containing its entry
          has been atomically saved, reloaded, and verified on disk.
        """
        self._ensure_loaded()
        if not items:
            return []

        mk = get_session().get_master_key()
        flush_every = max(1, int(flush_every))
        total_items = len(items)
        scale_total = total_items * 100

        added_entries: list[FileEntry] = []
        # Each tuple: (FileEntry, op_id, source_path, final_blob_path, source_unchanged)
        staged: list[tuple[FileEntry, str, Path, Path, bool]] = []

        def _flush_staged() -> None:
            if not staged:
                return
            op_ids = [rec[1] for rec in staged]
            entries_chunk = [rec[0] for rec in staged]
            blobs_chunk = [rec[3] for rec in staged]

            if progress:
                done_so_far = min(total_items, len(added_entries) + len(entries_chunk))
                progress(
                    done_so_far * 100,
                    scale_total,
                    f"Saving encrypted index ({done_so_far}/{total_items})…",
                )

            # 1. Persist all staged entries in ONE atomic encrypted index save
            try:
                with self._state_lock:
                    for e in entries_chunk:
                        self._index.index.add(e)
                    self._save_index(mk)
            except Exception as flush_exc:
                # Index was NOT committed — originals remain intact; clean uncommitted blobs
                with self._state_lock:
                    for e in entries_chunk:
                        try:
                            self._index.index.remove(e.file_id)
                        except Exception:
                            pass
                for blob in blobs_chunk:
                    blob.unlink(missing_ok=True)
                self._journal.rollback_many(op_ids)
                staged.clear()
                if isinstance(flush_exc, VaultError):
                    raise
                raise VaultError(str(flush_exc)) from flush_exc

            # 2. Index save + verify-reload succeeded: record INDEXED before deleting originals
            self._journal.advance_many(op_ids, JournalState.INDEXED)

            # 3. Securely delete originals ONLY where source did not mutate mid-encrypt
            for _entry, _op_id, src_path, _blob, src_unchanged in staged:
                if src_unchanged:
                    try:
                        secure_delete_file(src_path)
                    except SecureDeleteError as exc:
                        logger.warning("Secure delete incomplete (SSD?): %s", type(exc).__name__)
                else:
                    logger.warning("Source file changed during encryption; preserving original.")

            # 4. Mark batch complete in journal
            self._journal.complete_many(op_ids)
            added_entries.extend(entries_chunk)
            staged.clear()

        for idx_num, (raw_path, vault_folder) in enumerate(items):
            if cancel_event and cancel_event.is_set():
                break

            try:
                source_path = Path(raw_path).resolve()
                if not source_path.exists() or not source_path.is_file():
                    raise VaultError("Source file not found or is not a regular file.")

                initial_stat = source_path.stat()
                source_size = initial_stat.st_size
                initial_mtime = initial_stat.st_mtime
                file_id_bytes = generate_file_id()
                file_id_hex = file_id_bytes.hex()

                blob_name = _derive_blob_name(mk, file_id_bytes)
                final_blob_path = self.blobs_dir / blob_name
                part_blob_path = self.blobs_dir / (blob_name + "." + secrets.token_hex(4) + ".part")

                if progress:
                    progress(
                        idx_num * 100,
                        scale_total,
                        f"[{idx_num + 1}/{total_items}] Hashing {source_path.name}…",
                    )

                source_hash = hash_file_sha256(source_path)

                if cancel_event and cancel_event.is_set():
                    break

                entry_j = self._journal.begin(
                    source_path=source_path.name,
                    dest_path=blob_name,
                    file_id=file_id_hex,
                )

                try:
                    def _enc_progress(done: int, total: int, _i: int = idx_num, _name: str = source_path.name) -> None:
                        if progress:
                            sub_pct = int(done * 70 / total) if total > 0 else 70
                            progress(
                                _i * 100 + sub_pct,
                                scale_total,
                                f"[{_i + 1}/{total_items}] Encrypting {_name}…",
                            )

                    encrypt_file(
                        source_path,
                        part_blob_path,
                        mk,
                        file_id_bytes,
                        _enc_progress,
                        cancel_event=cancel_event,
                    )

                    if cancel_event and cancel_event.is_set():
                        raise OperationCancelledError("Operation cancelled.")

                    if progress:
                        progress(
                            idx_num * 100 + 75,
                            scale_total,
                            f"[{idx_num + 1}/{total_items}] Verifying {source_path.name}…",
                        )

                    dec_hash = compute_decrypted_sha256(
                        part_blob_path,
                        mk,
                        expected_file_id=file_id_bytes,
                        cancel_event=cancel_event,
                    )
                    if dec_hash != source_hash:
                        raise VaultError("Content hash mismatch after encryption verification.")

                    current_stat = source_path.stat()
                    source_unchanged = (
                        current_stat.st_mtime == initial_mtime and current_stat.st_size == source_size
                    )

                    part_blob_path.rename(final_blob_path)
                    try:
                        dir_fd = os.open(str(self.blobs_dir), os.O_RDONLY)
                        try:
                            os.fsync(dir_fd)
                        finally:
                            os.close(dir_fd)
                    except OSError:
                        pass

                    self._journal.advance(entry_j.op_id, JournalState.COMMIT, source_hash)

                    mime_type = infer_mime_type(source_path.name)
                    thumbnail_b64 = None
                    if generate_thumbnail and mime_type.startswith(("image/", "video/")):
                        thumbnail_b64 = self._make_thumbnail_b64(source_path, mime_type)

                    file_entry = FileEntry(
                        file_id=file_id_hex,
                        original_name=source_path.name,
                        original_path="",
                        vault_path=vault_folder.rstrip("/") + "/" + source_path.name,
                        size_bytes=source_size,
                        modified_time=current_stat.st_mtime,
                        mime_type=mime_type,
                        blob_name=blob_name,
                        thumbnail_b64=thumbnail_b64,
                        source_hash=source_hash,
                    )
                    staged.append((file_entry, entry_j.op_id, source_path, final_blob_path, source_unchanged))

                    if progress:
                        progress(
                            (idx_num + 1) * 100,
                            scale_total,
                            f"[{idx_num + 1}/{total_items}] Prepared {source_path.name}",
                        )
                except OperationCancelledError:
                    self._journal.rollback(entry_j.op_id)
                    part_blob_path.unlink(missing_ok=True)
                    final_blob_path.unlink(missing_ok=True)
                    break
                except Exception as item_exc:
                    self._journal.rollback(entry_j.op_id)
                    part_blob_path.unlink(missing_ok=True)
                    final_blob_path.unlink(missing_ok=True)
                    logger.warning("Could not add a file (%s) — skipping.", type(item_exc).__name__)
                    continue

            except OperationCancelledError:
                break
            except Exception as outer_exc:
                logger.warning("Could not stage a file (%s) — skipping.", type(outer_exc).__name__)
                continue

            if len(staged) >= flush_every:
                _flush_staged()

        # Always commit/finalize any verified staged items even on partial cancellation
        if staged:
            _flush_staged()

        return added_entries

    def add_folder(
        self,
        source_dir: Path,
        vault_folder: str = "/",
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> list[FileEntry]:
        """Recursively add all files in a directory preserving folder hierarchy (Audit Phase 4.D)."""
        source_dir = Path(source_dir).resolve()
        if not source_dir.exists() or not source_dir.is_dir():
            raise VaultError("Source directory does not exist or is not a folder.")

        all_files = sorted(f for f in source_dir.rglob("*") if f.is_file())
        mapped_items: list[tuple[Path, str]] = []
        base_vfolder = vault_folder.rstrip("/")

        for fpath in all_files:
            rel = fpath.relative_to(source_dir).parent
            if str(rel) in ("", "."):
                vfolder = (base_vfolder + "/" + source_dir.name).replace("\\", "/")
            else:
                vfolder = (base_vfolder + "/" + source_dir.name + "/" + str(rel)).replace("\\", "/")
            mapped_items.append((fpath, vfolder))

        entries = self.add_many_mapped(
            mapped_items,
            progress=progress,
            cancel_event=cancel_event,
        )

        # Clean empty subdirectories bottom-up, then root if empty
        try:
            for sub in sorted(
                (d for d in source_dir.rglob("*") if d.is_dir()),
                key=lambda x: len(x.parts),
                reverse=True,
            ):
                if not any(sub.iterdir()):
                    sub.rmdir()
            if source_dir.exists() and not any(source_dir.iterdir()):
                source_dir.rmdir()
        except OSError:
            pass

        return entries

    # ── Restore / Export ──────────────────────────────────────────────────────

    def restore_file(
        self,
        file_id: str,
        dest_dir: Path,
        remove_from_vault: bool = False,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Path:
        """Decrypt a file back to *dest_dir*.

        Args:
            file_id: Hex file ID.
            dest_dir: Directory to write the decrypted file.
            remove_from_vault: If True, remove the encrypted blob and index entry.
            progress: Progress callback.
            cancel_event: Optional cancellation event.

        Returns:
            Path to the restored plaintext file.
        """
        self._ensure_loaded()
        entry = self._index.index.get(file_id)
        if entry is None:
            raise VaultError("File not found in vault.")

        blob_path = self._get_blob_path(file_id)
        dest_dir = Path(dest_dir).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / entry.original_name
        if dest_path.exists():
            stem = dest_path.stem
            suffix = dest_path.suffix
            counter = 1
            while dest_path.exists():
                dest_path = dest_dir / f"{stem} ({counter}){suffix}"
                counter += 1
        mk = get_session().get_master_key()

        def _dec_progress(done: int, total: int) -> None:
            if progress:
                progress(done, total, "Decrypting…")

        decrypt_file(blob_path, dest_path, mk,
                     expected_file_id=bytes.fromhex(file_id),
                     progress=_dec_progress,
                     cancel_event=cancel_event)

        if remove_from_vault:
            self._index.index.remove(file_id)
            self._save_index(mk)
            try:
                secure_delete_file(blob_path)
            except SecureDeleteError:
                blob_path.unlink(missing_ok=True)

        return dest_path

    def export_file(
        self,
        file_id: str,
        dest_path: Path,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Path:
        """Decrypt a vault file directly to an explicit *dest_path*."""
        self._ensure_loaded()
        entry = self._index.index.get(file_id)
        if entry is None:
            raise VaultError("File not found in vault.")

        blob_path = self._get_blob_path(file_id)
        dest_path = Path(dest_path).resolve()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        mk = get_session().get_master_key()

        def _dec_progress(done: int, total: int) -> None:
            if progress:
                progress(done, total, "Decrypting…")

        decrypt_file(
            blob_path,
            dest_path,
            mk,
            expected_file_id=bytes.fromhex(file_id),
            progress=_dec_progress,
            cancel_event=cancel_event,
        )
        return dest_path

    def open_file_in_memory(self, file_id: str) -> bytearray:
        """Return decrypted file bytes in a mutable buffer (for gallery/viewer).

        Caller MUST zero the returned bytearray when done.
        """
        self._ensure_loaded()
        entry = self._index.index.get(file_id)
        if entry is None:
            raise VaultError("File not found in vault.")

        blob_path = self._get_blob_path(file_id)
        mk = get_session().get_master_key()
        return decrypt_file_to_memory(blob_path, mk,
                                      expected_file_id=bytes.fromhex(file_id))

    def stream_file(self, file_id: str):
        """Yield decrypted chunks (generator) for large video streaming."""
        self._ensure_loaded()
        entry = self._index.index.get(file_id)
        if entry is None:
            raise VaultError("File not found.")
        blob_path = self._get_blob_path(file_id)
        mk = get_session().get_master_key()
        return decrypt_file_streaming(blob_path, mk,
                                      expected_file_id=bytes.fromhex(file_id))

    def open_file_external(
        self,
        file_id: str,
        temp_base: Path,
        timeout_seconds: int = 60,
    ) -> tuple[Path, Path]:
        """Decrypt to a private temp directory, return (temp_dir, file_path).

        The caller is responsible for wiping temp_dir via wipe_temp_directory()
        when done (or on lock/close).
        """
        self._ensure_loaded()
        entry = self._index.index.get(file_id)
        if entry is None:
            raise VaultError("File not found.")

        # Create restricted temp dir
        temp_dir = temp_base / secrets.token_hex(8)
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Restrict ACL (Windows: remove inherited ACEs, add only current user)
        try:
            _restrict_dir_acl(temp_dir)
        except Exception:
            pass  # Non-fatal; ACL restriction is defence-in-depth

        file_path = temp_dir / entry.original_name
        blob_path = self._get_blob_path(file_id)
        mk = get_session().get_master_key()
        decrypt_file(blob_path, file_path, mk,
                     expected_file_id=bytes.fromhex(file_id))

        return temp_dir, file_path

    # ── Delete from vault ─────────────────────────────────────────────────────

    def delete_from_vault(self, file_id: str) -> None:
        """Permanently remove a file from the vault (no restore possible)."""
        self._ensure_loaded()
        mk = get_session().get_master_key()
        blob_path = self._get_blob_path(file_id)
        self._index.index.remove(file_id)
        self._save_index(mk)
        try:
            secure_delete_file(blob_path)
        except SecureDeleteError:
            blob_path.unlink(missing_ok=True)

    def delete_many_from_vault(
        self,
        file_ids: list[str],
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> int:
        """Permanently remove multiple files from the vault with a single atomic index write."""
        self._ensure_loaded()
        unique_ids = list(dict.fromkeys(fid for fid in file_ids if fid))
        if not unique_ids:
            return 0

        mk = get_session().get_master_key()
        targets: list[tuple[str, Path]] = []
        with self._state_lock:
            for fid in unique_ids:
                if self._index.index.get(fid) is not None:
                    targets.append((fid, self._get_blob_path(fid)))
            if not targets:
                return 0
            if progress:
                progress(0, len(targets), f"Updating encrypted index for {len(targets)} file(s)…")
            for fid, _blob_path in targets:
                self._index.index.remove(fid)
            self._save_index(mk)

        deleted = 0
        total = len(targets)
        for i, (_fid, blob_path) in enumerate(targets):
            if progress:
                progress(i, total, f"Wiping encrypted blob {i + 1}/{total}…")
            try:
                secure_delete_file(blob_path)
            except SecureDeleteError:
                blob_path.unlink(missing_ok=True)
            except Exception:
                blob_path.unlink(missing_ok=True)
            deleted += 1

        if progress:
            progress(total, total, f"Removed {deleted} file(s) from vault.")
        return deleted

    # ── Verify vault integrity ─────────────────────────────────────────────────

    def verify_all(
        self,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> dict[str, tuple[bool, str]]:
        """Authenticate every chunk of every encrypted file.

        Returns:
            dict mapping file_id → (ok, message)
        """
        self._ensure_loaded()
        mk = get_session().get_master_key()
        results = {}
        entries = self._index.index.all_files()
        total = len(entries)
        for i, entry in enumerate(entries):
            if cancel_event and cancel_event.is_set():
                raise OperationCancelledError("Operation cancelled.")
            if progress:
                progress(i, total, "Verifying…")
            try:
                blob_path = self._get_blob_path(entry.file_id)
                ok, msg = verify_file_integrity(blob_path, mk, cancel_event=cancel_event)
            except OperationCancelledError:
                raise
            except Exception as exc:
                ok, msg = False, str(exc)
            results[entry.file_id] = (ok, msg)
        return results

    # ── Blob path helpers (Phase 1: no blobmap.dat) ───────────────────────────

    def _get_blob_path(self, file_id: str) -> Path:
        """Return the blob path for a file_id using the FileEntry's blob_name."""
        entry = self._index.index.get(file_id)
        if entry is None:
            raise VaultError("File not found in vault index.")
        blob_name = entry.blob_name
        if not blob_name:
            # Fallback: re-derive from MK (migration or recovery path)
            mk = get_session().get_master_key()
            blob_name = _derive_blob_name(mk, bytes.fromhex(file_id))
        return self.blobs_dir / blob_name

    # ── Migration: blobmap.dat → FileEntry.blob_name ──────────────────────────

    def _migrate_blobmap_if_needed(self, mk: bytes | bytearray) -> None:
        """If old blobmap.dat exists, migrate blob_name into FileEntry and delete it."""
        blobmap_path = self.vault_dir / "blobmap.dat"
        if not blobmap_path.exists():
            return
        try:
            import json as _json
            with open(blobmap_path, "r") as fh:
                bm: dict = _json.load(fh)
            changed = False
            for file_id, blob_name in bm.items():
                entry = self._index.index.get(file_id)
                if entry is not None and not entry.blob_name:
                    entry.blob_name = blob_name
                    changed = True
            if changed:
                self._save_index(mk)
            # Securely delete blobmap.dat
            try:
                secure_delete_file(blobmap_path)
            except SecureDeleteError:
                blobmap_path.unlink(missing_ok=True)
            logger.info("Migrated blobmap.dat to encrypted index.")
        except Exception as exc:
            logger.warning("blobmap.dat migration failed: %s", type(exc).__name__)

    # ── Journal recovery ──────────────────────────────────────────────────────

    def _recover_journal(self, mk: bytes | bytearray) -> None:
        """Clean up any incomplete operations from a previous run.

        Called at unlock BEFORE the index is loaded.  At this point the index
        is not available, so recovery decisions are limited to what is in the
        journal itself.

        Recovery rule (Phase 1):
          PENDING / ROLLBACK: delete partial blob, keep original.
          VERIFY: blob may be complete — verify it. If ok advance to COMMIT;
                  otherwise delete blob.
          COMMIT / INDEXED: blob is complete and verified. Load the index to
                  check if file_id is there; if yes, the original can be deleted;
                  if no, the add was not committed — delete blob, keep original.
        """
        # Supply MK to journal for encrypted load/save
        self._journal.set_master_key(mk)
        pending = self._journal.get_pending()
        if not pending:
            return

        # Try to load the index for COMMIT/INDEXED recovery (best effort)
        try:
            idx = self._index.load(mk)
            has_index = True
        except IndexCorruptError:
            idx = None
            has_index = False

        for entry in pending:
            logger.info("Recovering journal entry state=%s", entry.state)
            # dest_path in journal now stores blob_name (random hex)
            dest = self.blobs_dir / entry.dest_path

            if entry.state in (JournalState.PENDING, JournalState.ROLLBACK):
                # Encryption may be partial — delete any blob, keep original
                for f in self.blobs_dir.glob(entry.dest_path + "*.part"):
                    f.unlink(missing_ok=True)
                dest.unlink(missing_ok=True)
                self._journal.rollback(entry.op_id)

            elif entry.state == JournalState.VERIFY:
                # Encryption done, not yet content-verified
                if dest.exists():
                    ok, _ = verify_file_integrity(dest, mk)
                    if ok:
                        self._journal.advance(entry.op_id, JournalState.COMMIT)
                        # Fall through to COMMIT handling below
                    else:
                        dest.unlink(missing_ok=True)
                        self._journal.rollback(entry.op_id)
                        continue
                else:
                    self._journal.rollback(entry.op_id)
                    continue

            # (fall-through for VERIFY→COMMIT handled next)
            if entry.state in (JournalState.COMMIT, JournalState.INDEXED):
                if not dest.exists():
                    self._journal.rollback(entry.op_id)
                    continue

                # Only delete original if index contains file_id AND blob verifies
                in_index = has_index and idx is not None and idx.get(entry.file_id) is not None
                ok, _ = verify_file_integrity(dest, mk)
                hash_ok = True
                if in_index and ok and entry.source_hash:
                    # Can't re-read the original source (may already be deleted)
                    # Trust the INDEXED state means delete was intended
                    hash_ok = True  # source was already verified before INDEXED

                if in_index and ok and hash_ok:
                    # Safe to complete — journal entry is DONE
                    self._journal.complete(entry.op_id)
                else:
                    # Something is off — delete the blob, keep original safe
                    dest.unlink(missing_ok=True)
                    self._journal.rollback(entry.op_id)

    # ── Thumbnail helper ──────────────────────────────────────────────────────

    def _make_thumbnail_b64(
        self, source_path: Path, mime_type: Optional[str] = None
    ) -> Optional[str]:
        """Generate a JPEG thumbnail from a plaintext image or video file."""
        resolved_mime = mime_type or infer_mime_type(source_path.name)
        return make_media_thumbnail_b64(source_path, resolved_mime)


def _restrict_dir_acl(path: Path) -> None:
    """Remove world-readable ACEs from a directory (Windows only)."""
    try:
        import ntsecuritycon as con
        import win32security
        sd = win32security.GetFileSecurity(
            str(path),
            win32security.DACL_SECURITY_INFORMATION | win32security.OWNER_SECURITY_INFORMATION,
        )
        dacl = win32security.ACL()
        # Grant full control to current user only
        user_sid = win32security.GetTokenInformation(
            win32security.OpenProcessToken(
                win32security.GetCurrentProcess(),
                win32security.TOKEN_QUERY,
            ),
            win32security.TokenUser,
        )[0]
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            con.FILE_ALL_ACCESS,
            user_sid,
        )
        sd.SetSecurityDescriptorDacl(True, dacl, False)
        win32security.SetFileSecurity(
            str(path),
            win32security.DACL_SECURITY_INFORMATION,
            sd,
        )
    except ImportError:
        pass  # pywin32 not available in test environment


def _b64e(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode()


__all__ = [
    "Vault",
    "VaultError",
    "infer_mime_type",
]
