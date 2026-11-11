# continuo-contracts

The code that continuo and the projects that produce its nodes share to fingerprint a node:
canonical JSON, the Python code projection, the `content_hash` folds and the declaration of
which input moves which fingerprint. A producer's CI, continuo's topology-controller and the
runtime harness import the same functions, so they compute the same bytes and the same hashes
for a node.

The package is pure Python and has one runtime dependency, PyYAML. The terms of continuo that
this text uses, such as the compile extras and the code bundle, are defined in
[docs/fingerprints.md](https://github.com/carolsimone/continuo-contracts/blob/main/docs/fingerprints.md#terms).

## Install

```bash
pip install continuo-contracts==<version>
```

Python 3.12 and later. The wheel is pure (`py3-none-any`) and ships its data files, so an
environment that installs wheels only gets everything it needs.

## canonical

```python
from continuo_contracts.canonical import MAX_DEPTH, canonical_json, normalise_text
```

- `canonical_json(value: object) -> bytes` writes `value` as RFC 8785 canonical JSON, UTF-8
  encoded. It raises `CanonicalJsonError` (a `ContractsError`, which is a `ValueError`) for a
  value it cannot represent, and the message starts with the JSON path of that value, as in
  `$.config.start: unsupported type date`.
- `MAX_DEPTH` is 100, the deepest nesting that `canonical_json` writes, and a port to another
  language must refuse the same values. The outermost list, tuple or mapping is level 1 and a
  container inside it is level 2. A container on a level deeper than 100 raises
  `CanonicalJsonError`, and the message gives the JSON path of that container and says the
  nesting is too deep. A scalar is not a level: a scalar inside a container on level 100 is
  written.
- `normalise_text(text: str) -> str` turns CRLF and CR into LF and strips the six ASCII
  whitespace characters from both ends of `text`, and changes nothing else. It strips only those
  six, on purpose, so that its result does not depend on the Unicode tables of the Python that
  runs it.

```python
canonical_json({"b": [1.0, "\u00e9"], "a": True}) == b'{"a":true,"b":[1,"\xc3\xa9"]}'
```

### Public names

- `MAX_DEPTH`: the deepest level of nesting that `canonical_json` writes, 100.
- `canonical_json`: writes a value as RFC 8785 canonical JSON, UTF-8 encoded.
- `normalise_text`: turns CRLF and CR into LF and strips the six ASCII whitespace characters from
  both ends of a text.

## hashing

```python
from continuo_contracts.hashing import fold3, fold4, sha256_hex, shared_fold, shared_hash
```

A node's `content_hash` is `"sha256:"` followed by the lowercase hex SHA-256 of its parts joined
by `|`, each part encoded as UTF-8.

- `fold3(source, shared, config)` hashes `source|shared|config`. It is the fold of a Python node
  (`python-node`, `python-csv`, `python-api`).
- `fold4(source, shared, config, compiled)` hashes `source|shared|config|compiled`. It is the
  fold of every dbt node. A dbt seed and a dbt test have no compiled part and pass `""`; the
  last `|` stays, so `fold4(s, h, c, "")` is not `fold3(s, h, c)`.
- A part is bare lowercase hex, without a `sha256:` prefix, or `""` when the node has no such
  part. The folds do not check their parts: a seed in a stored artifact without compile extras
  carries dbt's `path` checksum as its source part, so a part is not always hex.
- `sha256_hex(data: bytes) -> str` returns the lowercase hex SHA-256 of `data`.
- `shared_fold(unit_digests)` takes a mapping from a unit id or a repo-relative POSIX path to the
  64 lowercase hex characters of the SHA-256 of that unit's bytes. It writes one line per unit,
  the key, a NUL character and the digest, sorts the lines by key, joins them with a line feed
  and returns the lowercase hex SHA-256 of the UTF-8 text. The lines are sorted by Unicode code
  point of the key, which is the order of its UTF-8 bytes (not UTF-16 order). The key is part of
  the line, so two files that swap their contents change the hash. An empty mapping gives `""`.
  A key that is empty or holds NUL or LF, and a digest in any other form, raise `HashInputError`
  (a `ContractsError`) that names the key.
- `shared_hash(units)` takes a mapping from a path to the bytes of that file, one entry per file
  (a file that is both imported and listed as an asset appears once), and returns `shared_fold`
  of the SHA-256 of each value.

```python
shared_hash({"lib/p.py": b"P = 7\n", "lib/q.py": b"Q = -7\n"})
fold3("1" * 64, shared_hash({}), "2" * 64)  # "sha256:23208c80..."
```

### Public names

- `sha256_hex`: the lowercase hex SHA-256 of bytes.
- `fold3`: the `content_hash` of a Python node, from its source, shared and config parts.
- `fold4`: the `content_hash` of a dbt node, from its source, shared, config and compiled parts.
- `shared_fold`: folds a mapping from unit key to digest into the shared part, `""` for no units.
- `shared_hash`: the shared part of a mapping from unit key to bytes, which is `shared_fold` of the
  SHA-256 of each unit.

## python

```python
from continuo_contracts.python import (
    PythonColumn, PythonNode, canonical_sql_type, code_projection, projection_hash,
)
```

A Python node is a table that a script (`python-node`), a CSV file (`python-csv`) or an API call
(`python-api`) fills. Its code projection is the part of its declaration that decides what it
computes, written in one fixed shape, so two producers that declare the same node write the same
bytes. The projection is the node's `raw_code` in continuo's code bundle, and `projection_hash`
(the lowercase hex SHA-256 of its canonical JSON) is the config part of its `content_hash`.

- `PythonNode` holds the twelve fields of a node's wire entry: `schema`, `table`, `kind`,
  `script`, `reads`, `output_columns` (a sequence of `PythonColumn(name, type, nullable=True)`),
  `extra_columns`, `config`, `secret_ref`, `mode_type`, `primary_key` and `assets`.
  `PythonNode.from_mapping(entry)` builds one from a wire entry: it ignores every other key and
  fills the fields that are absent with their defaults. `relation` is `<schema>.<table>`.
- `code_projection(node)` returns the twelve keys that `fingerprints.projection_keys()` lists, in
  that order. A stored `python-model` kind is `python-node`. The SQL of each read of a
  `python-node` has LF line endings and no ASCII whitespace around it. Each column type is
  written by `canonical_sql_type`. `config` is copied with every mapping as a `dict` and every
  list or tuple as a `list`, down to level `MAX_DEPTH` (the config itself is level 1), and a
  container nested deeper is left in place and refused by `projection_hash`. `primary_key` is the
  node's key on an `upsert` node and `[]` on `replace` and `append`, where the key only describes
  the table.
  Assets are sorted by Unicode code point, which is the order of their UTF-8 bytes (not UTF-16
  order). A `python-csv` node has no script and no assets, reads exactly `csv`, and only
  replaces. Only a `python-api` node has a `secret_ref`, and it reads nothing.
- `projection_hash(node)` returns the hash of that projection.
- `canonical_sql_type(raw)` strips outer whitespace, ignores case and writes `INT` as `INTEGER`,
  `DECIMAL(p, s)` as `NUMERIC(p,s)` and `VARCHAR(064)` as `VARCHAR(64)`. It supports `BIGINT`,
  `INT`, `INTEGER`, `DOUBLE PRECISION`, `TEXT`, `TIMESTAMP`, `DATE`, `BOOLEAN`, `NUMERIC(p,s)`,
  `DECIMAL(p,s)`, `VARCHAR(n)` and `CHAR(n)`, and refuses everything else, and a `NUMERIC`
  precision outside 1 to 38 or a scale above the precision. The outer whitespace that it removes
  is exactly these 29 code points and no other character: U+0009 to U+000D, U+001C to U+001F,
  U+0020, U+0085, U+00A0, U+1680, U+2000 to U+200A, U+2028, U+2029, U+202F, U+205F and U+3000.
  A zero width space (U+200B) or a byte order mark (U+FEFF) around a type is not stripped, so the
  type is refused. A port to another language strips these code points and no others, whatever
  its own trim function removes. They are the code points that Python's `str.strip()` removes,
  the same on Python 3.12, 3.13 and 3.14. After the comma of `NUMERIC(p, s)` any run of ASCII
  whitespace is accepted. `normalise_text` strips only the six ASCII characters, so the two rules
  differ.
- Every other string of the projection is written as given: no trimming, no case change, no
  Unicode normalisation. That holds for `schema`, `table`, `script`, the names in `reads`, the
  CSV URI of a `python-csv` node, `secret_ref`, the entries of `primary_key` and `assets`, the
  column names and the strings of `config`. Three things are not written as given: the SQL of
  each read of a `python-node` (normalised as above), the column types (canonicalised) and
  `kind` (a `python-model` kind is `python-node`).
- The numbers of `config` are written by `canonical_json`: `1` and `1.0` hash alike, `-0.0` is
  written `0`, and an integer outside -(2**53 - 1) to 2**53 - 1 is refused while a float of that
  size is accepted.
- A node that cannot be projected the same way everywhere raises `ProjectionError` (a
  `ContractsError`). The message of an error about a node starts with `<schema>.<table>: `, and
  the error of a `config` value that canonical JSON cannot write goes on with its JSON path.
  The projection is a mapping on level 1 and `config` is its value on level 2, so `config` may
  nest containers at most `MAX_DEPTH - 1` (99) levels deep, counting the config itself as level 1.

What `code_projection` refuses. A refused node has no projection and no hash, and a producer in
another language must refuse the same inputs: one that accepts a node Python refuses, or repairs
it (drops a repeated asset, say), writes a hash that Python never writes. Each line is one rule,
`field: what is refused`. A line marked `from_mapping` is refused when `PythonNode.from_mapping`
reads a wire entry (the messages of the first two have no `<schema>.<table>: ` prefix, since
there is no relation to name), a line marked `projection_hash` when the hash is written, and
every other line by `code_projection`. "Not a string", "not a list", "not a mapping" and "not a
bool" mean a value that is not a JSON string, array, object or boolean (Python also takes a tuple
for a list).

- `entry` (`from_mapping`): not a mapping.
- `schema`, `table` (`from_mapping`): absent.
- `reads`, `config` (`from_mapping`): not a mapping.
- `output_columns`, `primary_key`, `assets` (`from_mapping`): not a list.
- `output_columns[i]` (`from_mapping`): not a mapping.
- `output_columns[i]` (`from_mapping`): no `name`, or no `type`.
- `schema`, `table`: not a string, or empty.
- `kind`: not a string, or empty.
- `kind`: not `python-node`, `python-csv` or `python-api` (the stored `python-model` is read as
  `python-node`).
- `script`: not a string.
- `script`: not empty on a `python-csv` node.
- `reads`: not a mapping.
- `reads`: a name or a SQL text that is not a string.
- `reads`: on a `python-csv` node, not exactly the one key `csv`.
- `reads`: on a `python-api` node, not empty.
- `output_columns`: not a list.
- `output_columns`: an item that is not a `PythonColumn`.
- `output_columns[i].name`: not a string, or empty.
- `output_columns[i].nullable`: not a bool.
- `output_columns[i].type`: not a string.
- `output_columns[i].type`: not one of the types `canonical_sql_type` supports, such as `FLOAT`,
  `VARCHAR` with no length, `NUMERIC(10)`, `DOUBLE  PRECISION` with two spaces or a type with a
  non-ASCII letter or a zero width space in it.
- `output_columns[i].type`: `NUMERIC` or `DECIMAL` with a precision outside 1 to 38.
- `output_columns[i].type`: `NUMERIC` or `DECIMAL` with a scale above the precision.
- `extra_columns`: not `raise` or `warn`.
- `config`: not a mapping.
- `config`: a key of the top level that is not a string.
- `secret_ref`: on a `python-api` node, not a string.
- `secret_ref`: on a `python-node` or `python-csv` node, not empty.
- `mode_type`: not `replace`, `append` or `upsert`.
- `mode_type`: on a `python-csv` node, not `replace`.
- `primary_key`: on an `upsert` node, not a list.
- `primary_key`: on an `upsert` node, an entry that is not a string, or empty.
- `assets`: not a list.
- `assets`: an entry that is not a string, or empty.
- `assets`: a path listed twice.
- `assets`: on a `python-csv` node, not empty.
- `config` (`projection_hash`): a container on a level deeper than 99, the config being level 1.
- `config` (`projection_hash`): a value that canonical JSON cannot write: one that is not null, a
  bool, a number, a string, a list or a mapping (a `date`, say), NaN, an infinity, an integer
  outside -(2**53 - 1) to 2**53 - 1, a key below the top level that is not a string, and a
  container that contains itself.
- any string of the projection (`projection_hash`): a lone surrogate (U+D800 to U+DFFF), in the
  keys of `reads` and `config` too.

An empty string is refused in `schema`, `table`, `kind`, a column `name` and an entry of
`primary_key` or `assets`, and accepted in `script`, the names and SQL texts of `reads`, the keys
of `config` and the `secret_ref` of a `python-api` node. `primary_key` is read only on an `upsert`
node, so no value of it is refused on `replace` and `append`. An `upsert` node with an empty
`primary_key`, a repeated entry of `primary_key` and a repeated column name are accepted.

```python
node = PythonNode(
    schema="analytics",
    table="py_daily_kpis",
    script="scripts/py_daily_kpis.py",
    reads={"daily": "select created_at, amount_eur from analytics.daily_transactions"},
    output_columns=(
        PythonColumn("day", "date", nullable=False),
        PythonColumn("tx_count", "int", nullable=False),
        PythonColumn("total_amount", "decimal(12, 2)", nullable=False),
    ),
)
code_projection(node)["output_columns"][1]  # {"name": "tx_count", "nullable": False, "type": "INTEGER"}
projection_hash(node)  # "a01d7d6065f055bb2d87959aaf178d08f5a3f96c86912099b9876239d84f6efe"
```

Two data files pin this behaviour, and the wheel ships both:

- `vectors/projection.json` is a list of `{"name", "node", "projection", "projection_hash"}`.
  `node` holds the twelve fields as a wire entry carries them.
- `vectors/sql_types.json` is a list of `{"raw", "canonical"}`, with `canonical` null when the type
  is refused.

Read them with `importlib.resources.files("continuo_contracts") / "vectors" / "<file>"`. Every
consumer, in any language, runs every case: it builds each node, computes the projection and its
hash, and compares them with the pinned values.

### Public names

- `PYTHON_KINDS`: the three kinds of Python node, `python-node`, `python-csv` and `python-api`.
- `KIND_ALIASES`: the kinds that stand for another kind, which is `python-model` for
  `python-node`.
- `MODE_TYPES`: the values of `mode_type`, `replace`, `append` and `upsert`.
- `EXTRA_COLUMNS_POLICIES`: the values of `extra_columns`, `raise` and `warn`.
- `PythonColumn`: an output column of a node, with the fields `name`, `type` (the SQL type as
  declared) and `nullable` (`True` unless given).
- `PythonNode`: the declaration of a Python node, with the fields `schema`, `table`, `kind`,
  `script`, `reads`, `output_columns`, `extra_columns`, `config`, `secret_ref`, `mode_type`,
  `primary_key` and `assets`, the property `relation` and the class method `from_mapping`.
- `canonical_sql_type`: the canonical spelling of an SQL type, or a `ProjectionError` for a type it
  does not support.
- `code_projection`: the code projection of a node, one entry for each key of `projection_keys()`.
- `projection_hash`: the lowercase hex SHA-256 of the canonical JSON of the code projection, which
  is the config part of a Python node's `content_hash`.

## fingerprints

```python
from continuo_contracts.fingerprints import (
    classify, hashed_config_denylist, projection_keys, unclassified,
)
```

`continuo_contracts/fingerprints.yaml` says which fingerprint each input of a node moves, so a
producer's CI, continuo's topology-controller and the guards that check them agree on it. It holds
two lists, the `config` keys that stay out of a dbt node's hashed config and the keys of the Python
code projection, and one list of entries for each of eight sections: `every` (what every family
shares) and the seven node families `dbt-model`, `dbt-seed`, `dbt-snapshot`, `dbt-test`,
`python-node`, `python-csv` and `python-api`. An entry names the inputs it classifies and the
fingerprint they move: `content` (`content_hash`, with the parts of it that the input is folded
into), `contract` (`contract_hash`), `both` or `neither`. The reader refuses a repeated key in any
mapping of the file with a `FingerprintsFileError` that names the key and its line, so a second
`fingerprint:` in an entry cannot replace the first.

- `hashed_config_denylist()` returns the dbt `config` keys that stay out of the hashed config, as a
  `frozenset`.
- `projection_keys()` returns the twelve keys of the code projection, in projection order.
- `classify(family, name)` returns the entry that classifies the input `name` of a node of
  `family`, or `None`. An entry has `family` (the section it is in), `id`, `field`, `source`,
  `fingerprint`, `parts`, `why`, `inputs`, `when` and `rule`, and `entry.moves(part)` says whether
  the input moves that part of `content_hash`. In a dbt family, a `config.<key>` that no entry
  names and whose key is not in the denylist belongs to the family's `other_config` entry. It does
  not classify a sub-key of a denylisted key (`config.meta.owner` gives `None`), and a dotted name
  such as `config.contract.enforced` is classified only where an entry declares it as its own
  input (the dbt model has one), so in another dbt family it belongs to `other_config`. It raises
  `ValueError` for a `family` that is not one of the seven.
- `unclassified(family, names)` returns the distinct `names` that no entry classifies, sorted. A
  guard test passes it every input of a hash and expects `[]`.

```python
classify("dbt-model", "config.persist_docs").id  # "other_config"
classify("python-node", "ci.shared_code_hash").parts  # ("shared",)
```

[docs/fingerprints.md](https://github.com/carolsimone/continuo-contracts/blob/main/docs/fingerprints.md)
shows every entry as a table and lists the input names: `raw_code`, `config.<key>`,
`relation.schema`, `projection.<key>`, `ci.source_hash` and the others. The file is generated from
`fingerprints.yaml`. The input names written out in full are the tuple `FIXED_INPUT_NAMES` of
`continuo_contracts.fingerprints`, and every other input name is `config.<key>`, `config.*` or
`projection.<key>`. This repository's guard tests check that an entry names nothing outside that
vocabulary and that `docs/fingerprints.md` writes each name of the tuple and no other fixed name.

A pull request that adds an input to a hash adds it to `fingerprints.yaml` in the same change. A
projection key or a denylisted `config` key without an entry fails this repository's guard test, and
`unclassified()` finds any other input without one.

### Public names

- `FAMILIES`: the seven node families, `dbt-model`, `dbt-seed`, `dbt-snapshot`, `dbt-test`,
  `python-node`, `python-csv` and `python-api`.
- `DBT_FAMILIES`: the first four of `FAMILIES`, which are the dbt ones.
- `PYTHON_FAMILIES`: the last three of `FAMILIES`, which are the Python ones.
- `EVERY`: the name of the section for what every family shares, `every`.
- `SECTIONS`: the eight sections of the file, `EVERY` and then the seven of `FAMILIES`.
- `FIXED_INPUT_NAMES`: the input names written out in full, such as `raw_code` and
  `ci.source_hash`. Every other input name is `config.<key>`, `config.*` or `projection.<key>`.
- `When`: what an input moves besides its entry's fingerprint while a condition holds, with the
  fields `condition` (`enforced`, `upsert` or `sql_reads`), `fingerprint` and `parts`.
- `Entry`: one row of a section, with the fields `family` (the section it is in, `every` or one of
  `FAMILIES`), `id`, `field`, `source`, `fingerprint`, `parts`, `why`, `inputs`, `when` (a `When`
  or `None`) and `rule` (`outside_denylist` or `None`), and the method `moves(part)`.
- `Fingerprints`: a checked fingerprints file, with the fields `version`, `hashed_config_denylist`,
  `projection_keys` and `sections` (a read-only mapping from each section to its entries) and the
  methods `entries(section)`, `classify(family, name)` and `unclassified(family, names)`.
- `parse_fingerprints`: reads and checks the YAML text of a fingerprints file and returns a
  `Fingerprints`, or raises `FingerprintsFileError` for a file that is not in the form above.
- `load`: the packaged `fingerprints.yaml` as a `Fingerprints`, read and checked once.
- `hashed_config_denylist`: the dbt `config` keys that stay out of the hashed config, as a
  `frozenset`.
- `projection_keys`: the twelve keys of the code projection, in projection order.
- `classify`: the packaged entry that classifies an input of a family, or `None`.
- `unclassified`: the distinct input names that no packaged entry classifies for a family, sorted.
- `render_in`: the "In" cell of an entry in `docs/fingerprints.md`, with the fingerprint the entry
  moves, its parts and its `when`.
- `render_markdown`: the Markdown of `docs/fingerprints.md` for a `Fingerprints`, which
  `scripts/render_fingerprints.py` writes from the packaged file.

## errors

```python
from continuo_contracts.errors import (
    CanonicalJsonError, ContractsError, FingerprintsFileError, HashInputError, ProjectionError,
)
```

Every error that the package defines is a `ContractsError`, which is a `ValueError`, so one
`except ContractsError` catches all of them. A few refusals are Python's own errors: `classify` and
`unclassified` (the functions and the methods of `Fingerprints`) raise a plain `ValueError` for a
family outside `FAMILIES`, `Fingerprints.entries` raises one for a section outside `SECTIONS`,
`fold3` and `fold4` raise `UnicodeEncodeError` for a part that UTF-8 cannot encode, such as a lone
surrogate, and an argument of the wrong type can raise `TypeError` or `AttributeError`.

### Public names

- `ContractsError`: the base of the errors this package defines.
- `CanonicalJsonError`: `canonical_json` refuses a value that canonical JSON cannot represent, and
  the message starts with the JSON path of that value.
- `HashInputError`: `shared_fold` and `shared_hash` refuse a unit key or a digest that is in a
  form they do not accept, and the message names the key.
- `ProjectionError`: `canonical_sql_type`, `PythonNode.from_mapping`, `code_projection` and
  `projection_hash` refuse an SQL type or a Python node.
- `FingerprintsFileError`: `parse_fingerprints` and `load` refuse a fingerprints file that is not
  in the form described under [fingerprints](#fingerprints), and the message names the location in
  the file.

## Vectors

The wheel ships four data files in `continuo_contracts/vectors/`. Each is a JSON list of cases
that give the inputs and the expected output of one part of the package, so a producer in any
language can check its own implementation against the same values. Read a file with
`importlib.resources.files("continuo_contracts") / "vectors" / "<file>"`. Every consumer runs
every case of every file.

- `projection.json` and `sql_types.json` pin the Python projection and `canonical_sql_type`, and
  [python](#python) lists their fields. They include a `config` whose keys sort differently by
  UTF-16 code unit and by code point, and assets that sort by code point.
- `canonical_json.json` is a list of `{"name", "value", "canonical"}`. `value` is any JSON value
  and `canonical` is the text that `canonical_json` writes for it, which a consumer compares after
  encoding it as UTF-8. A number with a point or an exponent is a double, and every integer in a
  `value` is within -(2**53 - 1) to 2**53 - 1. The cases cover the RFC 8785 samples, the order of
  object keys by UTF-16 code unit (`U+1F600` sorts before `U+FF5E`), the forms of a number, the
  escapes of a string, nested empty containers and the nesting limit (a list nested 100 levels is
  written, one nested 101 levels is refused). A case `{"name", "value_text", "error": true}`
  holds JSON text instead of a value: read it with integers kept exact, and the value must be
  refused, as `canonical_json` refuses an integer outside that range and a container on a level
  deeper than `MAX_DEPTH`. The refusal case for nesting needs a JSON reader that accepts 101 levels
  of nesting.
- `hashing.json` is a list of `{"name", "function", "inputs", "expected"}`. `function` is
  `fold3`, `fold4`, `shared_fold` or `shared_hash`, and `inputs` holds its arguments by name:
  `source`, `shared` and `config` (and `compiled` for `fold4`), `unit_digests` for `shared_fold`
  (a unit key and its digest) and `units_hex` for `shared_hash` (a unit key and the bytes of that
  unit as lowercase hex). `expected` is the text the function returns, `""` for no units. A case
  `{"name", "function", "inputs", "error": true}` must be refused, as `shared_fold` refuses a key
  that is empty or holds NUL or LF and a digest that is not 64 lowercase hex characters. The keys
  `U+FF5E` and `U+1F600` are in the cases that pin the code point order of `shared_fold`, so a
  consumer that sorts them by UTF-16 code unit fails them.

## Pinning

Pin an exact version (`continuo-contracts==X.Y.Z`), because a hash is only comparable across
processes that run the same code. A dbt producer uses the version its continuo release
documents. `continuo_contracts.__version__` is the version of the installed distribution.

## Stability

A hash that this package computes is a contract. A change that moves any hash pinned in
`continuo_contracts/vectors/` or in the tests moves `content_hash` for every node of every
install, so it ships only together with a re-baseline in continuo: a continuo release of each
service, run once after such a change, that recomputes every node's stored hash with the new
code and validates and reloads nothing.

## Contributing

See [CONTRIBUTING.md](https://github.com/carolsimone/continuo-contracts/blob/main/CONTRIBUTING.md). Report a vulnerability as described in
[SECURITY.md](https://github.com/carolsimone/continuo-contracts/blob/main/SECURITY.md).

## License

Apache License 2.0. See [LICENSE](https://github.com/carolsimone/continuo-contracts/blob/main/LICENSE) and [NOTICE](https://github.com/carolsimone/continuo-contracts/blob/main/NOTICE).
