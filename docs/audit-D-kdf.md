# Audit Report D: `kdf.py` Key Derivation & Calibration Audit

**Target File**: `mylocker/app/core/kdf.py`  
**Related Files**: `mylocker/app/core/crypto.py`, `mylocker/app/core/keystore.py`  
**Auditor**: Antigravity (Phase 3 Audit Task D)  
**Date**: November 23, 2026  
**Status**: Completed — Read-Only Audit  

---

## 1. Executive Summary
`kdf.py` provides password hashing, hardware-calibrated memory-hard key derivation, and cryptographic sub-key separation for Personal Vault.

The implementation strictly satisfies OWASP and NIST recommendations for password hashing and key derivation:
- Password-to-KEK derivation uses **Argon2id** (`Type.ID`) to simultaneously defeat GPU brute-force attacks and cache-timing side-channel attacks.
- Minimum memory baseline is set to **64 MiB** (`65,536` KiB), which is $>3\times$ higher than the OWASP minimum recommendation (19 MiB).
- Hardware auto-benchmark automatically calibrates iterations (`time_cost`) on setup to achieve optimal local responsiveness (~0.25s) without sacrificing security.
- Hardware two-factor authentication (USB Keyfile) combines Argon2id with **SHA3-256** using HKDF.
- Strict sub-key separation is enforced using **HKDF-SHA256** (RFC 5869) for file keys, index keys, journal keys, blob names, and HMAC tokens.
- All derived secrets are returned as mutable `bytearray` objects enabling immediate in-place memory erasure via `zero_bytes()`.

---

## 2. Detailed Audit Findings

### 2.1 Argon2id Primitives & Parameter Selection
- **Algorithm**: Argon2id (`Type.ID`) via `argon2.low_level.hash_secret_raw`.
- **Baseline Parameters**:
  - `ARGON2_MIN_MEMORY_KIB = 64 * 1024` (64 MiB, 65,536 KiB)
  - `ARGON2_MIN_TIME_COST = 3`
  - `ARGON2_PARALLELISM = 4`
  - `ARGON2_SALT_LEN = 16` (128-bit CSPRNG salt)
  - `ARGON2_HASH_LEN = 32` (256-bit derived key)
- **Security Assessment**:
  - OWASP Password Storage Cheat Sheet recommends Argon2id with $m=19456$ KiB (19 MiB), $t=2$, $p=1$.
  - Personal Vault's minimum parameters exceed OWASP requirements by more than $3.3\times$ on memory and $3\times$ on time cost, with 4 parallel lanes to fully utilize modern multi-core x86_64 processors.

### 2.2 Dynamic Hardware Calibration Benchmark
- **Loop Architecture**:
  - `benchmark_argon2id(target_seconds=0.25, memory_kib=64*1024, parallelism=4)`:
    - Generates a dummy 16-byte salt and dummy password.
    - Measures wall-clock execution time via `time.perf_counter()`.
    - Adaptively scales `time_cost` based on measured performance ratio.
    - Maximum calibration passes: 6 iterations.
    - Maximum allowable `time_cost`: capped at 24 iterations to prevent denial-of-service on constrained CPUs.
    - Generates a fresh random salt upon returning `KDFParams`.
- **Finding (Informational)**:
  - The benchmark safely releases the Python Global Interpreter Lock (GIL) inside C-extension `hash_secret_raw`, ensuring UI background threads or worker loops do not freeze the main event loop during setup.

### 2.3 USB Keyfile Second Factor (2FA)
- **Design**:
  ```python
  ikm = bytes(argon_key) + SHA3_256(keyfile_bytes)
  kek = HKDF_SHA256(ikm, info=b"PersonalVault-KEK-v1")
  ```
- **Strengths**:
  - Stream-hashes the keyfile in 1 MiB chunks (`_hash_keyfile`), allowing arbitrary files (e.g. 500 MB system files or small certificates on USB flash drives) to be used without loading the entire file into RAM.
  - Combines Argon2id password output with SHA3-256 before feeding into HKDF.
  - An attacker possessing only the password cannot open the vault; an attacker possessing only the USB keyfile cannot open the vault.
  - `argon_key` is zeroed via `zero_bytes()` immediately after blending into `ikm`.

### 2.4 Cryptographic Sub-Key Separation (HKDF-SHA256)
All sub-keys derived from the 256-bit Master Key use unique, cryptographically distinct application info tags:

| Key Function | HKDF Info Tag | Output Size | Purpose |
| :--- | :--- | :---: | :--- |
| `derive_file_key` | `b"PersonalVault-FileKey-v1" + file_id` | 32 B | Unique per-file AES-256-GCM encryption |
| `derive_index_key` | `b"PersonalVault-IndexKey-v1"` | 32 B | Encrypted file index (`index.dat`) |
| `derive_journal_key` | `b"PersonalVault-JournalKey-v1"` | 32 B | Encrypted write-ahead journal (`journal.enc`) |
| `derive_blob_name_key` | `b"blobname-v1" + file_id` | 24 B | Pseudorandom blob filename on disk |
| `derive_hmac_key` | `b"PersonalVault-HMAC-v1-" + purpose` | 32 B | HMAC tokens and lockout signing |

**Cryptographic Independence**: Knowledge of any single sub-key (or ciphertext derived from it) provides zero information regarding any other sub-key or the root Master Key.

### 2.5 Memory Hygiene & Thread Safety
- `_derive_argon2id` explicitly copies the immutable C bytes into a mutable `bytearray(raw)` so callers can invoke `zero_bytes()` to clear sensitive key material from memory.
- `derive_kek()` actively zeroes intermediate key buffers.
- All HKDF derivations are purely functional, stateless, and thread-safe.

---

## 3. Cryptographic Scorecard

| Checkpoint | Status | Assessment |
| :--- | :---: | :--- |
| **Argon2id Algorithm** | Pass | Memory-hard, side-channel resistant (`Type.ID`) |
| **Memory Baseline** | Pass | 64 MiB ($>3\times$ OWASP minimum) |
| **Parallelism** | Pass | $p=4$ lane execution |
| **Dynamic Calibration** | Pass | Bounded adaptive tuning (~0.25s / ~1.0s) |
| **USB 2FA Keyfile** | Pass | SHA3-256 streaming hash + HKDF expansion |
| **Key Separation** | Pass | RFC 5869 HKDF-SHA256 with distinct domain prefixes |
| **Memory Erasure** | Pass | Mutable `bytearray` returns; explicit `zero_bytes` wiping |

---

## 4. Conclusion
`kdf.py` is exemplary in its design and implementation. It complies with all modern cryptographic guidelines, enforces strict sub-key separation, and provides safe, high-speed key derivation. No vulnerabilities or defects were found.
