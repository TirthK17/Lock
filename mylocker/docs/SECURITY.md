# Security Architecture & Threat Model

Personal Vault is designed from the ground up for single-user offline data confidentiality and privacy on Windows 11.

---

## 1. Cryptographic Design

### 1.1 Key Hierarchy
```
User Password ───────────────────┐
                                  ▼
[Optional USB Keyfile] ──► Argon2id (m=256MB, t>=3, p=4, 16B salt)
                                  │
                                  ▼
                     Key Encryption Key (KEK, 256-bit)
                                  │
                  AES-256-GCM Key Wrapping (12B nonce)
                                  │
                                  ▼
                    Master Key (MK, 256-bit CSPRNG)
                       [RAM only while unlocked]
                                  │
         ┌────────────────────────┼────────────────────────┐
         ▼                        ▼                        ▼
     HKDF-SHA256              HKDF-SHA256              HKDF-SHA256
(FileID context)          (IndexKey context)        (HMAC salt context)
         │                        │                        │
         ▼                        ▼                        ▼
      FileKey                  IndexKey                 HMAC Key
   (Per-file 256b)         (Index AES-256-GCM)       (Lockout Counter)
```

### 1.2 Algorithms & Standards
- **Symmetric Encryption**: AES-256-GCM authenticated encryption (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`).
- **Key Derivation**: Argon2id (`argon2-cffi`), memory cost ≥ 256 MiB, time cost auto-benchmarked to ~1 second, 4 lanes, random 16-byte salt per vault.
- **Sub-Key Derivation**: HKDF-SHA256 (RFC 5869) with unique domain separation strings (`PersonalVault-FileKey-v1`, `PersonalVault-IndexKey-v1`).
- **Chunked File Encryption**: 4 MiB streaming chunks. Each chunk has an independent 96-bit CSPRNG nonce and associates `b"chunk" + file_id + chunk_index` as Authenticated Additional Data (AAD). This mathematically prevents chunk reordering, truncation, substitution, and cross-file splicing.
- **Key Wrapping**: Master Key is stored wrapped with KEK using AES-256-GCM. A second copy is wrapped with an independently generated 256-bit Recovery Key.

---

## 2. Threat Model

### 2.1 What Personal Vault PROTECTS Against
| Threat / Adversary | Mitigation |
|---|---|
| **Casual Snooper (Friends, Family, Coworkers)** | Vault is locked behind password, auto-locks after idle timeout (default 5 min), locks on minimize, locks on Windows screen lock/sleep. Topmost App Lock suspends target applications. |
| **Offline Disk Thief / Vault Folder Copy** | All file contents, filenames, timestamps, and thumbnails are encrypted with AES-256-GCM. Unwrapped Master Key exists only in memory while unlocked. |
| **Windows Search & Thumbnail Leaks** | Vault folder has NTFS `FILE_ATTRIBUTE_NOT_CONTENT_INDEXED` set, `Desktop.ini` system masking, and thumbnails are stored only inside the encrypted index. Windows Recent Files / Jump Lists are programmatically purged on lock. |
| **Brute-Force Password Guessing** | Argon2id makes each guess computationally expensive (~1 second per attempt). Exponential backoff lockout is persisted on disk with HMAC-SHA256 integrity protection. Optional self-destruct wipes wrapped keys after threshold. |
| **Shoulder Surfing / Screen Sniffers** | Windows `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` blanks the application window from screenshots and screen recording software. |
| **Duress / Coerced Unlock** | Decoy vault password opens a harmless secondary vault while leaving the real vault hidden. |

---

## 3. Honest Limitations (What is NOT Protected)

Security engineering requires complete honesty about what user-mode software cannot prevent:

1. **Malware running under your user account**:
   If an infostealer or rootkit is already active on your machine, it can install an API hook, log keystrokes as you type your master password, or read process RAM.
2. **Hardware Keyloggers**:
   Physical hardware interposers between your keyboard and laptop cannot be detected by software.
3. **Live RAM dumps while unlocked**:
   While the vault is unlocked, the Master Key must exist in RAM to decrypt files. A forensic memory dump (e.g., via Cold Boot attack, DMA attack, or kernel driver) taken while unlocked can recover key material.
4. **SSD Wear-Leveling and Secure Deletion**:
   When files are deleted, our shredder overwrites the file blocks with random data 3 times, flushes OS buffers via `fsync()`, and renames the file before deletion. However, Flash Translation Layers (FTL) on modern Solid State Drives (SSDs) perform wear-leveling and may remap blocks rather than overwriting the physical NAND cells immediately.

> [!IMPORTANT]
> **CRITICAL RECOMMENDATION: ENABLE BITLOCKER**
> Because SSD wear-leveling can leave physical flash remnants of deleted plaintext files, you **must enable Windows BitLocker full-disk encryption** alongside Personal Vault. BitLocker encrypts the physical drive at the controller layer, ensuring wear-leveled NAND blocks remain encrypted ciphertext at rest.

