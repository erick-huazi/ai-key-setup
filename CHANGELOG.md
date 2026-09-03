# Changelog

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
