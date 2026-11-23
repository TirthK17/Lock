# Phase 3 Plan: Comprehensive Read-Only Security & Architecture Audit

## 1. Goal & Execution Rules
Phase 3 is a **strictly read-only deep-dive audit** across the unreviewed core modules and UI screens of Personal Vault.
Per `GEMINI.md` and Phase 3 specifications:
- **No code changes** in Phase 3.
- All findings, verification notes, and recommendations will be written to dedicated markdown audit reports in `docs/`:
  - `docs/audit-A-keystore.md` (Task A)
  - `docs/audit-B-chunked-io.md` (Task B)
  - `docs/audit-C-index-journal.md` (Task C)
  - `docs/audit-D-kdf.md` (Task D)
  - `docs/audit-E-ui-inventory.md` (Task E)
- Each report will detail:
  1. Scope & files inspected
  2. Security guarantees & threat model compliance
  3. Concrete findings (categorized by Critical, High, Medium, Low, Informational / Clean)
  4. Explicit verification of specific audit items
  5. Actionable recommendations for subsequent phases (Phases 4–7)

---

## 2. Scope & Tasks Breakdown

### Task A: `keystore.py` Security Audit (`docs/audit-A-keystore.md`)
- **Target File**: `mylocker/app/core/keystore.py` (and related `crypto.py`)
- **Key Inspection Points**:
  - Argon2id calibration integration and hardware baseline compatibility.
  - Uniform 4-slot layout (`SLOT_PRIMARY`, `SLOT_RECOVERY`, `SLOT_DECOY`, `SLOT_RESERVED`) and ciphertext length indistinguishability (deniable decoy existence).
  - AES-256-GCM slot encryption, salt generation, nonce uniqueness, and AAD binding.
  - Master Key memory zeroing hygiene (`zero_bytes`, bytearray handling, scope lifetimes).
  - Recovery key mathematics: base32 checksum validation, key derivation, re-key safety.
  - Emergency self-destruct mechanism (multi-pass random overwrite, metadata clearing).

### Task B: `chunked_io.py` Cryptographic Audit (`docs/audit-B-chunked-io.md`)
- **Target File**: `mylocker/app/core/chunked_io.py`
- **Key Inspection Points**:
  - Format V2 header specification: magic bytes (`PVLT`), version, file ID, total chunks, chunk size.
  - AES-256-GCM chunking: 64 KiB / 4 MiB streaming boundaries, memory footprint bounding.
  - Nonce generation and uniqueness: 96-bit nonces, counter/salt structuring, IV reuse prevention.
  - AAD Binding: verification that chunk index, total chunks, original length, and final flag are cryptographically authenticated to prevent splicing, reordering, truncation, or chunk extension.
  - Edge cases: 0-byte file handling, exact chunk boundary files, single-chunk vs multi-chunk files, tampered authentication tags.

### Task C: `index.py` & `journal.py` Integrity Audit (`docs/audit-C-index-journal.md`)
- **Target Files**: `mylocker/app/core/index.py`, `mylocker/app/core/journal.py`
- **Key Inspection Points**:
  - Index serialization, JSON payload structure, schema enforcement, and versioning.
  - Authenticated encryption of the file index using derived Index Key (`HKDF-SHA256(MK, ...)`).
  - Replay protection, backup index generation (`.index.bak`), and corruption recovery.
  - Journal transaction semantics: write-ahead logging (WAL), crash-safe atomic commits, replay of uncommitted transactions, idempotency of rollback/recovery.
  - Decoy mode index isolation (strict separation from primary index).

### Task D: `kdf.py` Key Derivation Audit (`docs/audit-D-kdf.md`)
- **Target File**: `mylocker/app/core/kdf.py`
- **Key Inspection Points**:
  - Argon2id parameters: memory cost ($m \ge 256$ MiB), time cost ($t \ge 3$), parallelism ($p = 4$).
  - Dynamic hardware calibration benchmark loop: precision, timeout caps, target latency bounds (~0.25s vs ~1.0s).
  - Fallback mechanisms for low-memory or constrained environments.
  - Thread safety during multi-threaded derivation or background calibration.
  - Salt generation (cryptographically secure 16-byte random salts).

### Task E: UI Screens Inventory & Consistency Audit (`docs/audit-E-ui-inventory.md`)
- **Target Directory**: `mylocker/app/ui/` (`login.py`, `setup_wizard.py`, `dashboard.py`, `settings.py`, `gallery.py`, `video_player.py`, `intruder_log.py`, `calculator.py`, `applock_ui.py`, etc.)
- **Key Inspection Points**:
  - Screen-by-screen inventory of all interactive elements: buttons, inputs, dialogs, shortcuts, status indicators.
  - Verification against functional requirements: no removed features, shortcuts, or controls.
  - Visual token usage: verifying compliance with `ui/theme.py` (`Colors`), `ui/dashboard_theme.py` (`DashColors`), and `ui/login_theme.py` (`LoginColors`).
  - Threading & safety compliance: GUI interactions strictly on main thread, thread cancellation before session lock, no blocking calls on main thread.
  - Accessibility & layout responsiveness: high-DPI scaling, resize behavior, reduced motion respect.

---

## 3. Deliverables & Next Steps
- Generate all 5 audit report documents in `docs/`:
  - `docs/audit-A-keystore.md`
  - `docs/audit-B-chunked-io.md`
  - `docs/audit-C-index-journal.md`
  - `docs/audit-D-kdf.md`
  - `docs/audit-E-ui-inventory.md`
- Commit: `docs: complete phase 3 security and ui audit reports (tasks A-E)`
- Present synthesized findings and audit matrix to user for Phase 4 (Qt Screen-by-Screen Redesign) prioritization.
