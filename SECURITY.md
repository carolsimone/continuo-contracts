# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately through GitHub's
[private vulnerability reporting](https://github.com/carolsimone/continuo-contracts/security/advisories/new).
If you cannot use that form, email simone@continuo-data.com instead. Include enough detail to
reproduce the problem: affected component, version or commit, and the steps or
input that trigger it. A proof of concept helps but is not required.

What to expect:

| Stage | Target |
| --- | --- |
| Acknowledgement of your report | 3 working days |
| Initial assessment and severity | 10 working days |
| Fix or documented mitigation | depends on severity, communicated in the assessment |

You will be credited in the release notes for the fix unless you prefer to stay
anonymous. Please give us a chance to ship a fix before disclosing publicly.

## Supported versions

This project is pre-1.0. Only the latest release receives security fixes; there are
no backports to earlier tags.

| Version | Supported |
| --- | --- |
| Latest release | Yes |
| Everything earlier | No |

## Scope

In scope: a defect in this package that breaks what a hash is for.

- Two nodes that differ in what they compute and get the same hash (spellings the README says
  hash alike are not defects).
- Output that differs between supported Python versions or platforms for the same input.

Out of scope:

- Findings that need an already-compromised producer CI or continuo install.
- Vulnerabilities in dependencies with no reachable call path from this code.
