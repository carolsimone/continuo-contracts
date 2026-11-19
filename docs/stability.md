# Stability

What may a new version change, and which version should I pin?

## Hashes

A hash that the [test vectors](vectors.md) fix does not move between versions. A moved
`content_hash` changes the hash of every node that continuo runs. A moved `contract_hash` gives
every node it covers a new contract version.

The one exception is a version that ships with a [re-baseline](glossary.md#re-baseline) and says
so in its TL;DR, the summary at the top of its section in `CHANGELOG.md`. A version that moves no
hash says so there ("No hash moves", or "No hash of <version> moves").

## Contracts

The contract check only widens. A newer version accepts every contract an older one accepted, and
gives it the same [stored form](glossary.md#stored-form) and the same `contract_hash`. The
vocabulary of a contract, its kinds, custom properties and values, grows, and a rule that would
refuse a contract an earlier version accepted is not added. An upgrade needs no edit to a contract.

Other output can change. 0.3.1, a patch, changed `dbt.generate`'s output for two inputs and made
the [lite check](glossary.md#lite-check) reject more: read a version's CHANGELOG section before you
upgrade. Its vectors stayed byte for byte the same, so no hash moved.

## Which version to use

Pin an exact version, `continuo-contracts==X.Y.Z`, because a hash compares only between runs of
the same code. Use the version that continuo's [release](glossary.md#release) uses, which whoever
runs continuo can tell you: a newer version may accept a contract that the release's version
refuses. `continuo_contracts.__version__` gives the installed version.

The owner publishes each version as [CONTRIBUTING](../CONTRIBUTING.md#releasing) describes.
