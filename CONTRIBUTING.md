# Contributing to continuo-contracts

Thanks for your interest. This repo is maintained by one person, so please open an issue
before a large pull request. The package fixes the bytes that continuo hashes, so a pull
request that changes any of them is painful to land no matter how good the code is.

## Licensing and sign-off

This project is licensed under the Apache License 2.0. Contributions are accepted under
the same license.

We use the [Developer Certificate of Origin](DCO) — a short statement that you wrote the
code, or otherwise have the right to submit it. You agree to it by adding a sign-off line
to each commit:

```
Signed-off-by: Your Name <your.email@example.com>
```

`git commit -s` adds this for you. To sign off a branch you already wrote:

```bash
git rebase --signoff origin/main
git push --force-with-lease
```

A CI check enforces this on every pull request. There is no separate agreement to sign.

We do **not** use per-file license headers. The root `LICENSE` covers the whole
repository; please do not add headers to new files.

## Development setup

Prerequisites: Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

`uv sync` installs the package and the `dev` group (pytest, ruff, mypy and the PyYAML type
stubs) into `.venv`.

## Before you open a pull request

```bash
uv run ruff check .
uv run mypy continuo_contracts scripts
uv run pytest
```

These are the checks `.github/workflows/ci.yml` runs on Python 3.12, 3.13 and 3.14.

## Conventions

- **Changelog.** A pull request that changes behaviour adds a line under
  `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md), Keep a Changelog style.
- **Python logging.** Use the standard `logging` module for diagnostic output, written to
  stderr, never `print`. The library modules log nothing. The only exception is a
  machine-read stdout value, which stays an explicit `print`.
- **Fingerprint declaration.** Change `continuo_contracts/fingerprints.yaml`, never
  `docs/fingerprints.md`; then run `uv run python scripts/render_fingerprints.py`. The test
  `test_the_docs_are_the_rendered_file` fails until you do.
- **No per-file license headers.** See above.
- **Hash stability.** A change that moves any hash pinned in `continuo_contracts/vectors/`
  or in the tests moves `content_hash` for every node of every install. It ships only
  together with a re-baseline in continuo, so say so in the pull request.

## Reporting security issues

Please don't open a public issue — see [SECURITY.md](SECURITY.md).
