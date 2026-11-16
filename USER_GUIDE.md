# Personal Vault — User Guide

**Personal Vault** is a high-security, offline file and folder encryption application designed exclusively for Windows 11. It protects your private photos, videos, confidential documents, and applications with military-grade AES-256-GCM and Argon2id encryption.

---

## 1. Quick Start & First-Run Setup

When launching Personal Vault for the first time, you will be guided through the **Setup Wizard**:

1. **Master Password**:
   - Choose a strong passphrase of at least 12 characters. The built-in entropy meter evaluates strength using `zxcvbn`.
   - *Tip*: Click **"🎲 Generate Passphrase"** to generate an easy-to-remember, highly secure 5-word Diceware passphrase.
2. **Vault Storage Location**:
   - Select the directory on your SSD, hard drive, or external drive where encrypted files will be kept.
   - The directory path is stored locally in your Windows `%APPDATA%\PersonalVaultSettings.json` file.
3. **Optional USB Key File**:
   - If desired, enable two-factor protection by generating or selecting a key file on a USB flash drive.
   - When enabled, unlocking requires **both** your master password and the physical USB drive plugged into your computer.
4. **Hardware Cryptographic Calibration**:
   - Click **"Create Vault"**. The application runs a benchmark on your CPU to calibrate Argon2id memory and iteration parameters to provide $\approx 1.0$ second of compute per unlock attempt, maximizing brute-force resistance.
5. **Recovery Key**:
   - The wizard generates a 256-bit emergency recovery key formatted as Base32 groups (e.g., `XXXX-XXXX-XXXX-...`).
   - Click **"📋 Copy"** or **"💾 Save to File"** to archive it offline (e.g., on paper or a trusted backup drive).
   - To confirm you saved it, the wizard requires you to verify 3 randomly chosen word groups before finalizing setup.

---

## 2. Unlocking and Camouflage

### Standard Unlock
- Open the application, enter your Master Password (and connect your USB key file if configured), and click **Unlock Vault**.
- If 3 failed attempts occur, a temporary 30-second backoff activates; 5 failed attempts trigger a 5-minute lockout.

### Calculator Camouflage Disguise
- You can enable **Calculator Mode** in Settings (`Ctrl+,`).
- When enabled, the application appears as a fully functional standard Windows calculator (`calc.exe` mimic).
- To reveal the unlock screen, simply enter your secret numeric PIN (e.g. `7391#`) into the calculator and press `=` (or click the secret trigger).

---

## 3. Managing Files & Folders

### Adding Files (Move & Encrypt)
- **Drag & Drop**: Drag files or folders directly into the Dashboard.
- **Add File / Add Folder Buttons**: Select items using the standard Windows file picker.
- **Safety Pipeline**:
  1. The original file is hashed (SHA-256).
  2. The file is encrypted into a temporary chunked blob (`.part`).
  3. The ciphertext is fully verified by test-decrypting and verifying the SHA-256 checksum.
  4. The encrypted blob is renamed atomically.
  5. The encrypted index is updated and verified.
  6. The original file is securely overwritten with random data and deleted.

### Viewing Photos & Documents Without Traces
- **Built-in Photo Gallery**:
  - Double-click any image (`.jpg`, `.png`, `.bmp`, `.webp`, `.gif`) in the dashboard.
  - Photos are decrypted **purely in system memory (RAM)**. No decrypted files are ever written to your temporary directory or disk.
  - Windows Photos, Explorer thumbnails, and cloud sync services cannot view or index your photos.
  - When you close the gallery or lock the vault, the in-memory image buffers are securely wiped (`ctypes.memset`).
- **Streaming Video Player**:
  - Double-click any video file.
  - Decrypted in 4 MiB memory chunks on-the-fly, allowing smooth playback of multi-gigabyte 4K videos while maintaining a low RAM footprint (~4 MiB).

### Restoring / Exporting Files
- Right-click any file and choose **"Restore File"**.
- Select the destination directory where you want the decrypted file saved.
- You can choose to leave the copy in the vault or delete it from the vault after restoring.

---

## 4. Decoy Vault (Coercion Resistance)

- Under **Settings → Security**, you can configure a **Decoy Password**.
- If forced to unlock your vault under duress:
  - Enter the Decoy Password at the login screen.
  - Personal Vault unlocks a completely isolated, innocent secondary vault located at `vault_dir/decoy`.
  - The Decoy Vault has its own separate index and files.
  - In Decoy Mode, all sensitive settings, app lock configurations, security logs, and your primary files are completely hidden.

---

## 5. App Lock (Application Protection)

- Protect executable applications (e.g., browsers, accounting software, email clients) from unauthorized access.
- In the **App Lock** tab:
  - Browse running processes or click **"Add App (.exe or .lnk)"**. Shortcuts (`.lnk`) are automatically resolved to their real executable paths.
  - Configure a **Grace Period** (e.g., 5 minutes) so you don't need to re-enter your password repeatedly while switching apps.
  - The App Lock supervisor monitors protected processes in the background. When an unauthorized launch is detected, it is suspended until authenticated.

---

## 6. Locking, Hotkeys & Privacy Cleanup

- **Panic Hotkey**: Press **`Win + Alt + L`** (or `Ctrl + Alt + L`) at any time to instantly lock the vault.
- **Screen Lock / Sleep**: Locking your laptop (`Win + L`) or closing the lid automatically locks Personal Vault.
- **Automated Privacy Cleanup**:
  - Locks all open media viewers and dialogs.
  - Wipes any temporary decryption files.
  - Clears the Windows clipboard if it contains vault-related data.
  - Shreds any vault shortcuts generated in Windows Recent Files.
  - Zeroes the 256-bit Master Key in RAM.

---

## 7. Emergency Self-Destruct

- Accessible in **Settings → Emergency Self-Destruct**.
- In an extreme emergency where you need to permanently render the vault unrecoverable:
  - Click **"🚨 Emergency Self-Destruct"**.
  - Type the confirmation phrase `"DESTROY MY VAULT"`.
  - Personal Vault overwrites all key slots in `vault.config` with cryptographically secure random bytes, wipes local configuration, clears memory, and exits immediately.
  - *Warning*: This operation is instantaneous, irreversible, and permanently destroys the keys required to decrypt all files in the vault.

