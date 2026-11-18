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

Prerequisites: Python 3.14 or later and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

`uv sync` installs the package and the `dev` group (pytest, pytest-cov, ruff, mypy, import-linter
and the PyYAML and jsonschema type stubs) into `.venv`.

## Before you open a pull request

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest -q --cov --cov-report=term-missing
```

These are the checks `.github/workflows/ci.yml` runs on Python 3.14.

The test run measures line and branch coverage of `continuo_contracts` and fails under 100%
(`fail_under` in `pyproject.toml`); the report lists each line and branch that no test reaches. The
only exclusion is the `if __name__ == "__main__":` guard, and `# pragma: no cover` is not used: reach
a line with a test through the public functions, or delete it when no input can reach it.

## Conventions

- **Changelog.** A pull request that changes behaviour adds a line under
  `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md), Keep a Changelog style.
- **Python logging.** Use the standard `logging` module for diagnostic output, written to
  stderr, never `print`. The library modules log nothing, and `cli` logs from `main`, not at
  import. The only exception is a machine-read stdout value, such as the JSON or the hash that
  `cli` prints, which stays an explicit `print` with a `# noqa: T201` that says why.
- **Formatting and lint.** `uv run ruff format .` formats the code at 82 columns, and
  `ruff check` enforces the rules `pyproject.toml` selects. A docstring follows the numpy
  convention: a one-line summary, a blank line, then the description. A test needs no
  docstring.
- **Fingerprint declaration.** Change `continuo_contracts/fingerprints.yaml`, never
  `docs/fingerprints.md`; then run `uv run python scripts/render_fingerprints.py`. The test
  `test_the_docs_are_the_rendered_file` fails until you do.
- **dbt fixtures.** A change to `tests/fixtures/dbt/projects` or `scripts/build_dbt_fixtures.py`
  needs `uv run pytest -m dbt` by hand, which rebuilds the manifests with the pinned dbt; the
  default run and CI leave those tests out.
- **No per-file license headers.** See above.
- **Hash stability.** A change that moves any hash pinned in `continuo_contracts/vectors/`
  or in the tests moves `content_hash` for every node of every install. It ships only
  together with a re-baseline in continuo, so say so in the pull request. A re-baseline is a
  continuo release of each service, run once after such a change, that recomputes every node's
  stored hash with the new code and validates and reloads nothing.

## Structure

The package is flat on purpose: it is a small library of pure functions that produce exact bytes,
not a service, and layers would add indirection without a second implementation.

- `errors`: `ContractsError` and the errors the other modules raise.
- `canonical`: RFC 8785 canonical JSON and the text normaliser.
- `hashing`: `sha256_hex`, the `content_hash` folds and the shared fold.
- `python`: the Python node, its code projection and the SQL type canonicaliser.
- `fingerprints`: the field declaration, its YAML loader and its Markdown renderer.
- `schema`: the packaged ODCS v3.2.0 JSON schema and the check of its bytes.
- `yaml_strict`: the reader of a contract file, YAML 1.2 core with no aliases, merge keys or
  repeated keys.
- `dbt`: the gates of a dbt manifest node, the names a kept node gets and the join of its tests to
  it; `generate`, which writes the ODCS contract of a kept node with the flags it carries and the
  SHA-256 of the manifest fields it read, which `read_tree` names; `logical_type`, the ODCS
  logical type of a declared column type; `run_facts`, which reads back from a contract what
  continuo runs the node with; and `seed_header_issues`, the comparison of a seed's CSV header
  with its declared columns.
- `vocabulary`: the custom properties of the `continuo` vendor and where each may sit.
- `validate`: the check of a Python node's or a dbt node's ODCS contract, the JSON schema and
  continuo's rules, with every problem as an `Issue` that has a path and a closed reason.
- `contract`: the stored form of a Python node's or a dbt node's ODCS contract, in the canonical
  form, and its `contract_hash`.
- `lite`: the producer's lite check of a dbt manifest, the report of the nodes it skips, the
  problems of each contract it generates and the seeds whose CSV header and declared columns
  differ.
- `cli`: the `continuo-contracts` command: `validate` and `hash` over Python contract files, and
  `dbt check`, which runs `dbt parse` and prints the lite check's report.

`errors` and `vocabulary` import nothing from the package. `canonical`, `hashing`, `fingerprints`
and `schema` import only `errors` (`fingerprints` also PyYAML), `yaml_strict` imports `canonical`,
`errors` and PyYAML, `dbt` imports `canonical`, `errors`, `hashing` and `vocabulary`, `python`
imports `canonical`, `errors`, `hashing`, `fingerprints` and `vocabulary`, `validate` imports
`canonical`, `contract`, `dbt`, `errors`, `python`, `schema`, `vocabulary` and jsonschema,
`contract` imports `canonical`, `errors`, `fingerprints`, `hashing`, `python` and `vocabulary`,
`lite` imports `contract`, `dbt`, `errors` and `validate`, `cli` imports `contract`, `errors`,
`lite`, `validate` and `yaml_strict`, nothing but `cli` imports `lite`, and nothing imports
`scripts` or `tests`. The only files the package reads are the packaged `fingerprints.yaml`, in
`fingerprints.load()`, the packaged ODCS schema, in `schema.load()`, and the files `cli` reads: the
contract files that its arguments name and, for `dbt check`, the `profiles.yml` of the profiles
folder, the manifest (the file `--manifest` names, or the `manifest.json` that `dbt parse` writes
in a temporary folder that `cli` makes and removes), the project's `dbt_project.yml` and the CSV
file of each seed. A CSV file is read only when it is a regular file whose real path, its
symbolic links resolved, lies in a folder that `seed-paths` of `dbt_project.yml` names (`seeds`
by default), and only its first MiB, so the manifest, which may come from another job, cannot
choose another file for `cli` to read. The only program
the package runs is the `dbt` executable that `dbt check` is given, as a new process: dbt runs on
its own Python, which is not the one this package needs, so no module imports dbt, and `cli` is
the only module that imports `subprocess`. A split into layers is proposed only when a module gets
a second reason to change.

import-linter (`uv run lint-imports`) enforces the ordering of these modules and the confinement of
the libraries, with the contracts under `[tool.importlinter]` in `pyproject.toml` and no
`ignore_imports` on any of them. The lists of imports above describe the graph and are not checked
edge by edge: the layers allow, for example, `canonical` to import `vocabulary` and `cli` to import
`hashing`.

- `import-graph` puts the modules in layers (an ordering of the flat modules, not a split of the
  package), top to bottom: `cli`, then `lite`, then `validate`, then `contract`, then `python`,
  `yaml_strict` and `dbt`, then `canonical`, `hashing`, `schema` and `fingerprints`, then `errors`
  and `vocabulary`. A module imports only from the layers below its own, and the modules of one
  layer import none of each other, so nothing imports `cli`, nothing but `cli` imports `lite`, and
  `errors` and `vocabulary` import nothing from the package. The contract is exhaustive: a module
  that no layer names fails it.
- `jsonschema-is-confined-to-validate` and `yaml-is-confined-to-fingerprints-and-yaml-strict` keep
  each library to the modules that use it, `only-cli-logs` keeps `logging` to `cli`, so the library
  modules log nothing, and `subprocess-is-confined-to-cli` keeps `subprocess` to `cli`, so the
  library modules start no process.

`tests/test_architecture.py` holds the contracts to the code. It fails on an `ignore_imports`, on a
module that a layer or a confinement does not name, on an import of `scripts` or `tests`, which sit
outside the package, and on an import of `cli`, `lite`, `logging`, `subprocess`, `jsonschema` or
`yaml` from a file that may not make it. That last check covers `__init__.py`, which no contract
can name without naming every module. The test also plants an import that breaks each contract in
a copy of the package and checks that `lint-imports` fails.

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
