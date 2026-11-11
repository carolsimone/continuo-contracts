# Contributing to continuo-contracts

Thanks for your interest. This repo is maintained by one person, so please open an issue
before a large pull request. The package fixes the bytes that continuo hashes, so a pull
request that changes any of them is painful to land no matter how good the code is.

## Licensing and sign-off

This project is licensed under the Apache License 2.0. Contributions are accepted under
the same license.

We use the [Developer Certificate of Origin](DCO): a short statement that you wrote the
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
  together with a re-baseline in continuo, so say so in the pull request. A re-baseline is a
  continuo release of each service, run once after such a change, that recomputes every node's
  stored hash with the new code and validates and reloads nothing.

## Releasing

A release is one commit and one tag, and the tag publishes, so only the owner releases.

1. Make one commit, `chore(release): continuo-contracts X.Y.Z`, that sets the version with
   `uv version X.Y.Z` (it rewrites `pyproject.toml` and `uv.lock`, so commit both) and turns
   `## [Unreleased]` in `CHANGELOG.md` into `## [X.Y.Z] - YYYY-MM-DD`. The section opens with a
   **TL;DR**.
2. Rehearse first. Push the tag `vX.Y.Z-test1` on that commit. The whole pipeline runs against
   TestPyPI instead of PyPI, under the version `X.Y.Z.dev<N>` (`<N>` is the run number times 100
   plus the run attempt, because TestPyPI never replaces a file), and no GitHub Release is made.
   The tag must name the version in `pyproject.toml` at that commit, so the rehearsal runs on the
   release commit itself.
3. Push the tag `vX.Y.Z` on the same commit. `publish-pypi.yml` tests the package, builds it once,
   uploads those files to PyPI and downloads the wheel back from the index to check it byte for
   byte. Then `release.yml` makes the GitHub Release, with the version's `CHANGELOG.md` section as
   its notes.

A tag has one of two shapes. `vX.Y.Z`, with digits only, is a release. `vX.Y.Z-test<anything>`,
with `-test` in any case (`v1.2.3-test1`, `v1.2.3-TEST-again`), is a rehearsal. Any other `v*` tag,
such as `v1.2.3rc1`, `v1.2.3-rc1` or `v1.2`, fails in `prepare` before anything is built, so
nothing reaches an index and no GitHub Release is made.

A dry run builds and checks the package from a branch and publishes nothing; a manual run can
never publish:

```bash
gh workflow run publish-pypi.yml --ref main -f dry_run=true
```

A release that carries a change to a pinned hash (see **Hash stability** under Conventions) says
so in its TL;DR, and its tag is pushed only together with the re-baseline in continuo.

When a run fails, the table says what exists and how to recover. PyPI never lets a published
file be replaced, so a real defect in a published release is fixed with a new version.

| Fails at | PyPI | GitHub Release | Recover |
|---|---|---|---|
| `prepare` | nothing | none | fix, delete the tag (`git push --delete origin vX.Y.Z`), tag again |
| `publish` | maybe the files | none | nothing on the index: re-run the failed job once the cause is fixed, then re-run `release.yml`. Some but not all files on the index: this pipeline cannot complete that version (a file that is already on the index fails the upload); yank the version on pypi.org and release the next patch version |
| `verify-index` | published | none | index lag: re-run the failed jobs, then re-run `release.yml`; a real defect: yank the release on pypi.org and fix forward with a new version |
| `release.yml` | published | missing | re-run it; it updates an existing Release |

`release.yml` waits up to 30 minutes for `publish-pypi.yml`, and that time includes any wait for an
approval of the `pypi` environment. If it times out or fails early, re-run it once
`publish-pypi.yml` has succeeded.

**Trusted publishers.** The PyPI and TestPyPI projects each trust the workflow
`publish-pypi.yml`, environment `pypi` or `testpypi`, of `carolsimone/continuo-contracts`. Only
the owner can change that, on pypi.org and test.pypi.org. Renaming the workflow file or either
environment breaks publishing.

## Reporting security issues

Please don't open a public issue; see [SECURITY.md](SECURITY.md).
