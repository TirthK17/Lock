"""
test_integration.py — End-to-end integration test suite for Personal Vault.

Verifies:
  - Vault setup and initialisation
  - File encryption, original shredding, and bit-for-bit restore
  - In-memory photo/video decryption
  - Decoy vault creation and unlock
  - Backup creation and restore verification
  - Raw filesystem byte scans proving ZERO plaintext leaks
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mylocker.app.core import kdf as kdf_mod
from mylocker.app.core.backup import create_vault_backup, restore_vault_backup
from mylocker.app.core.crypto import zero_bytes
from mylocker.app.core.kdf import KDFParams
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.session import get_session
from mylocker.app.core.vault import Vault


@pytest.fixture
def fast_kdf(monkeypatch):
    """Speed up tests by using minimal Argon2id cost."""
    def _fast_benchmark(**kwargs):
        return KDFParams.new_with_fresh_salt(memory_kib=16 * 1024, time_cost=1)
    monkeypatch.setattr(kdf_mod, "benchmark_argon2id", _fast_benchmark)


class TestVaultIntegration:

    def test_full_vault_lifecycle(self, tmp_path, fast_kdf):
        vault_dir = tmp_path / "VaultData"
        keystore = Keystore(vault_dir)

        # 1. Setup
        master_password = "CorrectHorseBatteryStaple123!"
        mk, recovery_key = keystore.setup(master_password, benchmark=True)
        assert len(mk) == 32
        assert len(recovery_key) > 40

        # Unlock session
        session = get_session()
        session.unlock(mk, is_decoy=False)

        vault = Vault(vault_dir)
        vault.initialise()
        index = vault.open()
        assert len(index.all_files()) == 0

        # 2. Add File
        plaintext_content = b"TopSecretDocumentContent_AlphaBetaGamma_987654321" * 1000
        source_file = tmp_path / "confidential.txt"
        source_file.write_bytes(plaintext_content)
        source_sha256 = hashlib.sha256(plaintext_content).hexdigest()

        entry = vault.add_file(source_file, vault_folder="/Documents")

        # 3. Verify original is gone (shredded)
        assert not source_file.exists()
        assert entry.original_name == "confidential.txt"
        assert entry.size_bytes == len(plaintext_content)

        # 4. In-Memory Decryption
        dec_mem = vault.open_file_in_memory(entry.file_id)
        assert hashlib.sha256(dec_mem).hexdigest() == source_sha256
        zero_bytes(dec_mem)

        # 5. Restore File to Disk
        restore_dir = tmp_path / "Restored"
        restore_dir.mkdir()
        restored_path = vault.restore_file(entry.file_id, restore_dir)

        assert restored_path.exists()
        assert restored_path.read_bytes() == plaintext_content

        # 6. Deep Vault Integrity Check
        results = vault.verify_all()
        assert len(results) == 1
        ok, msg = results[entry.file_id]
        assert ok
        assert msg == "OK"

        # 7. Decoy Vault Setup & Verification
        decoy_pw = "DecoyPassword999!"
        decoy_mk = keystore.setup_decoy(decoy_pw)
        assert len(decoy_mk) == 32

        # Unlock with decoy
        unlocked_decoy = keystore.unlock_decoy(decoy_pw)
        assert bytes(unlocked_decoy) == bytes(decoy_mk)
        zero_bytes(unlocked_decoy)
        zero_bytes(decoy_mk)

        # 8. Encrypted Backup & Restore
        backup_tar = tmp_path / "vault_backup.tar.gz"
        ok, msg = create_vault_backup(vault_dir, backup_tar)
        assert ok
        assert backup_tar.exists()

        # Restore into clean directory
        restore_vault_dir = tmp_path / "RestoredVaultData"
        ok, msg = restore_vault_backup(backup_tar, restore_vault_dir)
        assert ok

        # Verify restored vault works with same master key
        restored_vault = Vault(restore_vault_dir)
        restored_index = restored_vault.open()
        assert len(restored_index.all_files()) == 1
        assert restored_index.all_files()[0].original_name == "confidential.txt"

        # 9. Clean session lock
        session.lock()
        assert not session.is_unlocked

    def test_strict_no_plaintext_leak_in_vault_directory(self, tmp_path, fast_kdf):
        """Scans every single file in the vault directory to verify no plaintext leaks."""
        vault_dir = tmp_path / "VaultData"
        keystore = Keystore(vault_dir)

        mk, _ = keystore.setup("StrictPassword1234!", benchmark=True)
        session = get_session()
        session.unlock(mk, is_decoy=False)

        vault = Vault(vault_dir)
        vault.initialise()
        vault.open()

        # Distinctive tokens
        canary_content = b"CANARY_SECRET_DATA_TOKEN_XYZ_1234567890"
        canary_filename = "SuperClassifiedFile2026.docx"

        src = tmp_path / canary_filename
        src.write_bytes(canary_content)

        vault.add_file(src)

        # Iterate over all files in vault directory
        for p in vault_dir.rglob("*"):
            if p.is_file():
                raw = p.read_bytes()
                assert canary_content not in raw, f"Plaintext content leaked in {p.name}!"
                assert canary_filename.encode() not in raw, f"Plaintext filename leaked in {p.name}!"

        session.lock()

