# continuo-contracts

The code that continuo and the projects that produce its nodes share to fingerprint a node:
canonical JSON, the Python code projection, the `content_hash` folds and the declaration of
which input moves which fingerprint. A producer's CI, continuo's topology-controller and the
runtime harness import the same functions, so they compute the same bytes and the same hashes
for a node.

The package is pure Python and has two runtime dependencies, PyYAML and jsonschema. The terms of
continuo that this text uses, such as the compile extras and the code bundle, are defined in
[docs/fingerprints.md](https://github.com/carolsimone/continuo-contracts/blob/main/docs/fingerprints.md#terms).

## Install

```bash
pip install continuo-contracts==<version>
```

Python 3.14 and later. The wheel is pure (`py3-none-any`) and ships its data files, so an
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
canonical_json(
    {"b": [1.0, "\u00e9"], "a": True}
) == b'{"a":true,"b":[1,"\xc3\xa9"]}'
```

### Public names

- `MAX_DEPTH`: the deepest level of nesting that `canonical_json` writes, 100.
- `canonical_json`: writes a value as RFC 8785 canonical JSON, UTF-8 encoded.
- `normalise_text`: turns CRLF and CR into LF and strips the six ASCII whitespace characters from
  both ends of a text.

## hashing

```python
from continuo_contracts.hashing import (
    fold3,
    fold4,
    sha256_hex,
    shared_fold,
    shared_hash,
)
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
  carries dbt's checksum as its source part, and that checksum is the file path when the seed is
  over 1 MiB, so a part is not always hex.
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
    PythonColumn,
    PythonNode,
    canonical_sql_type,
    code_projection,
    node_from_odcs,
    projection_hash,
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
- `node_from_odcs(doc)` returns the `PythonNode` that a Python node's ODCS contract declares, for a
  document that `validate.validate_python_contract` accepted. `schema` is the id without the
  `.<table>` that ends it, as the validator reads it (the table with its ASCII letters in lower
  case), `table` is the name of the schema object, `kind` is the `kind` property (a
  `python-model` is `python-node`, and a contract that writes none is a `python-node`) and
  `script`, `extraColumns`, `config`, `secretRef`, `modeType` and `assets` are the properties of
  those names, each with the default of `PythonNode`. `reads` is `readQueries`, or
  `{"csv": csvSource}` on a `python-csv` node, or empty. The columns are the properties of the
  schema object in file order, each with its `physicalType` as written and nullable unless it is
  `required`. `primary_key` is the names of the columns with `primaryKey: true` in the order of
  their `primaryKeyPosition` (a whole number from 1, and `1.0` is 1), then in file order, whatever
  the `modeType`: `code_projection` leaves the key out of a node that does not upsert. A document
  that is not a mapping, that has no single named schema object, whose id is not
  `<schema>.<table>`, or whose columns, `readQueries`, `config` or `assets` are not the
  containers they must be raises `ProjectionError`. The projection of a node read this way is the
  projection of the same node written as a wire entry, so `projection_hash` does not move when a
  node moves from one form to the other.
- `canonical_sql_type(raw)` strips outer whitespace, ignores case and writes `INT` as `INTEGER`,
  `DECIMAL(p, s)` as `NUMERIC(p,s)` and `VARCHAR(064)` as `VARCHAR(64)`. It supports `BIGINT`,
  `INT`, `INTEGER`, `DOUBLE PRECISION`, `TEXT`, `TIMESTAMP`, `DATE`, `BOOLEAN`, `NUMERIC(p,s)`,
  `DECIMAL(p,s)`, `VARCHAR(n)` and `CHAR(n)`, and refuses everything else, and a `NUMERIC`
  precision outside 1 to 38 or a scale above the precision. The outer whitespace that it removes
  is exactly these 29 code points and no other character: U+0009 to U+000D, U+001C to U+001F,
  U+0020, U+0085, U+00A0, U+1680, U+2000 to U+200A, U+2028, U+2029, U+202F, U+205F and U+3000.
  A zero width space (U+200B) or a byte order mark (U+FEFF) around a type is not stripped, so the
  type is refused. A port to another language strips these code points and no others, whatever
  its own trim function removes. They are the code points that Python's `str.strip()` removes
  on Python 3.14. After the comma of `NUMERIC(p, s)` any run of ASCII whitespace is accepted.
  `normalise_text` strips only the six ASCII characters, so the two rules differ.
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
node: `code_projection` refuses no value of it on `replace` and `append`, while `from_mapping`
refuses a value that is not a list on any node. An `upsert` node with an empty `primary_key`, a
repeated entry of `primary_key` and a repeated column name are accepted.

```python
node = PythonNode(
    schema="analytics",
    table="py_daily_kpis",
    script="scripts/py_daily_kpis.py",
    reads={
        "daily": "select created_at, amount_eur from analytics.daily_transactions"
    },
    output_columns=(
        PythonColumn("day", "date", nullable=False),
        PythonColumn("tx_count", "int", nullable=False),
        PythonColumn("total_amount", "decimal(12, 2)", nullable=False),
    ),
)
code_projection(node)["output_columns"][
    1
]  # {"name": "tx_count", "nullable": False, "type": "INTEGER"}
projection_hash(
    node
)  # "a01d7d6065f055bb2d87959aaf178d08f5a3f96c86912099b9876239d84f6efe"
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
- `node_from_odcs`: the `PythonNode` that a Python node's ODCS contract declares.

## fingerprints

```python
from continuo_contracts.fingerprints import (
    classify,
    contract_hash_exclusions,
    hashed_config_denylist,
    projection_keys,
    unclassified,
)
```

`continuo_contracts/fingerprints.yaml` says which fingerprint each input of a node moves, so a
producer's CI, continuo's topology-controller and the guards that check them agree on it. It holds
three lists, the `config` keys that stay out of a dbt node's hashed config, the keys of the Python
code projection and the root keys of a Python contract's stored form that `contract_hash` leaves
out, and one list of entries for each of eight sections: `every` (what every family
shares) and the seven node families `dbt-model`, `dbt-seed`, `dbt-snapshot`, `dbt-test`,
`python-node`, `python-csv` and `python-api`. An entry names the inputs it classifies and the
fingerprint they move: `content` (`content_hash`, with the parts of it that the input is folded
into), `contract` (`contract_hash`), `both` or `neither`. The reader refuses a repeated key in any
mapping of the file with a `FingerprintsFileError` that names the key and its line, so a second
`fingerprint:` in an entry cannot replace the first.

- `hashed_config_denylist()` returns the dbt `config` keys that stay out of the hashed config, as a
  `frozenset`.
- `projection_keys()` returns the twelve keys of the code projection, in projection order.
- `contract_hash_exclusions()` returns the root keys of a Python contract's stored form that
  `contract_hash` leaves out, `apiVersion`, `kind`, `status` and `version`, as a `frozenset`.
  `contract.contract_hash` reads this list, so the file and the hash cannot disagree.
- `classify(family, name)` returns the entry that classifies the input `name` of a node of
  `family`, or `None`. An entry has `family` (the section it is in), `id`, `field`, `source`,
  `fingerprint`, `parts`, `why`, `inputs`, `when` and `rule`, and `entry.moves(part)` says whether
  the input moves that part of `content_hash`. In a dbt family, a `config.<key>` that no entry
  names and whose key is not in the denylist belongs to the family's `other_config` entry. It does
  not classify a sub-key of a denylisted key (`config.meta.owner` gives `None`), and a dotted name
  such as `config.contract.enforced` is classified only where an entry declares it as its own
  input (the dbt model has one), so in another dbt family it belongs to `other_config`. In a
  Python family, an `odcs.<path>` that no entry names belongs to the family's `standard_field`
  entry, which classifies every standard ODCS field of the stored form as `contract`; a `continuo`
  custom property (`odcs.custom.<property>` or `odcs.schema.custom.<property>`) is classified only
  by an entry, so one that no entry names gives `None`. It raises `ValueError` for a `family` that
  is not one of the seven.
- `unclassified(family, names)` returns the distinct `names` that no entry classifies, sorted. A
  guard test passes it every input of a hash and expects `[]`.

```python
classify("dbt-model", "config.persist_docs").id  # "other_config"
classify("python-node", "ci.shared_code_hash").parts  # ("shared",)
classify("python-node", "odcs.custom.timeoutMinutes").fingerprint  # "contract"
classify("python-node", "odcs.description.usage").rule  # "standard_field"
classify("python-node", "odcs.custom.madeUp")  # None
```

[docs/fingerprints.md](https://github.com/carolsimone/continuo-contracts/blob/main/docs/fingerprints.md)
shows every entry as a table and lists the input names: `raw_code`, `config.<key>`,
`relation.schema`, `projection.<key>`, `ci.source_hash`, `odcs.<path>` and the others. The file is
generated from `fingerprints.yaml`. The input names written out in full are the tuple
`FIXED_INPUT_NAMES` of `continuo_contracts.fingerprints`, and every other input name is
`config.<key>`, `config.*`, `projection.<key>` or `odcs.<path>`. An `odcs.<path>` names a field of
a Python contract's stored form: `odcs` and then the ODCS names of the path as they are written
(camelCase allowed) with the array indexes dropped, such as `odcs.team.name` and
`odcs.schema.properties.logicalType`; a `continuo` custom property is `odcs.custom.<property>` on
the contract (`odcs.custom.timeoutMinutes`) and `odcs.schema.custom.<property>` on the schema
object (`odcs.schema.custom.reads`); `odcs.*` is the one name of the `standard_field` entry. This
repository's guard tests check that an entry names nothing outside that vocabulary and that
`docs/fingerprints.md` writes each name of the tuple and no other fixed name.

A pull request that adds an input to a hash adds it to `fingerprints.yaml` in the same change. A
projection key or a denylisted `config` key without an entry fails this repository's guard test, so
does a field of the stored form of a Python contract that no entry or rule classifies as `contract`
or `both`, and `unclassified()` finds any other input without one.

### Public names

- `FAMILIES`: the seven node families, `dbt-model`, `dbt-seed`, `dbt-snapshot`, `dbt-test`,
  `python-node`, `python-csv` and `python-api`.
- `DBT_FAMILIES`: the first four of `FAMILIES`, which are the dbt ones.
- `PYTHON_FAMILIES`: the last three of `FAMILIES`, which are the Python ones.
- `EVERY`: the name of the section for what every family shares, `every`.
- `SECTIONS`: the eight sections of the file, `EVERY` and then the seven of `FAMILIES`.
- `FIXED_INPUT_NAMES`: the input names written out in full, such as `raw_code` and
  `ci.source_hash`. Every other input name is `config.<key>`, `config.*`, `projection.<key>` or
  `odcs.<path>`.
- `When`: what an input moves besides its entry's fingerprint while a condition holds, with the
  fields `condition` (`enforced`, `upsert` or `sql_reads`), `fingerprint` and `parts`.
- `Entry`: one row of a section, with the fields `family` (the section it is in, `every` or one of
  `FAMILIES`), `id`, `field`, `source`, `fingerprint`, `parts`, `why`, `inputs`, `when` (a `When`
  or `None`) and `rule` (`outside_denylist`, `standard_field` or `None`), and the method
  `moves(part)`.
- `Fingerprints`: a checked fingerprints file, with the fields `version`, `hashed_config_denylist`,
  `projection_keys`, `contract_hash_exclusions` and `sections` (a read-only mapping from each
  section to its entries) and the methods `entries(section)`, `classify(family, name)` and
  `unclassified(family, names)`.
- `parse_fingerprints`: reads and checks the YAML text of a fingerprints file and returns a
  `Fingerprints`, or raises `FingerprintsFileError` for a file that is not in the form above.
- `load`: the packaged `fingerprints.yaml` as a `Fingerprints`, read and checked once.
- `hashed_config_denylist`: the dbt `config` keys that stay out of the hashed config, as a
  `frozenset`.
- `projection_keys`: the twelve keys of the code projection, in projection order.
- `contract_hash_exclusions`: the root keys of a Python contract's stored form that
  `contract_hash` leaves out, as a `frozenset`.
- `classify`: the packaged entry that classifies an input of a family, or `None`.
- `unclassified`: the distinct input names that no packaged entry classifies for a family, sorted.
- `render_in`: the "In" cell of an entry in `docs/fingerprints.md`, with the fingerprint the entry
  moves, its parts and its `when`.
- `render_markdown`: the Markdown of `docs/fingerprints.md` for a `Fingerprints`, which
  `scripts/render_fingerprints.py` writes from the packaged file.

## schema

```python
from continuo_contracts.schema import SCHEMA_FILE, SCHEMA_SHA256, load
```

The wheel ships the JSON schema of the Open Data Contract Standard (ODCS) v3.2.0, byte for byte as
the `bitol-io/open-data-contract-standard` repository publishes it at the tag `v3.2.0`, in the file
`continuo_contracts/odcs-json-schema-v3.2.0.json`. It is a JSON Schema of draft 2019-09.

- `load() -> Mapping[str, Any]` returns the schema, the same object on every call. The first call
  hashes the file and compares the SHA-256 with `SCHEMA_SHA256`, and it raises `SchemaFileError`
  (a `ContractsError`) when they differ, so a schema that was edited or damaged never checks a
  contract. The message names the file, the hash it found and the pinned hash. A call that raises
  caches nothing. Treat the returned mapping as read-only.
- `SCHEMA_FILE` is the name of the file inside the package, `odcs-json-schema-v3.2.0.json`, and
  `SCHEMA_SHA256` is the lowercase hex SHA-256 of its bytes.

```python
load()["$schema"]  # "https://json-schema.org/draft/2019-09/schema"
```

### Public names

- `SCHEMA_FILE`: the name of the packaged schema file, `odcs-json-schema-v3.2.0.json`.
- `SCHEMA_SHA256`: the lowercase hex SHA-256 that the bytes of the packaged schema must have.
- `load`: returns the ODCS v3.2.0 JSON schema after it checks the file's SHA-256, and raises
  `SchemaFileError` when the file has other bytes.

## yaml_strict

```python
from continuo_contracts.yaml_strict import load
```

`load` reads a contract file, such as an ODCS contract, into plain Python values. It is PyYAML with
the scalar rules of the YAML 1.2 core schema in place of its YAML 1.1 ones, and with refusals for
what lets one small file mean more than it says or mean different things to different readers.

- `load(text: str) -> object` returns the value of the one document in `text`: a `dict` with `str`
  keys in the order of the file, a `list`, a `str`, an `int`, a `float`, a `bool` or `None`. A text
  with no document, such as an empty text or a comment only, reads as `None`. One leading byte
  order mark is dropped and CRLF reads like LF, so the same file saved by another editor reads the
  same. A second byte order mark right after the first is refused (`line 1, column 1`), and one
  anywhere else in the text is an ordinary character of the text. It raises `StrictYamlError` (a
  `ContractsError`) for every refusal below.
- Only these plain scalars are not strings: `true`, `True`, `TRUE`, `false`, `False` and `FALSE`
  (`bool`), `~`, `null`, `Null`, `NULL` and an empty value (`None`), a decimal integer with an
  optional sign, `0o17` and `0x1F` (`int`; a leading zero is decimal, so `0777` is 777), and a
  number with a point or an exponent such as `1e3`, `.5` and `1.` (`float`). So `on`, `yes`, `NO`,
  `y`, `2026-11-14`, `1:20`, `1_000` and `0b101` are strings. A quoted scalar is always a string,
  and so is a scalar written after a lone `!` (`! 5` is `"5"`), as in YAML 1.2; a sequence or a
  mapping after a lone `!` stays a sequence or a mapping.
- The reader refuses an alias (`*name`, which lets a few lines expand into a tree of any size), a
  plain `<<` key (a merge key), a repeated key at any level, a key that is not a string, a tag
  other than the seven core ones (`!!str`, `!!int`, `!!float`, `!!bool`, `!!null`, `!!seq` and
  `!!map`, and then the value must fit the tag, so `!!int abc` is refused), a second document, and
  a container nested more than `canonical.MAX_DEPTH` levels deep. An anchor (`&name`) that no alias
  uses is read and ignored.
- The reader refuses a value that `canonical_json` cannot write, so everything it returns can be
  hashed: a float that is not finite (`.inf`, `.nan`, `1e999`), an integer outside
  -(2**53 - 1) to 2**53 - 1, and a string with a lone surrogate (a `"\ud800"` escape).
- The message of the error is `line <line>, column <column>: <reason>`, with both counted from 1
  and `line` and `column` on the error as numbers. A value that `canonical_json` cannot write has
  no position in the text, so both are 0 and the reason starts with the JSON path of the value, as
  in `line 0, column 0: $.config.rate: NaN and the infinities are not JSON numbers`.

```python
load(
    "a: on\nb: 1e3\nc: 2026-11-14\n"
)  # {"a": "on", "b": 1000.0, "c": "2026-11-14"}
load(
    "a: &x 1\nb: *x\n"
)  # StrictYamlError: line 2, column 4: YAML aliases are not allowed
```

### Public names

- `load`: reads one YAML document as the YAML 1.2 core schema and raises `StrictYamlError` for
  aliases, merge keys, repeated keys, keys that are not strings, other tags, more than one
  document, nesting that is too deep and values that canonical JSON cannot write.

## vocabulary

```python
from continuo_contracts.vocabulary import (
    PROPERTIES,
    VENDOR,
    ContinuoProperty,
    continuo_values,
)
```

An ODCS contract carries custom properties as `{vendor, property, value}` items in a
`customProperties` list, on the contract, on its schema object and on a quality check. The items
with `vendor: continuo` are continuo's own, and their names are camel case. This module lists the
ones the package knows and where each may sit. It reads no file and checks no contract.

- `PROPERTIES` maps each property name to a `ContinuoProperty` row. The mapping is read-only.
  `sits_on` says which `customProperties` list carries the property, and `python` says who may
  write it in a Python contract.

  | `sits_on` | `python` | Names |
  |---|---|---|
  | `root` | `authored` | `service`, `kind`, `schedule`, `criticality`, `timeoutMinutes`, `script`, `readQueries`, `csvSource`, `extraColumns`, `config`, `secretRef`, `modeType`, `assets` |
  | `root` | `refused` | `filePath`, `materialization`, `contractEnforced`, `access`, `group`, `deprecationDate`, `freshnessBuildAfter`, `snapshot`, `quoteColumns`, `provenance` |
  | `schema` | `filled` | `reads` |
  | `quality` | `authored` | `where`, `warnIf`, `errorIf`, `failCalc` |

  `authored` means the author writes the property. `filled` means continuo writes it and the
  author may not. `refused` means no Python contract carries it. A property that is not in the
  mapping is unknown to this release.
- `continuo_values(custom_properties: object) -> dict[str, Any]` returns the value of each
  `continuo` item of a `customProperties` list, by property name, in the order of the names' first
  appearance in the list. It takes any object, so it can run on a contract that nothing has
  checked: an argument that is not a list gives `{}`, and an item that is not a mapping, has
  another vendor, or lacks a string `vendor`, a string `property` or a `value` is skipped. A value
  is returned as written, whether or not `PROPERTIES` names its property. A property written twice
  keeps its last value, in the place of its first appearance, so a caller that refuses a repeat
  looks at the list itself.

```python
continuo_values(
    [
        {"vendor": "lotse", "property": "schedule", "value": "x"},
        {"vendor": "continuo", "property": "schedule", "value": "daily"},
    ]
)  # {"schedule": "daily"}
PROPERTIES[
    "reads"
]  # ContinuoProperty(name="reads", sits_on="schema", python="filled")
```

### Public names

- `VENDOR`: the vendor name of continuo's custom properties, `continuo`.
- `ContinuoProperty`: one custom property, a frozen dataclass with the fields `name`, `sits_on`
  (`root`, `schema` or `quality`) and `python` (`authored`, `filled` or `refused`).
- `PROPERTIES`: the read-only mapping from each property name to its `ContinuoProperty`.
- `continuo_values`: returns the value of each `continuo` item of a `customProperties` list, by
  property name, and skips every item it cannot use.

## validate

```python
from continuo_contracts.validate import (
    REASONS,
    Issue,
    rule_issues,
    schema_issues,
    validate_python_contract,
)
```

`validate_python_contract` checks the ODCS contract of a Python node and returns every problem it
finds, each with the path of the place it is at and a closed reason code. It runs in two stages. The
first, `schema_issues`, checks the document against the packaged ODCS v3.2.0 JSON schema, after a
scan that refuses a column that nests. The second, `rule_issues`, applies continuo's rules to a
document that the first accepted, because the rules read a document of the shape the schema
describes. The module logs nothing.

- `Issue` is a frozen dataclass with the fields `path`, `reason` and `message`. `path` is written
  as `schema[0].properties[2].physicalType`, with `$` for the contract as a whole, and `reason` is
  one of `REASONS`. `str(issue)` is `<path>: <message>`.
- `REASONS` is the closed tuple of reason codes, so a consumer can label a counter with them. The
  checks of this module give these:

  | Reason | The problem |
  |---|---|
  | `odcs_schema` | the document does not fit the ODCS JSON schema |
  | `api_version` | `apiVersion` is not `v3.2.0` |
  | `identity` | the id, `name` or `physicalName` does not agree with the table, the id is not in lower case, the name of the table or the schema part of the id is not a name (blank, whitespace, a dot, a slash or a control character), or the contract does not hold exactly one schema object that is a table |
  | `file_name` | the base name of the file is neither `<table>.odcs.yaml` nor `<schema>.<table>.odcs.yaml` |
  | `service` | a `service` property names another service than the release's |
  | `missing_field` | `team.name`, `schedule`, `criticality`, the columns or the `physicalType` of a column is missing |
  | `refused_field` | the contract writes `servers`, `tenant` or `contractCreatedTs`, or a column has a `physicalName` other than its name, is partitioned, has a partition key position, nests `properties`, `items` or a `map`, or writes `logicalTypeOptions` |
  | `unknown_property` | a `continuo` property is not in `vocabulary.PROPERTIES` |
  | `refused_property` | a `continuo` property is one that continuo fills or no Python contract carries, or it sits on the wrong object |
  | `kind_rule` | a node misses a property its kind needs, writes one its kind refuses, or a python-csv node does not replace its table |
  | `column_type` | the `physicalType` of a column is not a type continuo accepts (`canonical_sql_type` refuses it, or its length is not from 1 to 2**53 - 1) |
  | `primary_key` | an `upsert` node has no key, a key column is not `required: true` or is `DOUBLE PRECISION`, or the columns of a key of several do not each have their own whole-number `primaryKeyPosition` from 1 |
  | `value` | a `continuo` property is written twice in one list, or has a value outside what it allows (a `service` that is not a string with text among them), the `kind` is not a Python kind, the name of a column is not a name or is the name of an earlier column, or a custom quality rule has an empty `implementation` |

  `strict_yaml` is for a caller that reports a `yaml_strict.StrictYamlError` as an issue, and this
  module never gives it.
- `schema_issues(doc: object) -> list[Issue]` first scans the columns of every schema object of
  `doc`. When a column carries `items`, a `map` or `properties`, it returns a `refused_field` issue
  at `schema[<n>].properties[<i>].<field>` for each such field, the issue `rule_issues` gives for
  it, and does not run the JSON schema. The column of a Python contract nests nothing, and the JSON
  schema check of a nested column takes time that grows exponentially with the depth of the
  nesting, while the scan takes time in proportion to the number of columns. The other problems of
  such a document show once the nesting is gone. Otherwise it gives one `odcs_schema` issue for
  each error of `doc` against the JSON schema, and an empty list when it fits. `doc` can be any
  value, as `yaml_strict.load` returns it: one that is not a mapping gives a single issue at `$`.
  Two errors with the same path and message give one issue, and so does the "Unevaluated
  properties" error of an object when every property it names has an error of its own, since the
  error of the value says what is wrong. A message longer than 300 characters is cut and ends in
  `...`.
- `rule_issues(doc: Mapping[str, Any], *, path: str, service: str | None) -> list[Issue]` applies
  the rules to a document that `schema_issues` accepted. `path` is the repository path of the file,
  of which only the base name counts (`/` and `\` both separate), and `service` is the service of the
  release that carries the contract, or `None` when it is not known, which skips the `service`
  check. The rules are these:
  - `apiVersion` is `v3.2.0`.
  - The contract holds one schema object, and its `physicalType` is `table`. Its `name` is the
    table. The `id` is in lower case and ends in `.<table>` in lower case after its schema part.
    Lower case here means the ASCII letters only: `A` to `Z` are compared as `a` to `z` and every
    other character as written, so `analytics.k\u00c4se` is the id of the table `K\u00c4SE` on
    every Python, whatever its Unicode tables. The root `name` and the `physicalName` of the schema
    object, when written, equal the table.
  - The base name of the file is `<table>.odcs.yaml`, with the table as written, or
    `<schema>.<table>.odcs.yaml`, with the schema part of the id. The issue is at `$`.
  - `team.name` is a string with text in it. A `team` written as a list has no name.
  - `servers`, `tenant` and `contractCreatedTs` are not written, not even empty.
  - A custom property of the `continuo` vendor is looked up in `vocabulary.PROPERTIES`, in every
    `customProperties` list of the contract, wherever it sits. A name the vocabulary does not know
    is `unknown_property`. A property that continuo fills, or that no Python contract carries, is
    `refused_property`, and so is a property in the list of another object than the one it
    belongs to: the contract, the schema object or a quality rule, so a property in the list of a
    column, of the description or of the team is on the wrong object. A property of another vendor
    is never looked at, whatever its name, and a `customProperties` key inside a free-form value is
    data. The free-form values are those of the canonical form in [contract](#contract), by object
    type: the `value` of a custom property, the `examples` of a column, the `implementation`,
    `arguments`, `mustBe` and `mustNotBe` of a quality rule, the `value` and `valueExt` of a
    service level and the `value` of an enum entry. The `value` of a column's `map` is a property
    and is walked. A property that sits where it may is then checked for a repeat, which is a
    `value` issue at each later copy that names the place of the first. A `service` property is a
    string with text (a `value` issue otherwise, even when the service is not known) and names the
    release's service (a `service` issue). Every refusal says why, such as that continuo fills
    `reads` from the tables the read queries name, or that a dbt property says nothing about a
    Python node.
  - A name, wherever this list asks for one, is a string that is not blank and has no whitespace
    at either end and no control character (U+0000 to U+001F and U+007F to U+009F). Whitespace is
    what `str.isspace` reads as whitespace, the same 29 code points on every supported Python.
  - The name of the table and the schema part of the id are names with no whitespace, dot or slash
    anywhere. A table name that fails is compared with nothing else, so it gives one issue.
  - The contract writes `schedule` and `criticality`, as customProperties of the contract: a
    missing one is a `missing_field` at `customProperties`. `schedule` is a name with no whitespace
    anywhere, at most 50 characters long, the longest schedule name continuo stores for a run.
    `script` is the path of a file inside the project, as `assets` describes it below.
    `criticality` is `REGULATORY`, `CORE` or `SECONDARY`. `timeoutMinutes` is an
    integer from 1 to 10080 (a bool or a float is not). `extraColumns` is `raise` or `warn`.
    `modeType` is `replace`, `append` or `upsert`. `config` is a mapping. `secretRef` is the name
    of a Kubernetes Secret, `continuo-api-` and then lowercase letters, digits and `-`, ending
    with a letter or a digit, at most 253 characters. `csvSource` is `s3://<bucket>/<key>` or
    `https://<host>[/...]`, each part with text. `readQueries` is a mapping of names to queries
    with text, at least one. `assets` is a list of paths of files inside the project, none listed
    twice. Such a path is a name, relative and POSIX, written in its normalised form: its parts are
    separated by `/` and not `\`, it does not start with `/`, `~` or a drive letter such as `C:`,
    and no part is empty, `.` or `..`, so `./a.json`, `a//b` and `a/../b` are refused and two
    paths that name one file are written alike. A bad value is a `value` issue at the item of the
    property.
  - The `kind` is `python-node` when the contract writes none, `python-model` is read as
    `python-node`, and any other word that is not `python-csv` or `python-api` is one `value`
    issue that also skips the rules below. A `python-node` writes `script` and `readQueries` and
    does not write `csvSource` or `secretRef`. A `python-csv` writes `csvSource`, does not write
    `script`, `readQueries`, `secretRef` or `assets` and replaces its table, so its `modeType` is
    `replace`. A `python-api` writes `script`, may write `secretRef` and `assets`, and does not
    write `readQueries` or `csvSource`. A property that is missing is a `kind_rule` issue at
    `customProperties`, and one that is refused is a `kind_rule` issue at its item and is not
    checked for its value.
  - The table lists at least one column (`missing_field` at `schema[0].properties`). Each column
    has a name, used once, and a `physicalType` (a `missing_field` when it is left out) that
    `canonical_sql_type` accepts, with a length from 1 to 2**53 - 1 (the largest integer canonical
    JSON writes) for a `VARCHAR(n)` or `CHAR(n)`, a `column_type` issue at `physicalType`
    otherwise. A column does not
    write a `physicalName` other than its name, `partitioned: true` or a `partitionKeyPosition`
    other than -1, and it nests no `properties`, `items` or `map`, because the column of a Python
    node has a name, a type and a nullability. It writes no `logicalTypeOptions`, which continuo
    derives from `physicalType`; a written `logicalType` is accepted and replaced. `unique` is not
    checked.
  - The `implementation` of a custom quality rule, on the schema object or a column, is not `""`
    or `{}` (`value`).
  - On an `upsert` node the key is the columns with `primaryKey: true`. There is at least one
    (`primary_key` at `schema[0].properties`), and each is `required: true` and not
    `DOUBLE PRECISION`. When there are several, each has its own whole-number `primaryKeyPosition`
    of 1 or more (`1.0` is 1, so `1` and `1.0` collide), which orders the key. A column of such a
    key that has none, or has 0 or a negative number, is one issue, and so is a column that
    repeats the position of an earlier one. A key of one column may leave its position out. A
    python-csv node gets no key rule, because its `modeType` is already refused. On a `replace`
    or `append` node `primaryKey` describes the table and is not checked.

  The issues come in path order, with the root first and array indexes in numeric order
  (`customProperties[4]` before `customProperties[10]`), then in message order.
- `validate_python_contract(doc: object, *, path: str, service: str | None) -> list[Issue]`
  returns `schema_issues(doc)` when that has any issue, and `rule_issues` of the same `path` and
  `service` otherwise. An empty list means the contract passed every check of this module.

```python
validate_python_contract(
    doc, path="contracts/kpis.odcs.yaml", service="service-py"
)
# [Issue(path="$", reason="file_name",
#        message="the file is named 'kpis.odcs.yaml', it must be 'py_daily_kpis.odcs.yaml' or "
#                "'analytics.py_daily_kpis.odcs.yaml'")]
```

### Public names

- `REASONS`: the closed tuple of the reason codes of an issue.
- `Issue`: one problem of a contract, a frozen dataclass with the fields `path`, `reason` and
  `message`.
- `schema_issues`: the issues of a document against the ODCS v3.2.0 JSON schema, or the
  `refused_field` issues of its columns that nest, which it finds first.
- `rule_issues`: the issues that continuo's rules find in a document that the schema accepted.
- `validate_python_contract`: every issue of a Python contract, the schema issues first and the
  rules once the schema accepts the document.

## contract

```python
from continuo_contracts.contract import (
    contract_hash,
    logical_type,
    normalise_python,
)
```

continuo stores a Python contract in one canonical shape, its stored form, and fingerprints it
with `contract_hash`. Two files that mean the same thing store the same document and get the same
hash, whatever their line endings, byte order mark, key order or quoting. The module logs nothing.

- `normalise_python(doc: Mapping[str, Any], *, service: str, reads: Sequence[str]) -> dict[str,
  Any]` returns the stored form of a contract that `validate.validate_python_contract` accepted,
  as a new dict, and leaves `doc` unchanged. `service` is the service of the release, and `reads`
  lists the tables that the node's read queries name, each `<schema>.<table>`; the library never
  reads SQL, so the caller lists them. The stored form is `doc` with these values filled:
  - `version: "0"` and `status: active`, in place of the written ones;
  - the root `name` and the `physicalName` of the schema object, set to the table when absent;
  - the `continuo` properties `kind` (the kind of the node, `python-model` stored as
    `python-node`, and `python-node` when none is written) and `service`, always written;
  - on each column, the `physicalType` as `python.canonical_sql_type` writes it (`date` is `DATE`,
    `int` is `INTEGER`, `decimal(12, 02)` is `NUMERIC(12,2)` and `VARCHAR(0010)` is `VARCHAR(10)`),
    so two spellings of a type store alike, and the `logicalType` and `logicalTypeOptions` that
    `logical_type` gives for it, in place of any written ones, and neither when it gives none;
  - when `reads` is not empty, `{vendor: continuo, property: reads, value: [...]}` on the schema
    object, with the tables in lower case (ASCII letters only), sorted by code point and listed
    once.

  It then writes the document in the canonical form, which walks the ODCS structure and, on each
  object:
  - strips description text: a `description` string, and each string of the contract's
    `description` object, gets LF line endings and loses its outer ASCII whitespace
    (`canonical.normalise_text`); a description is never removed for its content;
  - lower-cases the ASCII letters of the `severity` of a quality rule, so the result is the same
    on every Python version;
  - drops a value that is null, an empty string, an empty list or an empty object, and a value
    equal to the ODCS default of its object type: `deprecated: false` on a schema object or a
    column; `logicalType: object`, the only logical type ODCS allows there, on a schema object;
    `required: false`, `unique: false`, `primaryKey: false`, `primaryKeyPosition: -1`,
    `partitioned: false`, `partitionKeyPosition: -1`, `criticalDataElement: false` and
    `semanticType: column` on a column; `type: library` on a quality rule; `type: foreignKey` on
    a relationship;
  - drops the `physicalName` of a column when it equals the column's `name`;
  - drops the `primaryKeyPosition` of a column when it is the only column with `primaryKey: true`,
    since a position orders the columns of a key and a key of one column has no order, so `1`, `7`
    and no position store alike;
  - drops a custom property whose value is null or empty, and the `continuo` properties that
    say continuo's default, `modeType: replace` and `extraColumns: raise`;
  - sorts `customProperties`, `tags`, `roles`, `quality`, `relationships` and
    `authoritativeDefinitions` by the canonical JSON of each item and keeps an item that equals
    an earlier one once, and keeps every other list, the columns in `properties` among them, in
    file order.

  A free-form value keeps its content as written: the `value` of a custom property, the `examples`
  of a column, the `implementation`, `arguments`, `mustBe` and `mustNotBe` of a quality rule, the
  `value` and `valueExt` of a service level and the `value` of an enum entry. Nothing inside one is
  stripped, dropped or sorted, so a `tags` list inside a `config` keeps its order. A free-form value
  that is null or empty as a whole is dropped, except `mustBe`, `mustNotBe` and the `value` of a
  service level or an enum entry, which stay, because there the author compares with that value.
  `validate` reads the same keys as data. Everywhere, free-form values included, a float that is a
  whole number within -(2**53 - 1) to 2**53 - 1 becomes an integer, so `batch_size: 10.0` is stored
  as `10`.

  Raises `HashInputError` when `service` is empty or blank, when `reads` is one string, or lists a
  table that is not `<schema>.<table>`: a string of exactly two parts joined by a dot, neither
  empty, with no whitespace and no control character anywhere (`"."`, `"a."`, `".b"`,
  `"db.analytics.x"` and `"analytics. x"` are refused), and `CanonicalJsonError` when an item
  of a set-like list holds a value that canonical JSON cannot write, which a document that
  `yaml_strict.load` read never holds.
- `contract_hash(stored: Mapping[str, Any]) -> str` returns `"sha256:"` and the lowercase hex
  SHA-256 of the RFC 8785 canonical JSON of the stored form without its root keys `apiVersion`,
  `kind`, `status` and `version`, 71 characters in all. Those keys are the list
  `contract_hash_exclusions` of `fingerprints.yaml`, which `fingerprints.contract_hash_exclusions()`
  returns. Every stored form holds those four with the same values, so leaving them out changes
  nothing but keeps the hash free of the version continuo numbers from history. `stored` is
  hashed as it is, so pass the result of `normalise_python`. Raises `CanonicalJsonError` for a
  value that canonical JSON cannot write.
- `logical_type(physical_type: str) -> dict[str, Any] | None` returns the ODCS logical type of an
  SQL type, read as `python.canonical_sql_type` writes it: `INTEGER` and `BIGINT` are
  `{"logicalType": "integer"}`, `NUMERIC(p,s)` and `DOUBLE PRECISION` are `number`, `TEXT` is
  `string`, `VARCHAR(n)` and `CHAR(n)` are
  `{"logicalType": "string", "logicalTypeOptions": {"maxLength": n}}`, and `DATE`, `TIMESTAMP`
  and `BOOLEAN` are `date`, `timestamp` and `boolean`. A type that `canonical_sql_type` refuses
  gives `None`, and so does a `VARCHAR(n)` or `CHAR(n)` whose length is not from 1 to 2**53 - 1,
  the lengths the contract check accepts, however many digits it is written with. Each call
  returns a new dict.

```python
stored = normalise_python(
    doc, service="service-py", reads=["Analytics.Daily_Transactions"]
)
stored["schema"][0]["customProperties"]
# [{"vendor": "continuo", "property": "reads", "value": ["analytics.daily_transactions"]}]
contract_hash(
    stored
)  # "sha256:15268bb30f205e0e5cfb4791715391f3cf7ba527a671f319a6b77e1b087dda59"
logical_type(
    "varchar(64)"
)  # {"logicalType": "string", "logicalTypeOptions": {"maxLength": 64}}
```

### Public names

- `normalise_python`: returns the stored form of a Python contract that the contract check
  accepted, written in the canonical form.
- `contract_hash`: returns the `contract_hash` of a stored form, `"sha256:"` and 64 hex digits.
- `logical_type`: returns the ODCS logical type of an SQL type, or `None`.

## cli

```bash
continuo-contracts validate PATH... [--service SERVICE] [--json]
continuo-contracts hash FILE --service SERVICE [--read TABLE]... [--json]
```

```python
from continuo_contracts.cli import main
```

The package installs the console script `continuo-contracts`, which checks the ODCS contract of a
Python node. A producer's CI runs `validate` on its contract files, and a person runs `hash` to
see the `contract_hash` of a file. Both commands read a file with `yaml_strict.load` and check it
with `validate.validate_python_contract`, so they find what topology-controller finds. Both log
`this release checks Python contracts` at INFO, in either output mode.

- `validate PATH...` checks each `PATH`, a `*.odcs.yaml` file or a folder. A folder gives its
  `*.odcs.yaml` files in name order, without the folders below it. Every issue is logged at ERROR
  as `<file>: <path>: <message>`, and a last INFO line counts the files checked and the files with
  an issue. `--service` is the service of the release, and a `service` property that names
  another one is an issue; without `--service` that check is skipped. A `--service` that is empty
  or blank is a usage error. A text that `yaml_strict.load` refuses is the one issue
  `strict_yaml` at `$`, with the line and the column in its message. With `--json`, stdout is one
  document, and a file with no issue is listed with an empty `issues`:

  ```json
  {"valid": false,
   "files": [{"path": "contracts/kpis.odcs.yaml",
              "issues": [{"path": "$", "reason": "file_name", "message": "..."}]}]}
  ```

- `hash FILE --service SERVICE` checks one file and prints its `contract_hash` alone on stdout.
  With `--json`, stdout is `{"contract_hash": "sha256:...", "stored": {...}}`, where `stored` is
  the stored form that the hash is of. `--service` is required, because it is part of the stored
  form, and it may not be empty or blank. `--read TABLE` names one table that the node's read
  queries name, written `<schema>.<table>` (exactly two parts joined by a dot, neither empty, with
  no whitespace and no control character), and may be repeated. topology-controller passes these
  at release time and the hash covers them, so pass the same tables to get the hash it stores. A
  file with an issue logs each issue at ERROR, prints nothing on stdout and exits 1.

The human text goes through `logging` to stderr. Stdout holds only the result, so
`continuo-contracts hash ... > hash.txt` and `continuo-contracts validate ... --json | jq` read
it whole. When the command cannot run, stdout is empty.

| Exit status | Meaning |
|---|---|
| 0 | every contract is valid, or `hash` printed its hash |
| 1 | a contract has an issue |
| 2 | a usage error of the arguments (a `--service` that is empty or blank among them), a path that names no contract file (a folder with no `*.odcs.yaml` file, a path that does not exist, a file not named `*.odcs.yaml`), a path that cannot be looked up (one below a folder the process may not search, among them), a folder that cannot be listed, a file that is not UTF-8 text or cannot be read, or a `--read` value that is not a table. An ERROR line names each such path and what the system refused |

Nothing is checked when one path of the command is refused with status 2.

- `main(argv: Sequence[str] | None = None) -> int` runs the command with `argv` (`sys.argv[1:]`
  by default) and returns the exit status. It sets up logging to stderr when it runs, not when
  the module is imported. An error of the arguments exits through `argparse` with status 2, as
  does `--help` with status 0.

```bash
continuo-contracts hash contracts/py_daily_kpis.odcs.yaml --service service-py \
  --read analytics.daily_transactions
# sha256:15268bb30f205e0e5cfb4791715391f3cf7ba527a671f319a6b77e1b087dda59
```

### Public names

- `main`: runs the `continuo-contracts` command and returns its exit status.

## errors

```python
from continuo_contracts.errors import (
    CanonicalJsonError,
    ContractsError,
    FingerprintsFileError,
    HashInputError,
    ProjectionError,
    SchemaFileError,
    StrictYamlError,
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
  form they do not accept, and the message names the key. `contract.normalise_python` refuses an
  empty or blank `service` and a `reads` entry that is not `<schema>.<table>` (exactly two parts
  joined by a dot, neither empty, with no whitespace and no control character), and the message
  names the service or the entry.
- `ProjectionError`: `canonical_sql_type`, `PythonNode.from_mapping`, `code_projection` and
  `projection_hash` refuse an SQL type or a Python node.
- `FingerprintsFileError`: `parse_fingerprints` and `load` refuse a fingerprints file that is not
  in the form described under [fingerprints](#fingerprints), and the message names the location in
  the file.
- `SchemaFileError`: `schema.load` refuses a packaged schema file whose SHA-256 is not the pinned
  one, and the message names the file and both hashes.
- `StrictYamlError`: `yaml_strict.load` refuses a text, and the message is
  `line <line>, column <column>: <reason>`. It has the numbers `line` and `column` (counted from
  1, and 0 when the reason has no position in the text) and the `reason` text.

## Vectors

The wheel ships six data files in `continuo_contracts/vectors/`. Each is a JSON list of cases
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
- `strict_yaml.json` is a list of `{"name", "text", "value"}`. `text` is a whole YAML document and
  `value` is the JSON that `yaml_strict.load` reads it as, which a consumer compares with its types
  (`1000.0` is a float, `1` an integer and `true` a boolean). A case `{"name", "text", "error":
  true}` must be refused. The cases cover the scalars that YAML 1.1 reads as booleans, numbers and
  dates and the core schema reads as strings (`on`, `yes`, `NO`, `1:20`, `2026-11-14`, `1_000`),
  the core integers, floats, booleans and nulls, the safe integer range, a lone `!` before a
  scalar, a quoted `"<<"` key, a byte order mark with CRLF, and the refusals: infinities and NaN,
  integers outside the range, repeated keys, keys that are not strings, tags other than the core
  ones, aliases, merge keys, two documents, two byte order marks, a control character and a lone
  surrogate. The file is ASCII, so the byte order mark and the line endings are written as JSON
  escapes.
- `contracts.json` is a list of `{"name", "path", "service", "reads", "text", "stored",
  "contract_hash"}`. `text` is a whole Python contract file, `path` its repository path, `service`
  the service of the release and `reads` the tables its read queries name. Read `text` with
  `yaml_strict.load`, check it with `validate.validate_python_contract(doc, path=path,
  service=service)`, and pass it to `contract.normalise_python(doc, service=service, reads=reads)`:
  the result is `stored`, and `contract.contract_hash(stored)` is `contract_hash`. A case `{"name",
  "path", "service", "reads", "text", "issues"}` is refused, and `issues` lists the `{"path",
  "reason"}` of each issue in order; a text that `yaml_strict.load` refuses gives the one issue
  `{"path": "$", "reason": "strict_yaml"}`. The valid cases are the three demo contracts of
  service-py, three contracts that write every field a Python contract may carry, one per kind, and
  the python-api one again in every spelling the canonical form removes (a byte order mark and CRLF,
  lists out of order with a repeat, descriptions with outer whitespace, an upper-case severity,
  `type: library`, `logicalType: object`, `primaryKey: false`, a column `physicalName` equal to its
  name and an empty `valueExt`), whose `stored` and `contract_hash` are those of the clean file, and
  the demo `py_daily_kpis` again with its column types spelled ` Date`, `int` and
  `decimal(012, 02)`, which stores and hashes like the demo file. The refused ones are a `NUMERIC(12)` column, a contract that names its `servers`, a file name that
  does not match the table, a repeated key, and one case for each name and path rule: a schema
  part of the id that is blank, has whitespace or has a slash, a table name with whitespace or a
  control character, a column name with outer whitespace or a control character, a read query
  name with outer whitespace, a `schedule` with outer whitespace or longer than 50 characters, a
  `script` with outer whitespace or that is not a normalised path, six `assets` that are not
  normalised paths, a `VARCHAR(0)` column and a blank `service` property. The file is ASCII.

## Pinning

Pin an exact version (`continuo-contracts==X.Y.Z`), because a hash is only comparable across
processes that run the same code. A dbt producer uses the version its continuo release
documents. `continuo_contracts.__version__` is the version of the installed distribution.

A producer bundle names the version of this package that wrote it. topology-controller refuses a
bundle written by a newer version than the one it runs, because a newer version may accept a
contract that its own refuses or read one differently, and it takes a bundle written by an older
version, because every contract an older version accepted is accepted by a newer one (see
[Stability](#stability)). A producer therefore pins a version that is not newer than the one its
continuo release runs.

## Stability

A hash that this package computes is a contract. A change that moves any hash pinned in
`continuo_contracts/vectors/` or in the tests moves `content_hash` for every node of every
install, so it ships only together with a re-baseline in continuo: a continuo release of each
service, run once after such a change, that recomputes every node's stored hash with the new
code and validates and reloads nothing.

A `contract_hash` pinned in `vectors/contracts.json` is a contract in the same way: a change that
moves one gives every Python node of every install a new contract version.

The contract check only widens. A newer version accepts every contract that an older version
accepted, and gives it the same stored form and the same `contract_hash`: the vocabulary of a
contract (its kinds, custom properties and values) grows, and a rule that would refuse a contract
an earlier version accepted is not added. A producer that upgrades the package does not have to
edit a contract file for the check to pass.

## Contributing

See [CONTRIBUTING.md](https://github.com/carolsimone/continuo-contracts/blob/main/CONTRIBUTING.md). Report a vulnerability as described in
[SECURITY.md](https://github.com/carolsimone/continuo-contracts/blob/main/SECURITY.md).

## License

Apache License 2.0. See [LICENSE](https://github.com/carolsimone/continuo-contracts/blob/main/LICENSE) and [NOTICE](https://github.com/carolsimone/continuo-contracts/blob/main/NOTICE).
