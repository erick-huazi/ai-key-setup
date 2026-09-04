# Security Policy

## Credentials

Never include API keys, tokens, cookies, or populated `.env` files in commits, issues, screenshots, or logs.

If a credential is exposed, revoke it at the provider immediately and create a replacement. Removing it from the latest file is not enough because Git history may retain it.

`ai-key-setup` deliberately writes only an environment variable name to Codex `config.toml`. Windows user environment variables remain readable by programs running as the same Windows user. On macOS and Linux, the managed environment file is restricted to mode `600`, but processes running as the same user may still access the value.

Static `Authorization`, `X-API-Key`, `API-Key`, and `Proxy-Authorization` values in a target provider's `http_headers` are rejected. Use `env_key` or Codex `env_http_headers` instead.

Configuration backups are exact copies. If the source config already contains a credential, its backup will contain that credential too. Rotate the credential before cleaning affected files and backups.

## Network verification

- Remote provider URLs require HTTPS by default.
- Credentials embedded in URLs, URL query strings, and fragments are rejected.
- Cross-origin redirects are blocked before an authorization header can be forwarded.
- Verification responses are size-limited and error output is redacted.
- `--verify-mode responses` and `both` perform a real model request and may incur a small charge.

## Configuration integrity

Writes are atomic and guarded against concurrent edits. Provider network verification happens before configuration changes. If Codex validation or environment persistence fails after a candidate config is written, the original bytes are restored automatically.

The local Codex schema check runs from an isolated temporary working directory with credential-like and proxy environment variables removed. This prevents an unrelated project-local Codex configuration from affecting the result. Provider credentials are used only by the tool's direct HTTPS verification request and by the final environment persistence step.

## Reporting a vulnerability

Prefer a private GitHub Security Advisory for security-sensitive reports. Never include real credentials or private configuration. If private reporting is unavailable, open a sanitized issue containing only synthetic examples.

## Supported version

Security fixes are applied to the latest release on the default branch.
