"""
session.py — In-memory session holding the Master Key while unlocked.

Security design:
- MK stored in a mutable bytearray so it can be explicitly zeroed on lock.
- Python's GC may still leave copies in RAM; this is a known, documented
  limitation of CPython (no way to prevent without C extensions).
- Session is a singleton accessed from one place; no global variables elsewhere.
- lock() zeros the key and clears all references.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from .crypto import zero_bytes

logger = logging.getLogger(__name__)


class Session:
    """Holds the decrypted Master Key and vault session state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mk: Optional[bytearray] = None
        self._unlocked_at: Optional[float] = None
        self._is_decoy: bool = False

    # ── Unlock ────────────────────────────────────────────────────────────────

    def unlock(self, master_key: bytearray, is_decoy: bool = False) -> None:
        """Store the Master Key and mark the session as active.

        Args:
            master_key: Mutable bytearray from Keystore.unlock_*.
            is_decoy: True if this is the decoy vault session.
        """
        with self._lock:
            if self._mk is not None:
                zero_bytes(self._mk)
            self._mk = master_key
            self._unlocked_at = time.monotonic()
            self._is_decoy = is_decoy
        logger.info("Session unlocked (decoy=%s).", is_decoy)

    # ── Lock ──────────────────────────────────────────────────────────────────

    def lock(self) -> None:
        """Zero the Master Key and invalidate the session."""
        with self._lock:
            if self._mk is not None:
                zero_bytes(self._mk)
                self._mk = None
            self._unlocked_at = None
            self._is_decoy = False
        logger.info("Session locked — master key zeroed.")

    # ── Access ────────────────────────────────────────────────────────────────

    @property
    def is_unlocked(self) -> bool:
        with self._lock:
            return self._mk is not None

    @property
    def is_decoy(self) -> bool:
        with self._lock:
            return self._is_decoy

    def get_master_key(self) -> bytearray:
        """Return the current Master Key.

        Raises:
            RuntimeError: If vault is locked.

        IMPORTANT: Caller must NOT store this reference longer than needed
        and must NOT zero it (session owns the buffer).
        """
        with self._lock:
            if self._mk is None:
                raise RuntimeError("Vault is locked.")
            return self._mk

    @property
    def unlocked_duration(self) -> Optional[float]:
        """Seconds since unlock, or None if locked."""
        with self._lock:
            if self._unlocked_at is None:
                return None
            return time.monotonic() - self._unlocked_at

    def __del__(self) -> None:
        self.lock()


# ── Module-level singleton ────────────────────────────────────────────────────
_session: Optional[Session] = None


def get_session() -> Session:
    global _session
    if _session is None:
        _session = Session()
    return _session


__all__ = ["Session", "get_session"]

