# Security Policy

## Credentials

Never include API keys, tokens, cookies, or populated `.env` files in commits, issues, screenshots, or logs.

If a credential is exposed, revoke it at the provider immediately and create a replacement. Removing it from the latest file is not enough because Git history may retain it.

## Reporting a vulnerability

Report code vulnerabilities through a GitHub issue without including real credentials or private configuration. If the report itself contains sensitive material, first remove or replace that material with synthetic examples.

## Supported version

Security fixes are applied to the latest release on the default branch.
