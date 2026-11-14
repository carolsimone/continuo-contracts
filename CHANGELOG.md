# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `continuo_contracts.schema`: the ODCS v3.2.0 JSON schema ships in the wheel, and `load()` returns
  it after it checks that the file has the pinned SHA-256 (`SchemaFileError` otherwise).
- `jsonschema>=4.26` is a runtime dependency, next to PyYAML.
- `continuo_contracts.yaml_strict`: `load()` reads a contract file as YAML 1.2 core, so `on`,
  `yes`, `NO`, `2026-11-14`, `1:20` and `1_000` are strings. It refuses aliases, merge keys,
  repeated keys, keys that are not strings, tags other than the core ones, more than one
  document, nesting deeper than `canonical.MAX_DEPTH` and values that `canonical_json` cannot
  write, with a `StrictYamlError` that names the line and the column. The new vector file
  `vectors/strict_yaml.json` pins it.
- `continuo_contracts.vocabulary`: `PROPERTIES` lists the custom properties of the `continuo`
  vendor with the object of a contract each sits on (`root`, `schema` or `quality`) and who may
  write it in a Python contract (`authored`, `filled` or `refused`). `continuo_values()` returns
  the values of the `continuo` items of a `customProperties` list (a name written twice keeps its
  last value), `VENDOR` is `continuo`, and `ContinuoProperty` is the row type.
- `continuo_contracts.validate`: `validate_python_contract()` checks the ODCS contract of a Python
  node against the JSON schema and continuo's envelope, identity, file name, team and refusal
  rules, and returns every problem as an `Issue` (a path, a reason from the closed `REASONS` and a
  message) in path order. `schema_issues()` runs the JSON schema alone and `rule_issues()` runs the
  rules alone on a document the schema accepted, so a caller can cache the first. The rules refuse
  `servers`, `tenant`, `contractCreatedTs`, a `continuo` property that is unknown, filled by
  continuo, not carried by a Python contract or on the wrong object, and a `continuo` property
  written twice.
- `continuo_contracts.validate` also checks what depends on the kind of the node: `schedule` and
  `criticality` are written, each property has a value of its kind (`timeoutMinutes` is 1 to
  10080, `secretRef` is a `continuo-api-` Secret name of at most 253 characters, `csvSource` is
  `s3://<bucket>/<key>` or `https://<host>`, `readQueries` and `assets` are well formed), and a
  python-node, python-csv or python-api writes the properties its kind needs and none that it
  refuses (`kind_rule`). It also checks the columns (a name used once, a `physicalType` that
  `canonical_sql_type` accepts as `column_type`, no `physicalName`, partition or partition key
  position) and the key of an `upsert` node (`primary_key`: one exists, each key column is
  `required` and not `DOUBLE PRECISION`, and each column of a key of several has its own
  whole-number `primaryKeyPosition` from 1). The name of the table may not be blank, have outer
  whitespace or hold a dot, and the schema part of the id may not hold a dot.
- `continuo_contracts.python.node_from_odcs()` returns the `PythonNode` that a Python node's ODCS
  contract declares, so its `code_projection()` and `projection_hash()` are those of the same node
  written as a wire entry. The three demo nodes of service-py are checked in as ODCS contracts
  next to the old entries they replace, and a test holds their projections to the old ones.

## [0.1.0] - 2026-11-11

**TL;DR**

- First release. continuo-contracts holds the code continuo and its producers share to fingerprint nodes.
- `canonical_json()` writes RFC 8785 canonical JSON (nesting deeper than 100 levels is refused); `normalise_text()` turns CRLF and CR into LF and strips the ends.
- `fold3()` and `fold4()` build `content_hash`; `shared_fold()` and `shared_hash()` fold each file's path or unit id with its digest, so two files that swap contents change the hash.
- `PythonNode`, `code_projection()` and `projection_hash()` give a Python node's config part; `canonical_sql_type()` spells `INT` as `INTEGER`.
- `fingerprints.yaml` says which input moves which fingerprint; `docs/fingerprints.md` shows it.
- Four vector files ship in the wheel, so every consumer can check that it computes the same bytes.
- Python 3.12 and later. Apache-2.0.

### Added

- `continuo_contracts.canonical`: `canonical_json()` writes RFC 8785 canonical JSON and names the
  JSON path of any value it refuses; `normalise_text()` turns CRLF and CR into LF and strips ASCII
  whitespace from both ends.
- `continuo_contracts.canonical.MAX_DEPTH` (100) limits nesting: `canonical_json()` and
  `projection_hash()` refuse a container on a deeper level with its JSON path, and no depth
  raises a bare `RecursionError`.
- `continuo_contracts.hashing`: `fold3()` and `fold4()` build `content_hash`; `shared_fold()` and
  `shared_hash()` fold each unit's id or path with its digest, so two files that swap contents
  change the hash; `sha256_hex()` returns the lowercase hex SHA-256 of bytes.
- `continuo_contracts.errors`: `CanonicalJsonError`, `HashInputError`, `ProjectionError` and
  `FingerprintsFileError` are each a `ContractsError`, which is a `ValueError`. `classify()` and
  `unclassified()` raise a plain `ValueError` for a family that is not one of the seven.
- `continuo_contracts.fingerprints`: reads and checks `fingerprints.yaml`; `classify()` and
  `unclassified()` say which entry classifies an input of a node family;
  `hashed_config_denylist()` and `projection_keys()` hand the hashing code its lists.
- `continuo_contracts.python`: `PythonNode`, `code_projection()` and `projection_hash()` give a
  Python node's config part with a fixed shape; `canonical_sql_type()` spells a type as the runtime
  does (`INT` is `INTEGER`). `vectors/projection.json` and `vectors/sql_types.json` pin both.
- `canonical_sql_type()` strips exactly 29 code points from both ends of a type, listed in the
  README and the docstring, and `vectors/sql_types.json` pins U+0085, U+2028, U+001F and the
  refusal of U+200B around a type.
- `fingerprints.yaml` classifies every input of `content_hash` for each node family, and the dbt
  `config` keys `hashed_config` leaves out; `docs/fingerprints.md` shows it.
- `parse_fingerprints()` refuses a key written twice in a mapping, in an entry, under `lists`,
  under `families` or at the top level, with a `FingerprintsFileError` that says `duplicate key`
  and names the key and its line, instead of keeping the last.
- `continuo_contracts.fingerprints.FIXED_INPUT_NAMES` lists the input names written out in full;
  a guard test ties it to the input names that `docs/fingerprints.md` shows.
- `vectors/canonical_json.json` and `vectors/hashing.json` pin `canonical_json()` (with its
  nesting limit), `fold3()`, `fold4()`, `shared_fold()` and `shared_hash()` for a consumer in any
  language, and two new cases of `vectors/projection.json` pin the UTF-16 order of `config` keys
  and the code point order of assets. The README lists the files under Vectors.
- The README ends the section of each module with its public names, one line each, and has an
  `errors` section for the error classes; a test fails for a public name that has no line.
- The README lists what `code_projection` refuses, one rule for each line, so a producer in
  another language can refuse the same inputs.
- A lone surrogate in the schema or the table of a node shows as its backslash escape in the
  `<schema>.<table>: ` prefix of a `ProjectionError`, so the message can be written to a UTF-8
  stream; every other character of the prefix stays as it is.
