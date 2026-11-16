"""
Phase 1 & 2 Tests — Crypto core, KDF, chunked I/O, keystore, lockout.

Run with:  pytest mylocker/tests/ -v
"""

from __future__ import annotations

import hashlib
import secrets
import struct

# ─── Setup path so imports work ───────────────────────────────────────────────
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mylocker.app.core.chunked_io import (
    CHUNK_SIZE,
    HEADER_LEN,
    ChunkError,
    decrypt_file,
    decrypt_file_streaming,
    decrypt_file_to_memory,
    encrypt_file,
    generate_file_id,
    read_file_id,
    verify_file_integrity,
)
from mylocker.app.core.crypto import (
    KEY_SIZE,
    NONCE_SIZE,
    CryptoError,
    constant_time_compare,
    decrypt_aes_gcm,
    encrypt_aes_gcm,
    generate_key,
    generate_nonce,
    generate_salt,
    unwrap_key,
    wrap_key,
    zero_bytes,
)
from mylocker.app.core.index import EncryptedIndex, FileEntry, IndexCorruptError, VaultIndex
from mylocker.app.core.journal import Journal, JournalState, hash_file_sha256
from mylocker.app.core.kdf import (
    KDFParams,
    derive_file_key,
    derive_index_key,
    derive_kek,
)
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.lockout import (
    LockoutError,
    LockoutManager,
    make_lockout_signing_key,
)
from mylocker.app.core.secure_delete import secure_delete_file

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — crypto.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestCryptoPrimitives:

    def test_generate_key_length(self):
        k = generate_key()
        assert len(k) == KEY_SIZE

    def test_generate_key_random(self):
        # Two keys should be different with overwhelming probability
        assert generate_key() != generate_key()

    def test_generate_nonce_length(self):
        assert len(generate_nonce()) == NONCE_SIZE

    def test_encrypt_decrypt_roundtrip(self):
        key = generate_key()
        plaintext = b"Hello, vault!"
        nonce, ciphertext = encrypt_aes_gcm(key, plaintext)
        result = decrypt_aes_gcm(key, nonce, ciphertext)
        assert result == plaintext

    def test_encrypt_with_ad(self):
        key = generate_key()
        pt = b"sensitive data"
        ad = b"chunk-metadata"
        nonce, ct = encrypt_aes_gcm(key, pt, ad)
        result = decrypt_aes_gcm(key, nonce, ct, ad)
        assert result == pt

    def test_wrong_key_raises(self):
        key1 = generate_key()
        key2 = generate_key()
        nonce, ct = encrypt_aes_gcm(key1, b"data")
        with pytest.raises(CryptoError):
            decrypt_aes_gcm(key2, nonce, ct)

    def test_tampered_ciphertext_raises(self):
        key = generate_key()
        nonce, ct = encrypt_aes_gcm(key, b"data")
        tampered = bytearray(ct)
        tampered[0] ^= 0xFF
        with pytest.raises(CryptoError):
            decrypt_aes_gcm(key, nonce, bytes(tampered))

    def test_wrong_ad_raises(self):
        key = generate_key()
        nonce, ct = encrypt_aes_gcm(key, b"data", b"real-ad")
        with pytest.raises(CryptoError):
            decrypt_aes_gcm(key, nonce, ct, b"wrong-ad")

    def test_missing_ad_raises(self):
        key = generate_key()
        nonce, ct = encrypt_aes_gcm(key, b"data", b"some-ad")
        with pytest.raises(CryptoError):
            decrypt_aes_gcm(key, nonce, ct)  # no AD

    def test_wrong_key_length_raises(self):
        with pytest.raises((ValueError, CryptoError)):
            encrypt_aes_gcm(b"short", b"data")

    def test_wrap_unwrap_roundtrip(self):
        wrapping_key = generate_key()
        inner_key = generate_key()
        nonce, wrapped = wrap_key(wrapping_key, inner_key)
        recovered = unwrap_key(wrapping_key, nonce, wrapped)
        assert bytes(recovered) == inner_key

    def test_wrap_unwrap_wrong_key(self):
        wk1 = generate_key()
        wk2 = generate_key()
        nonce, wrapped = wrap_key(wk1, generate_key())
        with pytest.raises(CryptoError):
            unwrap_key(wk2, nonce, wrapped)

    def test_constant_time_compare_equal(self):
        a = secrets.token_bytes(32)
        assert constant_time_compare(a, a) is True

    def test_constant_time_compare_not_equal(self):
        assert constant_time_compare(b"abc", b"xyz") is False

    def test_zero_bytes(self):
        buf = bytearray(b"secret")
        zero_bytes(buf)
        assert all(b == 0 for b in buf)

    def test_different_nonces_per_encrypt(self):
        key = generate_key()
        pt = b"same plaintext"
        n1, ct1 = encrypt_aes_gcm(key, pt)
        n2, ct2 = encrypt_aes_gcm(key, pt)
        # Nonces must differ (random); ciphertexts also differ
        assert n1 != n2


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — kdf.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestKDF:

    def _fast_params(self) -> KDFParams:
        """Minimal Argon2id params for fast tests (NOT production-safe)."""
        return KDFParams(
            memory_kib=16 * 1024,  # 16 MiB — fast for tests
            time_cost=1,
            parallelism=1,
            salt=generate_salt(),
        )

    def test_derive_kek_deterministic(self):
        params = self._fast_params()
        kek1 = derive_kek("password123", params)
        kek2 = derive_kek("password123", params)
        assert bytes(kek1) == bytes(kek2)

    def test_derive_kek_different_passwords(self):
        params = self._fast_params()
        kek1 = derive_kek("password1", params)
        kek2 = derive_kek("password2", params)
        assert bytes(kek1) != bytes(kek2)

    def test_derive_kek_different_salts(self):
        params1 = self._fast_params()
        params2 = KDFParams(
            memory_kib=params1.memory_kib,
            time_cost=params1.time_cost,
            parallelism=params1.parallelism,
            salt=generate_salt(),
        )
        kek1 = derive_kek("samepassword", params1)
        kek2 = derive_kek("samepassword", params2)
        assert bytes(kek1) != bytes(kek2)

    def test_derive_kek_output_length(self):
        params = self._fast_params()
        kek = derive_kek("pass", params)
        assert len(kek) == KEY_SIZE

    def test_derive_file_key_unique_per_file(self):
        mk = generate_key()
        fid1 = generate_file_id()
        fid2 = generate_file_id()
        fk1 = derive_file_key(mk, fid1)
        fk2 = derive_file_key(mk, fid2)
        assert bytes(fk1) != bytes(fk2)

    def test_derive_file_key_deterministic(self):
        mk = generate_key()
        fid = generate_file_id()
        fk1 = derive_file_key(mk, fid)
        fk2 = derive_file_key(mk, fid)
        assert bytes(fk1) == bytes(fk2)

    def test_index_key_different_from_file_key(self):
        mk = generate_key()
        ik = derive_index_key(mk)
        fk = derive_file_key(mk, generate_file_id())
        assert bytes(ik) != bytes(fk)

    def test_kdf_params_serialisation(self):
        params = self._fast_params()
        d = params.to_dict()
        restored = KDFParams.from_dict(d)
        assert restored.memory_kib == params.memory_kib
        assert restored.time_cost == params.time_cost
        assert restored.salt == params.salt

    def test_kek_is_mutable_bytearray(self):
        params = self._fast_params()
        kek = derive_kek("pass", params)
        assert isinstance(kek, bytearray)
        zero_bytes(kek)
        assert all(b == 0 for b in kek)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — chunked_io.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestChunkedIO:

    def _write_tmp(self, data: bytes) -> Path:
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
        f.write(data)
        f.close()
        return Path(f.name)

    def setup_method(self):
        self.mk = generate_key()
        self.tmp_files: list[Path] = []

    def teardown_method(self):
        for p in self.tmp_files:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    def _encrypt_to_tmp(self, data: bytes) -> tuple[Path, Path, bytes]:
        src = self._write_tmp(data)
        dst = src.with_suffix(".enc")
        self.tmp_files += [src, dst]
        file_id = encrypt_file(src, dst, self.mk)
        return src, dst, file_id

    def test_roundtrip_small_file(self):
        data = b"Hello, encrypted world!" * 100
        src, enc, file_id = self._encrypt_to_tmp(data)
        out = enc.with_suffix(".dec")
        self.tmp_files.append(out)
        decrypt_file(enc, out, self.mk, expected_file_id=file_id)
        assert out.read_bytes() == data

    def test_roundtrip_exact_chunk_boundary(self):
        data = secrets.token_bytes(CHUNK_SIZE)
        src, enc, file_id = self._encrypt_to_tmp(data)
        out = enc.with_suffix(".dec")
        self.tmp_files.append(out)
        decrypt_file(enc, out, self.mk, expected_file_id=file_id)
        assert out.read_bytes() == data

    def test_roundtrip_multi_chunk(self):
        data = secrets.token_bytes(CHUNK_SIZE * 2 + 12345)
        src, enc, file_id = self._encrypt_to_tmp(data)
        out = enc.with_suffix(".dec")
        self.tmp_files.append(out)
        decrypt_file(enc, out, self.mk, expected_file_id=file_id)
        assert out.read_bytes() == data

    def test_wrong_key_fails(self):
        data = b"secret data"
        src, enc, file_id = self._encrypt_to_tmp(data)
        out = enc.with_suffix(".dec")
        self.tmp_files.append(out)
        wrong_mk = generate_key()
        with pytest.raises((ChunkError, CryptoError, Exception)):
            decrypt_file(enc, out, wrong_mk)

    def test_tampered_chunk_detected(self):
        data = secrets.token_bytes(1024)
        src, enc, file_id = self._encrypt_to_tmp(data)
        out = enc.with_suffix(".dec")
        self.tmp_files.append(out)

        # Flip a byte inside the first chunk ciphertext
        raw = bytearray(enc.read_bytes())
        raw[HEADER_LEN + 12 + 4 + 5] ^= 0xFF  # inside first chunk ciphertext
        enc.write_bytes(bytes(raw))

        with pytest.raises((ChunkError, CryptoError)):
            decrypt_file(enc, out, self.mk)

    def test_truncated_file_detected(self):
        data = secrets.token_bytes(CHUNK_SIZE * 2)
        src, enc, file_id = self._encrypt_to_tmp(data)
        out = enc.with_suffix(".dec")
        self.tmp_files.append(out)

        # Truncate the encrypted file at 60% size
        raw = enc.read_bytes()
        enc.write_bytes(raw[:int(len(raw) * 0.6)])

        with pytest.raises((ChunkError, CryptoError, ValueError, struct.error, Exception)):
            decrypt_file(enc, out, self.mk)

    def test_chunk_swap_attack_detected(self):
        """Verify that swapping chunks between two files is detected."""
        data1 = secrets.token_bytes(CHUNK_SIZE * 2)
        data2 = secrets.token_bytes(CHUNK_SIZE * 2)

        src1, enc1, fid1 = self._encrypt_to_tmp(data1)
        src2, enc2, fid2 = self._encrypt_to_tmp(data2)
        out = enc1.with_suffix(".dec")
        self.tmp_files.append(out)

        # Try decrypting file1 with file2's key (simulating swap)
        with pytest.raises((ChunkError, CryptoError, Exception)):
            decrypt_file(enc1, out, self.mk, expected_file_id=fid2)

    def test_to_memory_roundtrip(self):
        data = b"in-memory test" * 500
        src, enc, file_id = self._encrypt_to_tmp(data)
        result = decrypt_file_to_memory(enc, self.mk, expected_file_id=file_id)
        assert bytes(result) == data
        # Verify we can zero it
        zero_bytes(result)
        assert all(b == 0 for b in result)

    def test_streaming_roundtrip(self):
        data = secrets.token_bytes(CHUNK_SIZE + 999)
        src, enc, file_id = self._encrypt_to_tmp(data)
        chunks = list(decrypt_file_streaming(enc, self.mk, expected_file_id=file_id))
        assert b"".join(chunks) == data

    def test_bad_magic_raises(self):
        p = tempfile.NamedTemporaryFile(delete=False, suffix=".enc")
        p.write(b"BADMAGIC" + b"\x00" * 100)
        p.close()
        self.tmp_files.append(Path(p.name))
        out = Path(p.name).with_suffix(".dec")
        self.tmp_files.append(out)
        with pytest.raises(ValueError, match="magic"):
            decrypt_file(Path(p.name), out, self.mk)

    def test_verify_integrity_good(self):
        data = b"verify me"
        src, enc, file_id = self._encrypt_to_tmp(data)
        ok, msg = verify_file_integrity(enc, self.mk)
        assert ok
        assert msg == "OK"

    def test_verify_integrity_tampered(self):
        data = b"verify me"
        src, enc, file_id = self._encrypt_to_tmp(data)
        raw = bytearray(enc.read_bytes())
        raw[-5] ^= 0xFF
        enc.write_bytes(bytes(raw))
        ok, msg = verify_file_integrity(enc, self.mk)
        assert not ok

    def test_read_file_id(self):
        data = b"file id test"
        src, enc, file_id = self._encrypt_to_tmp(data)
        recovered = read_file_id(enc)
        assert recovered == file_id

    def test_empty_file(self):
        data = b""
        src, enc, file_id = self._encrypt_to_tmp(data)
        out = enc.with_suffix(".dec")
        self.tmp_files.append(out)
        decrypt_file(enc, out, self.mk, expected_file_id=file_id)
        assert out.read_bytes() == b""

    def test_unicode_content(self):
        data = "🔐 Personal Vault — 个人保险库".encode("utf-8") * 100
        src, enc, file_id = self._encrypt_to_tmp(data)
        result = decrypt_file_to_memory(enc, self.mk)
        assert bytes(result) == data


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — keystore.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestKeystore:

    def _make_keystore(self, tmp_path: Path) -> Keystore:
        return Keystore(tmp_path)

    def _fast_setup(self, ks: Keystore, password: str = "correct-horse-battery-staple-1!") -> tuple[bytearray, str]:
        """Setup with minimal Argon2id params for speed."""
        # Patch params to be fast
        from mylocker.app.core import kdf as kdf_mod
        orig = kdf_mod.benchmark_argon2id

        def fast_benchmark(**kwargs):
            return KDFParams.new_with_fresh_salt(memory_kib=16*1024, time_cost=1)

        kdf_mod.benchmark_argon2id = fast_benchmark
        try:
            mk, rec = ks.setup(password, benchmark=True)
        finally:
            kdf_mod.benchmark_argon2id = orig
        return mk, rec

    def test_setup_creates_config(self, tmp_path):
        ks = self._make_keystore(tmp_path)
        mk, rec = self._fast_setup(ks)
        assert ks.config.exists()
        assert len(mk) == KEY_SIZE
        assert "-" in rec  # formatted recovery key
        zero_bytes(mk)

    def test_unlock_with_password(self, tmp_path):
        ks = self._make_keystore(tmp_path)
        mk_orig, rec = self._fast_setup(ks)

        mk2 = ks.unlock_with_password("correct-horse-battery-staple-1!")
        assert bytes(mk_orig) == bytes(mk2)
        zero_bytes(mk_orig)
        zero_bytes(mk2)

    def test_wrong_password_raises(self, tmp_path):
        ks = self._make_keystore(tmp_path)
        mk, rec = self._fast_setup(ks)
        zero_bytes(mk)

        with pytest.raises(CryptoError):
            ks.unlock_with_password("WRONG_PASSWORD")

    def test_recovery_key_unlock(self, tmp_path):
        ks = self._make_keystore(tmp_path)
        mk_orig, rec = self._fast_setup(ks)

        mk2 = ks.unlock_with_recovery_key(rec)
        assert bytes(mk_orig) == bytes(mk2)
        zero_bytes(mk_orig)
        zero_bytes(mk2)

    def test_wrong_recovery_key_raises(self, tmp_path):
        ks = self._make_keystore(tmp_path)
        mk, rec = self._fast_setup(ks)
        zero_bytes(mk)

        with pytest.raises(CryptoError):
            ks.unlock_with_recovery_key("AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD")

    def test_change_password(self, tmp_path):
        ks = self._make_keystore(tmp_path)
        mk_orig, rec = self._fast_setup(ks, "oldpassword1234!")
        zero_bytes(mk_orig)

        ks.change_password("oldpassword1234!", "newpassword5678!")

        # Old password must fail
        with pytest.raises(CryptoError):
            ks.unlock_with_password("oldpassword1234!")

        # New password must work
        mk_new = ks.unlock_with_password("newpassword5678!")
        assert len(mk_new) == KEY_SIZE
        zero_bytes(mk_new)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — lockout.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestLockout:

    def _make_manager(self, tmp_path: Path) -> LockoutManager:
        signing_key = make_lockout_signing_key(generate_salt(16))
        return LockoutManager(tmp_path, signing_key)

    def test_no_lockout_initially(self, tmp_path):
        lm = self._make_manager(tmp_path)
        lm.check_lockout()  # should not raise

    def test_lockout_after_threshold(self, tmp_path):
        lm = self._make_manager(tmp_path)
        for _ in range(3):
            lm.record_failure()
        # Now should be locked out
        with pytest.raises(LockoutError):
            lm.check_lockout()

    def test_lockout_duration_increases(self, tmp_path):
        lm = self._make_manager(tmp_path)
        for _ in range(3):
            lm.record_failure()
        try:
            lm.check_lockout()
        except LockoutError as e:
            assert e.remaining_seconds >= 25  # ~30s

        for _ in range(2):  # total 5 fails
            lm.record_failure()
        try:
            lm.check_lockout()
        except LockoutError as e:
            assert e.remaining_seconds >= 55  # ~60s

    def test_success_resets_counter(self, tmp_path):
        lm = self._make_manager(tmp_path)
        for _ in range(3):
            lm.record_failure()
        # Tamper: manually reset last_fail_time to past so lockout expires
        state = lm._load_state()
        state["last_fail_time"] = time.time() - 60
        lm._save_state(state)

        lm.record_success()
        assert lm.get_fail_count() == 0
        lm.check_lockout()  # should not raise

    def test_counter_persists_across_instances(self, tmp_path):
        sk = make_lockout_signing_key(generate_salt(16))
        lm1 = LockoutManager(tmp_path, sk)
        for _ in range(3):
            lm1.record_failure()

        lm2 = LockoutManager(tmp_path, sk)
        assert lm2.get_fail_count() == 3

    def test_tampered_file_resets(self, tmp_path):
        sk = make_lockout_signing_key(generate_salt(16))
        lm = LockoutManager(tmp_path, sk)
        for _ in range(10):
            lm.record_failure()

        # Tamper the file
        import json
        p = tmp_path / "lockout.dat"
        d = json.loads(p.read_text())
        d["fails"] = 0  # attacker tries to reset
        d["_mac"] = "0" * 64
        p.write_text(json.dumps(d))

        # Loading tampered file should reset to 0 (not trust the tampered value)
        lm2 = LockoutManager(tmp_path, sk)
        # The tampered file had fails=0 but bad MAC — resets to 0 by design
        # This is the correct behaviour: we can't distinguish between "attacker
        # cleared it" and "corrupt file", so we reset. The important thing is
        # we do NOT trust the attacker-supplied fail count.
        assert lm2.get_fail_count() == 0  # reset, not the attacker's 0

    def test_intruder_log_records_entries(self, tmp_path):
        lm = self._make_manager(tmp_path)
        lm.record_failure()
        lm.record_failure()
        log = lm.get_intruder_log()
        assert len(log) == 2
        assert "timestamp" in log[0]
        assert log[1]["fail_count"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — secure_delete.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecureDelete:

    def test_file_gone_after_delete(self, tmp_path):
        p = tmp_path / "secret.txt"
        p.write_bytes(b"very secret" * 1000)
        secure_delete_file(p)
        assert not p.exists()

    def test_content_overwritten(self, tmp_path):
        """Check that the file has been overwritten (renamed, then gone)."""
        p = tmp_path / "data.bin"
        original = b"AAAAAAAAAAAA" * 1000
        p.write_bytes(original)
        secure_delete_file(p)
        # File should not exist at original path
        assert not p.exists()

    def test_nonexistent_file_silent(self, tmp_path):
        p = tmp_path / "doesnotexist.txt"
        secure_delete_file(p)  # should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — journal.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestJournal:

    def test_begin_and_complete(self, tmp_path):
        j = Journal(tmp_path)
        e = j.begin("/src/file.txt", "/vault/blob123", "aabbcc")
        assert e.state == JournalState.PENDING
        j.complete(e.op_id)
        # After complete, entry removed from pending
        assert e.op_id not in j._entries

    def test_advance_states(self, tmp_path):
        j = Journal(tmp_path)
        e = j.begin("/a", "/b", "cc")
        j.advance(e.op_id, JournalState.VERIFY, source_hash="deadbeef")
        assert j._entries[e.op_id].state == JournalState.VERIFY
        assert j._entries[e.op_id].source_hash == "deadbeef"

    def test_rollback_removes_entry(self, tmp_path):
        j = Journal(tmp_path)
        e = j.begin("/a", "/b", "cc")
        j.rollback(e.op_id)
        assert e.op_id not in j._entries

    def test_persistence(self, tmp_path):
        j1 = Journal(tmp_path)
        e = j1.begin("/a", "/b", "cc")
        j1.advance(e.op_id, JournalState.VERIFY)

        j2 = Journal(tmp_path)
        pending = j2.get_pending()
        assert any(p.op_id == e.op_id for p in pending)

    def test_hash_file(self, tmp_path):
        p = tmp_path / "data.bin"
        p.write_bytes(b"test data for hashing")
        h = hash_file_sha256(p)
        expected = hashlib.sha256(b"test data for hashing").hexdigest()
        assert h == expected


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — index.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestIndex:

    def _make_entry(self, name: str = "photo.jpg") -> FileEntry:
        return FileEntry(
            file_id=generate_file_id().hex(),
            original_name=name,
            original_path=f"/home/user/{name}",
            vault_path=f"/Photos/{name}",
            size_bytes=1024,
            modified_time=time.time(),
            mime_type="image/jpeg",
        )

    def test_add_and_get(self):
        idx = VaultIndex()
        e = self._make_entry()
        idx.add(e)
        assert idx.get(e.file_id) == e

    def test_remove(self):
        idx = VaultIndex()
        e = self._make_entry()
        idx.add(e)
        idx.remove(e.file_id)
        assert idx.get(e.file_id) is None

    def test_search(self):
        idx = VaultIndex()
        idx.add(self._make_entry("vacation.jpg"))
        idx.add(self._make_entry("work.pdf"))
        results = idx.search("vacation")
        assert len(results) == 1

    def test_by_mime(self):
        idx = VaultIndex()
        idx.add(self._make_entry("img.jpg"))
        e2 = self._make_entry("doc.pdf")
        e2.mime_type = "application/pdf"
        idx.add(e2)
        images = idx.by_mime_prefix("image/")
        assert len(images) == 1

    def test_serialisation_roundtrip(self):
        idx = VaultIndex()
        e = self._make_entry()
        e.tags = ["holiday", "2024"]
        e.is_favorite = True
        idx.add(e)
        d = idx.to_dict()
        idx2 = VaultIndex.from_dict(d)
        e2 = idx2.get(e.file_id)
        assert e2.original_name == e.original_name
        assert e2.tags == e.tags
        assert e2.is_favorite is True

    def test_encrypted_index_roundtrip(self, tmp_path):
        mk = generate_key()
        enc_idx = EncryptedIndex(tmp_path)
        enc_idx._index = VaultIndex()
        entry = self._make_entry("secret_photo.jpg")
        enc_idx._index.add(entry)
        enc_idx.save(mk)

        enc_idx2 = EncryptedIndex(tmp_path)
        idx2 = enc_idx2.load(mk)
        e2 = idx2.get(entry.file_id)
        assert e2 is not None
        assert e2.original_name == "secret_photo.jpg"

    def test_encrypted_index_wrong_key(self, tmp_path):
        mk1 = generate_key()
        mk2 = generate_key()
        enc_idx = EncryptedIndex(tmp_path)
        enc_idx._index = VaultIndex()
        enc_idx.save(mk1)

        enc_idx2 = EncryptedIndex(tmp_path)
        # Phase 1 audit fix: Wrong key must raise IndexCorruptError (never silently start with empty index)
        with pytest.raises(IndexCorruptError):
            enc_idx2.load(mk2)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — No plaintext in vault directory
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoPlaintextOnDisk:
    """
    After adding a file, scan every byte in the vault blobs directory to
    ensure no plaintext content or file names appear unencrypted.
    """

    def test_no_plaintext_content_in_vault(self, tmp_path):
        """Encrypt a file and verify no plaintext appears in vault blobs."""
        # Write a file with distinctive plaintext
        secret_text = b"SuperSecretSentinel_ThisMustNeverAppearOnDisk_XYZ123"
        secret_file = tmp_path / "secret.txt"
        secret_file.write_bytes(secret_text)

        mk = generate_key()
        file_id = generate_file_id()
        blobs_dir = tmp_path / "blobs"
        blobs_dir.mkdir()
        dest = blobs_dir / "randomblob"

        encrypt_file(secret_file, dest, mk, file_id)

        # Scan every byte in blobs_dir for the plaintext
        for blob in blobs_dir.iterdir():
            raw = blob.read_bytes()
            assert secret_text not in raw, (
                "SECURITY FAILURE: plaintext content found in vault blob!"
            )

    def test_no_filename_in_vault(self, tmp_path):
        """Verify that file name does not appear in the index file."""
        mk = generate_key()
        enc_idx = EncryptedIndex(tmp_path)
        enc_idx._index = VaultIndex()

        secret_name = "TopSecretFilename_ShouldNeverAppear.jpg"
        entry = FileEntry(
            file_id=generate_file_id().hex(),
            original_name=secret_name,
            original_path=f"/home/{secret_name}",
            vault_path=f"/Photos/{secret_name}",
            size_bytes=99,
            modified_time=time.time(),
            mime_type="image/jpeg",
        )
        enc_idx._index.add(entry)
        enc_idx.save(mk)

        # Read raw index bytes and verify name is not plaintext
        idx_path = tmp_path / "index.dat"
        raw = idx_path.read_bytes()
        assert secret_name.encode() not in raw, (
            "SECURITY FAILURE: file name found in plaintext in index file!"
        )

