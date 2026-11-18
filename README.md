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
code_projection(node)["output_columns"][1]
# {"name": "tx_count", "nullable": False, "type": "INTEGER"}
projection_hash(node)
# "a01d7d6065f055bb2d87959aaf178d08f5a3f96c86912099b9876239d84f6efe"
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
code projection and the root keys of the stored form that `contract_hash` leaves
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
- `contract_hash_exclusions()` returns the root keys of the stored form that `contract_hash`
  leaves out, `apiVersion`, `kind`, `status` and `version`, as a `frozenset`.
  `contract.contract_hash` reads this list, so the file and the hash cannot disagree.
- `classify(family, name)` returns the entry that classifies the input `name` of a node of
  `family`, or `None`. An entry has `family` (the section it is in), `id`, `field`, `source`,
  `fingerprint`, `parts`, `why`, `inputs`, `when` and `rule`, and `entry.moves(part)` says whether
  the input moves that part of `content_hash`. In a dbt family, a `config.<key>` that no entry
  names and whose key is not in the denylist belongs to the family's `other_config` entry. It does
  not classify a sub-key of a denylisted key (`config.meta.owner` gives `None`), and a dotted name
  such as `config.contract.enforced` is classified only where an entry declares it as its own
  input (the dbt model, seed and snapshot each have one), so in the dbt test family it belongs to
  `other_config`. In a Python family, an `odcs.<path>` that no entry names belongs to the family's
  `standard_field` entry, which classifies every standard ODCS field of the stored form as
  `contract`; a `continuo` custom property (`odcs.custom.<property>` or
  `odcs.schema.custom.<property>`) is classified only by an entry, so one that no entry names
  gives `None`. A dbt family has no such rule: its entries name every `odcs.<path>` that
  `contract_hash` hashes (it leaves out the keys of `contract_hash_exclusions`), and one they do
  not name gives `None`. It raises `ValueError` for a `family` that is not one of the seven.
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
a contract's stored form: `odcs` and then the ODCS names of the path as they are written
(camelCase allowed) with the array indexes dropped, such as `odcs.team.name` and
`odcs.schema.properties.logicalType`; a `continuo` custom property is `odcs.custom.<property>` on
the contract (`odcs.custom.timeoutMinutes`) and `odcs.schema.custom.<property>` on the schema
object (`odcs.schema.custom.reads`); `odcs.*` is the one name of the `standard_field` entry. This
repository's guard tests check that an entry names nothing outside that vocabulary and that
`docs/fingerprints.md` writes each name of the tuple and no other fixed name.

A pull request that adds an input to a hash adds it to `fingerprints.yaml` in the same change. A
projection key or a denylisted `config` key without an entry fails this repository's guard test, so
does a field of the stored form of a Python or a dbt contract that no entry or rule classifies as
`contract` or `both`, and `unclassified()` finds any other input without one.

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
- `contract_hash_exclusions`: the root keys of the stored form that `contract_hash` leaves out, as
  a `frozenset`.
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
load("a: on\nb: 1e3\nc: 2026-11-14\n")
# {"a": "on", "b": 1000.0, "c": "2026-11-14"}
load("a: &x 1\nb: *x\n")
# StrictYamlError: line 2, column 4: YAML aliases are not allowed
```

### Public names

- `load`: reads one YAML document as the YAML 1.2 core schema and raises `StrictYamlError` for
  aliases, merge keys, repeated keys, keys that are not strings, other tags, more than one
  document, nesting that is too deep and values that canonical JSON cannot write.

## dbt

```python
from continuo_contracts.dbt import (
    DEFAULT_CRITICALITY,
    FLAGS,
    RESOURCE_TYPES,
    SEED_SCHEDULE,
    SKIP_REASONS,
    Generated,
    RunFacts,
    generate,
    logical_type,
    node_service,
    project_service,
    read_tree,
    run_facts,
    schedule,
    seed_header_issues,
    skip_reason,
    table_name,
    tests_by_node,
    unique_id,
)
```

continuo keeps the models, seeds and snapshots of a dbt `manifest.json` that no gate leaves out.
These functions are those gates and the names a kept node gets. They read a node as `json.load`
returns it from the manifest (a `Mapping[str, Any]`), so a producer's CI and continuo's parser give
the same answer for the same node. A field in a shape they cannot read raises `DbtNodeError` (a
`ContractsError`), and the message starts with the node's id and names the field, as in
`model.shop.orders: tags must be a list of strings, got str`.

The gates run in this order and the first one that applies names the reason:

1. `no_owner`: the node has no `config.meta.owner`, or an empty one. A `config` or a `meta` that
   is missing or is not a mapping is no owner either.
2. `local_stub`: the node has the tag `local_stub`.
3. `installed_package`: the node has no tag and its service is not the project's. dbt applies the
   project's top-level `+meta` to the nodes of its installed packages too, so such a node can
   carry the project's owner and still not be the project's to schedule. With no project (the
   manifest names none and no service is declared) no node is left out for this reason.

A node's service is the first entry of its `fqn` with `_` written `-`, and a node with no `fqn`
has no service (`""`). The project is `metadata.project_name` written the same way, else the
declared service, else `""`. A node's id is `<schema>.<table>` lowered as one string with
`str.lower`, so a final capital sigma at the end of the table takes its final form, while the
`reads` of a contract lower only their ASCII letters.

```python
manifest = json.loads(text)
project = project_service(manifest, declared_service="")
kept = {
    unique_id(node): schedule(node)
    for node in manifest["nodes"].values()
    if node["resource_type"] in RESOURCE_TYPES
    and skip_reason(node, project=project) is None
}
# {"analytics.orders": "daily", "analytics.customer.v2": "daily", ...}
```

`generate` writes the ODCS contract of a kept node from the node and its tests, with nothing from
the database or the compiled SQL:

- the envelope `apiVersion: v3.2.0`, `kind: DataContract`, `version: "0"` and `status: active`;
- `id` from `unique_id`, `name` from `table_name`, the node's `description`, trimmed, as
  `description.purpose`, `config.meta.owner` as `team.name`, and the node's `tags` in the order
  dbt lists them, so a project's tags come before the ones a model adds;
- `roles`: each of `config.meta.continuo.readers` as `{role: <reader>, access: read}`, then each
  grantee of each privilege of `config.grants`, in privilege name order, as
  `{role: <grantee>, access: <privilege>}`; a reader or a grantee that is empty or blank raises
  `DbtNodeError`, since the stored form would drop it and ODCS requires a role;
- the root `customProperties`, each a `{vendor: continuo, property, value}` item, in this order and
  only when it has a value: `service` (`node_service`), `kind` (`dbt-model`, `dbt-seed` or
  `dbt-snapshot`), `schedule` (`schedule`, left out for a dormant node), `criticality`
  (`config.meta.criticality`, else `SECONDARY`), `timeoutMinutes` (`config.meta.timeout_minutes`),
  `filePath` (`original_file_path`), `materialization` (`{materialized}` with `uniqueKey` and
  `incrementalStrategy` when set and `onSchemaChange` when it is not `ignore`), `contractEnforced`
  (`true` when `config.contract.enforced` is), `access` (a model's `config.access` when it is not
  `protected`), `group` (`config.group`), `deprecationDate` (the date part of a model's
  `deprecation_date` as written: a calendar date `YYYY-MM-DD` in ASCII digits, alone or followed by
  `T` or a space and a time, which is not read, so it is the date the team wrote in every time zone
  and `2026-11-18T24:00:00` is 2026-11-18; any other form, such as `2026-W47-3`, `20261118` or
  `2026-02-30`, raises `DbtNodeError`), `freshnessBuildAfter` (`{count, period}` of a model's
  `config.freshness.build_after`), `snapshot` (a snapshot's `strategy`, `uniqueKey`, `updatedAt`,
  `checkCols`, `hardDeletes`, `metaColumnNames` and `validToCurrent`, each when set) and
  `quoteColumns` (a seed's `config.quote_columns` when set);
- one schema object with `name` the table, `physicalName` the alias (else the table),
  `physicalType` `view` for a view, `ephemeral` for an ephemeral model and `table` otherwise, the
  node's description, `config.meta.continuo.reads` as its `reads` custom property, its
  `properties` and its `quality`.

The `properties` are the columns the node declares, in the order of its `columns` (dbt's declared
order), then, on a snapshot, the columns dbt adds to its table: `dbt_scd_id`, `dbt_updated_at`,
`dbt_valid_from`, `dbt_valid_to`, and `dbt_is_deleted` when `hard_deletes` is `new_record`, each
under the name `snapshot_meta_column_names` gives it and left out when the snapshot declares it.
A declared column has:

- `name`, `description` (trimmed) and `tags`;
- `physicalType`, the declared `data_type` trimmed, or on a seed the column's
  `config.column_types` entry, which wins; and the `logicalType` (and `logicalTypeOptions`) that
  `logical_type` gives for it;
- `classification` from the column's `meta.continuo.classification`;
- `primaryKey: true` when a model's `primary_key` lists it, with `primaryKeyPosition`, its place
  in that list counted from 1, when the key has two or more columns;
- what the tests on it say, below.

Each dbt test the node has becomes a quality rule, or a relationship:

- `not_null` is `{id: <test name>, metric: nullValues, mustBe: 0}`, `unique` the same with
  `duplicateValues`, and `accepted_values` `{id, metric: invalidValues, mustBe: 0, arguments:
  {validValues: <values>, ...}}`, whose arguments also keep its other kwargs as written (but
  `model` and `column_name`, which says where the rule goes, below), such as `quote: false`, which
  changes what the test compares. A
  `not_null` test makes its column `required: true`, and a `unique`
  test makes it `unique: true`, only when the test has severity `error`, no `where`, and dbt's
  default `warn_if`, `error_if` and `fail_calc`.
- A strict `relationships` test (severity `error`, no `where`, dbt's default `warn_if`, `error_if`
  and `fail_calc`, the same rule as for `required` and `unique`) adds `{to: "<target id>.<field>"}`
  to the column's `relationships`. The target is the entry of the test's `depends_on.nodes` other
  than its `attached_node`: a manifest node is named by `unique_id`, a source by
  `<schema>.<identifier or name>` in lower case, and with no other entry the target is the node
  itself. A `relationships` test that is not strict is a `custom` rule like any other generic
  test, below, with its severity and its `where`, and adds no relationship.
- Any other generic test is `{id, name: <test name>, type: custom, engine: dbt, implementation:
  {test, arguments}}`, where `test` is the generic test's name, written `<namespace>.<name>` for a
  test from a package (so `not_null` from a package is not dbt's), and `arguments` are its kwargs
  but `model` and `column_name`, left out when there are none.
- A singular test is `{id, name, type: custom, engine: dbt, implementation: <test name>}` on the
  schema object.
- Every rule from a test carries `severity` when it is not `error` (`warn` is written `warning`),
  the test's `description` and `tags`, and `where`, `warnIf`, `errorIf` and `failCalc` as
  `continuo` properties when they are set and are not dbt's defaults. A relationship has no field
  for them, so only a strict test gives one.
- The column a test checks is its `column_name` kwarg when that differs from the test's own
  `column_name`, and the test's own `column_name` otherwise: dbt writes a test declared on the
  model with a null `column_name` and the column or the SQL expression it checks in the kwarg, and
  a column test whose arguments name another column keeps the column it is declared under in its
  own `column_name`. A name in one pair of SQL quotes, `"..."`, `` `...` `` or `[...]` with a
  doubled closing mark read as one, as dbt writes a column with `quote: true` in a test and in a
  model's inferred `primary_key`, names the declared column inside the quotes when no declared
  column has the quoted name itself; no adapter is read. The contract carries the declared name.
- A test on a column the node does not declare, or on an expression, goes on the schema object, as
  the same rule with that column or expression as `column_name` in its arguments (inside
  `implementation` for a `custom` rule), and sets no `required` or `unique`; a `relationships`
  test there is a `custom` rule.

The rules on a column, and the test rules on the schema object, come in the order of their
canonical JSON. The schema object's table tests follow them in the order the team writes them:
each item `{name, enabled, granularity, params}` of `config.meta.continuo.tests` is
`{type: custom, engine: data-quality, name, implementation: {enabled, granularity, params}}`, with
`enabled` `true` when it is not set.

`flags` names the marks of `FLAGS` the contract carries: `jinja_in_test_arguments` when a test's
kwargs (but `model`, and a `column_name` equal to the test's own) or its `where` hold `{{` or `{%`,
which the rule keeps as written; `test_column_not_declared` when a test checks a column the node
does not declare or an expression; and `primary_key_column_not_declared` when a model's
`primary_key` names a column the node does not declare, which is left out.

A field with no value is left out of the document. The result does not depend on the order of the
tests, on the order of the keys of the node other than `columns`, or on the machine's time zone.

`source_sha256` covers exactly the fields `generate` reads and nothing else of the node: the node's
`resource_type`, `name`, `version`, `schema`, `alias`, `fqn`, `original_file_path`,
`description`, `tags`, `deprecation_date` and `primary_key`; from `config`, the keys the lists
above map (`contract.enforced` and `freshness.build_after`'s `count` and `period` only, not the
rest of `contract` or `freshness`, and `column_types`); from `config.meta`, `owner`, `criticality`
and `timeout_minutes`; from `config.meta.continuo`, `readers`, `reads` and each table test's
`name`, `enabled`, `granularity` and `params`; and from each column, in their order, its `name`,
`description`, `data_type`, `tags` and `meta.continuo.classification`. Of each test it covers the
`name`, `column_name`, `attached_node`, `depends_on.nodes`, `test_metadata`'s `name`,
`namespace` and kwargs (but `model`, and a `column_name` equal to the test's own `column_name`,
which that field covers), `description`, `tags`, and `config`'s
`severity`, `where`, `warn_if`, `error_if` and `fail_calc`; and of a relationship's target, a
node's `schema`, `name` and `version` or a source's `schema`, `identifier` and `name`. Any other
`config` or `meta` key, a column's `quote` or `constraints`, a test's `limit` or `store_failures`,
`created_at`, `raw_code` and the compiled SQL never move it. A field it reads whose value canonical
JSON cannot write, such as an integer outside -(2**53 - 1) to 2**53 - 1, raises `DbtNodeError`
naming the field; such a value in a field it does not read changes nothing.

`read_tree` gives these fields as trees, and `source_sha256` is the lowercase hex SHA-256 of the
canonical JSON (`canonical.canonical_json`) of an object with three keys:

- `node`: the fields of the node that `read_tree("node")` names;
- `tests`: for each test, the fields that `read_tree("test", test=test)` names, the list sorted by
  the canonical JSON of each item, so the order of the tests does not count;
- `targets`: for each manifest node or source that a strict `relationships` test on a declared
  column points to, other than the tested node, its manifest key, such as
  `model.shop.customer.v2`, mapped to the fields that `read_tree("nodes")` or
  `read_tree("sources")` names. A mapping with no such target is `{}`.

A tree picks the fields of a value: each key of the tree that the value holds keeps its value,
picked by the tree under that key, a key the value does not hold is left out (nothing is written
for it), and a leaf `{}` keeps the value whole. A `*` step turns a mapping into a list of
`[key, value]` pairs in the mapping's own order and keeps the items of a list in their order, so
the columns are an ordered list of `[name, column]` pairs, whose order counts although canonical
JSON sorts object keys. A value that is not a mapping where the tree names keys, or neither a
mapping nor a list at a `*`, is kept whole. Left out are the `unique_id` of the node and of each
test, every field no tree names, and the target of a `relationships` test that is not strict or
is on a column the node does not declare.

```python
generated = generate(
    node, tests_by_node(manifest).get(node["unique_id"], []), manifest=manifest
)
generated.document["id"]  # "analytics.fx_transactions_eur"
generated.document["schema"][0]["properties"][0]
# {"name": "transaction_id", "primaryKey": True, "required": True, "unique": True,
#  "quality": [{"id": "not_null_fx_transactions_eur_transaction_id", "metric": "nullValues",
#               "mustBe": 0}, {"id": "unique_fx_transactions_eur_transaction_id",
#               "metric": "duplicateValues", "mustBe": 0}]}
generated.flags  # ()
logical_type("varchar(64)")
# {"logicalType": "string", "logicalTypeOptions": {"maxLength": 64}}
```

`run_facts` reads back from a dbt contract what continuo runs the node with, so a reader checks
that the contract and the node it parsed agree. It reads a contract that
`validate.validate_dbt_contract` accepted, as `generate` wrote it or in its stored form, and both
give the same facts. Each value is read as the contract holds it, and none is checked: `unique_id`
is the `id`, `schema` the part of the `id` before its first dot, which is the `id` without
`.<table>` (so it is lower case), `table` the schema
object's `name`, `physical_name` its `physicalName` (else the table), `owner` the `team.name`,
`materialized` the `materialized` of the `materialization` property, and `service`, `kind`,
`schedule`, `criticality`, `timeout_minutes` and `file_path` the root properties `service`, `kind`,
`schedule`, `criticality`, `timeoutMinutes` and `filePath`. A dormant node has no `schedule`
property, and its `schedule` is `""`, as `schedule(node)` gives; with no `timeoutMinutes` the
`timeout_minutes` is `None`; with no `criticality` the `criticality` is `DEFAULT_CRITICALITY`.

```python
from continuo_contracts.contract import normalise_dbt

facts = run_facts(generated.document)
facts.table  # "fx_transactions_eur"
facts.schema, facts.schedule  # ("analytics", "daily")
facts == run_facts(normalise_dbt(generated.document, reads=[]))  # True
```

### Public names

- `RESOURCE_TYPES`: `("model", "seed", "snapshot")`, the resource types of the nodes that can get a
  contract.
- `SKIP_REASONS`: `("no_owner", "local_stub", "installed_package")`, the values of `skip_reason`
  in the order of the gates.
- `DEFAULT_CRITICALITY`: `"SECONDARY"`, the criticality of a node whose `meta` names none.
- `SEED_SCHEDULE`: `"seed"`, the schedule of a seed that carries no tag.
- `node_service`: `node_service(node) -> str` returns the first entry of the node's `fqn` with `_`
  written `-`, and `""` for a node with no `fqn` or an empty one. An `fqn` that is not a list of
  strings raises `DbtNodeError`.
- `project_service`: `project_service(manifest, declared_service) -> str` returns the manifest's
  `metadata.project_name` with `_` written `-`, else `declared_service` as given. A `metadata`
  that is not a mapping, or a `project_name` that is not a string, raises `DbtNodeError`, whose
  message starts with `manifest`.
- `skip_reason`: `skip_reason(node, *, project) -> str | None` returns the first of
  `SKIP_REASONS` that applies to the node, or `None` for a node continuo keeps. `project` is what
  `project_service` returns, and with `""` no node is left out as `installed_package`. It raises
  `DbtNodeError` for `tags` that are not a list of strings and for an `fqn` that is not a list of
  strings, only for a node that an earlier gate lets through.
- `schedule`: `schedule(node) -> str` returns the node's first tag, else `seed` for a seed, else
  `""`, the schedule of a dormant node (a model or snapshot with no tag), which runs only by hand.
  It raises `DbtNodeError` for `tags` that are not a list of strings.
- `table_name`: `table_name(node) -> str` returns the node's `name`, or `<name>.v<version>` for a
  versioned model, keeping the case. A `name` that is not a non-empty string, or a `version` that
  is neither a string nor a number, raises `DbtNodeError`.
- `unique_id`: `unique_id(node) -> str` returns `<schema>.<table>` in lower case, with the table of
  `table_name`; the alias is not part of it. A `schema` that is not a non-empty string raises
  `DbtNodeError`.
- `tests_by_node`: `tests_by_node(manifest) -> dict[str, list[Mapping[str, Any]]]` joins the nodes
  of resource type `test` to the nodes they test, the way dbt counts them: a test counts for its
  `attached_node` when it has one, else for every entry of its `depends_on.nodes`, once for each.
  The key is a node id and the tests of one node come in the order of the manifest's keys. A
  `nodes` that is not a mapping of mappings raises `DbtNodeError`, whose message starts with
  `manifest`, and so does, starting with the test's id, a test whose `attached_node` is not a
  string, whose `depends_on` is not a mapping or whose `depends_on.nodes` is not a list of
  strings.
- `FLAGS`: `("jinja_in_test_arguments", "test_column_not_declared",
  "primary_key_column_not_declared")`, the marks a generated contract may carry for a fact that
  `generate` keeps as written but that the team may want to look at.
- `Generated`: what `generate` returns, a frozen dataclass with the fields `document` (the ODCS
  contract, a `dict`), `flags` (the marks of `FLAGS` the contract carries, a tuple) and
  `source_sha256` (the lowercase hex SHA-256 of the canonical JSON of the manifest fields
  `generate` reads for the node and its tests, and of nothing else).
- `generate`: `generate(node, tests, *, manifest) -> Generated` writes the ODCS contract of a node
  that `skip_reason` keeps, from the node, its tests as `tests_by_node` joins them and the manifest
  they come from, which holds the targets of its `relationships` tests. A test that is not a
  mapping, a manifest `nodes` or `sources` that a `relationships` test reads and that is not a
  mapping, a field of the node, of a test or of a target in a shape it cannot read, such as a
  `config.meta` that is not a mapping, a `config.meta.owner` that is not a non-empty string or a
  test `severity` other than `warn` or `error`, or a field it reads whose value canonical JSON
  cannot write, raises `DbtNodeError`. The message starts with the node's id; for a test it then
  names the test, as in `model.shop.orders: test test.shop.not_null_orders_id.1a2b: config.where
  must be a string, got int`.
- `logical_type`: `logical_type(data_type) -> dict[str, Any] | None` returns the ODCS logical type
  of a declared column type. The type is a name, then at most one `(...)` with no parenthesis inside
  it, then nothing but whitespace: text after the closing `)`, as in `varchar(10) not null`,
  `varchar(10)x` or `varchar(10) with time zone`, gives `None`. The one exception is `timestamp`,
  whose `(...)` may be followed by `with time zone` or `without time zone`, in any case and with
  whitespace between the words, so `timestamp(6) with time zone` and `TIMESTAMP(3) WITHOUT TIME
  ZONE` are `timestamp`. Whitespace is a space or a tab and no other character, so a newline or
  U+001C is part of the name. The name is read in lower case, without the whitespace at its ends and
  with each run of whitespace inside it read as one space, so `varchar (10)` is `varchar` and
  `Character  Varying` is `character varying`: `int`, `integer`, `int4`, `bigint`, `int8`,
  `smallint` and `int2` are `integer`; `numeric`, `decimal`, `double precision`, `float`, `float4`,
  `float8`, `real` and `double` are `number`; `text`, `string`, `varchar`, `character varying`,
  `char` and `character` are `string`; `date` is `date`; `timestamp`, `timestamptz`, `timestamp with
  time zone`, `timestamp without time zone` and `datetime` are `timestamp`; `boolean` and `bool` are
  `boolean`. Any other name gives `None`, and so does an array type (one with `[`, such as
  `integer[]` or `varchar(10)[]`) and a type with a character outside ASCII. A string type whose
  parentheses hold a length, ASCII digits with optional whitespace around them, has
  `logicalTypeOptions: {maxLength: n}` when the length, read with its leading zeros left out (so
  `varchar(00000000000000010)` has 10), is from 1 to 2**53 - 1; another length, such as `0`, or
  other text, such as `max`, keeps the type with no `maxLength`. The result is a new dict, such as
  `{"logicalType": "integer"}`.
- `read_tree`: `read_tree(part, *, test=None) -> dict[str, Any]` returns the fields that `generate`
  reads, and `source_sha256` covers, of one part of its input, as a tree: `part` is `node` for
  the model, seed or snapshot, `test` with the test node as `test`, and `nodes` or `sources` for
  the manifest node or source that a `relationships` test points to. The tree is a dict whose keys
  are field names, each holding the tree of the fields read inside it and `{}` for a field read
  whole; the key `*` stands for every key of a mapping or every item of a list, in order. A
  test's tree names each of its own kwargs but `model` and a `column_name` equal to the test's own
  `column_name`. The result is a new dict
  on every call. It raises `ValueError` for another `part`, for `test` with no test node, and for a
  test node given with another part.
- `RunFacts`: what `run_facts` returns, a frozen dataclass with the fields `unique_id`, `schema`,
  `table`, `physical_name`, `service`, `kind`, `schedule`, `criticality`, `timeout_minutes` (an
  `int`, or `None` when the contract sets none), `file_path`, `owner` and `materialized`.
- `run_facts`: `run_facts(document) -> RunFacts` reads the run facts of a dbt contract that
  `validate.validate_dbt_contract` accepted, generated or stored, as the contract holds them.
  `schedule` is `""` for a dormant node and `timeout_minutes` is `None` when the contract has no
  `timeoutMinutes`.
- `seed_header_issues`: `seed_header_issues(declared, header) -> list[str]` compares the column
  names a seed's contract declares with the names in the header of its CSV file, and gives one
  message for each column the two disagree on: first, in the order of `header`, `<column> is in
  the CSV header but not declared`, then, in the order of `declared`, `<column> is declared but
  not in the CSV header`. Names are compared exactly as written, so `ID` and `id` are two columns,
  and a name a list repeats gets one message. Agreeing lists, in any order, give `[]`.

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
  `sits_on` says which `customProperties` list carries the property, `python` says who may write
  it in a Python contract, and `dbt` says what a dbt contract does with it.

  | `sits_on` | `python` | `dbt` | Names |
  |---|---|---|---|
  | `root` | `authored` | `written` | `service`, `kind`, `schedule`, `criticality`, `timeoutMinutes` |
  | `root` | `authored` | `not_used` | `script`, `readQueries`, `csvSource`, `extraColumns`, `config`, `secretRef`, `modeType`, `assets` |
  | `root` | `refused` | `written` | `filePath`, `materialization`, `contractEnforced`, `access`, `group`, `deprecationDate`, `freshnessBuildAfter`, `snapshot`, `quoteColumns` |
  | `root` | `refused` | `not_used` | `provenance` |
  | `schema` | `filled` | `filled` | `reads` |
  | `quality` | `authored` | `written` | `where`, `warnIf`, `errorIf`, `failCalc` |

  `authored` means the author writes the property. `filled` means continuo writes it and the
  author may not. `refused` means no Python contract carries it. For a dbt contract, `written`
  means `dbt.generate` writes the property, `filled` means continuo fills it, and `not_used` means
  no dbt contract carries it. The two answers are independent: no rule for a Python contract reads
  `dbt`. A property that is not in the mapping is unknown to this release.
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
)
# {"schedule": "daily"}
PROPERTIES["reads"]
# ContinuoProperty(name="reads", sits_on="schema", python="filled", dbt="filled")
```

### Public names

- `VENDOR`: the vendor name of continuo's custom properties, `continuo`.
- `ContinuoProperty`: one custom property, a frozen dataclass with the fields `name`, `sits_on`
  (`root`, `schema` or `quality`), `python` (`authored`, `filled` or `refused`) and `dbt`
  (`written`, `filled` or `not_used`).
- `PROPERTIES`: the read-only mapping from each property name to its `ContinuoProperty`.
- `continuo_values`: returns the value of each `continuo` item of a `customProperties` list, by
  property name, and skips every item it cannot use.

## validate

```python
from continuo_contracts.validate import (
    REASONS,
    Issue,
    dbt_rule_issues,
    rule_issues,
    schema_issues,
    validate_dbt_contract,
    validate_python_contract,
)
```

`validate_python_contract` checks the ODCS contract of a Python node and returns every problem it
finds, each with the path of the place it is at and a closed reason code. It runs in two stages. The
first, `schema_issues`, checks the document against the packaged ODCS v3.2.0 JSON schema, after a
scan that refuses a column that nests. The second, `rule_issues`, applies continuo's rules to a
document that the first accepted, because the rules read a document of the shape the schema
describes. `validate_dbt_contract` does the same for the contract that `dbt.generate` writes for a
dbt node, with `dbt_rule_issues` as its second stage. The module logs nothing.

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
  module never gives it. A dbt contract gets only `odcs_schema`, `refused_field` (a nested column,
  which `schema_issues` finds), `api_version`, `identity`, `missing_field`, `unknown_property`,
  `refused_property` and `value` issues, which `schema_issues` and `dbt_rule_issues` below
  describe.
- `schema_issues(doc: object) -> list[Issue]` first scans the columns of every schema object of
  `doc`. When a column carries `items`, a `map` or `properties`, it returns a `refused_field` issue
  at `schema[<n>].properties[<i>].<field>` for each such field, the issue `rule_issues` gives for
  it, and does not run the JSON schema. The column of a contract, Python or dbt, nests nothing, and
  the JSON schema check of a nested column takes time that grows exponentially with the depth of the
  nesting, while the scan takes time in proportion to the number of columns. The other problems of
  such a document show once the nesting is gone. Otherwise it gives one `odcs_schema` issue for each
  error of `doc` against the JSON schema, and an empty list when it fits. `doc` can be any value, as
  `yaml_strict.load` returns it: one that is not a mapping gives a single issue at `$`. Two errors
  with the same path and message give one issue, and so does the "Unevaluated properties" error of
  an object when every property it names has an error of its own, since the error of the value says
  what is wrong. A message longer than 300 characters is cut and ends in `...`.
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
- `dbt_rule_issues(doc: Mapping[str, Any]) -> list[Issue]` applies the rules to a dbt contract
  that `schema_issues` accepted, as `dbt.generate` writes it or in its stored form. It takes no
  file name and no service of a release, because a dbt node has no contract file to name and its
  `service` is not compared with anything. The rules are these:
  - `apiVersion` is `v3.2.0` (`api_version`).
  - The contract holds exactly one schema object (`identity` at `schema`). Its `name`, the table,
    is a name, as above, with no slash or backslash, and may hold a dot, which a versioned model's
    does, with no empty, `.` or `..` part between its dots; whitespace inside it is accepted
    (`identity` at `schema[0].name`). The `id` is in ASCII lower case, and is a schema part, a dot
    and the table in lower case (`identity` at `id`). The schema part ends at the first dot of the
    id and is a name with no whitespace, dot or slash anywhere, as the schema part of a Python
    contract's id is. The table part is the table lowered with `str.lower` in the id's own string:
    `<schema part>.<table>` lowered as one string, as `dbt.unique_id` lowers it, so a final capital
    sigma takes its final form (`analytics.\u03c2` is the id of the table `\u03a3`). So
    `analytics.customer.v2` is the id of the table `customer.v2`, and the alias is not part of it.
    The `physicalName` and the root `name` are not compared with the table.
  - `team.name` is a string with text in it (`missing_field` at `team.name`).
  - A custom property of the `continuo` vendor is looked up in `vocabulary.PROPERTIES`, in every
    `customProperties` list of the contract. A name the vocabulary does not know is
    `unknown_property`. A property that `vocabulary` marks `not_used` for dbt, such as `script`,
    is `refused_property` with the message `<name> is not a dbt contract property`, and so is a
    property in the list of another object than the one it belongs to. A property that continuo
    fills, such as the `reads` of the schema object, is accepted where it sits. A property written
    twice in one list is a `value` issue at each later copy.
  - The contract writes `service`, `kind`, `filePath` and `materialization` in its
    `customProperties` (`missing_field` at `customProperties`). A contract needs no `schedule`,
    because a dormant node has none, and no `criticality` or `timeoutMinutes`.
  - `service` and `filePath` are strings with text, `kind` is `dbt-model`, `dbt-seed` or
    `dbt-snapshot`, `materialization` is a mapping whose `materialized` is a string with text,
    `criticality` is `REGULATORY`, `CORE` or `SECONDARY`, `schedule` is a name with no whitespace
    at most 50 characters long, and `timeoutMinutes` is an integer from 1 to 10080 (a bool or a
    float is not). A bad value is a `value` issue at the `value` of the item, as in
    `customProperties[3].value`, and a `materialized` that is not text is at
    `customProperties[4].value.materialized`. A Python contract puts its `value` issues at the item
    itself, `customProperties[3]`, so the two paths differ by `.value`.
  - A contract that has no issue is one that [`dbt.run_facts`](#dbt) reads whole: it needs
    `team.name`, the `id`, the name of the schema object, `service`, `kind`, `filePath` and the
    `materialized` of `materialization`, and each of them has passed one of the rules above.

  The issues come in the order that `rule_issues` gives them.
- `validate_dbt_contract(doc: object) -> list[Issue]` returns `schema_issues(doc)` when that has
  any issue, and `dbt_rule_issues(doc)` otherwise. An empty list means the contract passed every
  check of this module.

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
- `dbt_rule_issues`: the issues that continuo's rules find in a dbt contract that the schema
  accepted.
- `validate_dbt_contract`: every issue of a dbt contract, the schema issues first and the rules
  once the schema accepts the document.

## contract

```python
from continuo_contracts.contract import (
    contract_hash,
    logical_type,
    normalise_dbt,
    normalise_python,
)
```

continuo stores a contract in one canonical shape, its stored form, and fingerprints it
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
- `normalise_dbt(doc: Mapping[str, Any], *, reads: Sequence[str]) -> dict[str, Any]` returns the
  stored form of a dbt contract that `dbt.generate` wrote, or of its stored form, as a new dict,
  and leaves `doc` unchanged. `reads` lists the tables that the node's compiled SQL names, each
  `<schema>.<table>`; the library never reads SQL, so the caller lists them. The stored form is
  `doc` with:
  - the `continuo` property `reads` of the schema object holding the tables of `reads` joined with
    the ones the node declares (`config.meta.continuo.reads`, which `generate` writes there), in
    lower case (ASCII letters only), sorted by code point and listed once, and left out when there
    are none;
  - the `primaryKeyPosition` of the only column with `primaryKey: true` dropped, as for a Python
    contract.

  It then writes the document in the canonical form above. Storing a stored form again with no
  `reads` gives it back unchanged. Raises `HashInputError` when `reads` is one string, or when
  `reads` or the declared reads list a table that is not `<schema>.<table>` as above, and
  `CanonicalJsonError` when an item of a set-like list holds a value that canonical JSON cannot
  write, such as a `mustBe` of 2**60: a contract `dbt.generate` writes never holds one, but a
  stored contract passed back that `validate.validate_dbt_contract` accepts can.
- `contract_hash(stored: Mapping[str, Any]) -> str` returns `"sha256:"` and the lowercase hex
  SHA-256 of the RFC 8785 canonical JSON of the stored form without its root keys `apiVersion`,
  `kind`, `status` and `version`, 71 characters in all. Those keys are the list
  `contract_hash_exclusions` of `fingerprints.yaml`, which `fingerprints.contract_hash_exclusions()`
  returns. Every stored form holds those four with the same values, so leaving them out changes
  nothing but keeps the hash free of the version continuo numbers from history. `stored` is
  hashed as it is, so pass the result of `normalise_python` or `normalise_dbt`. Raises
  `CanonicalJsonError` for a value that canonical JSON cannot write.
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
contract_hash(stored)
# "sha256:15268bb30f205e0e5cfb4791715391f3cf7ba527a671f319a6b77e1b087dda59"
logical_type("varchar(64)")
# {"logicalType": "string", "logicalTypeOptions": {"maxLength": 64}}
```

### Public names

- `normalise_python`: returns the stored form of a Python contract that the contract check
  accepted, written in the canonical form.
- `normalise_dbt`: returns the stored form of a dbt contract, with the tables its compiled SQL
  names joined with its declared reads, written in the canonical form.
- `contract_hash`: returns the `contract_hash` of a stored form, `"sha256:"` and 64 hex digits.
- `logical_type`: returns the ODCS logical type of an SQL type, or `None`.

## lite

```python
from continuo_contracts.lite import (
    BANNER,
    RELEASE_ONLY,
    Line,
    LiteReport,
    check_manifest,
)
```

A producer's CI can check what continuo will make of a dbt service before the release does.
`check_manifest` takes the `manifest.json` that `dbt parse` writes, which needs no warehouse, and
does with each model, seed and snapshot what the release does: it leaves out the nodes
`dbt.skip_reason` skips, writes the contract of each node it keeps with `dbt.generate`, checks it
with `validate.validate_dbt_contract`, and hashes its stored form with `contract.normalise_dbt` and
`contract.contract_hash`. It also compares the columns each seed declares with the header of its
CSV file. It reads no file, runs no program, logs nothing and prints nothing: the caller reads the
manifest and the CSV headers, and prints the report. [cli](#cli) is that caller for `dbt check`.

The check is lite because the release does more than it, so a report with no error does not
guarantee that the release creates the service's contracts. A caller prints `BANNER` first and
last, and the rows of `RELEASE_ONLY` between them.

```python
import csv


def read_header(node):
    try:
        path = project_dir / node["original_file_path"]
        with path.open(newline="", encoding="utf-8-sig") as stream:
            return next(csv.reader(stream))
    except OSError, UnicodeError, StopIteration:
        return None


report = check_manifest(
    manifest, declared_service="finance", read_header=read_header
)
report.ok  # False
report.lines[0]
# Line(level="error", node_id="seed.finance.seed_operational_costs",
#      message="cost_date is in the CSV header but not declared")
report.contracts["analytics.fx_transactions_eur"]
# "sha256:6a1e6606f0d6c8acb0cc6120b93029dba8d1d2782763de8ced721a36285cdd84"
```

The nodes are taken in the code point order of their node ids, so the report is the same for any
order of the manifest. The `node_id` of a line is the key of its node in the manifest, such as
`model.shop.orders` (`""` for the line about the whole service), and a key of `contracts` is a
contract id, `<schema>.<table>` in lower case, such as `analytics.orders`. For each node, in this
order:

1. A node `dbt.skip_reason` leaves out gives one `info` line, and nothing else: `no owner,
   continuo skips it`, `local stub, continuo skips it` or `from an installed package and has no
   tag, continuo skips it`. A skipped node is never an error, and a skipped seed gets no header
   check. A node whose gate cannot read a field (`DbtNodeError`) gives one `error` line instead.
2. A kept node with no tag, a dormant node, gives the `info` line `no tag: a dormant node, it runs
   only by hand`.
3. `dbt.generate` writes the contract. A field it cannot read (`DbtNodeError`) gives one `error`
   line, and the node has no more lines. The message of such a line does not repeat the node id.
4. Each issue of `validate.validate_dbt_contract` gives an `error` line, `<path>: <message>`.
5. A contract with no issue has its stored form hashed, and `contracts` holds the hash. A
   contract with an issue has no hash, since only a valid contract gets one, so a hash in
   `contracts` never suggests that the release creates a contract it would reject. A declared read
   that is not `<schema>.<table>` gives an `error` line and no hash, because the stored form cannot
   be written.
6. For a kept seed of the project, `read_header(node)` gives the names in the first row of its CSV
   file, or `None` when the file cannot be read, which gives one `error` line. Otherwise
   `dbt.seed_header_issues` compares them with the seed's declared columns, the names of the
   properties of its contract in order, and each difference gives an `error` line. A kept seed of
   an installed package, one whose `package_name` is not the manifest's `metadata.project_name`,
   has its CSV file in that package and not in the project: it gets the `info` line `a seed of an
   installed package: its CSV header is not checked`, and `read_header` is not called for it. A
   seed with no `package_name`, or a manifest that names no project, counts as the project's.

When every model, seed and snapshot is skipped, or the manifest has none, the last line is an
`error` with the empty `node_id`, `every node of the service is skipped: the release rejects it as
invalid_artifact (manifest contains no model/seed nodes)`. A node whose gate cannot read it counts
as one that is not skipped. A field of the manifest that is no node's, a `metadata` or a `nodes`
or a field of a test that `dbt.tests_by_node` reads, in a shape the library cannot read raises
`DbtNodeError` from `check_manifest`.

The contract of the lite check is the one `dbt.generate` writes, stored with no tables from
compiled SQL, since `dbt parse` compiles nothing. Its hash is the `contract_hash` of that stored
form, so it differs from the hash the release stores for a node whose compiled SQL names a table
the node does not declare. A seed whose header differs from its declared columns still has a hash,
because its contract is valid.

### Public names

- `BANNER`: the text a caller prints first and last: `LITE CHECK. Passing it does not guarantee
  that continuo will create this service's contracts at release time.`
- `RELEASE_ONLY`: the six things only the release does, as `(what, why it can differ)` pairs of
  text in this order: `dbt runs in the team's image`, `the whole estate is parsed`, `the tables a
  node reads`, `the install's library version`, `install settings` and `the install's ontology`.
- `Line`: one thing the check says about a node, a frozen dataclass with the fields `level` (`info`
  for a node the check leaves out, a dormant node or a seed of an installed package, `error` for a
  problem that fails the check), `node_id` (the key of the node in the manifest, or `""` for the
  line about the whole service) and `message`.
- `LiteReport`: what `check_manifest` returns, a frozen dataclass with the fields `service` (the
  service the gates compare each node with, as `dbt.project_service` gives it), `lines` (a tuple of
  `Line`, by node id, with the line about the whole service last), `contracts` (the `contract_hash`
  of each contract that was generated, has no issue and can be stored, by contract id, in the order
  of the node ids) and the property `ok`, `True` when no line is an `error`.
- `check_manifest`: `check_manifest(manifest, *, declared_service, read_header) -> LiteReport`
  checks the manifest `json.load` returns. `declared_service` is the service of the project when
  the manifest names none, as in `dbt.project_service`. `read_header` takes a seed's manifest node
  and returns the column names of its CSV header, or `None` when the file cannot be read; it is
  called once for each seed of the project that gets a contract, and an exception it raises is not
  caught. It raises `DbtNodeError` for a manifest `metadata`, `nodes` or test field in a shape the
  library cannot read.

## cli

```bash
continuo-contracts validate PATH... [--service SERVICE] [--json]
continuo-contracts hash FILE --service SERVICE [--read TABLE]... [--json]
continuo-contracts dbt check PROJECT_DIR [--dbt PATH] [--profiles-dir DIR] [--target NAME] \
  [--manifest FILE] [--service SERVICE] [--json]
```

```python
from continuo_contracts.cli import main
```

The package installs the console script `continuo-contracts`. `validate` and `hash` check the ODCS
contract of a Python node: a producer's CI runs `validate` on its contract files, and a person runs
`hash` to see the `contract_hash` of a file. Both commands read a file with `yaml_strict.load` and
check it with `validate.validate_python_contract`, so they find what topology-controller finds.
Both log `this release checks Python contracts` at INFO, in either output mode. `dbt check` is the
producer's lite check of a dbt project, described below.

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

- `dbt check PROJECT_DIR` is the producer's lite check of a dbt project: what continuo will make of
  its models, seeds and snapshots at release time, found without a warehouse. It runs the `dbt`
  executable it is given, as a new process, and never imports dbt, so it runs in a Python 3.14
  environment of its own while dbt runs on the Python of the team's image. Without `--manifest`
  it runs

  ```bash
  <dbt> --write-json --log-path <TMP> parse --project-dir PROJECT_DIR --profiles-dir <DIR> \
    --target-path <TMP> [--target NAME]
  ```

  and reads `<TMP>/manifest.json`. `<dbt>` is `--dbt`, a path or a name on `PATH`, and `dbt` on
  `PATH` without it. `<DIR>` is `--profiles-dir`, and `PROJECT_DIR` without it. `<TMP>` is a
  temporary folder that the command makes before dbt runs and removes when it is done, on every
  path, so dbt writes neither a `target` nor a `logs` folder in the project, and a project folder
  that cannot be written is checked all the same. `--write-json` makes dbt write the manifest even
  when `DBT_WRITE_JSON=false` or the project's `flags` turn dbt's JSON files off; it and
  `--log-path` come before `parse`, where every dbt from 1.5 reads them. The command runs no
  other dbt command: it never runs `dbt deps`.
  dbt gets the environment of the command, and, for each `env_var('NAME')` of `<DIR>/profiles.yml`
  that has no default (single or double quotes, `env_var('NAME')` and not `env_var('NAME',
  default)`) when no variable `NAME` is set, the placeholder `continuo-lite-check` as that variable.
  `NAME` is a plain name, a letter or `_` and then letters, digits and `_`, so a call such as
  `env_var('A=B')` gets no placeholder. The command scans the text of the profiles file for those
  calls and never evaluates Jinja, so it also finds a call that a condition would leave out. The
  placeholder is text, so a profile that reads a variable as a number, such as `env_var('PORT') |
  as_number`, needs a real value in CI. A profiles file that cannot be read adds nothing, and dbt
  reports what is wrong with it. A `dbt parse` that exits with a status other than 0 gives exit
  status 1, no report and nothing on stdout: the command logs `dbt parse exited with status <N>` at
  ERROR, then the last 20 lines of dbt's output, standard error and standard output together, each
  at ERROR with the colour codes dbt writes left out.

  With `--manifest FILE` the command reads that `manifest.json`, which a team may have built
  already, and runs no dbt: `--dbt`, `--profiles-dir` and `--target` have no effect, and no `dbt`
  need be installed. The manifest must be a JSON object, and its `nodes`, when it has them, an
  object of objects; a `metadata`, or a field of a test that `dbt.tests_by_node` reads, in a shape
  the library cannot read is a usage error too, and a field of a node gives that node an error
  line. `PROJECT_DIR` must still be a folder, because the CSV files of the seeds are read from it.

  `--service` is the service of the project that the gates compare each node with when the
  manifest names none, as in `dbt.project_service`; without it the service is the name of
  `PROJECT_DIR` with `_` written `-`, and `.` names the current folder. The header of a seed is the
  first row of `PROJECT_DIR/<original_file_path>` of its manifest node, read as UTF-8 with an
  optional byte order mark and with the delimiter of the seed's `config.delimiter`, a comma when it
  has none, so a BOM or CRLF line endings change nothing. The command reads that file only when it
  is a regular file whose real path, its symbolic links resolved, lies in a seed folder of the
  project, and reads at most its first MiB, so a manifest from another job cannot make it show
  another file, and a pipe or a device never stalls it. The seed folders are the entries of the
  list `seed-paths` of `PROJECT_DIR/dbt_project.yml`, each read from `PROJECT_DIR` (an absolute
  one as it is, so a folder outside the project works), with an entry that is not a string or
  holds a NUL left out; the folder is `seeds`, dbt's default, when that file is missing, cannot be
  read as `yaml_strict` reads YAML, is not a mapping or has no string in a list under
  `seed-paths`. A file that cannot be read (missing, outside the seed folders, not a regular file,
  not UTF-8, a first row longer than a MiB, a path with a NUL, or a delimiter `csv` refuses, such
  as a quote or a newline) gives the error line that `lite.check_manifest` words for it, which
  shows nothing of the file, and an empty file has an empty header. A seed of an installed package
  gets the information line instead.

  The report goes through `logging` to stderr: the `lite.BANNER` at INFO, one line for each line
  of the check, `<node id>: <message>` (the message alone for the line about the whole service),
  at INFO for an `info` line and at ERROR for an `error` line, `What only the release does:` and
  one INFO line for each row of `lite.RELEASE_ONLY`, `  <what>: <why>`, and the banner again. The
  command does not log the line `this release checks Python contracts`. With `--json`, stdout is
  one document, and `contracts` holds the `contract_hash` of each valid contract by contract id:

  ```json
  {"lite": true,
   "ok": false,
   "service": "finance",
   "lines": [{"level": "error",
              "node_id": "seed.finance.seed_operational_costs",
              "message": "cost_date is in the CSV header but not declared"}],
   "contracts": {"analytics.fx_transactions_eur": "sha256:..."}}
  ```

  `ok` is true when no line is an `error`.

The human text goes through `logging` to stderr. Stdout holds only the result, so
`continuo-contracts hash ... > hash.txt` and `continuo-contracts validate ... --json | jq` read
it whole, and `dbt check` without `--json` prints nothing on stdout. When the command cannot run,
stdout is empty.

| Exit status | `validate` and `hash` | `dbt check` |
|---|---|---|
| 0 | every contract is valid, or `hash` printed its hash | the check found no error |
| 1 | a contract has an issue | the check found an error line, or `dbt parse` exited with a status other than 0 |
| 2 | a usage error of the arguments (a `--service` that is empty or blank among them), a path that names no contract file (a folder with no `*.odcs.yaml` file, a path that does not exist, a file not named `*.odcs.yaml`), a path that cannot be looked up (one below a folder the process may not search, among them), a folder that cannot be listed, a file that is not UTF-8 text or cannot be read, or a `--read` value that is not a table. An ERROR line names each such path and what the system refused | a usage error of the arguments (a `--service` that is empty or blank among them), a `PROJECT_DIR` that is not a folder or cannot be looked up, no `dbt` executable (`--dbt` names none that can run, or there is no `dbt` on `PATH`), or a manifest that cannot be read, is not JSON (or is nested deeper than the JSON reader goes), or is not the shape above, whether `--manifest` names it or `dbt parse` wrote it. An ERROR line says which |

Nothing is checked when one path of `validate` or `hash` is refused with status 2.

- `main(argv: Sequence[str] | None = None) -> int` runs the command with `argv` (`sys.argv[1:]`
  by default) and returns the exit status. It sets up logging to stderr when it runs, not when
  the module is imported. An error of the arguments exits through `argparse` with status 2, as
  does `--help` with status 0.

```bash
continuo-contracts hash contracts/py_daily_kpis.odcs.yaml --service service-py \
  --read analytics.daily_transactions
# sha256:15268bb30f205e0e5cfb4791715391f3cf7ba527a671f319a6b77e1b087dda59

continuo-contracts dbt check services/finance --dbt .venv-dbt/bin/dbt --json
```

### Public names

- `main`: runs the `continuo-contracts` command and returns its exit status.

## errors

```python
from continuo_contracts.errors import (
    CanonicalJsonError,
    ContractsError,
    DbtNodeError,
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
  the message starts with the JSON path of that value. `contract.normalise_python`,
  `contract.normalise_dbt` and `contract.contract_hash` raise it for such a value in a contract.
- `HashInputError`: `shared_fold` and `shared_hash` refuse a unit key or a digest that is in a
  form they do not accept, and the message names the key. `contract.normalise_python` refuses an
  empty or blank `service`, and `contract.normalise_python` and `contract.normalise_dbt` refuse a
  `reads` entry that is not `<schema>.<table>` (exactly two parts joined by a dot, neither empty,
  with no whitespace and no control character), and the message names the service or the entry.
- `ProjectionError`: `canonical_sql_type`, `PythonNode.from_mapping`, `code_projection` and
  `projection_hash` refuse an SQL type or a Python node.
- `DbtNodeError`: `dbt.skip_reason`, `dbt.node_service`, `dbt.schedule`, `dbt.table_name`,
  `dbt.unique_id`, `dbt.tests_by_node`, `dbt.project_service` and `dbt.generate` refuse a field of
  a manifest node, or of the manifest, in a shape they cannot read, and the message starts with
  the node's id (`manifest` for a field of the manifest), then `: ` and the field with what it
  must be and what it is. `lite.check_manifest` raises it for a field of the manifest.
- `FingerprintsFileError`: `parse_fingerprints` and `load` refuse a fingerprints file that is not
  in the form described under [fingerprints](#fingerprints), and the message names the location in
  the file.
- `SchemaFileError`: `schema.load` refuses a packaged schema file whose SHA-256 is not the pinned
  one, and the message names the file and both hashes.
- `StrictYamlError`: `yaml_strict.load` refuses a text, and the message is
  `line <line>, column <column>: <reason>`. It has the numbers `line` and `column` (counted from
  1, and 0 when the reason has no position in the text) and the `reason` text.

## Vectors

The wheel ships seven data files in `continuo_contracts/vectors/`. Each is a JSON list of cases
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
  the demo `py_daily_kpis` again with its column types spelled ` Date`, `int` and `decimal(012,
  02)`, which stores and hashes like the demo file. The refused ones are a `NUMERIC(12)` column, a
  contract that names its `servers`, a file name that does not match the table, a repeated key, and
  one case for each name and path rule: a schema part of the id that is blank, has whitespace or has
  a slash, a table name with whitespace or a control character, a column name with outer whitespace
  or a control character, a read query name with outer whitespace, a `schedule` with outer
  whitespace or longer than 50 characters, a `script` with outer whitespace or that is not a
  normalised path, six `assets` that are not normalised paths, a `VARCHAR(0)` column and a blank
  `service` property. The file is ASCII.
- `dbt_contracts.json` is a list of `{"name", "node", "tests", "manifest", "reads",
  "source_sha256", "generated", "flags", "stored", "contract_hash"}`. `node` is a manifest node,
  `tests` its test nodes in the order `dbt.tests_by_node` joins them, and `manifest` the part of the
  manifest that `generate` reads for them. Pass them to `dbt.generate(node, tests,
  manifest=manifest)`: its `document` is `generated`, its `flags` are `flags` and its
  `source_sha256` is `source_sha256`, the hash of the fields it read that the [dbt](#dbt) section
  describes. `contract.normalise_dbt(generated, reads=reads)` is `stored`, and
  `contract.contract_hash(stored)` is `contract_hash`; the `generated` contract of such a case
  passes `validate.validate_dbt_contract`. `reads` are the tables the node's compiled SQL names. A
  case `{"name", "node", "tests", "manifest", "reads", "source_sha256", "issues"}` is a node whose
  generated contract is not valid: `validate.validate_dbt_contract` gives, for the `document` of
  `dbt.generate(node, tests, manifest=manifest)`, the `{"path", "reason"}` of each issue in
  `issues`, in order, and `source_sha256` is the hash of that call. The nine cases with a contract
  come first, then the one case with `issues`, `bad_criticality`, then two cases with a contract
  whose inputs are fixture nodes the writer edits. The first ten come from the `cases` fixture
  project as it is, one for each rule: a model with tests (every kind of test, relationships to a
  versioned model and to a source, and Jinja in a kwarg, which flags it), a seed with
  `column_types`, a dormant model, a versioned model, an ephemeral model, a snapshot (its unique
  key in both `materialization` and `snapshot`, and its history columns), an enforced contract, a
  governed model (readers, grants, access, group, freshness, a timeout, declared reads and a table
  test), the governed model again with the reads `["Analytics.Orders", "analytics.orders",
  "raw.events"]`, which store joined with its declared read, once and in lower case, and a model
  with the criticality `high`, which is the case with `issues`. The two edited cases start from the
  model with tests, and their names state the edit:
  `namespaced_test_thresholds_extra_kwarg_and_loose_relationship` makes the test of `amount` the
  generic test `accepted_range` of the package `dbt_utils`, gives the warned `not_null` test the
  `warn_if` `> 5` and the `fail_calc` `sum(n)`, gives the `accepted_values` test of `kind` the extra
  kwarg `quote: false`, and gives the `relationships` test of `customer_id` the severity `warn`, so
  it is a `custom` rule and not a relationship; `composite_key_lengths_and_undeclared_columns`
  makes the node's `primary_key` `id`, `status` and `ghost` (a column it does not declare, which
  flags it), gives `id` the type `varchar(36)` and `status` the type `character varying(00020)`,
  and moves the lenient `unique` test to the column `ghost`, which flags it too. Each input is
  pruned to the fields `generate` reads, which `dbt.read_tree` gives: `node` keeps its `unique_id`
  and those fields, `tests` keep their `unique_id` and those fields (each kwarg but `model` and a
  `column_name` equal to the test's own), and `manifest` holds under `nodes` and `sources` each entry of a test's
  `depends_on.nodes` other than its `attached_node` and the tested node, with a node's `schema`,
  `name` and `version` or a source's `schema`, `identifier` and `name`. A field the node does not
  hold is left out, and a value is kept whole. The file is ASCII and the keys of every object are
  sorted, except the keys of a node's `columns`, which keep the order in which the node declares
  its columns: the contract's columns follow it, so read that object with its key order kept.

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
moves one gives every Python node of every install a new contract version. A `contract_hash`
pinned in `vectors/dbt_contracts.json` is one too: a change that moves one gives every dbt node of
every install a new contract version.

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
