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
from continuo_contracts.canonical import canonical_json, normalise_text
```

- `canonical_json(value: object) -> bytes` writes `value` as RFC 8785 canonical JSON, UTF-8
  encoded. It raises `CanonicalJsonError` (a `ContractsError`, which is a `ValueError`) for a
  value it cannot represent, and the message starts with the JSON path of that value, as in
  `$.config.start: unsupported type date`.
- `normalise_text(text: str) -> str` turns CRLF and CR into LF and strips the six ASCII
  whitespace characters from both ends of `text`, and changes nothing else. It strips only those
  six, on purpose, so that its result does not depend on the Unicode tables of the Python that
  runs it.

```python
canonical_json({"b": [1.0, "\u00e9"], "a": True}) == b'{"a":true,"b":[1,"\xc3\xa9"]}'
```

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
  list or tuple as a `list`, at every depth. `primary_key` is the node's key on an
  `upsert` node and `[]` on `replace` and `append`, where the key only describes the table.
  Assets are sorted by Unicode code point, which is the order of their UTF-8 bytes (not UTF-16
  order). A `python-csv` node has no script and no assets, reads exactly `csv`, and only
  replaces. Only a `python-api` node has a `secret_ref`, and it reads nothing.
- `projection_hash(node)` returns the hash of that projection.
- `canonical_sql_type(raw)` strips outer whitespace, ignores case and writes `INT` as `INTEGER`,
  `DECIMAL(p, s)` as `NUMERIC(p,s)` and `VARCHAR(064)` as `VARCHAR(64)`. It supports `BIGINT`,
  `INT`, `INTEGER`, `DOUBLE PRECISION`, `TEXT`, `TIMESTAMP`, `DATE`, `BOOLEAN`, `NUMERIC(p,s)`,
  `DECIMAL(p,s)`, `VARCHAR(n)` and `CHAR(n)`, and refuses everything else, and a `NUMERIC`
  precision outside 1 to 38 or a scale above the precision. The outer whitespace that it removes
  is whatever Python's `str.strip()` removes (Unicode whitespace, so a no-break space around a
  type is stripped), and after the comma of `NUMERIC(p, s)` any run of ASCII whitespace is
  accepted. `normalise_text` strips only the six ASCII characters, so the two rules differ.
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
into), `contract` (`contract_hash`), `both` or `neither`.

- `hashed_config_denylist()` returns the dbt `config` keys that stay out of the hashed config, as a
  `frozenset`.
- `projection_keys()` returns the twelve keys of the code projection, in projection order.
- `classify(family, name)` returns the entry that classifies the input `name` of a node of
  `family`, or `None`. An entry has `id`, `field`, `source`, `fingerprint`, `parts`, `why`,
  `inputs`, `when` and `rule`, and `entry.moves(part)` says whether the input moves that part of
  `content_hash`. In a dbt family, a `config.<key>` that no entry names and whose key is not in the
  denylist belongs to the family's `other_config` entry. It does not classify a sub-key of a
  denylisted key (`config.meta.owner` gives `None`), and a dotted name such as
  `config.contract.enforced` is classified only where an entry declares it as its own input (the
  dbt model has one), so in another dbt family it belongs to `other_config`. It raises
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
`fingerprints.yaml`.

A pull request that adds an input to a hash adds it to `fingerprints.yaml` in the same change. A
projection key or a denylisted `config` key without an entry fails this repository's guard test, and
`unclassified()` finds any other input without one.

## Pinning

Pin an exact version (`continuo-contracts==X.Y.Z`), because a hash is only comparable across
processes that run the same code. A dbt producer uses the version its continuo release
documents.

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
