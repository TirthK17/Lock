"""
bitlocker.py — Check Windows BitLocker / Device Encryption status.

Audit Requirement (Phase 5, Item 40):
  - Check if BitLocker is enabled on the vault drive.
  - Warn the user during setup if disabled or unconfirmed.
  - Recommend enabling BitLocker for hardware-level offline defense.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


def check_bitlocker_status(drive_path: Path) -> Tuple[bool, str]:
    """Check BitLocker encryption status for the target drive.

    Returns:
        (is_enabled, message)
    """
    drive_letter = drive_path.drive or "C:"
    if not drive_letter.endswith("\\"):
        mount_point = drive_letter
    else:
        mount_point = drive_letter.rstrip("\\")

    # 1. Attempt WMI query via win32com (initialize COM cleanly for worker threads)
    com_initialized = False
    try:
        try:
            import pythoncom
            pythoncom.CoInitialize()
            com_initialized = True
        except Exception:
            pass
        import win32com.client
        wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2\\Security\\MicrosoftVolumeEncryption")
        for vol in wmi.InstancesOf("Win32_EncryptableVolume"):
            if getattr(vol, "DriveLetter", "").upper() == mount_point.upper():
                # ProtectionStatus: 0 = Unprotected, 1 = Protected, 2 = Unknown
                status = getattr(vol, "ProtectionStatus", 0)
                if status == 1:
                    logger.info("BitLocker confirmed ACTIVE on %s", mount_point)
                    return True, f"BitLocker is enabled and protecting drive {mount_point}."
                else:
                    return False, f"BitLocker is NOT enabled on drive {mount_point} (Status: {status})."
    except Exception as exc:
        logger.debug("WMI BitLocker query failed (likely non-admin): %s", exc)
    finally:
        if com_initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass

    # 2. Fallback: query via PowerShell Get-BitLockerVolume
    try:
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-BitLockerVolume -MountPoint '{mount_point}' -ErrorAction SilentlyContinue).ProtectionStatus",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        out = res.stdout.strip().lower()
        if "on" in out or "1" in out:
            logger.info("BitLocker confirmed ACTIVE via PowerShell on %s", mount_point)
            return True, f"BitLocker is enabled on drive {mount_point}."
        elif "off" in out or "0" in out:
            return False, f"BitLocker is turned OFF on drive {mount_point}."
    except Exception as exc:
        logger.debug("PowerShell BitLocker check failed: %s", exc)

    # If unconfirmed (e.g. non-elevated user), provide informative recommendation
    return False, (
        f"BitLocker status could not be verified on drive {mount_point} (requires administrator rights). "
        "For maximum offline protection, ensure BitLocker or Windows Device Encryption is enabled."
    )


__all__ = ["check_bitlocker_status"]
