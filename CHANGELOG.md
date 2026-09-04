# Changelog

## 3.0.0 - 2026-09-04

- Made Codex configuration updates transactional with automatic byte-for-byte rollback after strict-check or environment-persistence failures.
- Added concurrent-edit detection and UTF-8 BOM preservation.
- Replaced provider-section regular expressions with TOML-aware table scanning that handles quoted keys, nested tables, and multiline strings.
- Preserved unmanaged target-provider settings while explicitly resolving incompatible command authentication.
- Added safe `models`, `responses`, `both`, and `none` verification modes with cross-origin redirect blocking and bounded response reads.
- Added `audit`, `unset`, and `backups` maintenance commands.
- Added subcommand-free quick setup and installable `ai-key-setup` console packaging.
- Expanded regression coverage across configuration structure, transaction failures, network handling, credential redaction, and maintenance commands.

## 2.0.0 - 2026-09-03

- Added a native PowerShell entry point for Windows.
- Rebuilt Codex configuration as a targeted, idempotent update that preserves unrelated settings.
- Moved API keys out of tool configuration files and into user environment storage.
- Added the Hyaloria `kimi-k3` preset and generic custom-provider options.
- Added atomic writes, timestamped backups, rollback, dry-run, strict configuration checks, and model verification.
- Added automated tests for preservation, secret isolation, idempotence, backup, rollback, and dry-run behavior.
- Corrected repository URLs and removed obsolete v1 instructions.

## 1.0.0

- Initial Bash implementation.
