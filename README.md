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
