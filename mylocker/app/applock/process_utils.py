"""
process_utils.py — Windows process management (suspend, resume, terminate)
and scanning of installed applications for App Lock.

Uses:
  - ntdll.NtSuspendProcess / NtResumeProcess via ctypes
  - psutil for process enumeration and termination
  - winreg / filesystem search for installed programs
"""

from __future__ import annotations

import ctypes
import logging
import os
import winreg
from pathlib import Path
from typing import Dict, List, Set

import psutil

logger = logging.getLogger(__name__)

# Windows process access rights
PROCESS_ALL_ACCESS = 0x1FFFFF
PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_TERMINATE = 0x0001

# Load ntdll for NtSuspendProcess / NtResumeProcess
ntdll = ctypes.windll.ntdll
kernel32 = ctypes.windll.kernel32


def suspend_process(pid: int) -> bool:
    """Suspend all threads of the target process given its PID."""
    try:
        h_process = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not h_process:
            logger.warning("Could not open process %d for suspend: %d", pid, kernel32.GetLastError())
            return False

        status = ntdll.NtSuspendProcess(h_process)
        kernel32.CloseHandle(h_process)
        if status == 0:
            logger.info("Suspended process %d", pid)
            return True
        else:
            logger.warning("NtSuspendProcess failed for pid %d with status 0x%08X", pid, status)
            return False
    except Exception as exc:
        logger.error("Exception suspending process %d: %s", pid, exc)
        return False


def resume_process(pid: int) -> bool:
    """Resume all threads of a previously suspended process."""
    try:
        h_process = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not h_process:
            logger.warning("Could not open process %d for resume: %d", pid, kernel32.GetLastError())
            return False

        status = ntdll.NtResumeProcess(h_process)
        kernel32.CloseHandle(h_process)
        if status == 0:
            logger.info("Resumed process %d", pid)
            return True
        else:
            logger.warning("NtResumeProcess failed for pid %d with status 0x%08X", pid, status)
            return False
    except Exception as exc:
        logger.error("Exception resuming process %d: %s", pid, exc)
        return False


def terminate_process(pid: int) -> bool:
    """Forcefully terminate a process by PID."""
    try:
        proc = psutil.Process(pid)
        proc.kill()
        logger.info("Terminated process %d", pid)
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        logger.warning("Could not kill process %d: %s", pid, exc)
        return False


def get_running_gui_processes() -> List[Dict[str, str]]:
    """Return a list of currently running user processes that have a window or exe."""
    results: List[Dict[str, str]] = []
    seen_names: Set[str] = set()

    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            info = proc.info
            name = (info.get("name") or "").lower()
            exe = info.get("exe") or ""

            # Filter system / helper processes
            if not name or name in seen_names or not exe:
                continue
            if name in {"svchost.exe", "system", "idle", "explorer.exe", "conhost.exe", "csrss.exe", "services.exe", "taskhostw.exe"}:
                continue

            seen_names.add(name)
            results.append({
                "pid": str(info.get("pid")),
                "name": info.get("name", ""),
                "exe": exe
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return sorted(results, key=lambda x: x["name"].lower())


def scan_installed_applications() -> List[Dict[str, str]]:
    """Scan Start Menu folders and Windows Registry for installed applications."""
    apps: Dict[str, str] = {}

    # 1. Search Start Menu directories for .lnk shortcuts
    start_menu_dirs = [
        Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]

    for start_dir in start_menu_dirs:
        if not start_dir.exists():
            continue
        for lnk in start_dir.rglob("*.lnk"):
            try:
                # We can record the shortcut base name
                app_name = lnk.stem
                if app_name.lower() not in {"uninstall", "help", "documentation"}:
                    apps[app_name] = str(lnk)
            except Exception:
                continue

    # 2. Check Windows Uninstall Registry Keys
    reg_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    for root_key, sub_path in reg_paths:
        try:
            with winreg.OpenKey(root_key, sub_path) as key:
                num_subkeys = winreg.QueryInfoKey(key)[0]
                for i in range(num_subkeys):
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            disp_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                            install_loc = ""
                            try:
                                install_loc, _ = winreg.QueryValueEx(subkey, "InstallLocation")
                            except FileNotFoundError:
                                pass
                            if disp_name and disp_name not in apps:
                                apps[disp_name] = install_loc or subkey_name
                    except Exception:
                        continue
        except Exception:
            continue

    return [{"name": name, "path": path} for name, path in sorted(apps.items(), key=lambda x: x[0].lower())]


def resolve_shortcut_target(path_str: str) -> str:
    """Resolve a .lnk Windows shortcut to its actual target executable path (Item 45)."""
    p = Path(path_str)
    if not p.suffix.lower() == ".lnk":
        return str(p.resolve()) if p.exists() else path_str

    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(str(p.resolve()))
        target = shortcut.TargetPath
        if target:
            return str(Path(target).resolve())
    except Exception as exc:
        logger.debug("Failed to resolve .lnk shortcut via WScript.Shell: %s", exc)

    return path_str


__all__ = [
    "suspend_process",
    "resume_process",
    "terminate_process",
    "get_running_gui_processes",
    "scan_installed_applications",
    "resolve_shortcut_target",
]

