# Audit Report B: `chunked_io.py` Cryptographic & Streaming Audit

**Target File**: `mylocker/app/core/chunked_io.py`  
**Related Files**: `mylocker/app/core/crypto.py`, `mylocker/app/core/kdf.py`  
**Auditor**: Antigravity (Phase 3 Audit Task B)  
**Date**: November 23, 2026  
**Status**: Completed — Read-Only Audit  

---

## 1. Executive Summary
`chunked_io.py` is the streaming cryptographic engine of Personal Vault. It transforms arbitrarily large files and media streams into authenticated ciphertext blobs while enforcing a strictly bounded memory footprint ($\le 4$ MiB per chunk).

The implementation strictly satisfies modern cryptographic AEAD streaming requirements:
- Uses **AES-256-GCM** with 96-bit CSPRNG nonces per chunk.
- Format V2 cryptographically binds the complete 41-byte header, the 64-bit chunk index, and an explicit finality flag (`is_final`) into the Associated Authenticated Data (AAD) of every chunk.
- Includes an authenticated trailing block (`PVTR`) verifying the unencrypted payload length and asserting unambiguous EOF.
- Defends against chunk reordering, truncation, extension, inter-file splicing, and header manipulation.
- Implements crash-safe atomic write patterns (`.part -> fsync -> rename`) and cooperative thread cancellation.

---

## 2. Detailed Audit Findings

### 2.1 Format V2 Framing & Envelope Specification
- **Header Structure (41 Bytes)**:
  ```
  [0..7]   MAGIC (8B): "PVAULT01"
  [8]      VERSION (1B): 0x02
  [9..24]  FILE_ID (16B): CSPRNG random bytes
  [25..32] TOTAL_CHUNKS (8B): uint64 LE
  [33..40] ORIG_SIZE (8B): uint64 LE
  ```
- **Chunk Structure**:
  ```
  [NONCE (12B)][CHUNK_LEN (4B uint32 LE)][CIPHERTEXT (variable)][TAG (16B)]
  ```
- **Trailer Structure (44 Bytes)**:
  ```
  [MAGIC (4B): "PVTR"][NONCE (12B)][CIPHERTEXT (8B: uint64 orig_size) + TAG (16B)]
  ```

### 2.2 Associated Authenticated Data (AAD) Binding & Attack Resilience
- **Construction**:
  ```python
  ad = b"chunk-v2" + header_bytes + struct.pack("<QB", chunk_index, 1 if is_final else 0)
  ```
- **Attack Vector Analysis**:
  - **Chunk Reordering**: If an adversary swaps Chunk 0 and Chunk 1, decryption fails because `chunk_index` in AAD does not match the expected generator index.
  - **Inter-File Chunk Splicing**: If an adversary replaces a chunk in File A with a chunk from File B, decryption fails because `header_bytes` (including `file_id`) is cryptographically bound into the tag.
  - **File Truncation**:
    1. If the last chunk is removed, the generator detects that `total_chunks` was not reached.
    2. If an intermediate chunk is dropped, the `is_final` flag or sequence index fails.
    3. The authenticated trailer (`PVTR`) requires a valid GCM tag over `trailer_ad = b"trailer-v2" + header_bytes`, guaranteeing that no truncated file can decrypt as valid.
  - **File Extension / Garbage Injection**:
    - Line 299 inspects `src.read(1)` immediately after trailer validation; any trailing bytes raise `ChunkError("Extraneous data detected after final chunk.")`.
  - **Header Tampering**:
    - Changing `orig_size` or `total_chunks` in the plaintext header invalidates every chunk's tag since `header_bytes` is bound in the AAD.

### 2.3 Key Derivation & Nonce Safety
- **Per-File Key Separation**:
  - `file_key = derive_file_key(master_key, file_id)` uses `HKDF-SHA256` with `salt=file_id` and `info=b"PersonalVault-FileKey-v1"`.
  - Compromise of one file's key does not compromise other files or the Master Key.
- **Nonce Space & Collision Limits**:
  - Each chunk generates a fresh 12-byte (96-bit) nonce via `secrets.token_bytes(12)`.
  - In a massive 100 GiB file ($25,000$ chunks), the probability of collision is $p \approx N^2 / (2 \cdot 2^{96}) \approx 3.9 \times 10^{-21}$, which is practically zero.
- **Key Zeroing**:
  - `file_key` is allocated as a `bytearray` and zeroed in `finally:` blocks in `encrypt_file()` and `decrypt_file_streaming()`.

### 2.4 Edge Cases & Robustness
- **Zero-Byte Files**:
  - `encrypt_file` on an empty file writes the 41-byte header (`total_chunks=0`, `orig_size=0`) and the 44-byte authenticated trailer.
  - `decrypt_file_streaming` verifies the trailer and returns an empty iterator cleanly.
- **Exact Chunk Boundaries**:
  - Files with sizes that are exact multiples of 4 MiB (e.g. 4 MiB, 8 MiB, 16 MiB) correctly calculate `total_chunks = (size + CHUNK_SIZE - 1) // CHUNK_SIZE`. The final chunk contains exactly 4 MiB plaintext with `is_final=True`.
- **Malicious Chunk Length**:
  - Line 274 enforces `chunk_len <= CHUNK_SIZE + TAG_SIZE`. This prevents an attacker from forging a `chunk_len = 0xFFFFFFFF` in the header to induce an out-of-memory crash.

### 2.5 Crash-Safety & Cancellation
- **Atomic Destination**:
  - `decrypt_file()` writes to a temporary file: `dest_path.with_name(f"{dest_path.name}.{secrets.token_hex(4)}.part")`.
  - Calls `dst.flush()` and `os.fsync(dst.fileno())` prior to atomic `replace()`.
  - Unlinks the `.part` file on any failure or cancellation.
- **Cancellation**:
  - Inspects `cancel_event.is_set()` before processing every chunk.
  - Raises `OperationCancelledError` immediately, halting disk I/O and releasing file handles.

---

## 3. Cryptographic Scorecard

| Checkpoint | Status | Assessment |
| :--- | :---: | :--- |
| **AES-256-GCM AEAD** | Pass | Full authenticated streaming; 128-bit authentication tags |
| **AAD Context Binding** | Pass | Header (41B), chunk index (uint64), finality flag (uint8) bound into tag |
| **Nonce Hygiene** | Pass | 96-bit CSPRNG nonces per chunk; collision probability $< 10^{-20}$ |
| **Anti-Truncation Trailer** | Pass | Encrypted trailer authenticates original file length and asserts EOF |
| **Zero-Byte Support** | Pass | Handled with full authenticated header and trailer verification |
| **Memory Bounding** | Pass | 4 MiB streaming buffer; in-memory decryption capped at 100 MiB |
| **Crash Safety** | Pass | `.part` staging, `fsync()`, atomic replace, fail-closed deletion |
| **Thread Cancellation** | Pass | Cooperative cancellation via `threading.Event` |

---

## 4. Conclusion
`chunked_io.py` provides state-of-the-art AEAD streaming security. It completely prevents truncation, extension, swapping, and header-tampering attacks. No architectural or security flaws were discovered.
