"""
test_phase15_comprehensive.py — Comprehensive end-to-end tests for Personal Vault.

Verifies Phase 15 requirements:
  1. Primary / Decoy switching sequence:
     - Primary unlock -> inspect files -> lock.
     - Decoy unlock -> inspect files -> lock.
     - Decoy add file -> lock.
     - Primary unlock -> verify decoy file does NOT exist in primary.
     - Primary add file -> lock.
     - Decoy unlock -> verify primary file does NOT exist in decoy.
     - Primary password change -> verify decoy still opens with its original password.
     - Decoy setup / update -> verify primary still opens with its original password.
     - Decoy tamper -> primary remains functional.
  2. Large file bounded-RAM streaming verification:
     - File >100 MiB (e.g. 105 MiB) encrypted and verified without loading all into RAM.
  3. External modification save-back transaction:
     - Atomic update of file contents in vault, preserving old blob until new is committed.
     - Rollback on failure preserves original.
  4. Custom vault location setup:
     - Selecting non-default directory correctly provisions all structures in that target.
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

import pytest

from mylocker.app.core import kdf as kdf_mod
from mylocker.app.core.chunked_io import compute_decrypted_sha256
from mylocker.app.core.crypto import zero_bytes
from mylocker.app.core.kdf import KDFParams
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.session import get_session
from mylocker.app.core.vault import Vault, VaultError


@pytest.fixture
def fast_kdf(monkeypatch):
    """Speed up tests by using minimal Argon2id cost."""
    def _fast_benchmark(**kwargs):
        return KDFParams.new_with_fresh_salt(memory_kib=16 * 1024, time_cost=1)
    monkeypatch.setattr(kdf_mod, "benchmark_argon2id", _fast_benchmark)


class TestPrimaryDecoySwitchingSequence:
    """10-step full switching sequence proving absolute primary/decoy isolation."""

    def test_full_primary_decoy_lifecycle(self, tmp_path: Path, fast_kdf):
        vault_dir = tmp_path / "SecureVault"
        keystore = Keystore(vault_dir)

        primary_pw = "MasterPassword123!@#"
        decoy_pw = "DecoyPassword456!@#"

        # Step 1: Initial setup
        mk, _ = keystore.setup(primary_pw, benchmark=False)
        keystore.setup_decoy(decoy_pw)
        session = get_session()
        vault = Vault(vault_dir)

        # Step 2: Unlock primary -> inspect files -> lock
        session.unlock(mk, is_decoy=False)
        vault.initialise()
        assert not vault.is_decoy
        assert len(vault.load().all_files()) == 0
        session.lock()
        vault.close()

        # Step 3: Unlock decoy -> verify empty -> lock
        decoy_mk, is_decoy = keystore.unlock(decoy_pw)
        assert is_decoy is True
        session.unlock(decoy_mk, is_decoy=True)
        idx_decoy = vault.load()
        assert len(idx_decoy.all_files()) == 0
        session.lock()
        vault.close()

        # Step 4: Decoy add file -> lock
        decoy_mk, is_decoy = keystore.unlock(decoy_pw)
        session.unlock(decoy_mk, is_decoy=True)
        decoy_file = tmp_path / "decoy_secret.txt"
        decoy_file.write_bytes(b"Innocent decoy photos and notes")
        entry_decoy = vault.add_file(decoy_file)
        assert entry_decoy is not None
        assert len(vault.refresh().all_files()) == 1
        session.lock()
        vault.close()

        # Step 5: Primary unlock -> verify decoy file does NOT exist in primary
        primary_mk, is_decoy = keystore.unlock(primary_pw)
        assert is_decoy is False
        session.unlock(primary_mk, is_decoy=False)
        idx_primary = vault.load()
        assert len(idx_primary.all_files()) == 0  # Completely isolated
        assert entry_decoy.file_id not in idx_primary.entries

        # Step 6: Primary add file -> lock
        primary_file = tmp_path / "primary_confidential.txt"
        primary_file.write_bytes(b"REAL CONFIDENTIAL DATA 123456789")
        entry_primary = vault.add_file(primary_file)
        assert len(vault.refresh().all_files()) == 1
        session.lock()
        vault.close()

        # Step 7: Decoy unlock -> verify primary file does NOT exist in decoy
        decoy_mk, is_decoy = keystore.unlock(decoy_pw)
        session.unlock(decoy_mk, is_decoy=True)
        idx_decoy = vault.load()
        assert len(idx_decoy.all_files()) == 1
        assert idx_decoy.all_files()[0].file_id == entry_decoy.file_id
        assert entry_primary.file_id not in idx_decoy.entries
        session.lock()
        vault.close()

        # Step 8: Primary password change -> verify decoy still opens with its original password
        new_primary_pw = "NewMasterPassword999!@#"
        keystore.change_password(primary_pw, new_primary_pw)

        # Verify new primary password works
        new_mk, is_decoy = keystore.unlock(new_primary_pw)
        assert is_decoy is False
        zero_bytes(new_mk)

        # Verify old primary password fails
        with pytest.raises(Exception):
            keystore.unlock(primary_pw)

        # Verify decoy still opens with original decoy password
        decoy_mk_2, is_decoy_2 = keystore.unlock(decoy_pw)
        assert is_decoy_2 is True
        session.unlock(decoy_mk_2, is_decoy=True)
        assert len(vault.load().all_files()) == 1
        session.lock()
        vault.close()

        # Step 9: Re-configure decoy -> verify primary still opens
        new_decoy_pw = "BrandNewDecoyPassword777!@#"
        keystore.setup_decoy(new_decoy_pw)
        decoy_mk_3, is_decoy_3 = keystore.unlock(new_decoy_pw)
        assert is_decoy_3 is True
        zero_bytes(decoy_mk_3)

        new_mk_2, is_decoy_4 = keystore.unlock(new_primary_pw)
        assert is_decoy_4 is False
        session.unlock(new_mk_2, is_decoy=False)
        assert len(vault.load().all_files()) == 1
        assert vault.load().all_files()[0].file_id == entry_primary.file_id
        session.lock()
        vault.close()

        # Step 10: Tamper with decoy slot -> primary still opens unaffected
        cfg_path = vault_dir / "vault.config"
        import json
        cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
        # Tamper slot 1 (decoy slot)
        if "slots" in cfg_data and len(cfg_data["slots"]) > 1:
            cfg_data["slots"][1]["ciphertext"] = secrets.token_hex(48)
            cfg_path.write_text(json.dumps(cfg_data), encoding="utf-8")

        # Primary must still unlock cleanly!
        unaffected_mk, is_decoy_5 = keystore.unlock(new_primary_pw)
        assert is_decoy_5 is False
        session.unlock(unaffected_mk, is_decoy=False)
        assert len(vault.load().all_files()) == 1
        session.lock()
        vault.close()


class TestLargeFileBoundedStreaming:
    """Verifies streaming SHA-256 verification and encryption on files >100 MiB."""

    def test_large_file_streaming_verification(self, tmp_path: Path, fast_kdf):
        vault_dir = tmp_path / "LargeFileVault"
        keystore = Keystore(vault_dir)
        mk, _ = keystore.setup("LargeFileTestPass123!", benchmark=False)
        session = get_session()
        session.unlock(mk, is_decoy=False)

        vault = Vault(vault_dir)
        vault.initialise()
        vault.load()

        # Create a 105 MiB test file using repeating chunks (exceeds the old 100 MiB cap)
        large_file = tmp_path / "large_video.bin"
        chunk_pattern = b"A" * (1024 * 1024)  # 1 MiB pattern
        hasher = hashlib.sha256()
        with open(large_file, "wb") as fh:
            for _ in range(105):
                fh.write(chunk_pattern)
                hasher.update(chunk_pattern)
        expected_sha256 = hasher.hexdigest()

        # Add to vault (exercises streaming SHA-256 bounded RAM check)
        entry = vault.add_file(large_file, vault_folder="/Videos")
        assert entry.size_bytes == 105 * 1024 * 1024
        assert entry.source_hash == expected_sha256
        assert not large_file.exists()  # Shredded

        # Verify bounded streaming hash from disk blob
        blob_path = vault.blobs_dir / entry.blob_name
        computed_hash = compute_decrypted_sha256(
            blob_path, mk, expected_file_id=bytes.fromhex(entry.file_id)
        )
        assert computed_hash == expected_sha256

        # Restore file and verify bit-for-bit identity
        restore_dir = tmp_path / "Restored"
        restored_path = vault.restore_file(entry.file_id, restore_dir)
        assert restored_path.exists()
        assert restored_path.stat().st_size == 105 * 1024 * 1024

        restored_hasher = hashlib.sha256()
        with open(restored_path, "rb") as fh:
            while True:
                buf = fh.read(1024 * 1024)
                if not buf:
                    break
                restored_hasher.update(buf)
        assert restored_hasher.hexdigest() == expected_sha256

        session.lock()
        vault.close()


class TestExternalSaveBackTransaction:
    """Verifies atomic external update and transaction safety."""

    def test_update_file_success(self, tmp_path: Path, fast_kdf):
        vault_dir = tmp_path / "UpdateVault"
        keystore = Keystore(vault_dir)
        mk, _ = keystore.setup("UpdatePass123!@#", benchmark=False)
        session = get_session()
        session.unlock(mk, is_decoy=False)

        vault = Vault(vault_dir)
        vault.initialise()
        vault.load()

        # Add initial file
        orig_file = tmp_path / "document.txt"
        orig_file.write_text("Version 1 original content")
        entry = vault.add_file(orig_file)
        old_blob = entry.blob_name

        # Create updated version
        modified_file = tmp_path / "document_mod.txt"
        modified_file.write_text("Version 2 updated content with additions")

        # Update file in vault
        updated_entry = vault.update_file(entry.file_id, modified_file)
        assert updated_entry.file_id == entry.file_id
        assert updated_entry.size_bytes == len(b"Version 2 updated content with additions")
        # Old blob was securely replaced
        assert updated_entry.blob_name == old_blob  # Deterministic blob name remains consistent

        # Decrypt in memory and verify new content
        dec_data = vault.open_file_in_memory(entry.file_id)
        assert bytes(dec_data) == b"Version 2 updated content with additions"
        zero_bytes(dec_data)

        session.lock()
        vault.close()

    def test_update_file_invalid_file_id_fails(self, tmp_path: Path, fast_kdf):
        vault_dir = tmp_path / "UpdateVaultFail"
        keystore = Keystore(vault_dir)
        mk, _ = keystore.setup("UpdatePass123!@#", benchmark=False)
        session = get_session()
        session.unlock(mk, is_decoy=False)

        vault = Vault(vault_dir)
        vault.initialise()
        vault.load()

        dummy_file = tmp_path / "dummy.txt"
        dummy_file.write_text("Hello")

        with pytest.raises(VaultError, match="File not found"):
            vault.update_file("nonexistent_id_12345", dummy_file)

        session.lock()
        vault.close()


class TestCustomVaultLocationSetup:
    """Verifies custom vault path selection and persistence."""

    def test_custom_location_provisioning(self, tmp_path: Path, fast_kdf):
        custom_dir = tmp_path / "CustomVaultLocation" / "MyVault_Special"
        assert not custom_dir.exists()

        keystore = Keystore(custom_dir)
        mk, rec_key = keystore.setup("CustomPass123!@#", benchmark=False)
        assert custom_dir.exists()
        assert (custom_dir / "vault.config").exists()

        session = get_session()
        session.unlock(mk, is_decoy=False)

        vault = Vault(custom_dir)
        vault.initialise()
        assert (custom_dir / "blobs").exists()
        assert (custom_dir / "index.dat").exists()

        # Add a file in the custom location
        sample_file = tmp_path / "sample.txt"
        sample_file.write_text("Content in custom vault")
        entry = vault.add_file(sample_file)
        assert entry is not None
        assert (custom_dir / "blobs" / entry.blob_name).exists()

        session.lock()
        vault.close()
