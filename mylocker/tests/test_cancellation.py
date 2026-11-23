"""
test_cancellation.py — Regression tests for cancellation support across all long-running vault operations.

Covers:
  - restore_file & export_file cancellation midway
  - verify_all cancellation midway
  - create_vault_backup & restore_vault_backup cancellation midway
  - rotate_vault_keys cancellation midway
  - VaultService cancellation handling
"""

import threading
from pathlib import Path

import pytest

from mylocker.app.core.backup import (
    create_vault_backup,
    restore_vault_backup,
    rotate_vault_keys,
)
from mylocker.app.core.chunked_io import (
    CHUNK_SIZE,
    OperationCancelledError,
    verify_file_integrity,
)
from mylocker.app.core.kdf import KDFParams
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.session import get_session
from mylocker.app.core.vault import Vault


def _fast_kdf() -> KDFParams:
    return KDFParams.new_with_fresh_salt(memory_kib=1024, time_cost=1)


@pytest.fixture
def initialized_vault(tmp_path: Path):
    get_session().lock()
    vault_dir = tmp_path / "test_vault"
    ks = Keystore(vault_dir)
    mk, _ = ks.setup("TestPass123!", benchmark=False, kdf_params=_fast_kdf())
    get_session().unlock(mk, is_decoy=False)

    v = Vault(vault_dir)
    v.initialise()
    yield v, vault_dir
    get_session().lock()


def test_restore_file_cancellation(initialized_vault, tmp_path: Path):
    vault, _ = initialized_vault
    # Create a multi-chunk file (> 2 MiB)
    large_data = b"CANCELLATION_TEST_DATA_" * (CHUNK_SIZE // 10)
    src_file = tmp_path / "large_file.bin"
    src_file.write_bytes(large_data)

    entry = vault.add_file(src_file)
    assert entry is not None

    cancel_ev = threading.Event()
    dest_dir = tmp_path / "restored"

    # Set cancel_event during progress callback
    def _cancel_progress(done: int, total: int, status: str = ""):
        if done > 0:
            cancel_ev.set()

    with pytest.raises(OperationCancelledError):
        vault.restore_file(
            entry.file_id,
            dest_dir=dest_dir,
            progress=_cancel_progress,
            cancel_event=cancel_ev,
        )

    # Confirm no partially decrypted file left behind
    assert not (dest_dir / "large_file.bin").exists()
    assert list(dest_dir.glob("*.part")) == []


def test_export_file_cancellation(initialized_vault, tmp_path: Path):
    vault, _ = initialized_vault
    large_data = b"EXPORT_CANCEL_TEST_" * (CHUNK_SIZE // 10)
    src_file = tmp_path / "large_export.bin"
    src_file.write_bytes(large_data)

    entry = vault.add_file(src_file)
    assert entry is not None

    cancel_ev = threading.Event()
    dest_path = tmp_path / "exported" / "export_out.bin"

    def _cancel_progress(done: int, total: int, status: str = ""):
        if done > 0:
            cancel_ev.set()

    with pytest.raises(OperationCancelledError):
        vault.export_file(
            entry.file_id,
            dest_path=dest_path,
            progress=_cancel_progress,
            cancel_event=cancel_ev,
        )

    assert not dest_path.exists()
    assert list(dest_path.parent.glob("*.part")) == []


def test_verify_all_cancellation(initialized_vault, tmp_path: Path):
    vault, _ = initialized_vault
    for i in range(3):
        f = tmp_path / f"test_{i}.bin"
        f.write_bytes(b"DATA" * 1000)
        vault.add_file(f)

    cancel_ev = threading.Event()

    def _cancel_on_first(done: int, total: int, status: str = ""):
        cancel_ev.set()

    with pytest.raises(OperationCancelledError):
        vault.verify_all(progress=_cancel_on_first, cancel_event=cancel_ev)


def test_verify_file_integrity_cancellation(initialized_vault, tmp_path: Path):
    vault, _ = initialized_vault
    f = tmp_path / "test_chunk.bin"
    f.write_bytes(b"DATA" * (CHUNK_SIZE // 5))
    entry = vault.add_file(f)

    blob_path = vault._get_blob_path(entry.file_id)
    mk = get_session().get_master_key()

    cancel_ev = threading.Event()
    cancel_ev.set()

    with pytest.raises(OperationCancelledError):
        verify_file_integrity(blob_path, mk, cancel_event=cancel_ev)


def test_create_vault_backup_cancellation(initialized_vault, tmp_path: Path):
    vault, vault_dir = initialized_vault
    for i in range(3):
        f = tmp_path / f"doc_{i}.bin"
        f.write_bytes(b"BACKUP_CONTENT" * 2000)
        vault.add_file(f)

    backup_path = tmp_path / "test_backup.tar.gz"
    cancel_ev = threading.Event()

    def _cancel_cb(done: int, total: int, msg: str = ""):
        cancel_ev.set()

    with pytest.raises(OperationCancelledError):
        create_vault_backup(vault_dir, backup_path, progress=_cancel_cb, cancel_event=cancel_ev)

    assert not backup_path.exists()
    assert not (vault_dir / "backup_manifest.json").exists()


def test_restore_vault_backup_cancellation(initialized_vault, tmp_path: Path):
    vault, vault_dir = initialized_vault
    f = tmp_path / "file1.txt"
    f.write_text("Hello Vault", encoding="utf-8")
    vault.add_file(f)

    backup_path = tmp_path / "valid_backup.tar.gz"
    ok, msg = create_vault_backup(vault_dir, backup_path)
    assert ok is True

    restore_target = tmp_path / "restored_vault"
    cancel_ev = threading.Event()

    def _cancel_cb(done: int, total: int, msg: str = ""):
        cancel_ev.set()

    with pytest.raises(OperationCancelledError):
        restore_vault_backup(
            backup_path,
            restore_target,
            password="TestPass123!",
            progress=_cancel_cb,
            cancel_event=cancel_ev,
        )

    # Confirm staging dir was cleaned and restore didn't complete
    assert not restore_target.exists()
    assert list(tmp_path.glob("restore_staging_*")) == []


def test_rotate_vault_keys_cancellation(initialized_vault, tmp_path: Path):
    vault, vault_dir = initialized_vault
    for i in range(2):
        f = tmp_path / f"rotate_{i}.bin"
        f.write_bytes(b"ROTATE_DATA_" * 1000)
        vault.add_file(f)

    old_mk = get_session().get_master_key()
    cancel_ev = threading.Event()

    def _cancel_cb(done: int, total: int, msg: str = ""):
        cancel_ev.set()

    with pytest.raises(OperationCancelledError):
        rotate_vault_keys(
            vault_dir,
            old_mk,
            password="NewPassword123!",
            progress=_cancel_cb,
            cancel_event=cancel_ev,
        )

    # Confirm vault.new was cleaned up and original vault remains valid
    vault_new = vault_dir.parent / f"{vault_dir.name}.new"
    assert not vault_new.exists()

    # Original vault still decrypts with old_mk
    v2 = Vault(vault_dir)
    idx = v2.open()
    assert len(idx.all_files()) == 2


def test_vault_service_cancel_restore(initialized_vault, tmp_path: Path, qtbot):
    from mylocker.app.core.service import VaultService

    vault, _ = initialized_vault
    # Multi-chunk file (~2.5 MiB) to ensure multiple decrypt progress iterations
    large_payload = b"MULTICHUNK_SERVICE_DATA_" * (CHUNK_SIZE // 10)
    f = tmp_path / "large_service_file.bin"
    f.write_bytes(large_payload)
    entry = vault.add_file(f)

    svc = VaultService(vault)
    try:
        finished_results = []
        svc.task_finished.connect(lambda tid, ok, msg: finished_results.append((tid, ok, msg)))

        dest_dir = tmp_path / "svc_restored"

        # Hook progress to trigger cancel_current when first chunk finishes
        svc.progress.connect(lambda done, total, msg: svc.cancel_current() if done > 0 else None)

        svc.restore_file(entry.file_id, dest_dir)

        # Wait for task completion signal
        qtbot.waitUntil(lambda: len(finished_results) > 0, timeout=5000)
        tid, ok, msg = finished_results[0]
        assert ok is False
        assert "cancelled" in msg.lower()
    finally:
        svc.shutdown()


def test_dashboard_pre_lock_hook_cancels_service_worker(initialized_vault, tmp_path: Path, qtbot):
    from unittest.mock import MagicMock

    from mylocker.app.system.lock_controller import get_lock_controller
    from mylocker.app.ui.dashboard import Dashboard

    vault, _ = initialized_vault
    dash = Dashboard(vault)
    qtbot.addWidget(dash)
    qtbot.wait(50)

    # Verify _on_pre_lock is registered with lock_controller
    lc = get_lock_controller()
    assert dash._on_pre_lock in lc._pre_lock_hooks

    # Verify calling _on_pre_lock cancels vault service without destroying worker thread
    mock_cancel = MagicMock()
    dash._vault_service.cancel_current = mock_cancel
    dash._on_pre_lock()
    mock_cancel.assert_called_once()

    # Clean up dashboard and verify hook is unregistered
    dash.cleanup()
    assert dash._on_pre_lock not in lc._pre_lock_hooks

