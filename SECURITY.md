# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| Latest  | :white_check_mark: |

We provide security updates for the latest released version on the `master` branch.

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, please email **chanmeng.dev@gmail.com** with:

- A description of the vulnerability
- Steps to reproduce or a proof of concept
- The potential impact
- A suggested fix, if you have one

## Response Timeline

- **Acknowledgment**: within 48 hours of receiving your report
- **Assessment**: within 7 days we will confirm the issue and assess severity
- **Resolution**: we aim to release a fix within 30 days for confirmed vulnerabilities

## Scope

Security concerns most likely to apply to this project include:

- Injection or insecure handling of user-supplied input
- Exposure or leakage of sensitive data
- Dependencies with known vulnerabilities

## Published advisories

| Advisory | Severity | Affected | Fixed | Summary |
|---|---|---|---|---|
| [GHSA-4fj9-49x6-vgjp](https://github.com/ChanMeng666/echook/security/advisories/GHSA-4fj9-49x6-vgjp) | High (CWE-78) | ≤ 7.2.3, Windows and WSL only | [7.2.4](https://github.com/ChanMeng666/echook/releases/tag/v7.2.4) | PowerShell command injection through typographic quotes in the desktop notification, text to speech and audio path |

Advisories are published on the repository's Security tab once a fixed release is out.

## Attribution

We appreciate responsible disclosure. With your permission, contributors who report valid security
issues will be acknowledged in the project. A reporter who does not ask for credit is not named
until they say they want to be.
