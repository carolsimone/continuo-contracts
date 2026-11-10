# continuo-contracts

The code that continuo and the projects that produce its nodes share to fingerprint a node:
canonical JSON, the Python code projection, the `content_hash` folds and the declaration of
which input moves which fingerprint. A producer's CI, continuo's topology-controller and the
runtime harness import the same functions, so they compute the same bytes and the same hashes
for a node.

The package is pure Python and has one runtime dependency, PyYAML.

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
  whitespace characters from both ends of `text`, and changes nothing else.

```python
canonical_json({"b": [1.0, "\u00e9"], "a": True}) == b'{"a":true,"b":[1,"\xc3\xa9"]}'
```

## hashing

```python
from continuo_contracts.hashing import fold3, fold4, sha256_hex, shared_fold, shared_hash
```

A node's `content_hash` is `"sha256:"` followed by the lowercase hex SHA-256 of its parts joined
by `|`, each part encoded as UTF-8.

- `fold3(source, shared, config)` hashes `source|shared|config`.
- `fold4(source, shared, config, compiled)` hashes `source|shared|config|compiled`.
- A part is bare lowercase hex, without a `sha256:` prefix, or `""` when the node has no such
  part. The folds do not check their parts: a seed in a stored artifact without compile extras
  carries dbt's `path` checksum as its source part, so a part is not always hex.
- `sha256_hex(data: bytes) -> str` returns the lowercase hex SHA-256 of `data`.
- `shared_fold(unit_digests)` takes a mapping from a unit id or a repo-relative POSIX path to the
  64 lowercase hex characters of the SHA-256 of that unit's bytes. It writes one line per unit,
  the key, a NUL character and the digest, sorts the lines by key, joins them with a line feed
  and returns the lowercase hex SHA-256 of the UTF-8 text. The key is part of the line, so two
  files that swap their contents change the hash. An empty mapping gives `""`. A key that is
  empty or holds NUL or LF, and a digest in any other form, raise `HashInputError` (a
  `ContractsError`) that names the key.
- `shared_hash(units)` takes a mapping from a path to the bytes of that file, one entry per file
  (a file that is both imported and listed as an asset appears once), and returns `shared_fold`
  of the SHA-256 of each value.

```python
shared_hash({"lib/p.py": b"P = 7\n", "lib/q.py": b"Q = -7\n"})
fold3("1" * 64, shared_hash({}), "2" * 64)  # "sha256:23208c80..."
```

## Who pins it

Every consumer pins an exact version, because a hash is only comparable across processes that
run the same code.

- topology-controller and continuo-python-runtime pin an exact version.
- A dbt producer pins the version that its continuo chart release names.

## Stability

A hash that this package computes is a contract. A change that moves any hash pinned in
`continuo_contracts/vectors/` or in the tests moves `content_hash` for every node of every
install, so it ships only together with a re-baseline in continuo.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Report a vulnerability as described in
[SECURITY.md](SECURITY.md).

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
