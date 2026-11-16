# Personal Vault

[![CI](https://github.com/TirthK17/Lock/actions/workflows/ci.yml/badge.svg)](https://github.com/TirthK17/Lock/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PySide6%20%2F%20Qt6-brightgreen.svg)](https://doc.qt.io/qtforpython/)
[![Encryption](https://img.shields.io/badge/Encryption-AES--256--GCM%20%7C%20Argon2id-blueviolet.svg)](https://cryptography.io/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2011%20x64-0078D4.svg)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)](#)

**Personal Vault** is a high-security, offline file and folder encryption application designed exclusively for Windows. It provides zero-knowledge, end-to-end authenticated encryption for photos, videos, confidential documents, and applications with military-grade **AES-256-GCM** chunked streaming and **Argon2id** key derivation, paired with an elegant dark navy glassmorphic user interface.

---

## Key Highlights

- 🔒 **Zero Plaintext on Disk**: Every encrypted file is split into 64 KiB authenticated ciphertext chunks. Decryption occurs purely in memory.
- 🎨 **Modern Dark Navy Dashboard**: Tailored aesthetic (`#060B16` / `#091122`), landscape file cards with instant previews, animated neon sine wave metric cards, and responsive 5-column landscape grid layout.
- 🎬 **Secure Streaming Media Player**: In-memory `QIODevice` streaming playback for high-bitrate 4K/60fps encrypted videos and multi-format audio without disk caching.
- 📷 **Intruder Defense & Webcam Capture**: Automatically captures encrypted intruder snapshots on failed password attempts via Windows DPAPI.
- 🧮 **Calculator Camouflage Disguise**: Disguises the vault as a fully functional Windows Calculator (`calc.exe` clone); entry of a secret numeric PIN unlocks the vault.
- 🛡️ **Defensive Screen & Process Protection**: Uses Windows API `SetWindowDisplayAffinity` to prevent screen scraping/recording, alongside global panic lock (`Ctrl+Shift+L`) and auto-lock on session switch.
- 🔑 **Hardware Calibrated Argon2id & USB 2FA**: Dynamic hardware CPU benchmark for Argon2id parameters, plus two-factor keyfile authentication via USB drive.

---

## Architecture & Security Model

```
Personal Vault Security Architecture
├── Authentication & Key Derivation (Argon2id + Optional USB Keyfile)
│   ├── Memory-hard Argon2id (OWASP calibrated compute)
│   └── HKDF-SHA256 for key separation (Master Key → Storage Key, Index Key, HMAC Key)
├── Storage & Cryptographic Core
│   ├── Format V2: Chunked 64 KiB AES-256-GCM AEAD
│   ├── Ephemeral .part atomic write pipeline with SHA-256 integrity verification
│   └── Encrypted dual-slot index table with MAC authentication
├── User Interface & Experience (PySide6 / Qt6)
│   ├── Clean Login Screen with authentic lock emblem and status telemetry
│   ├── Responsive 5-column Dashboard with video overlays and document vector badges
│   └── In-memory Gallery & Streaming Video Player
└── Operating System Hardening (Windows API)
    ├── SetWindowDisplayAffinity (Screenshot / Screen recording protection)
    ├── Windows DPAPI for intruder photo encryption
    └── Session lock & power suspend event interception
```

---

## Directory Structure

```
.
├── .github/
│   └── workflows/
│       └── ci.yml               # Automated CI (lint, types, bandit, pytest, PyInstaller build)
├── mylocker/
│   ├── __main__.py              # Application entry point
│   ├── app/
│   │   ├── applock/             # Application locking & process monitors
│   │   ├── core/                # Cryptography, KDF, keystore, index, sessions
│   │   ├── system/              # Windows APIs (display affinity, webcam, hotkeys)
│   │   └── ui/                  # PySide6 modern UI (login, dashboard, video player)
│   ├── docs/                    # Architecture and protocol specifications
│   └── tests/                   # Comprehensive automated test suite (170+ tests)
├── build.spec                   # PyInstaller production build specification
├── pyproject.toml               # Project metadata & tool configurations
├── requirements.txt             # Core runtime dependencies
├── requirements-dev.txt         # Development & testing dependencies
├── ACCEPTANCE_REPORT.md         # Full feature acceptance and verification matrix
├── CHANGELOG.md                 # Project version history and updates
├── SECURITY.md                  # Cryptographic specification and threat disclosures
├── THREAT_MODEL.md              # Threat model & attack surface analysis
└── USER_GUIDE.md                # End-user operational documentation
```

---

## Getting Started

### Prerequisites

- **Windows 10 / 11** (64-bit)
- **Python 3.12+**
- Recommended: Windows Terminal, Git

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/TirthK17/Lock.git
   cd Lock
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

---

## Running the Application

Launch Personal Vault directly from source:
```bash
python -m mylocker
```

---

## Testing & Verification

Run the comprehensive automated test suite (includes offscreen Qt rendering, cryptography roundtrips, and batch performance):
```bash
python -m pytest mylocker/tests/ -v --tb=short
```

Run linter:
```bash
python -m ruff check mylocker
```

---

## Building Standalone Executable

To compile a standalone, single-file Windows executable with PyInstaller:
```bash
python -m PyInstaller build.spec --noconfirm
```

The output executable `PersonalVault.exe` will be located in the `dist/` directory.

---

## Documentation

- 📖 [User Guide](USER_GUIDE.md) — First-run setup, adding files, calculator camouflage, and recovery.
- 🛡️ [Security Specification](SECURITY.md) — Cryptographic primitives, key derivation, and defense-in-depth guarantees.
- 🎯 [Threat Model](THREAT_MODEL.md) — Adversary model, attack vectors, and mitigations.
- 📋 [Acceptance Report](ACCEPTANCE_REPORT.md) — Verification records and compliance matrix.
- 📜 [Changelog](CHANGELOG.md) — Version details and release notes.

---

## License

This software is confidential and proprietary. All rights reserved.
