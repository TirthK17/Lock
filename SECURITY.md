# Security Policy & Threat Model

Personal Vault is designed as a defense-in-depth, offline privacy locker for personal data, confidential documents, and media on Windows 11. It protects user files using standard, audited cryptographic primitives (`AES-256-GCM`, `Argon2id`, and `HKDF-SHA256`).

---

## 1. What Personal Vault Protects Against

- **Casual Snooping & Shoulder Surfing**:
  - The application provides a Calculator disguise mode (`calc.exe` mimic).
  - Screen capture blocking (`SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` / `WDA_MONITOR`) prevents screenshot utilities, OBS, Discord screen sharing, and remote viewing tools from capturing unlocked vault contents.
  - Windows Search Indexer is explicitly prevented from indexing the vault folder (`FILE_ATTRIBUTE_NOT_CONTENT_INDEXED` and `desktop.ini`).
  - Windows Explorer thumbnail generation and Recent Files pollution are suppressed; any vault shortcuts created in `%APPDATA%\Microsoft\Windows\Recent` are selectively shredded upon locking.

- **Unattended Device Compromise (Family, Friends, Coworkers)**:
  - Global panic hotkey (`Win+Alt+L` / `Ctrl+Alt+L`) instantly locks the vault, clears the Windows clipboard, closes all media viewers, and wipes temporary decrypt staging directories.
  - Native Windows session event filtering (`WM_WTSSESSION_CHANGE` / `WTS_SESSION_LOCK` and `WM_POWERBROADCAST` / `PBT_APMSUSPEND`) automatically locks the vault when the laptop screen locks (`Win+L`) or goes to sleep.
  - Configurable idle inactivity timer automatically locks the vault after a designated period of user inactivity.

- **Offline Physical Storage Theft**:
  - If the laptop or drive is stolen, or the vault directory is copied to another machine, the attacker cannot read any files, metadata, or directory structures.
  - File names, timestamps, mime types, and directory paths are encrypted inside the index with AES-256-GCM.
  - File blobs on disk are named using pseudo-random deterministic derivation (`HKDF(MK, file_id, "blobname")`) and contain no recognizable headers, extensions, or plaintext metadata.
  - Master Key (MK) is protected using `Argon2id` (memory $\ge$ 256 MiB, time cost $\ge$ 3, parallelism 4), calibrated during setup to require $\approx 1.0$ second of CPU compute per password attempt.
  - Optional USB Key File factor: when enabled, the Key Encryption Key (KEK) is derived from both the user password and SHA-256 of a physical key file located on a removable drive (`KEK = HKDF(Argon2id(pw) || SHA256(keyfile))`).

- **Brute-Force & Replay Attacks**:
  - Fail-closed lockout manager enforces progressive lockouts (3 failed attempts = 30s delay; 5 failed attempts = 5-minute lockout).
  - State file (`lockout.dat`) is encrypted and integrity-protected via Windows DPAPI (`CryptProtectData`) and HMAC-SHA256.
  - Rollback detection: monotonic uptime counters (`time.monotonic()`) and wall-clock verification prevent resetting failed attempt counters by deleting or reverting `lockout.dat`.
  - Coercion resistance: uniform 4-slot keystore layout with constant-size JSON config and dummy ciphertext slots. Decoy password unlocks an isolated, harmless secondary vault (`vault_dir/decoy`) while hiding all audit logs, app lock configs, and primary files.

---

## 2. What Personal Vault Does NOT Protect Against (Explicit Boundaries)

Security engineering requires clear honesty about technical limitations. The following threat scenarios are outside the scope of an application-level user-space vault:

1. **Malware Running as Current User (Ring 3)**:
   - If an active keylogger, Trojan, or RAT is running under your Windows user account, it can inspect keystrokes when you type your master password or read memory while the vault is unlocked.
2. **Kernel-Level Malware & Rootkits (Ring 0 / Hypervisor)**:
   - Kernel drivers or compromised UEFI firmware have direct physical memory access and bypass user-space permissions.
3. **Live RAM Acquisition & Memory Dumps**:
   - While the vault is unlocked, the 256-bit Master Key resides in RAM (in a mutable `bytearray`). While it is zeroed (`ctypes.memset`) immediately upon locking, a crash dump or hardware DMA attack while unlocked could extract the key.
4. **Hardware Keyloggers & Compromised Peripherals**:
   - Physical USB hardware keyloggers intercept raw keyboard scan codes before the OS receives them.
5. **SSD Flash Wear-Leveling & Secure Deletion Limits**:
   - On modern NVMe and SATA SSDs, the Flash Translation Layer (FTL) remaps logical block addresses across flash memory dies to balance wear. Multi-pass pseudorandom overwriting of original files is performed on a best-effort basis; the physical NAND blocks originally hosting the data may be retained in unallocated flash blocks until garbage collection / TRIM runs.

---

## 3. Recommended Security Measures

To achieve maximum protection against the limitations noted above, **strongly implement the following**:

1. **Enable BitLocker Drive Encryption**:
   - Personal Vault checks your BitLocker status during first-run setup. Enable BitLocker or Windows Device Encryption on your OS drive (`C:`) and any external drives storing vaults. BitLocker encrypts the physical drive at rest, neutralizing SSD wear-leveling remnants and cold-storage extractions.
2. **Use a Strong Master Passphrase**:
   - Use the built-in Diceware 5-word generator or create a passphrase with high entropy ($\ge 12$ characters, non-dictionary).
3. **Store Recovery Key Securely**:
   - Print or store your Base32 recovery key offline in a physically secure location (e.g., safe deposit box or paper notebook).
4. **Set Up a Removable USB Key File**:
   - For sensitive environments, require a USB key file so that physical possession of the USB drive is required to unlock the vault.

---

## 4. Reporting Security Vulnerabilities

If you discover a security vulnerability or cryptographic weakness in Personal Vault, please report it responsibly by contacting the maintainer directly. Do not open public GitHub issues for undisclosed security flaws.

