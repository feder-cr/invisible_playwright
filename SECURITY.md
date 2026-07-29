# Security Policy

## Disclaimer

This is an educational project. It is provided as-is, with no warranties. The maintainers take no responsibility for how it is used. Use it at your own risk and in compliance with the laws of your jurisdiction.

## Supported versions

Only the latest release on `main` receives fixes.

| Version | Supported |
|---------|-----------|
| latest  | ✅        |
| older   | ❌        |

## Reporting a vulnerability

Do not report security issues via public GitHub issues, discussions, or pull requests.

Send a report to `federico.elia.majo@gmail.com` with subject prefix `[security][invisible_playwright]`.

Include:

- What the issue is and what it affects
- Steps to reproduce
- Version of `invisible_playwright` and OS
- Fix suggestion if you have one

## Scope

In scope:

- The Python wrapper (this repo)
- The binary download and verification flow
- The CLI

Out of scope:

- Vulnerabilities in the patched Firefox source - report to [feder-cr/firefox_antidetect_patch](https://github.com/feder-cr/firefox_antidetect_patch/security/advisories/new)
- Upstream Firefox / mozilla-central - report to Mozilla directly
- Third-party dependencies - report to those projects

Not security issues:

- The browser being detected by an anti-bot service - open a regular issue
- Social engineering
- DoS requiring physical or local privileged access
