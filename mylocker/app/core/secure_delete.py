"""
secure_delete.py — Best-effort secure deletion of plaintext originals.

Security note (documented honestly):
- On SSDs with wear-leveling, the OS/firmware may leave copies of old data
  in flash cells that are not visible via the filesystem. This overwrite is
  best-effort and effective on HDDs; on SSDs it reduces but does not
  eliminate forensic recoverability.
- The app warns users about this limitation prominently.
- Recommend enabling BitLocker (or Veracrypt) so the entire drive is encrypted
  at rest, making SSD wear-leveling irrelevant to data confidentiality.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

OVERWRITE_PASSES = 3   # DoD 5220.22-M style (overkill for SSDs, fine for HDDs)


class SecureDeleteError(Exception):
    """Raised when secure deletion encounters an unrecoverable error."""


def secure_delete_file(path: Path, passes: int = OVERWRITE_PASSES) -> None:
    """Overwrite a file with random data, rename it, then delete it.

    Steps:
        1. Overwrite with random data *passes* times (fsync each pass).
        2. Rename to a random name in the same directory.
        3. Delete.

    Args:
        path: Path to the file to destroy.
        passes: Number of overwrite passes (default 3).

    Raises:
        SecureDeleteError: If the file is in use or permissions deny access.
    """
    if not path.exists():
        logger.debug("secure_delete_file: %s does not exist, skipping.", "[redacted]")
        return

    try:
        file_size = path.stat().st_size
        with open(path, "r+b") as fh:
            for _ in range(passes):
                fh.seek(0)
                remaining = file_size
                while remaining > 0:
                    chunk_size = min(remaining, 1 << 20)
                    fh.write(secrets.token_bytes(chunk_size))
                    remaining -= chunk_size
                fh.flush()
                os.fsync(fh.fileno())
    except OSError as exc:
        raise SecureDeleteError(
            f"Could not overwrite file (possibly in use or read-only): {exc}"
        ) from exc

    # Rename to a random name before deletion
    random_name = secrets.token_hex(16)
    new_path = path.parent / random_name
    try:
        path.rename(new_path)
    except OSError:
        new_path = path  # proceed with original path if rename fails

    try:
        new_path.unlink()
    except OSError as exc:
        raise SecureDeleteError(f"Could not delete file: {exc}") from exc

    logger.info("Securely deleted a file (name redacted from logs).")


def secure_delete_directory(directory: Path, passes: int = OVERWRITE_PASSES) -> None:
    """Recursively securely delete all files in a directory, then remove it.

    Args:
        directory: Directory to wipe.
        passes: Overwrite passes per file.
    """
    if not directory.is_dir():
        return

    errors: list[str] = []
    for child in directory.rglob("*"):
        if child.is_file():
            try:
                secure_delete_file(child, passes)
            except SecureDeleteError as exc:
                errors.append(str(exc))

    try:
        shutil.rmtree(directory, ignore_errors=True)
    except Exception as exc:
        errors.append(str(exc))

    if errors:
        raise SecureDeleteError(
            f"Some files could not be securely deleted ({len(errors)} error(s)). "
            "They may still be recoverable — consider running BitLocker or "
            "a disk-wipe tool for full assurance on SSDs."
        )


def wipe_temp_directory(temp_dir: Path) -> None:
    """Wipe a temporary directory created for opening files in external apps."""
    try:
        secure_delete_directory(temp_dir)
    except SecureDeleteError as exc:
        logger.warning("Temp directory wipe incomplete: %s", exc)


__all__ = [
    "SecureDeleteError",
    "secure_delete_file",
    "secure_delete_directory",
    "wipe_temp_directory",
    "OVERWRITE_PASSES",
]

