# Errors

Which exceptions does the package raise, and when? Every error the package defines subclasses
`errors.ContractsError`, a `ValueError`, so one `except ContractsError` catches them all.

## The package's errors

| Exception | When | The message starts with |
|---|---|---|
| `CanonicalJsonError` | a value [canonical JSON](hashing.md) refuses | the JSON path, `$.a[0]` |
| `HashInputError` | a bad shared unit, `service` or `reads` | the key, service or entry |
| `ProjectionError` | an unsupported SQL type or Python node | `<schema>.<table>:`, or nothing |
| `DbtNodeError` | a manifest field the library cannot read | the node or test id, or `manifest` |
| `FingerprintsFileError` | a malformed `fingerprints.yaml` | the place in the file |
| `SchemaFileError` | the packaged ODCS schema has other bytes: reinstall | the file |
| `StrictYamlError` | YAML that the strict reader refuses | `line <n>, column <n>:` |

- `HashInputError`: a shared unit key that is empty, holds NUL or LF, or that UTF-8 cannot encode.
  Also a digest that is not 64 lowercase hex characters, a blank `service`, `reads` given as one
  string, or a `reads` entry that is not `<schema>.<table>`.
- `ProjectionError`: it starts with `<schema>.<table>: ` from `code_projection`, `projection_hash`,
  and a `from_mapping` entry (also reached through `node_from_odcs`) whose `reads`, `config`,
  columns, `primary_key` or `assets` is the wrong container. It has no prefix from
  `canonical_sql_type`, from an entry that is not a mapping or lacks `schema` or `table`, or from
  `node_from_odcs`'s own checks (schema object, id, columns).

## Problems that are not raised

The `validate` functions return [`Issue` lists](validation.md), and the `validate` and `hash`
commands turn a `StrictYamlError` into the one issue `strict_yaml` at `$`. `lite.check_manifest`
turns a `DbtNodeError` from a node's field, or a `HashInputError` from a declared read, into an
`error` line of that node. A field of the manifest's `metadata` or `nodes`, or a test's
`attached_node` or `depends_on.nodes`, still raises `DbtNodeError`. `dbt check` then prints
`the manifest is not the shape of a dbt manifest` and exits 2.

## Python's own errors

| Exception | Raised by | When |
|---|---|---|
| `ValueError` | `Fingerprints.entries`, `classify`, `unclassified` | an unknown section or family |
| `ValueError` | `dbt.read_tree` | an unknown part, or `test` without a test node or the reverse |
| `UnicodeEncodeError` | `hashing.fold3`, `fold4` | a part UTF-8 cannot encode (a lone surrogate) |
| `TypeError`, `AttributeError` | any function | an argument of the wrong type |
