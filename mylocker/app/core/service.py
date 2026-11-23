"""
service.py — Single-writer VaultService coordinating all vault mutations.

Audit Requirements (Phase 1):
  - Single writer: one RLock around every mutation.
  - Worker thread with a task queue.
  - UI submits tasks (add, restore, delete, verify, backup, rotate).
  - Emits per-file and per-chunk progress signals.
  - Real cancel: passes threading.Event through chunk loops; cancellation before
    secure delete step rolls back cleanly without deleting original.
  - Unique temp names (random suffixes), never shared .tmp names.
  - Strong references to QThread and worker objects to avoid garbage collection.
  - Clean shutdown (quit() / wait()).
"""

from __future__ import annotations

import logging
import queue
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from mylocker.app.core.backup import create_vault_backup, restore_vault_backup, rotate_vault_keys
from mylocker.app.core.chunked_io import OperationCancelledError
from mylocker.app.core.vault import Vault

logger = logging.getLogger(__name__)


@dataclass
class VaultTask:
    """Represents a queued task for the VaultWorker."""
    action: str                        # 'add', 'restore', 'delete', 'verify', 'backup', 'rotate'
    args: tuple                        # arguments for the action
    kwargs: dict                       # keyword arguments for the action
    task_id: str = ""

    def __post_init__(self) -> None:
        if not self.task_id:
            self.task_id = secrets.token_hex(4)


class VaultWorker(QObject):
    """Processes queued vault tasks sequentially on a dedicated worker thread."""

    task_started = Signal(str, str)             # (task_id, task_description)
    progress = Signal(int, int, str)            # (done, total, status_message)
    file_completed = Signal(str, object)        # (file_id, result_metadata)
    task_finished = Signal(str, bool, str)      # (task_id, success, message)
    error_occurred = Signal(str, str, str)      # (task_id, error_code, friendly_message)

    def __init__(self, vault: Vault, lock: threading.RLock) -> None:
        super().__init__()
        self._vault = vault
        self._lock = lock
        self._queue: queue.Queue[Optional[VaultTask]] = queue.Queue()
        self._cancel_event = threading.Event()
        self._running = True

    def submit(self, task: VaultTask) -> None:
        """Add a task to the queue."""
        self._queue.put(task)

    def cancel_current(self) -> None:
        """Signal cancellation to the current in-progress task."""
        self._cancel_event.set()

    def stop(self) -> None:
        """Signal the worker loop to terminate."""
        self._running = False
        self._cancel_event.set()
        self._queue.put(None)  # Sentinel to unblock get()

    def run(self) -> None:
        """Main worker loop running on QThread."""
        while self._running:
            try:
                task = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if task is None or not self._running:
                break

            if self._cancel_event.is_set():
                self._cancel_event.clear()
                self.task_finished.emit(task.task_id, False, "Operation was cancelled.")
                self._queue.task_done()
                continue

            self._execute_task(task)
            self._cancel_event.clear()
            self._queue.task_done()

    def _execute_task(self, task: VaultTask) -> None:
        action = task.action
        self.task_started.emit(task.task_id, f"Running {action}…")

        last_emit_ts = 0.0
        last_emit_pct = -1
        last_emit_stage = ""

        def _progress_cb(done: int, total: int, label: str = "") -> None:
            nonlocal last_emit_ts, last_emit_pct, last_emit_stage
            now = time.monotonic()
            pct = int(done * 100 / total) if total > 0 else 0
            stage = label.split(" ", 1)[0] if label else ""
            if (
                last_emit_pct < 0
                or pct >= 100
                or (now - last_emit_ts) >= 0.045
                or (stage != last_emit_stage and (now - last_emit_ts) >= 0.015)
            ):
                last_emit_ts = now
                last_emit_pct = pct
                last_emit_stage = stage
                self.progress.emit(done, total, label)

        try:
            with self._lock:
                if action == "add_file":
                    source_path, vault_folder = task.args
                    entry = self._vault.add_file(
                        source_path=Path(source_path),
                        vault_folder=vault_folder,
                        progress=_progress_cb,
                        cancel_event=self._cancel_event,
                    )
                    self.file_completed.emit(entry.file_id, entry)
                    self.task_finished.emit(task.task_id, True, "File added successfully.")

                elif action == "add_folder":
                    source_dir, vault_folder = task.args
                    entries = self._vault.add_folder(
                        source_dir=Path(source_dir),
                        vault_folder=vault_folder,
                        progress=_progress_cb,
                        cancel_event=self._cancel_event,
                    )
                    if len(entries) <= 25:
                        for e in entries:
                            self.file_completed.emit(e.file_id, e)
                    elif entries:
                        self.file_completed.emit(entries[-1].file_id, entries)
                    self.task_finished.emit(task.task_id, True, f"{len(entries)} files imported from folder.")

                elif action == "add_batch":
                    paths, vault_folder = task.args
                    mapped_items: list[tuple[Path, str]] = []
                    src_dirs: list[Path] = []
                    base_vfolder = vault_folder.rstrip("/")
                    for p_str in paths:
                        if self._cancel_event.is_set():
                            break
                        p = Path(p_str)
                        if p.is_dir():
                            resolved_dir = p.resolve()
                            src_dirs.append(resolved_dir)
                            for fpath in sorted(f for f in resolved_dir.rglob("*") if f.is_file()):
                                rel = fpath.relative_to(resolved_dir).parent
                                if str(rel) in ("", "."):
                                    vfolder = (base_vfolder + "/" + resolved_dir.name).replace("\\", "/")
                                else:
                                    vfolder = (base_vfolder + "/" + resolved_dir.name + "/" + str(rel)).replace("\\", "/")
                                mapped_items.append((fpath, vfolder))
                        elif p.is_file():
                            mapped_items.append((p, vault_folder))

                    entries = self._vault.add_many_mapped(
                        mapped_items,
                        progress=_progress_cb,
                        cancel_event=self._cancel_event,
                    )

                    for src_dir in src_dirs:
                        try:
                            if src_dir.exists():
                                for sub in sorted(
                                    (d for d in src_dir.rglob("*") if d.is_dir()),
                                    key=lambda x: len(x.parts),
                                    reverse=True,
                                ):
                                    if not any(sub.iterdir()):
                                        sub.rmdir()
                                if not any(src_dir.iterdir()):
                                    src_dir.rmdir()
                        except OSError:
                            pass

                    if len(entries) <= 25:
                        for e in entries:
                            self.file_completed.emit(e.file_id, e)
                    elif entries:
                        self.file_completed.emit(entries[-1].file_id, entries)

                    self.task_finished.emit(
                        task.task_id, True, f"{len(entries)} item(s) added to vault."
                    )

                elif action == "update_file":
                    file_id, new_source_path = task.args
                    entry = self._vault.update_file(
                        file_id=file_id,
                        new_source_path=Path(new_source_path),
                        progress=_progress_cb,
                        cancel_event=self._cancel_event,
                    )
                    self.file_completed.emit(file_id, entry)
                    self.task_finished.emit(task.task_id, True, "File updated successfully in vault.")

                elif action == "restore_file":
                    file_id, dest_dir, remove = task.args
                    restored_path = self._vault.restore_file(
                        file_id=file_id,
                        dest_dir=Path(dest_dir),
                        remove_from_vault=remove,
                        progress=_progress_cb,
                        cancel_event=self._cancel_event,
                    )
                    self.file_completed.emit(file_id, str(restored_path))
                    self.task_finished.emit(task.task_id, True, "File restored successfully.")

                elif action == "delete_file":
                    file_id = task.args[0]
                    self._vault.delete_from_vault(file_id)
                    self.file_completed.emit(file_id, None)
                    self.task_finished.emit(task.task_id, True, "File removed from vault.")

                elif action == "delete_many":
                    file_ids = list(task.args[0])
                    removed = self._vault.delete_many_from_vault(
                        file_ids,
                        progress=_progress_cb,
                        cancel_event=self._cancel_event,
                    )
                    self.file_completed.emit("", removed)
                    self.task_finished.emit(task.task_id, True, f"{removed} file(s) removed from vault.")

                elif action == "verify_all":
                    results = self._vault.verify_all(
                        progress=_progress_cb,
                        cancel_event=self._cancel_event,
                    )
                    all_ok = all(ok for ok, _ in results.values())
                    msg = "All files verified intact." if all_ok else "One or more files failed verification!"
                    self.task_finished.emit(task.task_id, all_ok, msg)

                elif action == "backup":
                    backup_path = Path(task.args[0])
                    ok, msg = create_vault_backup(
                        self._vault.vault_dir,
                        backup_path,
                        progress=_progress_cb,
                        cancel_event=self._cancel_event,
                    )
                    self.task_finished.emit(task.task_id, ok, msg)

                elif action == "restore_backup":
                    backup_path, password, kf_path = task.args
                    ok, msg = restore_vault_backup(
                        Path(backup_path),
                        self._vault.vault_dir,
                        password=password,
                        keyfile_path=kf_path,
                        progress=_progress_cb,
                        cancel_event=self._cancel_event,
                    )
                    self.task_finished.emit(task.task_id, ok, msg)

                elif action == "rotate_keys":
                    old_mk, password, kf_path = task.args
                    ok, msg = rotate_vault_keys(
                        self._vault.vault_dir,
                        old_mk,
                        password=password,
                        keyfile_path=kf_path,
                        progress=_progress_cb,
                        cancel_event=self._cancel_event,
                    )
                    self.task_finished.emit(task.task_id, ok, msg)

                else:
                    self.error_occurred.emit(task.task_id, "ERR-SVC-01", f"Unknown action: {action}")

        except OperationCancelledError:
            logger.info("Task %s cancelled by user.", task.task_id)
            self.task_finished.emit(task.task_id, False, "Operation was cancelled.")

        except Exception as exc:
            logger.error("Task %s failed: %s", task.task_id, type(exc).__name__)
            # Clean friendly error code without leaking secret names or paths
            self.error_occurred.emit(task.task_id, "ERR-VAULT-OP", "Operation failed. Your data remains safe.")
            self.task_finished.emit(task.task_id, False, "Operation failed.")


class VaultService(QObject):
    """Facade for the UI to submit tasks to the single-writer worker."""

    task_started = Signal(str, str)
    progress = Signal(int, int, str)
    file_completed = Signal(str, object)
    task_finished = Signal(str, bool, str)
    error_occurred = Signal(str, str, str)

    def __init__(self, vault: Vault) -> None:
        super().__init__()
        self.vault = vault
        self._lock = threading.RLock()

        # Dedicated QThread and Worker — keep strong references
        self._thread = QThread()
        self._worker = VaultWorker(vault, self._lock)
        self._worker.moveToThread(self._thread)

        # Wire signals from worker to service
        self._worker.task_started.connect(self.task_started)
        self._worker.progress.connect(self.progress)
        self._worker.file_completed.connect(self.file_completed)
        self._worker.task_finished.connect(self.task_finished)
        self._worker.error_occurred.connect(self.error_occurred)

        self._thread.started.connect(self._worker.run)
        self._thread.start()

    # ── Task submission API ───────────────────────────────────────────────────

    def add_file(self, source_path: Path | str, vault_folder: str = "/") -> str:
        task = VaultTask("add_file", (str(source_path), vault_folder), {})
        self._worker.submit(task)
        return task.task_id

    def add_folder(self, source_dir: Path | str, vault_folder: str = "/") -> str:
        task = VaultTask("add_folder", (str(source_dir), vault_folder), {})
        self._worker.submit(task)
        return task.task_id

    def add_batch(self, paths: list[Path | str], vault_folder: str = "/") -> str:
        task = VaultTask("add_batch", ([str(p) for p in paths], vault_folder), {})
        self._worker.submit(task)
        return task.task_id

    def update_file(self, file_id: str, new_source_path: Path | str) -> str:
        task = VaultTask("update_file", (file_id, str(new_source_path)), {})
        self._worker.submit(task)
        return task.task_id

    def restore_file(self, file_id: str, dest_dir: Path | str, remove: bool = False) -> str:
        task = VaultTask("restore_file", (file_id, str(dest_dir), remove), {})
        self._worker.submit(task)
        return task.task_id

    def delete_file(self, file_id: str) -> str:
        task = VaultTask("delete_file", (file_id,), {})
        self._worker.submit(task)
        return task.task_id

    def delete_files(self, file_ids: list[str]) -> str:
        task = VaultTask("delete_many", (list(file_ids),), {})
        self._worker.submit(task)
        return task.task_id

    def verify_all(self) -> str:
        task = VaultTask("verify_all", (), {})
        self._worker.submit(task)
        return task.task_id

    def create_backup(self, backup_path: Path | str) -> str:
        task = VaultTask("backup", (str(backup_path),), {})
        self._worker.submit(task)
        return task.task_id

    def restore_backup(
        self, backup_path: Path | str, password: Optional[str] = None,
        keyfile_path: Optional[str] = None
    ) -> str:
        task = VaultTask("restore_backup", (str(backup_path), password, keyfile_path), {})
        self._worker.submit(task)
        return task.task_id

    def rotate_keys(
        self, old_mk: bytearray, password: str, keyfile_path: Optional[str] = None
    ) -> str:
        task = VaultTask("rotate_keys", (old_mk, password, keyfile_path), {})
        self._worker.submit(task)
        return task.task_id

    def cancel_current(self) -> None:
        self._worker.cancel_current()

    def shutdown(self) -> None:
        """Clean shutdown: stops worker loop, waits for QThread."""
        self._worker.stop()
        self._thread.quit()
        self._thread.wait(3000)


__all__ = ["VaultService", "VaultTask", "VaultWorker"]
