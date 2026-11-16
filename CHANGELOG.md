# Changelog — Personal Vault Remediation

All notable changes and security audit remediations completed across Phases 0 through 8 are documented below.

---

## [2.0.0] — Audit Remediation & Hardening Release

### Phase 0: Startup Flow, Wizard & Test Harness
- **Setup Wizard Word-Wrap Bug**: Fixed PySide6 layout bug where `setWordWrap` was called on `QCheckBox` (rearchitected with `QHBoxLayout`, empty checkbox, and wrapped `QLabel`).
- **5-Step Setup Flow**: Implemented linear wizard: Master Password $\to$ Optional USB Key File $\to$ "Create Vault" (running Argon2 benchmark in dedicated background worker with smooth progress animation) $\to$ Recovery Key display $\to$ Random 3-group confirmation verification $\to$ Finish.
- **Vault Location Persistence**: Created `%APPDATA%\PersonalVaultSettings.json` saving user-selected vault folder across app launches.
- **Lazy Initialization**: Refactored `SetupWizard`, `Dashboard`, and `CalculatorDisguise` to construct on-demand only after needed, drastically minimizing initial memory footprint and attack surface.
- **Offscreen UI Testing**: Added 5 headless `pytest-qt` integration tests covering setup wizard, correct/incorrect logins, keyfile unlock, and master key memory zeroing.
- **GitHub Actions CI**: Added `.github/workflows/ci.yml` running linting, formatting, security scanning, and unit tests on `windows-latest`.

### Phase 1: Data Safety & Single Writer Architecture
- **Blob Map Retirement**: Deprecated plaintext `blobmap.dat` and introduced deterministic blob name derivation using `HKDF-SHA256(MasterKey, file_id, "blobname")`.
- **Crash-Safe 8-Step Add Pipeline**:
  1. Compute source SHA-256 hash.
  2. Encrypt into temporary `.part` file.
  3. Fully decrypt temporary file and compare SHA-256 hash against source.
  4. Flush (`fsync`) and rename `.part` to final deterministic blob name.
  5. Atomically write new index (`.tmp` $\to$ `fsync` $\to$ rename) and reload to verify.
  6. Journal state `INDEXED`.
  7. Securely shred original file with multi-pass random data.
  8. Journal state `DONE`.
- **Encrypted Journal**: Replaced plaintext `journal.json` with AES-256-GCM encrypted `journal.enc` keyed via `HKDF(MasterKey, "journalkey")`. Recovery runs once at unlock before workers spin up.
- **Index Corruption Recovery**: Replaced silent empty index reset with `IndexCorruptError` and rolling backup recovery (`index.bak1`, `index.bak2`, `index.bak3`).
- **Single-Writer Service (`VaultService`)**: Created central `VaultService` enforcing a single `threading.RLock`, thread-safe task queue, real cancellation via `threading.Event`, and unique temporary filenames.
- **Safe Tar Extraction**: Hardened backup restore with member path traversal checks (blocking `..`, absolute paths, symlinks) and safe extraction filter (`filter="data"`), staged in a separate folder with atomic swap.

### Phase 2: File Format v2 & Cryptographic Binding
- **Format v2 Layout**: Designed and deployed Format v2 with a 41-byte header: `MAGIC` (`PVAULT01`), `VERSION_V2` (`0x02`), `file_id` (16 bytes), `total_chunks` (8 bytes), and `source_size` (8 bytes).
- **AAD Chunk Binding**: Integrated `file_id`, 8-byte `chunk_index`, `is_final` flag, and `source_size` into AES-256-GCM Associated Authenticated Data (AAD) for every chunk, mathematically preventing chunk reordering, truncation, or splicing.
- **Authenticated Trailer (`PVTR`)**: Appended authenticated trailer containing expected chunk count and plaintext SHA-256 hash.
- **Backwards Compatibility**: Transparently reads and decrypts legacy Format v1 files without loss of functionality.

### Phase 3: Cryptographic Hardening & Keystore Layout
- **Memory Zeroing**: Replaced naive Python loop with native `ctypes.memset` for guaranteed in-place memory erasure.
- **Password Policy & Diceware**: Integrated `zxcvbn` strength evaluation ($\ge 3$, minimum 12 chars), pattern blacklist, and 5-word Diceware passphrase generator.
- **DPAPI-Protected Lockout**: Hardened `lockout.dat` using Windows DPAPI (`CryptProtectData`) and HMAC-SHA256 with fail-closed lockouts (3 attempts = 30s delay; 5 attempts = 5min delay). Added rollback protection using monotonic clock deltas (`time.monotonic()`).
- **Uniform 4-Slot Keystore**: Structured `vault.config` with 4 uniform slots (`SLOT_PRIMARY`, `SLOT_RECOVERY`, `SLOT_DECOY`, `SLOT_RESERVED`), constant JSON sizing, and dummy ciphertexts to prevent side-channel slot identification.
- **Decoy Vault Isolation**: Decoy password unlocks an isolated secondary directory (`vault_dir/decoy`) with its own independent index and blobs, while hiding all sensitive settings and logs.
- **Intruder Photo Capture**: Replaced unencrypted captures with Windows DPAPI encryption at rest and in-memory decryption viewer. Disabled by default to respect privacy.

### Phase 4: Locking, Viewers, Screenshots & Temp Files
- **Anti-Screenshot Protection**: Enforced Win32 `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` / `WDA_MONITOR` across all secure Qt windows to block screen recording and screenshot captures.
- **Lock Controller**: Built centralized `LockController` closing all registered viewers and dialogs, wiping temporary files, clearing the Windows clipboard, and zeroing the Master Key on lock.
- **Windows Session Event Interception**: Registered native event filter intercepting `WM_WTSSESSION_CHANGE` (`WTS_SESSION_LOCK` / `Win+L`) and `WM_POWERBROADCAST` (`PBT_APMSUSPEND` / sleep) to instantly lock the vault.
- **Targeted Recent Files Shredder**: Replaced aggressive global recent file wiping with `clean_vault_recent_files`, removing only vault-associated `.lnk` files in `%APPDATA%\Microsoft\Windows\Recent`.
- **In-Memory Gallery**: Implemented 5-image LRU cache with `WA_DeleteOnClose` and automatic buffer memory zeroing on close or vault lock.
- **Streaming Video Player**: Developed `StreamingVaultIODevice` providing seamless seeking and chunk streaming with an exact ~4 MiB memory ceiling.
- **Secure External File Opener**: External file viewer writes to random temporary folder with restricted NTFS DACL, launches via `ShellExecuteExW`, watches process handle, checks SHA-256 for re-encryption, and securely deletes on exit.

### Phase 5: Windows Hardening & Single Instance
- **NTFS Access Control Lists (ACL)**: Hardened vault directory using `SetNamedSecurityInfo` with `PROTECTED_DACL_SECURITY_INFORMATION`, blocking inherited permissions and granting access solely to current user SID.
- **Windows Search Exclusions**: Set `FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM` on vault directory and created `desktop.ini` with `FILE_ATTRIBUTE_NOT_CONTENT_INDEXED`.
- **BitLocker Advisory**: Added automated WMI `Win32_EncryptableVolume` query during first-run wizard recommending full-disk encryption.
- **Single-Instance Named Pipe**: Implemented Win32 named pipe server (`\\.\pipe\PersonalVault_SingleInstance_<user>`) with `FlushFileBuffers` + `DisconnectNamedPipe` to reliably bring existing instance to foreground when launched twice.

### Phase 6: App Lock Rebuild
- **Isolated AppLock Config**: Separated App Lock from vault Master Key using dedicated `applock.config` and Argon2id verifier (64 MiB, $t=2$, $p=1$).
- **Shortcut Target Resolution**: Added `resolve_shortcut_target` using `WScript.Shell` COM interface to resolve `.lnk` shortcuts to target executable paths.
- **Supervisor Monotonic Heartbeat**: Built `AppLockSupervisor` monitoring child watcher process with monotonic timestamp heartbeats and automatic restart on crash/death.

### Phase 7: Settings, UI/UX & Disguise
- **Integrity-Signed Settings**: Hardened `settings.dat` with HMAC-SHA256 signature, falling back cleanly to secure defaults upon tampering.
- **Settings Card Layout**: Resolved widget clipping by properly parenting title labels to card layouts.
- **Emergency Self-Destruct**: Added dedicated modal requiring user to type `"DESTROY MY VAULT"`, permanently overwriting all keystore slots with cryptographically secure random bytes.
- **Dashboard Search UX**: Implemented 250ms debounced search timer and `Ctrl+F` shortcut to focus search bar.

### Phase 8: Verification, Packaging & Quality Assurance
- **Linting & Formatting**: Cleaned all Ruff warnings; `ruff check mylocker` passes with 0 errors.
- **Security Auditing**: Executed Bandit AST security scanner; 0 High and 0 Medium vulnerabilities identified across 10,965 lines of code.
- **PyInstaller Packaging**: Configured `personal_vault.spec` and `build.spec` with QtSvg and multimedia plugins for single-file executable generation without UPX (eliminating AV heuristics false-positives).
- **Comprehensive Documentation**: Complete, production-grade `SECURITY.md`, `THREAT_MODEL.md`, `USER_GUIDE.md`, and `CHANGELOG.md`.

### Phase 9–12: Modern UI/UX Architecture & Vector SVG Engine
- **Vector SVG Icon Engine (`icons.py`)**: Built pure vector SVG rendering pipeline utilizing `QSvgRenderer` and `QPainter`. Renders crisp, scalable 24×24 vector icons for shield, lock, unlock, key, folder, file, image, video, music, archive, code, star, search, plus, trash, download, upload, settings, camera, calculator, and warning indicators. Completely replaced all text/emoji placeholders across sidebar items, file cards, setup wizard, login screen, and settings dialog.
- **Dynamic Responsive Grid**: Rearchitected `Dashboard` grid layout with event-driven column recomputation in `resizeEvent`, smoothly adjusting column counts and card widths to fit any window size from 900px to 4K displays.
- **Scoped File Search**: Enforced search query filtering against the currently active navigation section (e.g., Photos, Videos, Documents, Trash, Favorites), preventing category leakage.
- **Async Progress & Status Feedback**: Connected `VaultService` async worker signals (`progress` and `finished`) directly to dashboard status toasts and progress dialogs with user-friendly error codes.

### Phase 13–15: Comprehensive Integration & Stress Testing
- **10-Step Decoy/Primary Isolation Sequence**: Verified complete isolation between primary and decoy vaults under repeated lock/unlock cycles, confirming that neither files nor metadata leak across vaults.
- **Large File Bounded Streaming Verification**: Stress-tested streaming encryption and verification with a 105 MiB file, verifying consistent bounded memory usage and chunk integrity.
- **External File Modification Transaction**: Verified atomic in-place file editing and save-back with deterministic blob name preservation and zero orphaned artifacts.
- **Custom Location Provisioning**: Verified runtime creation and persistence of custom vault locations across application restarts.
- **Test Suite Perfection**: 121 automated tests passing with 0 failures across unit, integration, UI offscreen, and comprehensive multi-step suites.

