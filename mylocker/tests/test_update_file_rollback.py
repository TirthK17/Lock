"""
test_update_file_rollback.py — Regression tests for update_file crash-safe ordering and rollback (B3).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from mylocker.app.core.kdf import KDFParams
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.session import get_session
from mylocker.app.core.vault import Vault, VaultError


def _fast_kdf() -> KDFParams:
    return KDFParams.new_with_fresh_salt(memory_kib=1024, time_cost=1)


@pytest.fixture
def vault_env(tmp_path: Path):
    get_session().lock()
    vault_dir = tmp_path / "UpdateVault"
    ks = Keystore(vault_dir)
    mk, _ = ks.setup("SecurePass#123", benchmark=False, kdf_params=_fast_kdf())
    get_session().unlock(mk, is_decoy=False)

    vault = Vault(vault_dir)
    vault.initialise()
    yield vault, tmp_path
    get_session().lock()


def test_update_file_successful_commit(vault_env):
    vault, tmp_path = vault_env

    # 1. Add initial file
    initial_file = tmp_path / "doc.txt"
    initial_file.write_text("Version 1 content", encoding="utf-8")
    entry = vault.add_file(initial_file)
    file_id = entry.file_id
    orig_size = entry.size_bytes

    # 2. Update with new content
    updated_file = tmp_path / "doc_v2.txt"
    updated_file.write_text("Version 2 updated content with more text!", encoding="utf-8")
    updated_entry = vault.update_file(file_id, updated_file)

    assert updated_entry.file_id == file_id
    assert updated_entry.size_bytes != orig_size
    assert updated_entry.size_bytes == len("Version 2 updated content with more text!".encode("utf-8"))

    # 3. Decrypt and verify
    restore_dir = tmp_path / "restored"
    restored_path = vault.restore_file(file_id, restore_dir)
    assert restored_path.read_text(encoding="utf-8") == "Version 2 updated content with more text!"

    # 4. Verify no dangling .part or .old files
    assert list(vault.blobs_dir.glob("*.part")) == []
    assert list(vault.blobs_dir.glob("*.old")) == []


def test_update_file_rollback_on_index_save_failure(vault_env):
    vault, tmp_path = vault_env

    initial_file = tmp_path / "critical.txt"
    initial_file.write_text("Original critical data that must not be lost", encoding="utf-8")
    entry = vault.add_file(initial_file)
    file_id = entry.file_id
    orig_size = entry.size_bytes
    orig_hash = entry.source_hash

    # Prepare updated version
    updated_file = tmp_path / "critical_new.txt"
    updated_file.write_text("Corrupting update that fails midway", encoding="utf-8")

    # Simulate index save failure (disk full / I/O error)
    with patch.object(vault, "_save_index", side_effect=OSError("Disk full simulation")):
        with pytest.raises(VaultError, match="Disk full simulation"):
            vault.update_file(file_id, updated_file)

    # 1. In-memory entry must be rolled back
    current_entry = vault._index.index.get(file_id)
    assert current_entry.size_bytes == orig_size
    assert current_entry.source_hash == orig_hash

    # 2. Original encrypted blob must be rolled back and still decrypt to original content
    restore_dir = tmp_path / "restored_after_failure"
    restored_path = vault.restore_file(file_id, restore_dir)
    assert restored_path.read_text(encoding="utf-8") == "Original critical data that must not be lost"

    # 3. No dangling .part or .old files
    assert list(vault.blobs_dir.glob("*.part")) == []
    assert list(vault.blobs_dir.glob("*.old")) == []


def test_update_file_rollback_on_hash_mismatch(vault_env):
    vault, tmp_path = vault_env

    initial_file = tmp_path / "safe.txt"
    initial_file.write_text("Safe initial data", encoding="utf-8")
    entry = vault.add_file(initial_file)
    file_id = entry.file_id

    updated_file = tmp_path / "safe_v2.txt"
    updated_file.write_text("New data", encoding="utf-8")

    # Simulate hash verification failure
    with patch("mylocker.app.core.vault.compute_decrypted_sha256", return_value="bad_hash_value"):
        with pytest.raises(VaultError, match="Content hash mismatch"):
            vault.update_file(file_id, updated_file)

    # Original still decrypts correctly
    restore_dir = tmp_path / "restored_safe"
    restored_path = vault.restore_file(file_id, restore_dir)
    assert restored_path.read_text(encoding="utf-8") == "Safe initial data"

    assert list(vault.blobs_dir.glob("*.part")) == []
    assert list(vault.blobs_dir.glob("*.old")) == []
