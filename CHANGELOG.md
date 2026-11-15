# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `continuo_contracts.schema`: the ODCS v3.2.0 JSON schema ships in the wheel, and `load()` returns
  it after it checks that the file has the pinned SHA-256 (`SchemaFileError` otherwise).
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
  node and returns every problem as an `Issue` (a path, a reason from the closed `REASONS` and a
  message) in path order. `schema_issues()` runs the JSON schema and `rule_issues()` runs
  continuo's rules alone on a document the schema accepted, so a caller can cache the first.
  `schema_issues()` first scans the columns of every schema object and, when one nests `items`, a
  `map` or `properties`, returns the `refused_field` issues the rules give for them without
  running the JSON schema, whose check of a nested column takes exponential time.
- The rules of `continuo_contracts.validate` check the envelope, the identity (the id, `name` and
  `physicalName` agree with the table, the id is in lower case, and the file is named
  `<table>.odcs.yaml` or `<schema>.<table>.odcs.yaml`), `team.name`, `schedule` and
  `criticality`, and the `service` property, a string with text that names the service of the
  release when one is given. They refuse `servers`, `tenant`, `contractCreatedTs`, and a
  `continuo` property that is unknown, filled by continuo, not carried by a Python contract, on
  the wrong object or written twice.
- A name in a Python contract is not blank and has no whitespace at either end and no control
  character: the name of the table and the schema part of the id (`identity`, which also refuses
  whitespace, a dot or a slash anywhere in them), the name of a column and of a read query, and
  the `schedule` (`value`, which also refuses whitespace anywhere in it and a name longer than 50
  characters). `script` and each entry of `assets` are normalised POSIX paths inside the project
  (`value`): no empty, `.` or `..` part, no `\`, and no `/`, `~` or drive letter at the start, so
  `./a.json`, `a//b`, `a\..\etc` and `C:/x` are refused.
- The id and the table are compared with their ASCII letters in lower case and every other
  character as written, so a Python contract is accepted or refused alike whatever Unicode tables
  the Python that runs it has, which lower-case different letters.
- The rules also check what depends on the kind of the node (`kind_rule`): each property has a
  value of its kind (`timeoutMinutes` is 1 to 10080, `secretRef` is a `continuo-api-` Secret name
  of at most 253 characters, `csvSource` is `s3://<bucket>/<key>` or `https://<host>`,
  `readQueries` maps names to queries with text, and `assets` lists each path once), and a
  python-node, python-csv or python-api writes the properties its kind needs and none that it
  refuses.
- The rules check the columns and the key. A column has a name used once and a `physicalType` that
  `canonical_sql_type` accepts (`column_type`, with a `VARCHAR(n)` or `CHAR(n)` of length 1 to
  2**53 - 1), and no `physicalName` other than its name, partition, partition key position,
  nested `properties`, `items` or `map`, or `logicalTypeOptions` (`refused_field`). The key of an
  `upsert` node (`primary_key`) exists, each key column is `required` and not `DOUBLE PRECISION`,
  and each column of a key of several has its own whole-number `primaryKeyPosition` from 1. A
  custom quality rule has a non-empty `implementation`.
- `continuo_contracts.python.node_from_odcs()` returns the `PythonNode` that a Python node's ODCS
  contract declares, so its `code_projection()` and `projection_hash()` are those of the same node
  written as a wire entry.
- `continuo_contracts.contract`: `normalise_python()` builds the stored form of a Python contract
  (`version: "0"`, `status: active`, the table's names, the `kind` and `service` properties, the
  `reads` tables in lower case, sorted and listed once, and per column the `physicalType` in the
  spelling `canonical_sql_type()` writes and a `logicalType` from `logical_type()`), written in
  the canonical form: description text stripped, quality severity lower-cased, empty values, ODCS
  defaults, the `primaryKeyPosition` of a key of one column and continuo's default `modeType` and
  `extraColumns` dropped, whole-number floats stored as integers and the set-like lists sorted,
  with free-form values kept as written. Two files that mean the same thing, whatever their line
  endings, byte order mark, key order, quoting or spelling of a column type, store the same form.
  It raises `HashInputError` for a `service` that is empty or blank and for a `reads` entry that
  is not exactly `<schema>.<table>`: two parts joined by a dot, neither empty, with no whitespace
  and no control character. `contract_hash()` hashes the stored form without `apiVersion`, `kind`,
  `status` and `version`. `logical_type()` gives `None` for a type `canonical_sql_type()` refuses
  and for a `VARCHAR(n)` or `CHAR(n)` whose length is not from 1 to 2**53 - 1. The new vector file
  `vectors/contracts.json` pins the stored form and the `contract_hash` of eight contracts, two of
  them messy spellings of another that store alike, and the issues of nineteen refused ones,
  among them one for each name and path rule.
- `fingerprints.yaml` classifies every field of a Python contract's stored form for `python-node`,
  `python-csv` and `python-api`, in the new input pattern `odcs.<path>` (`odcs.team.name`,
  `odcs.schema.properties.logicalType`, `odcs.custom.timeoutMinutes`, `odcs.schema.custom.reads`),
  and the new rule `standard_field` classifies every standard ODCS field no entry names as
  `contract`; a `continuo` custom property that no entry names stays unclassified. The list
  `lists.contract_hash_exclusions` holds the root keys that `contract_hash` leaves out
  (`apiVersion`, `kind`, `status`, `version`), and `fingerprints.contract_hash_exclusions()` and
  `Fingerprints.contract_hash_exclusions` return it. The guard test walks the stored form of the
  demo, full and messy contracts and fails for a field no entry classifies.
- `continuo_contracts.cli` and the console script `continuo-contracts`. `validate PATH...` checks
  `*.odcs.yaml` files (a folder is read for them, without the folders below it) and logs every
  issue at ERROR as `<file>: <path>: <message>`. `hash FILE --service SERVICE [--read TABLE]...`
  checks one file and prints its `contract_hash`. With `--json`, stdout is one JSON document
  (`validate`: `valid` and the `issues` of each file, `hash`: `contract_hash` and the `stored`
  form), and the log goes to stderr in either mode. Exit status 0 is valid, 1 is an issue and 2 is
  a usage error (a `--service` that is empty or blank among them), a path that names no contract
  file, a path that cannot be looked up (one below a folder the process may not search), a folder
  or a file that cannot be read or a `--read` value that is not `<schema>.<table>`.
- `errors.SchemaFileError` and `errors.StrictYamlError`, each a `ContractsError`.

### Changed

- The library requires Python 3.14 or later (`requires-python` is `>=3.14`). Python 3.12 and 3.13
  are no longer supported.
- `jsonschema>=4.26` is a runtime dependency, next to PyYAML. The wheel stays pure Python.
- `parse_fingerprints()` refuses a file without `lists.contract_hash_exclusions`, and a `rule`
  entry is either `outside_denylist` (a dbt family) or `standard_field` (a Python family).
- The README says in Stability that a newer version accepts every contract an older one accepted,
  with the same stored form and `contract_hash`, and in Pinning that topology-controller refuses a
  producer bundle written by a newer version than the one it runs.

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
