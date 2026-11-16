# Personal Vault — User Guide

Welcome to Personal Vault. This guide explains how to set up, use, and maintain your offline vault on Windows 11.

---

## 1. Quick Start

### Installation
Ensure Python 3.12+ is installed on your Windows 11 system.
```powershell
pip install -r requirements.txt
python mylocker/main.py
```
Or run the compiled `PersonalVault.exe` directly.

---

## 2. Setup Wizard (First-Time Run)

When you first launch the app, the 5-step Setup Wizard appears:
1. **Vault Location**: Select where encrypted data is stored (default: `%LOCALAPPDATA%\PersonalVault`).
2. **Master Password**: Choose a strong password (minimum 12 characters). Live strength feedback is displayed.
3. **Optional USB Key File**: You can select any file on a USB flash drive as a physical second factor. The vault will not open unless this USB drive is plugged in!
4. **Recovery Key**: A 64-character Base32 recovery key is generated. You **must** copy or save it to a safe location before continuing.
5. **Argon2id Tuning**: The app benchmarks your CPU/memory so that key derivation takes approximately 1.0 second.

---

## 3. Daily Usage

### Adding Files & Folders
- **Drag & Drop**: Drag files or folders directly into the central vault window.
- **Button**: Click `+ Add Files` in the top bar.
- **What happens**:
  1. The file is hashed.
  2. The file is encrypted into 4 MiB authenticated AES-256-GCM chunks.
  3. The encrypted file is verified by decrypting and comparing SHA-256 hashes.
  4. The encrypted index is updated atomically.
  5. The original plaintext file is overwritten with random data 3 times and deleted.

### Viewing Files Without Traces
- **Photos**: Click any photo card. Decrypts directly into RAM and opens the in-memory viewer with zoom, fit-to-window, and slideshow. Decrypted bytes are zeroed on exit.
- **Videos**: Decrypts directly into an in-memory streaming device fed to the player. No plaintext file touches disk.
- **Documents & Other Files**: Decrypts into an isolated temporary folder with restricted NTFS ACLs, opens with the default Windows application, and automatically wipes the temporary files after 60 seconds or upon vault lock.

### Locking Your Vault
- Click **Lock Vault** in the sidebar.
- Use the shortcut **Ctrl + Alt + L** from inside the app.
- Press the **Global Panic Hotkey** (`Ctrl + Alt + Shift + L`) from anywhere in Windows to instantly hide and lock everything.
- Auto-lock engages after 5 minutes of inactivity or when the window is minimized.

---

## 4. Application Locker (App Lock)

Protect programs like WhatsApp, Telegram, Chrome, or Windows Settings:
1. Navigate to **App Lock** in the sidebar.
2. Click **Scan Running Apps** or **Browse Installed** to add target executables.
3. When a protected application is launched, Personal Vault immediately suspends it and shows a topmost password prompt.
4. If the master password is provided, the process is resumed. If cancelled, the process is terminated immediately.
5. Enable **Start App Lock automatically at Windows login** in settings to protect apps even before opening the vault.

---

## 5. Stealth & Disguise Mode

- In **Settings > Stealth & Camouflage**, enable **Calculator Disguise**.
- When launched, the app opens as a fully functional standard calculator!
- To access the vault, enter your secret PIN (default: `1337`) and press `=` on the calculator keypad. The calculator smoothly transitions to the vault login.

---

## 6. Backup & Recovery

- **Encrypted Backup**: In Settings, click **Export Encrypted Backup** to save a single `.tar.gz` archive containing all encrypted blobs, index, and a cryptographically verified manifest.
- **Verify Integrity**: Run **Verify Vault Integrity** to check the cryptographic authentication tag of every chunk of every file.
- **Password Recovery**: On the login screen, click **Use recovery key** to unlock with your Base32 recovery key if you forgot your master password.
- **Rotate Keys**: Generate a fresh Master Key and re-encrypt all vault files in one automated batch.

