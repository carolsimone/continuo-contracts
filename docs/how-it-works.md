# How it works

How is the package built: pipeline, modules, fingerprints, stored form? This page is for
maintainers and authors of a [port](glossary.md#port). To use the package, start with
[check your project](check-your-project.md) or [Python contracts](python-contracts.md).

## From a node to its contract hash

A [node](glossary.md#node)'s [contract](glossary.md#contract) is the [ODCS](glossary.md#odcs)
(Open Data Contract Standard) document that describes its table. A dbt node's contract is
generated from the [manifest](glossary.md#manifest), and a Python node's is a file the team writes.

| Step | dbt node | Python node |
|---|---|---|
| Read | `cli` runs `dbt parse` and reads the manifest | `yaml_strict.load` reads the file |
| Keep | `dbt.skip_reason` applies the [gates](glossary.md#gate) | none |
| Generate | `dbt.generate` writes it from the node and its tests | none: the team wrote it |
| Validate | `validate.validate_dbt_contract` | `validate.validate_python_contract` |
| Normalise | `contract.normalise_dbt` | `contract.normalise_python` |
| Hash | `contract.contract_hash` | `contract.contract_hash` |

Validation checks the ODCS JSON schema, then continuo's own rules. It returns every problem as an
`Issue`: a path in the contract, a reason and a message. Only a contract with no issue is
normalised into its [stored form](glossary.md#stored-form) and hashed.

Normalising adds [`reads`](glossary.md#reads), the tables the node reads. For a Python node,
`hash --read` passes the tables its read queries name.

A dbt contract lists the tables that `meta.continuo.reads` declares. continuo's release adds the
tables the compiled SQL names. The [lite check](glossary.md#lite-check) cannot, because `dbt parse`
compiles nothing, so its hash differs from the release's when the SQL reads an undeclared table.

continuo's [release](glossary.md#release) runs these steps on every node of the project. It
rejects the manifest as `invalid_artifact` when a kept node has no project or another project than
the [declared project](glossary.md#declared-project), or when every node is skipped. It rejects it
as `duplicate_table` when two nodes give one contract id, `<schema>.<table>`.
[dbt contracts](dbt-contracts.md) covers the gates and `generate`, and [validation](validation.md)
the rules.

## Modules and layers

A module imports only from the rows below it, and the modules of one row import none of each other.

| Modules | Job |
|---|---|
| `cli` | The `continuo-contracts` command: `validate`, `hash`, `dbt check` and `ontology check` |
| `lite` | The lite check: the dbt steps for every node of a manifest |
| `ontology_check` | The ontology rules over contracts in memory |
| `validate`, `ontology` | The ODCS schema check and continuo's rules, the ontology file and the ontology part of a contract |
| `contract` | The stored form and `contract_hash` |
| `python`, `yaml_strict`, `dbt` | Python node and projection, contract file reader, dbt nodes |
| `canonical`, `hashing`, `schema`, `fingerprints` | Canonical JSON, folds, ODCS schema, input map |
| `errors`, `vocabulary` | The errors, the `continuo` custom properties and where each may sit, and the rule for the name of a thing |

Each library is confined to the modules that need it: jsonschema to `validate`, PyYAML to
`yaml_strict` and `fingerprints`, `logging` and `subprocess` to `cli`. `uv run lint-imports`
enforces the rows and these confinements from `[tool.importlinter]` in `pyproject.toml`.

## The ontology modules

Two modules read contracts against an [ontology](glossary.md#ontology), a file that names the
projects, things and properties of the data ([ontology](ontology.md)). They take values and return
values like every module but `cli`: the caller reads the file and the contracts.

- `ontology` loads the file (`ontology.load` reads it with `yaml_strict` and checks its shape
  against the keys it lists) and answers which projects the file declares. It also reads the
  ontology part of a stored contract: `has_bindings` says whether the contract writes a
  [binding](glossary.md#binding), `strip` takes the bindings and the links to the ontology out, and
  `resolve` fills a stored form with what the ontology knows. `resolve` and `strip` end in
  `contract.canonical_form`, the walk that `normalise_python` and `normalise_dbt` end with, so a
  resolved form hashes as any stored form does.
- `ontology_check` runs the rules over stored contracts in memory: `check` takes the loaded
  `Ontology`, the contracts and the names of the rules, and returns `Finding`s, and
  `ontology_findings` checks the file alone. The [lite check](glossary.md#lite-check) resolves
  each contract of one project and runs the [per-project rules](glossary.md#per-project-rule). The
  rules that need the contracts of every project run only at the release.

The lite check hashes the stored form, so a resolved form changes no hash it reports.
`ontology.json` pins the first resolved forms and their hashes ([test vectors](vectors.md)).

## Why the package is pure

Every module but `cli` takes values and returns values: it reads no file but its packaged data,
starts no process and logs nothing. The team's CI and continuo's release call the same functions on
data each reads itself, so they get the same bytes. No module imports dbt-core (the `dbt`
package): dbt runs on its own Python, such as 3.12, so `cli` runs the `dbt` executable as a
separate process.

## The two fingerprints

A [fingerprint](glossary.md#fingerprint) is a hash that changes when what it covers changes. Both
are `sha256:` and 64 lowercase hex digits.

| | [Content hash](glossary.md#content-hash) | [Contract hash](glossary.md#contract-hash) |
|---|---|---|
| Name | `content_hash` | `contract_hash` |
| Covers | The node's code and configuration | The stored form, minus four constant root keys |
| A change means | The node's code changed | A new contract version in continuo |

The content hash folds the node's parts: a fold joins them with `|` and takes the SHA-256 of the
result. A dbt node has four, folded by `hashing.fold4`: source (SQL), shared (code that other nodes
share, such as [macros](glossary.md#dbt-words)), config and compiled SQL. continuo computes them.

A Python node has three, folded by `hashing.fold3`. The team's CI hashes the script (source), or
the CSV URI for a CSV node (`python-csv`), and the files it imports and its assets (shared, with
`hashing.shared_hash`). The config part is `python.projection_hash`, the hash of the node's
[projection](glossary.md#projection). [Fingerprints](fingerprints.md) says which input moves which
hash, rendered from `continuo_contracts/fingerprints.yaml`.

`dbt.generate` also returns `source_sha256`, the SHA-256 of the manifest fields it read: a
provenance value, not a third fingerprint. [Hashing](hashing.md), [contract-hash](contract-hash.md)
and [source-sha256](source-sha256.md) give the bytes of all three.

## The stored form

Two contracts that mean the same thing have one stored form and one contract hash. Reordering
tags, writing an ODCS default or spelling a type `int` instead of `INTEGER` changes neither. The
`contract` module docstring lists every rule, and [contract-hash](contract-hash.md) specifies them.

```python
>>> from continuo_contracts import contract
>>> doc = {"id": "analytics.kpis", "tags": ["finance", "daily", "finance"], "schema": [
...     {"name": "kpis", "properties": [{"name": "n", "physicalType": "int", "required": False}]}]}
>>> stored = contract.normalise_python(doc, project="shop", reads=[])
>>> stored["tags"], stored["schema"][0]["properties"]
(['daily', 'finance'], [{'name': 'n', 'physicalType': 'INTEGER', 'logicalType': 'integer'}])

```
