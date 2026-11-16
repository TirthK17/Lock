"""
startup.py — Windows Task Scheduler and Startup management for Personal Vault.

Provides:
  - Register task in Task Scheduler to launch at user logon with highest privileges.
  - Query startup status.
  - Remove task when disabled by user.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TASK_NAME = "PersonalVaultAppLock"


def is_run_at_startup_enabled() -> bool:
    """Check if the Task Scheduler task or Registry run entry exists."""
    cmd = ["schtasks", "/query", "/tn", TASK_NAME]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return res.returncode == 0
    except Exception:
        return False


def set_run_at_startup(enable: bool, exe_path: Optional[str] = None) -> bool:
    """Enable or disable launching Personal Vault at user logon."""
    if not enable:
        cmd = ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            logger.info("Task Scheduler task removed: %s", res.stdout)
            return res.returncode == 0
        except Exception as exc:
            logger.error("Failed to delete startup task: %s", exc)
            return False

    target = exe_path or sys.executable
    args = f'"{target}"'
    if not getattr(sys, "frozen", False):
        # Running as python script
        main_py = str(Path(__file__).parent.parent.parent / "main.py")
        args = f'"{sys.executable}" "{main_py}"'

    # Create task running at user logon (normal user privileges, not highest)
    cmd = [
        "schtasks", "/create",
        "/tn", TASK_NAME,
        "/tr", args,
        "/sc", "onlogon",
        "/rl", "limited",
        "/f"
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            logger.info("Startup task registered successfully in Task Scheduler.")
            return True
        else:
            logger.warning("schtasks returned code %d: %s", res.returncode, res.stderr)
            return False
    except Exception as exc:
        logger.error("Failed to register startup task: %s", exc)
        return False


__all__ = ["is_run_at_startup_enabled", "set_run_at_startup"]

