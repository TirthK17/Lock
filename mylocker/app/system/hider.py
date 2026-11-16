"""
hider.py — Set/remove Hidden + System file attributes on the vault directory.
Also sets a Desktop.ini to hide the folder name from Explorer.
"""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4


def hide_vault_directory(vault_dir: Path) -> None:
    """Apply Hidden + System attributes to the vault directory."""
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(vault_dir))
        if attrs == -1:
            return
        new_attrs = attrs | FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
        ctypes.windll.kernel32.SetFileAttributesW(str(vault_dir), new_attrs)
        logger.info("Vault directory hidden.")
    except Exception as exc:
        logger.warning("Could not hide vault directory: %s", exc)


def unhide_vault_directory(vault_dir: Path) -> None:
    """Remove Hidden + System attributes."""
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(vault_dir))
        if attrs == -1:
            return
        new_attrs = attrs & ~FILE_ATTRIBUTE_HIDDEN & ~FILE_ATTRIBUTE_SYSTEM
        ctypes.windll.kernel32.SetFileAttributesW(str(vault_dir), new_attrs)
    except Exception as exc:
        logger.warning("Could not unhide vault directory: %s", exc)


def exclude_from_search_indexing(vault_dir: Path) -> None:
    """Write a Desktop.ini marking the folder as not to be indexed."""
    desktop_ini = vault_dir / "desktop.ini"
    content = "[ViewState]\n[.ShellClassInfo]\nInfoTip=System folder\n"
    try:
        if not desktop_ini.exists():
            desktop_ini.write_text(content)
        # Ensure hidden + system attributes on desktop.ini
        if desktop_ini.exists():
            ctypes.windll.kernel32.SetFileAttributesW(
                str(desktop_ini),
                FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM,
            )
    except Exception as exc:
        logger.warning("Could not write desktop.ini: %s", exc)

    # Also set the NTFS "not content indexed" attribute on the directory
    try:
        FILE_ATTRIBUTE_NOT_CONTENT_INDEXED = 0x2000
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(vault_dir))
        if attrs != -1:
            ctypes.windll.kernel32.SetFileAttributesW(
                str(vault_dir),
                attrs | FILE_ATTRIBUTE_NOT_CONTENT_INDEXED,
            )
    except Exception:
        pass


__all__ = ["hide_vault_directory", "unhide_vault_directory", "exclude_from_search_indexing"]

