# Personal Vault — Detailed Threat Model

## 1. Assets to Protect
1. **Plaintext file contents** (photos, videos, confidential documents, keys).
2. **Metadata** (original file names, folder hierarchy, modification times, sizes).
3. **Derived artifacts** (image thumbnails, media caches, preview buffers).
4. **Master Key (MK)** and user credentials.
5. **App Lock integrity** (preventing protected processes like WhatsApp or Telegram from being viewed).

---

## 2. Attacker Personas

### Persona A: "The Curious Roommate / Family Member"
- **Access**: Physical access to unlocked or locked laptop while user is away for minutes or hours.
- **Tools**: Windows Explorer, Task Manager, search bar, browser history, recent files.
- **Objective**: View photos, read documents, launch private communication apps.
- **Mitigation**:
  - Auto-lock on idle (5 mins), minimize, or lid close.
  - Global Panic Hotkey (`Ctrl+Alt+Shift+L`) instantly hides and locks everything.
  - App Lock suspends protected apps immediately upon startup with fullscreen topmost prompt.
  - Calculator disguise mode disguises the app as a basic utility.

### Persona B: "The Device Thief / Stolen Laptop"
- **Access**: Full offline physical access to laptop drive or stolen backup flash drive.
- **Tools**: Linux live USB, forensic disk imagers, Autopsy, strings / binwalk.
- **Objective**: Extract files directly from the filesystem without booting Windows.
- **Mitigation**:
  - All vault files are stored as encrypted blobs with random 48-hex names in `%LOCALAPPDATA%\PersonalVault\blobs`.
  - Zero plaintext file names exist on disk; metadata is stored inside `index.dat` encrypted with an independent AES-256-GCM key.
  - Master Key wrapped using Argon2id-derived KEK; offline dictionary attack has a massive time/memory penalty (~1s per guess on a modern CPU).
  - Optional USB keyfile requirement prevents decryption even if password is compromised.

### Persona C: "The Coerced User (Duress)"
- **Access**: Attacker is forcing the user to provide the password in person.
- **Objective**: Inspect the vault under threat.
- **Mitigation**:
  - Decoy password feature: opening with the decoy password loads a fake vault with benign files, with no cryptographic indicator that another vault exists.

---

## 3. Cryptographic Attack Analysis

| Attack Vector | Vulnerability in Naive Systems | Personal Vault Defense |
|---|---|---|
| **Ciphertext Malleability** | Bit-flipping CBC mode | AES-256-GCM authenticated encryption with 128-bit authentication tags on every chunk. |
| **Chunk Reordering / Truncation** | Dropping or swapping chunks | Associated Data includes chunk index and 16-byte FileID. Any missing or swapped chunk fails tag check. |
| **Nonce Reuse** | Catastrophic key recovery in GCM | Fresh 96-bit CSPRNG nonce generated per chunk and per file. File keys are derived uniquely per file via HKDF. |
| **Side-Channel Timing** | Timing leaks during password/key verification | `secrets.compare_digest()` constant-time comparisons. |
| **Index Tampering** | Modifying metadata to crash parser | Encrypted rolling backups (`index.bak1`, `index.bak2`, `index.bak3`) and atomic write-then-rename semantics. |

