# Threat Model: Personal Vault

This document details the architectural threat model for **Personal Vault**, defining attacker personas, assets, attack surfaces, mitigations, and cryptographic specifications.

---

## 1. System Assets

| Asset | Description | Sensitivity | Protection Mechanism |
| :--- | :--- | :--- | :--- |
| **Master Key (MK)** | Random 256-bit symmetric root key | Critical | Wrapped via AES-256-GCM; stored in `vault.config`; in RAM only while unlocked; zeroed via `ctypes.memset`. |
| **User Data Files** | Documents, pictures, videos | High | Format v2 chunked AES-256-GCM encryption with per-file derived keys (`HKDF(MK, file_id)`). |
| **Metadata Index** | File names, paths, sizes, timestamps | High | Encrypted index file (`index.enc`) using AES-256-GCM with rollback protection. |
| **Recovery Key** | 256-bit high-entropy secret | Critical | Shown once during setup as Base32; wraps second MK slot; offline paper backup recommended. |
| **Decoy Master Key** | Secondary isolated root key | High | Stored in slot 2 of `vault.config`; unlocks completely separate directory tree (`decoy/`). |
| **Lockout Counters** | Failed login attempts & backoff timer | Medium | Encrypted state with Windows DPAPI + HMAC-SHA256 + monotonic clock anti-rollback checks. |

---

## 2. Attacker Personas

### Persona A: Casual Snooper / Shoulder Surfer
- **Profile**: Colleague, housemate, family member glancing at the screen or using the computer briefly while the owner is away.
- **Capabilities**: Viewing open windows, opening File Explorer, using Windows Search, inspecting recent files or taskbar thumbnails.
- **Countermeasures**:
  - `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` suppresses screen recording and screenshots.
  - Windows Search Indexing explicitly excluded on vault folders.
  - Targeted recent files cleanup on lock.
  - Instant panic hotkey (`Win+Alt+L`) and lock on session lock/sleep.
  - Calculator disguise mode (`calc.exe` lookalike with PIN entry).

### Persona B: Unattended Local User (Owner Stepped Away)
- **Profile**: Malicious actor with temporary physical hands-on access to the running machine while unlocked.
- **Capabilities**: Mouse/keyboard interaction, plugging in USB drives, trying common passwords.
- **Countermeasures**:
  - Auto-lock timer on user idle (configurable 1–60 minutes).
  - Screen-lock detection (`WM_WTSSESSION_CHANGE`) immediately locks vault upon `Win+L`.
  - Process watcher (App Lock) suspends unauthorized foreground applications until user authenticates.
  - Fail-closed progressive lockout on failed unlock attempts.

### Persona C: Forensic Analyst / Hardware Thief (Offline Attack)
- **Profile**: Thief stealing the laptop or external SSD; forensics investigator acquiring a disk image.
- **Capabilities**: Full access to raw disk filesystem, ability to read all files in hex editor, GPU brute-force cracking cluster.
- **Countermeasures**:
  - All vault files are stored as pseudorandom blob names derived via `HKDF(MK, file_id, "blobname")`. Zero metadata leaks into filenames or directory names.
  - Per-file encryption with unique 16-byte random salt/file ID and 96-bit nonce per 4 MiB chunk.
  - Format v2 authenticated chunking: chunk index, total chunks, plaintext length, and final-chunk flag are bound into AES-256-GCM Associated Authenticated Data (AAD). Chunks cannot be reordered, truncated, or spliced.
  - Argon2id key derivation calibrated to $\approx 1.0$ second per attempt on target CPU ($\ge 256$ MiB memory cost, $t \ge 3$, $p=4$).
  - Optional USB keyfile required to assemble KEK (`Argon2id(pw) || SHA256(keyfile)`).
  - Recommended pairing with BitLocker Full Disk Encryption.

### Persona D: Coercive Adversary ("$5 Wrench Attack")
- **Profile**: Robber or adversary coercing the user into providing their vault password.
- **Capabilities**: Forcing the user to open the vault in their presence.
- **Countermeasures**:
  - **Decoy Vault**: Uniform 4-slot keystore structure (`SLOT_PRIMARY`, `SLOT_RECOVERY`, `SLOT_DECOY`, `SLOT_RESERVED`). Decoy and primary slots have identical ciphertext lengths and JSON layouts.
  - Entering the decoy password unlocks a completely separate, innocent directory tree (`vault_dir/decoy`).
  - Decoy mode disables access to settings, security audit logs, self-destruct, and primary file index.
  - **Emergency Self-Destruct**: Rapidly overwrites all keystore slots with cryptographically secure random bytes and removes local decryption state.

---

## 3. Cryptographic Specification

### Key Hierarchy & Derivation
```
User Password + Salt (16 bytes)
           │
           ▼
     Argon2id (m≥256MB, t≥3, p=4)
           │
           ▼
      Argon Key (32 bytes)
           │
      [Optional: + SHA256(USB Key File)]
           │
           ▼
  HKDF-SHA256 (info: "PersonalVault-KEK-v1")
           │
           ▼
 Key-Encryption-Key (KEK, 32 bytes)
           │
           ▼
 AES-256-GCM Unwrap (vault.config Slot 0)
           │
           ▼
    Master Key (MK, 32 bytes)
    ├── HKDF(MK, file_id, "filekey") ───► Per-File Key (32 bytes)
    ├── HKDF(MK, "indexkey")        ───► Index Key (32 bytes)
    ├── HKDF(MK, "journalkey")      ───► Journal Key (32 bytes)
    └── HKDF(MK, file_id, "blobname") ──► Deterministic Blob Filename (32 hex chars)
```

### Format v2 Encrypted File Layout
```
+-----------------------------------------------------------------------------------+
| MAGIC (8B: 'PVAULT01') | VERSION (1B: 0x02) | FILE_ID (16B) | TOTAL_CHUNKS (8B)   |
+-----------------------------------------------------------------------------------+
| SOURCE_SIZE (8B)                                                                  |
+-----------------------------------------------------------------------------------+
| CHUNK 0: NONCE (12B) | CIPHERTEXT_LEN (4B) | CIPHERTEXT + GCM_TAG (len + 16B)     |
| [AAD: file_id + chunk_index(8B) + is_final(1B) + source_size(8B)]                 |
+-----------------------------------------------------------------------------------+
| ...                                                                               |
+-----------------------------------------------------------------------------------+
| CHUNK N-1: NONCE (12B) | CIPHERTEXT_LEN (4B) | CIPHERTEXT + GCM_TAG (len + 16B)   |
| [AAD: file_id + chunk_index(8B) + is_final=1 + source_size(8B)]                   |
+-----------------------------------------------------------------------------------+
| TRAILER: 'PVTR' (4B) | TOTAL_CHUNKS (8B) | SHA256_PLAINTEXT (32B)                 |
+-----------------------------------------------------------------------------------+
```

---

## 4. Threat Summary & Residual Risk Matrix

| Threat | Residual Risk | Acceptable? | Recommended User Action |
| :--- | :--- | :--- | :--- |
| Brute force offline password cracking | Very Low (Argon2id high memory/time parameters) | Yes | Use 12+ character non-dictionary password |
| Shoulder surfing / unauthorized screenshotting | Negligible (WDA display affinity) | Yes | Keep display affinity enabled |
| Data corruption during power loss | Negligible (Atomic write + journal + rolling backups) | Yes | None |
| SSD Flash Wear-Leveling Remanence | Medium (Physical hardware limitation of NAND flash) | Yes | Enable BitLocker Full Disk Encryption |
| Malware / Keylogger running as user | High (Operating system security boundary) | Yes (Out of scope) | Maintain updated antivirus, don't execute untrusted software |
| Physical cold-boot RAM dump | Low (Master Key zeroed on lock) | Yes | Lock vault when not in active use |

