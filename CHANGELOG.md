# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
