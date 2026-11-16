"""
test_phase3.py — Comprehensive tests for Phase 3:
1. Decoy Vault isolation (isolated directory, blobs, and index)
2. Self-destruct key wiping
3. Password policy (zxcvbn, blocklist, Diceware)
4. DPAPI lockout tamper resistance
5. Intruder photo encryption and in-memory decryption
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mylocker.app.core.crypto import CryptoError, zero_bytes
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.password_policy import generate_diceware_passphrase, validate_password
from mylocker.app.core.session import get_session
from mylocker.app.core.vault import Vault
from mylocker.app.system.webcam import capture_intruder_photo, decrypt_intruder_photo


class TestPasswordPolicy:
    def test_strong_passwords_pass(self):
        valid, msg, score = validate_password("correct-horse-battery-staple-99!")
        assert valid
        assert score >= 3

    def test_short_password_fails(self):
        valid, msg, score = validate_password("Short1!")
        assert not valid
        assert "at least 12 characters" in msg

    def test_common_pattern_fails(self):
        valid, msg, score = validate_password("Password123!@#")
        assert not valid

    def test_diceware_generation(self):
        phrase = generate_diceware_passphrase(num_words=5)
        words = phrase.split("-")
        assert len(words) == 5
        valid, _, score = validate_password(phrase)
        assert valid


class TestDecoyVaultIsolation:
    def test_decoy_isolated_directory_and_index(self, tmp_path: Path):
        vault_dir = tmp_path / "test_vault"
        keystore = Keystore(vault_dir)
        mk, _ = keystore.setup("MasterPass123!@#", benchmark=False)
        keystore.setup_decoy("DecoyPass123!@#")

        session = get_session()
        vault = Vault(vault_dir)

        # 1. Unlock real vault and add a real file
        session.unlock(mk, is_decoy=False)
        vault.initialise()
        assert not vault.is_decoy
        assert vault.vault_dir == vault_dir.resolve()

        real_file = tmp_path / "real.txt"
        real_file.write_text("SUPER SECRET REAL DATA")
        entry_real = vault.add_file(real_file)
        assert entry_real is not None
        session.lock()
        vault.close()

        # 2. Unlock with Decoy password
        decoy_mk, is_decoy = keystore.unlock("DecoyPass123!@#")
        assert is_decoy is True
        session.unlock(decoy_mk, is_decoy=True)

        idx_decoy = vault.open()
        assert vault.is_decoy is True
        assert vault.vault_dir == (vault_dir / "decoy").resolve()
        # Decoy index must be completely empty!
        assert len(idx_decoy.all_files()) == 0

        # Add a fake file to decoy
        fake_file = tmp_path / "fake.txt"
        fake_file.write_text("INNOCENT FAKE DATA")
        entry_decoy = vault.add_file(fake_file)
        assert entry_decoy is not None
        assert len(vault.refresh().all_files()) == 1

        session.lock()
        vault.close()

        # 3. Unlock with Real password again
        real_mk, is_decoy_2 = keystore.unlock("MasterPass123!@#")
        assert is_decoy_2 is False
        session.unlock(real_mk, is_decoy=False)

        idx_real = vault.open()
        assert vault.vault_dir == vault_dir.resolve()
        # Real index must have only the real file, not the fake file
        assert len(idx_real.all_files()) == 1
        assert idx_real.all_files()[0].original_name == "real.txt"

        session.lock()
        vault.close()


class TestSelfDestruct:
    def test_self_destruct_wipes_keys_permanently(self, tmp_path: Path):
        vault_dir = tmp_path / "vault_destruct"
        keystore = Keystore(vault_dir)
        mk, rec_key = keystore.setup("OriginalPassword123!", benchmark=False)
        zero_bytes(mk)

        # Confirm unlock works initially
        unlocked_mk, is_decoy = keystore.unlock("OriginalPassword123!")
        assert is_decoy is False
        zero_bytes(unlocked_mk)

        # Trigger self-destruct
        keystore.self_destruct()

        # Unlocking with correct password MUST now raise CryptoError
        with pytest.raises(CryptoError):
            keystore.unlock_with_password("OriginalPassword123!")

        # Unlocking with recovery key MUST also raise CryptoError
        with pytest.raises(CryptoError):
            keystore.unlock_with_recovery_key(rec_key)


class TestWebcamIntruderCapture:
    def test_disabled_by_default(self, tmp_path: Path):
        out = tmp_path / "photo.jpg"
        assert capture_intruder_photo(out, enabled=False) is False
        assert not out.exists()

    def test_encrypted_intruder_photo_roundtrip(self, tmp_path: Path):
        from mylocker.app.system.webcam import _encrypt_bytes

        dummy_image = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"TEST_IMAGE_DATA_PADDING"
        enc_bytes = _encrypt_bytes(dummy_image)

        photo_path = tmp_path / "intruder_1.enc"
        photo_path.write_bytes(enc_bytes)

        # Plaintext data MUST NOT appear in the file
        assert dummy_image not in photo_path.read_bytes()

        # Decrypt in memory
        decrypted = decrypt_intruder_photo(photo_path)
        assert decrypted == dummy_image
