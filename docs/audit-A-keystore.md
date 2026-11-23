# Audit Report A: `keystore.py` Security & Architecture Audit

**Target File**: `mylocker/app/core/keystore.py`  
**Related Files**: `mylocker/app/core/crypto.py`, `mylocker/app/core/kdf.py`  
**Auditor**: Antigravity (Phase 3 Audit Task A)  
**Date**: November 23, 2026  
**Status**: Completed — Read-Only Audit  

---

## 1. Executive Summary
`keystore.py` implements the root cryptographic trust anchor for Personal Vault. It manages Master Key (MK) generation, wrapping, uniform key slot storage (`vault.config`), credential change/reset verification, and plausible deniability for the decoy vault.

The implementation exhibits rigorous cryptographic discipline:
- Key encryption keys (KEKs) are derived using **Argon2id** with memory-hard parameters and optional hardware-calibrated benchmarking.
- Wrapped key material uses **AES-256-GCM** authenticated encryption with 96-bit unique nonces.
- Uniform 4-slot structure ensures that an observer examining `vault.config` cannot distinguish whether a decoy vault exists or if unused slots contain random noise.
- Atomic commit semantics (`temp file -> flush -> fsync -> rename`) protect configuration state from power-failure truncation.

Several minor memory-hygiene improvements were identified (notably ensuring `zero_bytes(mk)` is always protected by a `finally` block in `change_password`).

---

## 2. Detailed Audit Findings

### 2.1 Uniform 4-Slot Architecture & Plausible Deniability
- **Design**: The configuration adheres to a 4-slot uniform structure (`SLOT_PRIMARY=0`, `SLOT_RECOVERY=1`, `SLOT_DECOY=2`, `SLOT_SPARE=3`).
- **Plausible Deniability Verification**:
  - Inactive slots are populated with `_random_slot_entry()`.
  - Every slot entry contains identical JSON keys: `slot_id`, `salt` (16 bytes base64), `nonce` (12 bytes base64), and `ciphertext` (exactly 48 bytes base64).
  - 48 bytes ciphertext corresponds precisely to a 32-byte wrapped Master Key + 16-byte GCM authentication tag. Unused slots are filled with 48 bytes of cryptographically secure random bytes from `secrets.token_bytes()`.
  - An offline attacker cannot determine through static or statistical analysis whether Slot 2 hosts a legitimate Decoy Vault or pseudo-random noise.
- **Legacy Backwards Compatibility**:
  - The top-level JSON includes legacy aliases (`wrapped_mk`, `wrapped_mk_recovery`) for v1 test backwards compatibility.
  - Critically, `wrapped_mk_decoy` is **omitted** from top-level JSON when creating v2 vaults, preventing any leak of decoy existence.

### 2.2 Key Wrapping, Cryptographic Integrity & Proving Pattern
- **Proving Before Committing**:
  - `change_password()`, `reset_password_with_recovery_key()`, and `optimize_unlock_speed()` follow the "prove before replace" pattern:
    1. Derive candidate KEK with fresh salt.
    2. Wrap Master Key.
    3. Perform immediate unwrap trial and verify with `constant_time_compare(bytes(proven_mk), bytes(mk))`.
    4. Only upon 100% cryptographic verification is `vault.config` atomically overwritten.
- **Nonce & Salt Uniqueness**:
  - Every slot update and re-wrap generates a fresh 16-byte salt and a fresh 12-byte nonce via `secrets.token_bytes()`. Nonce reuse is completely prevented.

### 2.3 Memory Safety & Master Key Hygiene
- **Zeroing Review**:
  - `kek` bytearrays are explicitly zeroed via `zero_bytes()` in `finally:` clauses across `unlock_with_password()`, `unlock_with_recovery_key()`, and `unlock_decoy()`.
  - `proven_mk` bytearrays in verification checks are zeroed in `finally:` blocks.
- **Finding (Low Risk / Hygiene)**:
  - In `change_password()` (lines 418–452):
    ```python
    mk = self.unlock_with_password(old_password, old_keyfile_path)
    ...
    zero_bytes(mk) # Line 450
    ```
    If an unexpected exception is raised during `derive_kek()` or in step 3/4 before line 450, `mk` is not protected in a `try ... finally` block, potentially leaving the bytearray unzeroed until Python GC reclaims it.
    *Recommendation for Phase 4/6*: Enclose `change_password` body in `try: ... finally: zero_bytes(mk)` exactly as already done in `optimize_unlock_speed()`.

### 2.4 Recovery Key Mathematics & Parsing
- **Format**:
  - Generated via `secrets.token_bytes(32)`.
  - Base32-encoded without padding, formatted into hyphen-delimited 8-character blocks (`_format_recovery_key`).
- **Parsing Robustness**:
  - `_parse_recovery_key` strips hyphens and whitespace, forces uppercase, recalculates required RFC 4648 padding (`=`), and catches decoding errors fail-closed, raising `CryptoError("Authentication failed.")`.
- **Finding (Informational)**:
  - Standard Base32 does not embed an error-correcting or CRC checksum within the recovery key string itself. If a user typos a character, decoding either fails cleanly with `CryptoError` or produces an invalid KEK that fails AES-GCM tag verification. There is zero risk of data corruption, but a future enhancement could consider Bech32 or Crockford-Base32 with embedded checksum for instantaneous typo highlighting.

### 2.5 Emergency Self-Destruct
- **Implementation**:
  - Overwrites all slot dictionary entries in memory with random bytes.
  - Strips decoy and metadata fields.
  - Opens `vault.config` on disk and performs a 3-pass overwrite with random bytes, flushing and invoking `os.fsync()` after each pass before closing.
- **Verification**:
  - Self-destruct permanently renders all key slots cryptographically unrecoverable even if the file is subsequently extracted from disk.

---

## 3. Audit Scorecard

| Checkpoint | Status | Assessment |
| :--- | :---: | :--- |
| **Uniform Key Slots** | Pass | 4 identical slots, 48-byte fixed ciphertext, decoy indistinguishable |
| **AES-256-GCM Wrapping** | Pass | 96-bit CSPRNG nonces, 128-bit authentication tags, fail-closed unwrap |
| **Atomic File Operations** | Pass | Writes to `.tmp`, `flush()`, `os.fsync()`, atomic replace, clean unlink on error |
| **Prove-Before-Commit** | Pass | Roundtrip unwrap verification precedes disk replacement in all rekey paths |
| **Memory Zeroing** | Pass (Minor Note) | KEKs and MK zeroed; recommend wrapping `change_password` in `try...finally` |
| **Decoy Isolation** | Pass | Decoy password unwraps slot 2; no plaintext decoy flags or metadata leaked |
| **Self-Destruct** | Pass | 3-pass CSPRNG disk overwrite with `fsync`, memory zeroing |

---

## 4. Conclusion
`keystore.py` is structurally sound, cryptographically compliant with OWASP and NIST recommendations, and safe for production desktop use. No breaking changes or algorithmic modifications are necessary.
