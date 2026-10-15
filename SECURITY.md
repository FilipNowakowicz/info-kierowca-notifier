# Security policy

## Supported versions

Only the [latest release](../../releases/latest) receives fixes.

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately through
[GitHub's private vulnerability reporting](../../security/advisories/new) instead.

Include what's affected, how to reproduce it, and the impact. This project is maintained on a
best-effort basis, so there's no guaranteed response time, but security reports take priority.

## Scope

In scope — anything in this repository that could expose or misuse:
- Profil Zaufany credentials (stored in the OS credential vault) or info-kierowca.pl session cookies
- the PKK number or other personal data
- the local dashboard (`127.0.0.1:8787`), e.g. CSRF or DNS rebinding
- the booking automation acting on a reservation the user didn't intend

Out of scope:
- vulnerabilities in info-kierowca.pl, Profil Zaufany, or mObywatel themselves — report those to
  their operators or to [CERT Polska](https://cert.pl)
- issues that require an attacker to already control your computer or user account
