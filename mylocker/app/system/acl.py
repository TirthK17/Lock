"""
acl.py — Restrict NTFS permissions so only the current user has access.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def restrict_vault_acl(vault_dir: Path) -> bool:
    """Configure NTFS ACL so only the current user has full control.
    Removes inherited permissions (Everyone, Users, Authenticated Users).
    """
    try:
        import ntsecuritycon as con
        import win32api
        import win32security

        path_str = str(vault_dir)

        # Get current user SID
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32security.TOKEN_QUERY,
        )
        user_sid, _ = win32security.GetTokenInformation(
            token, win32security.TokenUser
        )

        # Create fresh DACL with inheritance flags for objects (files) and containers (subdirs)
        dacl = win32security.ACL()
        inherit_flags = (
            win32security.OBJECT_INHERIT_ACE
            | win32security.CONTAINER_INHERIT_ACE
        )
        dacl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION,
            inherit_flags,
            con.FILE_ALL_ACCESS,
            user_sid,
        )

        # Set DACL breaking inheritance using PROTECTED_DACL_SECURITY_INFORMATION
        sec_info = (
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION
        )
        win32security.SetNamedSecurityInfo(
            path_str,
            win32security.SE_FILE_OBJECT,
            sec_info,
            None,
            None,
            dacl,
            None,
        )

        # Recursively ensure all existing files and subdirectories inherit full control
        if vault_dir.is_dir():
            for item in vault_dir.rglob("*"):
                try:
                    item_dacl = win32security.ACL()
                    item_flags = inherit_flags if item.is_dir() else 0
                    item_dacl.AddAccessAllowedAceEx(
                        win32security.ACL_REVISION,
                        item_flags,
                        con.FILE_ALL_ACCESS,
                        user_sid,
                    )
                    win32security.SetNamedSecurityInfo(
                        str(item),
                        win32security.SE_FILE_OBJECT,
                        sec_info,
                        None,
                        None,
                        item_dacl,
                        None,
                    )
                except Exception:
                    pass

        logger.info("Vault NTFS ACL restricted to current user (inheritance blocked, children protected).")
        return True
    except Exception as exc:
        logger.warning("Could not set NTFS ACL (requires admin or NTFS volume): %s", exc)
        return False


__all__ = ["restrict_vault_acl"]

