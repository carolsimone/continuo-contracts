# Test vectors

How do I check a port or a code change against the test vectors?

The wheel ships seven JSON files in `continuo_contracts/vectors/`. Each is a list of
[test vectors](glossary.md#test-vector): an input and the output one function gives for it. A
[port](glossary.md#port), or a change to this package, passes when it gives every output.

## The files

- `canonical_json.json` fixes `canonical_json`: `value` gives `canonical`, a text to compare as
  UTF-8. In `value`, a number with a point or an exponent (`1e21`, `1E30`) is a double, and every
  integer lies within ±(2**53 - 1). A case with `value_text` and `error: true` has no `value`:
  read the text as JSON, and `canonical_json` must refuse the result.
- `hashing.json` fixes the [folds](hashing.md) `fold3` and `fold4`, `shared_fold` and
  `shared_hash`: `function` and `inputs` give `expected`. `inputs` holds the arguments by name:
  `source`, `shared`, `config` and `compiled` for a fold, `unit_digests` for `shared_fold`, and
  `units_hex` for `shared_hash`, each unit's bytes as lowercase hex. With `error: true` instead,
  refuse `inputs`.
- `projection.json` fixes `code_projection`: `node` gives `projection` and `projection_hash`. A
  `node` leaves out the fields that take their [default](hashing.md#projection_hash).
- `sql_types.json` fixes `canonical_sql_type`: `raw` gives `canonical`, and `null` means refuse it.
- `strict_yaml.json` fixes `yaml_strict.load`: `text` gives `value`. With `error: true`, refuse it.
- `contracts.json` fixes a Python contract, through `yaml_strict.load`, `validate_python_contract`
  and `normalise_python`: `text`, `path`, `service` and `reads` give `stored` and `contract_hash`.
  A case with `issues` lists the `{path, reason}` of each issue, in order, instead. A text that the
  strict reader refuses gives the one issue `$`, `strict_yaml`.
- `dbt_contracts.json` fixes a dbt contract: `node`, `tests`, `manifest` and `reads` give
  `generated` (from `dbt.generate`), `flags`, `source_sha256`, `stored` (from `normalise_dbt`) and
  `contract_hash`. A case with `issues` gives `source_sha256` and the issues instead.

## Read a vector

```python
>>> import json
>>> from importlib import resources
>>> from continuo_contracts.python import canonical_sql_type
>>> path = resources.files("continuo_contracts") / "vectors" / "sql_types.json"
>>> case = json.loads(path.read_text(encoding="utf-8"))[1]
>>> case, canonical_sql_type(case["raw"])
({'raw': 'int', 'canonical': 'INTEGER'}, 'INTEGER')

```

## Check a port

Compute each case's output from its input fields and compare it, types included (`1`, `1.0` and
`true` differ). Three groups of cases catch a skipped rule:

- Keys sort by UTF-16 code unit in `canonical_json`, and by code point in `shared_fold`,
  `shared_hash` and the projection's `assets`. The cases with `U+FF5E` and `U+1F600` show it.
- `nesting_101_refused` needs a JSON reader that accepts at least 101 levels, so that
  `canonical_json` and not the reader refuses it. Read `value_text` with integers kept exact, or an
  integer above 2**53 - 1 is not refused.
- A dbt node's `columns` object keeps the order in which the node declares its columns, and the
  contract's columns follow it. Read that object with its order kept.

## Change a vector

The rule for the six files other than `dbt_contracts.json` is that they never change. The
procedure for `dbt_contracts.json` is `uv run python scripts/write_dbt_goldens.py`, which writes it
from the fixture manifests. `tests/test_dbt_vectors.py` fixes the SHA-256 of all seven files, so a
changed case also needs a deliberate edit of that test. [Stability](stability.md) says when a
change may move a hash.
