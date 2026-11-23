# Phase 2 — Backlog Item B7 Plan: Document Lockout Counter Threat Model Limitations

## 1. Problem Statement
The lockout counter (`LockoutManager`, `.lockout.dat`) enforces rate limiting, progressive exponential delay, and intruder logging on the local running machine.
However, if an attacker copies the raw encrypted vault files (`vault.config`, index files, and ciphertext blobs) to an external system, the local `.lockout.dat` file cannot prevent the attacker from conducting offline password guessing attacks against `vault.config`.
In an offline setting, the defense relies strictly on the computational cost and memory-hardness of **Argon2id** (plus the optional USB keyfile 2FA factor and BitLocker full-disk encryption). This limitation must be explicitly documented in `THREAT_MODEL.md` under Persona C (Offline Attack) and the residual risk matrix.

## 2. Proposed Changes
- In `THREAT_MODEL.md`:
  - Under `### Persona C: Forensic Analyst / Hardware Thief (Offline Attack)`:
    - Add explicit section explaining that the progressive lockout counter is a **local-only OS/application defense** for the running instance. It cannot enforce rate limiting against an attacker who extracts raw ciphertext blobs and attacks `vault.config` offline.
    - Clarify that offline brute-force attack cost is strictly bound by the **Argon2id** parameters (memory cost $\ge 256$ MiB, time cost $t \ge 3$, parallelism $p = 4$) and optional 2FA USB keyfile requirement.
  - In Section 5 (Residual Risk Matrix):
    - Update the "Brute force offline password cracking" row to clarify the local vs. offline security boundary.
- In `docs/threat-model.md` (or cross-referenced documentation if applicable):
  - Ensure documentation remains consistent.

## 3. Test & Verification
- Run `ruff check mylocker`
- Run `python -m pytest -q`
- Commit message: `fix: document local lockout counter threat model limitations (B7)`
