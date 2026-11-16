"""
test_phase2_format_v2.py — Regression and property-based tests for File Format v2.

Tests:
  - Header v2 authentication (header in AAD)
  - Final chunk flag in AAD
  - Plaintext length in authenticated trailer
  - Fail if final chunk missing, data after final, chunk_len > chunk_size+16
  - Truncation test: 3-chunk file cut to 2 chunks + fixed header MUST fail
  - v1 backward-compatibility reading
  - Cancellation via threading.Event
  - Hypothesis property-based testing
"""

from __future__ import annotations

import secrets
import struct
import threading

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mylocker.app.core.chunked_io import (
    CHUNK_SIZE,
    HEADER_V2_LEN,
    MAGIC,
    VERSION_V1,
    ChunkError,
    OperationCancelledError,
    decrypt_file,
    encrypt_file,
    generate_file_id,
    verify_file_integrity,
)
from mylocker.app.core.crypto import (
    CryptoError,
    encrypt_aes_gcm,
    generate_key,
    zero_bytes,
)
from mylocker.app.core.kdf import derive_file_key


@pytest.fixture
def master_key():
    mk = bytearray(generate_key())
    yield mk
    zero_bytes(mk)


class TestFormatV2Security:
    """Rigorous security testing for Format v2 chunk authentication."""

    def test_v2_roundtrip_various_sizes(self, tmp_path, master_key):
        """Test roundtrip for empty, small, exact boundary, and multi-chunk files."""
        sizes = [0, 1, 1024, CHUNK_SIZE, CHUNK_SIZE + 1, CHUNK_SIZE * 2 + 500]
        for size in sizes:
            src = tmp_path / f"src_{size}.bin"
            enc = tmp_path / f"enc_{size}.bin"
            dst = tmp_path / f"dst_{size}.bin"

            content = secrets.token_bytes(size)
            src.write_bytes(content)

            fid = encrypt_file(src, enc, master_key)
            assert len(fid) == 16

            decrypt_file(enc, dst, master_key, expected_file_id=fid)
            assert dst.read_bytes() == content

            # Also verify integrity
            ok, msg = verify_file_integrity(enc, master_key)
            assert ok is True

    def test_truncation_3_chunks_cut_to_2_with_fixed_header_fails(self, tmp_path, master_key):
        """CRITICAL AUDIT ITEM 17:
        A 3-chunk file cut to 2 chunks with header TOTAL_CHUNKS altered to 2 MUST fail.
        In v1 this would silently succeed; in v2 it MUST fail due to header AAD & finality flag.
        """
        # Create a 3-chunk file
        data = secrets.token_bytes(CHUNK_SIZE * 2 + 1024)
        src = tmp_path / "src_3chunks.bin"
        enc = tmp_path / "enc_3chunks.bin"
        dst = tmp_path / "dst_truncated.bin"

        src.write_bytes(data)
        file_id = encrypt_file(src, enc, master_key)

        raw = bytearray(enc.read_bytes())

        # Inspect header to find chunk offsets
        # Header v2: 8B magic + 1B ver + 16B fid + 8B total_chunks + 8B orig_size = 41 bytes
        header_len = HEADER_V2_LEN

        # Read first chunk length
        chunk0_len = struct.unpack("<I", raw[header_len + 12 : header_len + 16])[0]
        chunk0_total = 12 + 4 + chunk0_len

        # Read second chunk length
        pos_chunk1 = header_len + chunk0_total
        chunk1_len = struct.unpack("<I", raw[pos_chunk1 + 12 : pos_chunk1 + 16])[0]
        chunk1_total = 12 + 4 + chunk1_len

        # Cut off chunk 2 and the trailer completely
        cut_len = header_len + chunk0_total + chunk1_total
        cut_raw = raw[:cut_len]

        # Modify header TOTAL_CHUNKS from 3 to 2
        # Offset for TOTAL_CHUNKS in v2 header: 8 (magic) + 1 (ver) + 16 (fid) = 25
        struct.pack_into("<Q", cut_raw, 25, 2)

        tampered_enc = tmp_path / "tampered_cut.bin"
        tampered_enc.write_bytes(bytes(cut_raw))

        # Decryption MUST fail (GCM authentication failure on chunk 0 due to header AAD mismatch)
        with pytest.raises((ChunkError, CryptoError)):
            decrypt_file(tampered_enc, dst, master_key, expected_file_id=file_id)

    def test_header_tampering_invalidates_chunks(self, tmp_path, master_key):
        """Tampering with any header byte (e.g. original size) invalidates chunk AAD."""
        src = tmp_path / "src.bin"
        enc = tmp_path / "enc.bin"
        dst = tmp_path / "dst.bin"

        src.write_bytes(b"Sensitive financial or personal data" * 100)
        file_id = encrypt_file(src, enc, master_key)

        raw = bytearray(enc.read_bytes())
        # Flip a bit in the orig_size field (last 8 bytes of 41-byte header)
        raw[38] ^= 0x01
        enc.write_bytes(bytes(raw))

        with pytest.raises((ChunkError, CryptoError)):
            decrypt_file(enc, dst, master_key, expected_file_id=file_id)

    def test_missing_trailer_fails(self, tmp_path, master_key):
        """Removing the trailer from a v2 file causes decryption to fail."""
        src = tmp_path / "src_trailer.bin"
        enc = tmp_path / "enc_trailer.bin"
        dst = tmp_path / "dst_trailer.bin"

        src.write_bytes(b"Testing trailer detection" * 50)
        encrypt_file(src, enc, master_key)

        # Strip the last 32 bytes (trailer)
        raw = enc.read_bytes()
        enc.write_bytes(raw[:-32])

        with pytest.raises((ChunkError, CryptoError)):
            decrypt_file(enc, dst, master_key)

    def test_extraneous_trailing_data_fails(self, tmp_path, master_key):
        """Appending data after the authenticated trailer fails."""
        src = tmp_path / "src_trail.bin"
        enc = tmp_path / "enc_trail.bin"
        dst = tmp_path / "dst_trail.bin"

        src.write_bytes(b"Clean file" * 10)
        encrypt_file(src, enc, master_key)

        # Append garbage
        raw = enc.read_bytes()
        enc.write_bytes(raw + b"MALICIOUS_TRAILING_DATA")

        with pytest.raises(ChunkError, match="Extraneous data"):
            decrypt_file(enc, dst, master_key)

    def test_chunk_len_exceeding_max_fails(self, tmp_path, master_key):
        """Chunk claiming len > CHUNK_SIZE + 16 is immediately rejected."""
        src = tmp_path / "src_len.bin"
        enc = tmp_path / "enc_len.bin"
        dst = tmp_path / "dst_len.bin"

        src.write_bytes(b"Short payload")
        encrypt_file(src, enc, master_key)

        raw = bytearray(enc.read_bytes())
        # Offset 41 is nonce (12B), offset 53 is chunk_len (4B)
        # Set chunk_len to 50 MiB (far exceeding CHUNK_SIZE + 16)
        struct.pack_into("<I", raw, 53, 50 * 1024 * 1024)
        enc.write_bytes(bytes(raw))

        with pytest.raises(ChunkError, match="exceeds max size"):
            decrypt_file(enc, dst, master_key)

    def test_v1_backward_compatibility(self, tmp_path, master_key):
        """Verify that files written in legacy v1 format can still be decrypted."""
        src = tmp_path / "legacy_src.bin"
        enc = tmp_path / "legacy_v1.bin"
        dst = tmp_path / "legacy_dst.bin"

        content = b"Legacy v1 file content created before upgrade!" * 20
        src.write_bytes(content)

        file_id = generate_file_id()
        file_key = derive_file_key(master_key, file_id)

        # Manually construct a v1 encrypted file
        with open(enc, "wb") as fh:
            fh.write(MAGIC)
            fh.write(VERSION_V1)
            fh.write(file_id)
            fh.write(struct.pack("<Q", 1))  # 1 chunk

            ad_v1 = b"chunk" + file_id + struct.pack("<Q", 0)
            nonce, ct = encrypt_aes_gcm(file_key, content, ad_v1)
            fh.write(nonce)
            fh.write(struct.pack("<I", len(ct)))
            fh.write(ct)

        zero_bytes(file_key)

        # Reader must transparently read and decrypt v1
        decrypt_file(enc, dst, master_key, expected_file_id=file_id)
        assert dst.read_bytes() == content

    def test_cancellation_cleans_partial_file(self, tmp_path, master_key):
        """Cancelling an operation via threading.Event unlinks destination part file."""
        src = tmp_path / "large_src.bin"
        dst = tmp_path / "cancelled_dst.bin"
        src.write_bytes(b"A" * (CHUNK_SIZE * 2))

        cancel_ev = threading.Event()
        cancel_ev.set()  # pre-cancelled

        with pytest.raises(OperationCancelledError):
            encrypt_file(src, dst, master_key, cancel_event=cancel_ev)

        assert not dst.exists()


class TestPropertyBasedChunkedIO:
    """Property-based tests with Hypothesis to fuzz chunk boundaries and random inputs."""

    @settings(max_examples=25, deadline=5000)
    @given(st.binary(min_size=0, max_size=100000))
    def test_roundtrip_hypothesis(self, tmp_path_factory, data):
        tmp = tmp_path_factory.mktemp("hypo")
        src = tmp / "h_src.bin"
        enc = tmp / "h_enc.bin"
        dst = tmp / "h_dst.bin"

        mk = bytearray(generate_key())
        try:
            src.write_bytes(data)
            fid = encrypt_file(src, enc, mk)
            decrypt_file(enc, dst, mk, expected_file_id=fid)
            assert dst.read_bytes() == data
        finally:
            zero_bytes(mk)
