# Contributing

How do I set up, test and change the package? It has one maintainer, so open an issue before a
large pull request. Report a vulnerability as [SECURITY.md](SECURITY.md) says.

## Licence and sign-off

The Apache License 2.0 covers every file, so a new file gets no licence header. Each commit needs
a `Signed-off-by` line, your statement under the [Developer Certificate of Origin](DCO).
`git commit -s` adds it, and the `dco` CI check fails a pull request without it.

## Set up and checks

You need Python 3.14 and [uv](https://docs.astral.sh/uv/). `uv sync` installs the package and the
`dev` group. CI runs these checks, and you run them before a pull request:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest -q --cov --cov-report=term-missing
```

- **Coverage is 100 percent**, lines and branches, except the `if __name__ == "__main__":` guard.
  Do not use `# pragma: no cover`: reach the line through the public functions, or delete it.
- **`dbt` tests** rebuild the fixture manifests with the pinned dbt through `uvx`, which needs the
  network, so the default run skips them. After a change to `tests/fixtures/dbt/projects` or
  `scripts/build_dbt_fixtures.py`, run `uv run pytest -m dbt`.
- **Branches and titles.** A branch is `<type>/<topic>`, `<type>` being `feat`, `fix`, `docs`, `ci`
  or `chore`. A pull request title is a Conventional Commits subject, such as `fix(ci): <what>`.
  No CI check reads titles or commit subjects: `dco` reads only the sign-off.

## Code layout

- The modules and their layers are in [how it works](docs/how-it-works.md#modules-and-layers).
- No module imports `scripts` or `tests`, which `tests/test_architecture.py` checks. No module
  imports dbt-core (the `dbt` package): `cli` runs `dbt` as a process.
- `lint-imports` enforces the layers and the confinements of `[tool.importlinter]`. A confinement
  forbids a library in the modules its `source_modules` lists, every module but those that use it.
- A new module goes into a layer, into `source_modules` of each confinement whose library it does
  not use, and into `REQUIRED_MEMBERS` of `scripts/check_wheel.py`: the tests fail until then.

## Where to add a validation rule

A rule that would refuse a contract an earlier version accepted is not added, except the
exceptions listed in [stability](docs/stability.md), each of which needs the owner's decision and an
entry there. So a rule checks what a version adds: 0.3.0 added the dbt rules together with dbt
contracts.

1. In `continuo_contracts/validate.py`, add a function to `_RULES` (run by `rule_issues`, Python)
   or `_DBT_RULES` (run by `dbt_rule_issues`, dbt). A new reason extends `REASONS`, which
   `tests/test_validate.py` lists in full.
2. Test it in `tests/test_validate.py`, `tests/test_validate_kinds.py` (rules that depend on the
   node's kind) or `tests/test_validate_dbt.py`. Describe it in [validation](docs/validation.md).
3. A dbt rule gets an issues case: a node in `tests/fixtures/dbt/projects/cases`, then
   `uv run python scripts/build_dbt_fixtures.py cases`. Name the case in `ISSUES_CASES` of
   `scripts/write_dbt_goldens.py` and in `ISSUES` of `tests/test_dbt_vectors.py`.
4. Run `uv run python scripts/write_dbt_goldens.py`, which rewrites the golden contracts and
   `dbt_contracts.json`. Set `DBT_CONTRACTS` in `tests/test_dbt_vectors.py` to the hex digest that
   `shasum -a 256 continuo_contracts/vectors/dbt_contracts.json` prints.

## Conventions

- **Logging.** Use the standard `logging` module, to stderr, never `print`. The library modules
  log nothing, and `cli` configures logging in `main`. A value a machine reads on stdout, such as
  the JSON `cli` prints, stays a `print` with a `# noqa: T201` that says why.
- **Changelog.** A behaviour change adds a line to `## [Unreleased]` in [CHANGELOG](CHANGELOG.md).
- **Fingerprints.** Edit `continuo_contracts/fingerprints.yaml`, never `docs/fingerprints.md`, then
  run `uv run python scripts/render_fingerprints.py`. A test fails until you do.
- **Hash stability.** A change that moves a hash the vectors or the tests fix ships only with a
  [re-baseline](docs/glossary.md#re-baseline). That runs in continuo, outside this package: say in
  the pull request that the change needs one.
- **Documentation.** One page per topic in `docs/`, opened by the question of its README row,
  except a generated page such as `docs/fingerprints.md`. Examples are doctests that `pytest` runs
  (end a fenced block with a blank line), and `tests/test_docs_links.py` checks every link.

## Releasing

1. Commit `chore(release): continuo-contracts X.Y.Z`: run `uv version X.Y.Z`, which rewrites
   `pyproject.toml` and `uv.lock` (commit both), and turn `## [Unreleased]` in `CHANGELOG.md` into
   `## [X.Y.Z] - YYYY-MM-DD` with a **TL;DR**.
2. Push the tag `vX.Y.Z-test1` on that commit: a rehearsal on TestPyPI.
3. Push the tag `vX.Y.Z` on the same commit. The pipeline tests and builds once, then the
   `publish` job runs in the `pypi` environment and uploads those files to PyPI.
4. `release.yml` makes the GitHub Release, with the changelog section as its notes.

`publish-pypi.yml` runs three jobs. `prepare` checks the tag, tests and builds the files once, and
`publish` uploads them. `verify-index` downloads the published wheel, compares it with the built
one and runs a smoke test.

PyPI and TestPyPI never replace a file. When a run fails:

| Fails at | On PyPI | Recover |
|---|---|---|
| `prepare` | nothing | fix, `git push --delete origin vX.Y.Z`, tag again |
| `publish` | none or some files | none: fix, re-run, then `release.yml`. Some: yank, next patch |
| `verify-index` | published | index lag: re-run, then `release.yml`. A defect: yank, fix forward |
| `release.yml` | published | re-run it (it waits 30 minutes for `publish-pypi.yml`) |
