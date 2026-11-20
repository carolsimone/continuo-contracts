# Stability

What may a new version change, and which version should I pin?

## Hashes

A hash that the [test vectors](vectors.md) fix does not move between versions. A moved
`content_hash` changes the hash of every node that continuo runs. A moved `contract_hash` gives
every node it covers a new contract version.

The one exception is a version that ships with a [re-baseline](glossary.md#re-baseline) and says
so in its TL;DR, the summary at the top of its section in `CHANGELOG.md`. A version that moves no
hash says so there ("No hash moves", or "No hash of <version> moves").

0.4.0 moves no hash for a contract that writes no [binding](glossary.md#binding): the vector files
of 0.3.1 are byte for byte the same, and `ontology.json` is the one new file. Two cases move a
hash once:

- A dbt node that already writes binding keys under `meta.continuo` (`entity`, `grain`, `fixed` and
  `filter` on the node, `property`, `local`, `from`, `agg`, `where` and `period` on a column),
  which 0.3.1 ignored. Its `source_sha256` moves once, because those paths are now read, and so
  does the `contract_hash` of its contract, which now holds the bindings. A node with none keeps
  both.
- An enforced snapshot. Its columns and constraints are classified in `fingerprints.yaml` as a
  model's are, so a consumer that hashes them moves the `content_hash` of such a snapshot once.
  This package hashes none of it.

## Contracts

The contract check only widens, with one exception below. A newer version accepts every contract an
older one accepted, and gives it the same [stored form](glossary.md#stored-form) and the same
`contract_hash`. The vocabulary of a contract, its kinds, custom properties and values, grows, and a
rule that would refuse a contract an earlier version accepted is not added. An upgrade needs no edit
to a contract.

The exception is the binding keys of a dbt node. 0.4.0 adds seven bindings to the vocabulary: a
Python contract may write them, which 0.3.1 refused as `unknown_property`. It also reads a dbt
node's `meta.continuo` binding keys, and refuses a binding of the wrong shape whatever any setting
of the install says ([validation](validation.md#binding-values)). So a dbt project whose
`meta.continuo` carries a binding key of the wrong shape, such as `grain: []` or `entity: 42`, which
0.3.1 ignored, now gives a `value` issue, and the [lite check](glossary.md#lite-check) fails on it.
Correct or remove the key.

Other output can change. 0.3.1, a patch, changed `dbt.generate`'s output for two inputs and made
the [lite check](glossary.md#lite-check) reject more: read a version's CHANGELOG section before you
upgrade. Its vectors stayed byte for byte the same, so no hash moved. 0.4.0 adds the `warning`
level to the lines of the lite check, which `dbt check --json` reports as `"level": "warning"`.

## Which version to use

Pin an exact version, `continuo-contracts==X.Y.Z`, because a hash compares only between runs of
the same code. Use the version that continuo's [release](glossary.md#release) uses, which whoever
runs continuo can tell you: a newer version may accept a contract that the release's version
refuses. `continuo_contracts.__version__` gives the installed version.

The owner publishes each version as [CONTRIBUTING](../CONTRIBUTING.md#releasing) describes.
