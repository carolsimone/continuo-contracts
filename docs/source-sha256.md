# source_sha256

How is `source_sha256` of a dbt node built? It is the hash of the [manifest](glossary.md#manifest)
fields that `dbt.generate` reads to write a [node](glossary.md#node)'s
[contract](glossary.md#contract), so it moves only when one of them does.

## Hash input

`source_sha256` is the SHA-256 of the [canonical JSON](hashing.md#canonical-json) of
`{"node": ..., "tests": ..., "targets": ...}`, as 64 lowercase hex digits with no `sha256:` prefix:

- `node`: the fields of the node that the node tree selects.
- `tests`: the fields each test's tree selects, sorted by canonical JSON bytes, repeats kept. A
  [test](glossary.md#dbt-words) of the node is an entry of the manifest's `nodes` with
  `resource_type: test` and `config.enabled` not `false`. Its `attached_node` is the node's manifest
  key, or it has none (missing, null, `""`) and lists that key in `depends_on.nodes`.
- `targets`: what the node's `relationships` tests point to, [below](#relationship-targets).

## Read trees

`dbt.read_tree` gives each tree. A path is dotted, and `a.{b c}` stands for `a.b` and `a.c`. `*`
selects every entry (a mapping as `[key, value]` pairs in its order, a list as all its items), and
`0` the first item of a list. The paths of a part merge into one tree, so `config.meta.owner` and
`config.materialized` share the branch `config`.

```text
node:    resource_type name version schema alias fqn original_file_path description tags
         deprecation_date primary_key  columns.*.{name description data_type tags
         meta.continuo.classification}  config.meta.{owner criticality timeout_minutes}
         config.meta.continuo.{readers reads tests.*.{name enabled granularity params}}
         config.{materialized unique_key incremental_strategy on_schema_change contract.enforced
         access group freshness.build_after.{count period} grants strategy updated_at check_cols
         hard_deletes snapshot_meta_column_names dbt_valid_to_current quote_columns column_types}
test:    name column_name attached_node depends_on.nodes test_metadata.{name namespace}
         description tags config.{severity where warn_if error_if fail_calc}
nodes:   schema name version
sources: schema identifier name
```

A test tree also selects, for each test:

1. `test_metadata.kwargs.<k>` for each kwarg `k`, except `model`.
2. Except, too, a `column_name` kwarg equal to the test's own `column_name` (null when missing).
   Both are strings or null, or `generate` raises `DbtNodeError`, so equal means the same string
   or both null. With no kwarg left, `kwargs` is not selected.
3. `depends_on.macros.0` when `test_metadata.name` is `not_null`, `unique`, `accepted_values` or
   `relationships` and the namespace is missing, null or `""`.
4. `refs.*.name`, `refs.*.package` and `refs.*.version` for `relationships` with the namespace
   missing, null, `""` or `dbt`.

```text
select(value, tree):   # in the source's order; a missing key is left out, a null kept
  if value is a mapping and tree is not {}:
    if "*" in tree: return [[k, select(v, tree["*"])] for each k, v in value]
    return {k: select(value[k], sub) for each k, sub in tree if k is a key of value}
  if value is a list and "*" in tree: return [select(item, tree["*"]) for each item]
  if value is a list and "0" in tree: return [select(value[0], tree["0"])], or [] if empty
  return value                  # whole: a leaf, or not the shape the tree expects
```

## Relationship targets

`targets` maps a manifest key, such as `model.shop.customers`, to the fields the `nodes` tree
selects for a node or the `sources` tree for a source. A test adds a target when all four hold:

1. It is dbt's `relationships`: namespace `dbt`, or no namespace and `depends_on.macros[0]` is
   `macro.dbt.test_relationships`.
2. It is strict: `config.severity` null or `error` in any case, and `where`, `warn_if`,
   `error_if`, `fail_calc` each null, `""` or dbt's default (`!= 0`, `!= 0`, `count(*)`).
3. Its column is declared: a node column has that `name`, or the name without one pair of `"`,
   `` ` `` or `[]` around it. The column is a non-empty `column_name` kwarg, else the test's own.
4. `attached_node` is set, and `depends_on.nodes` holds exactly one distinct key besides it. That
   key is looked up in the manifest's `nodes`, then in its `sources`.

A test that fails 1, 2 or 3 adds no target and raises nothing here. When 1 to 3 hold:

| Case | Result |
|---|---|
| The `field` kwarg is missing or not a non-empty string | `DbtNodeError` |
| `attached_node` is missing, null or `""`, or no key besides it | no target |
| Two or more distinct keys besides `attached_node` | `DbtNodeError` |
| The key is in neither `nodes` nor `sources` | `DbtNodeError` |

With no key besides `attached_node`, the contract's relationship points at the tested node when two
or more `refs` name it, and `targets` gains no entry. A ref names it when its `name` is the node's.
A set `package` must equal the node's first `fqn` entry, `_` written `-` on both, and a set
`version` the node's `version` as Python's `str` writes both.

```python
>>> import hashlib
>>> from continuo_contracts import canonical, dbt
>>> read = {"resource_type": "model", "name": "t", "schema": "a", "original_file_path": "m/t.sql",
...     "config": {"materialized": "table", "meta": {"owner": "team"}}}
>>> node = read | {"unique_id": "model.p.t", "created_at": 1.5, "columns": {"id": {"name": "id"}}}
>>> read["columns"] = [["id", {"name": "id"}]]
>>> dbt.generate(node, [], manifest={}).source_sha256 == hashlib.sha256(
...     canonical.canonical_json({"node": read, "tests": [], "targets": {}})).hexdigest()
True

```
