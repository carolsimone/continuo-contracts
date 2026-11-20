# Validation

Which rules does `validate` apply, and what does each issue mean?

## Issues

`validate.validate_python_contract(doc, path=..., service=...)` checks a Python node's
[contract](glossary.md#contract): only the base name of `path` counts, and `service=None` skips
the service rule. `validate.validate_dbt_contract(doc)` checks the contract `dbt.generate` writes
for a dbt node. Both return a list of `validate.Issue`, sorted by path and empty when the contract
passes. An issue has a `path` (`$` is the whole contract), a `reason` (one of `validate.REASONS`)
and a `message`.

This contract sits in the wrong file, lacks the `readQueries` its kind needs, and has a bad
`criticality` and a column type continuo does not accept:

```python
>>> from continuo_contracts import validate, yaml_strict
>>> doc = yaml_strict.load("""
... apiVersion: v3.2.0
... kind: DataContract
... id: analytics.daily_kpis
... version: "0"
... team: {name: analytics-team}
... customProperties:
...   - {vendor: continuo, property: schedule, value: daily}
...   - {vendor: continuo, property: criticality, value: HIGH}
...   - {vendor: continuo, property: script, value: scripts/kpis.py}
... schema:
...   - {name: daily_kpis, physicalType: table, properties: [{name: x, physicalType: MONEY}]}
... """)
>>> for issue in validate.validate_python_contract(doc, path="kpis.odcs.yaml", service=None):
...     print(issue.path, issue.reason)
$ file_name
customProperties kind_rule
customProperties[1] value
schema[0].properties[0].physicalType column_type

```

Validation runs in three stages, each only when the one before finds nothing. First a scan of the
columns: `items`, `map` or `properties` on a column is a `refused_field` issue for both
**families**, Python contracts and dbt contracts. The scan comes first because the JSON schema
check of a nested column takes time that grows exponentially with its depth. Then the ODCS v3.2.0
JSON schema gives each error as an `odcs_schema` issue, and last come continuo's rules below.

## Reasons

| Reason | Triggered by | For |
|---|---|---|
| `odcs_schema` | the document does not fit the ODCS JSON schema | both |
| `api_version` | `apiVersion` is a known ODCS version other than `v3.2.0` | both |
| `identity` | the id, the table name and the schema object disagree | both |
| `file_name` | the file is not named after the table | Python |
| `service` | `service` is not the [declared service](glossary.md#declared-service) | Python |
| `missing_field` | a required field or property is absent | both |
| `unknown_property` | a `continuo` property not in `vocabulary.PROPERTIES` | both |
| `refused_property` | a property the family does not carry, or on the wrong object | both |
| `refused_field` | a field the contract may not write | both |
| `kind_rule` | a property the kind needs is missing, or one it refuses is written | Python |
| `column_type` | a `physicalType` continuo does not accept | Python |
| `primary_key` | the key of an `upsert` node is missing or malformed | Python |
| `value` | a value, a column name or a quality rule breaks its rule | both |
| `strict_yaml` | the `validate` or `hash` command could not read the YAML | Python |

The `validate` functions never return `strict_yaml`. A property's `value` issue is at its item in
a Python contract (`customProperties[1]`), at its value in a dbt one (`customProperties[1].value`),
except for a [binding](#binding-values), whose issue is at its value in both. A **name** is a
string, not blank, with no whitespace at either end and no control character.

## Identity

| Rule | Reason at path | For |
|---|---|---|
| one schema object | `identity` at `schema` | both |
| `schema[0].physicalType` is `table` | `identity` there | Python |
| the table, `schema[0].name`, is a name without whitespace, `.`, `/` | `identity` there | Python |
| the table is a name with no `/`, `\` or empty, `.`, `..` part | `identity` there | dbt |
| `id` is `<schema>.<table>`, in lower case | `identity` at `id` | both |
| the schema part of `id` is a name without whitespace, `.`, `/` | `identity` at `id` | both |
| root `name` and `schema[0].physicalName`, if written, are the table | `identity` there | Python |
| the file is `<table>.odcs.yaml` or `<schema>.<table>.odcs.yaml` | `file_name` at `$` | Python |
| `team.name` is a string with text | `missing_field` there | both |
| no `servers`, `tenant` or `contractCreatedTs`, not even empty | `refused_field` there | Python |

## Custom properties

The rules read every item with `vendor: continuo` in every `customProperties` list, except inside
a free-form value such as a property's `value`. An unknown name is `unknown_property`. A property
on the wrong object, or one the family does not carry, is `refused_property`. A repeat in one list
is a `value` issue at each copy after the first.

`vocabulary.PROPERTIES` names each property and the object whose `customProperties` list carries
it, `sits_on`: the contract (`root`), the schema object (`schema`), a quality rule (`quality`) or a
column (`column`). Most sit on the contract. `reads`, `entity`, `grain`, `fixed` and `filter` sit on
the schema object, `property`, `local` and `aggregate` on a column, and `where`, `warnIf`, `errorIf`
and `failCalc` on a quality rule. Each family refuses some:

- **Python refuses**: `reads` (continuo fills it), `provenance`, and the dbt properties
  `filePath` `materialization` `contractEnforced` `access` `group` `deprecationDate`
  `freshnessBuildAfter` `snapshot` `quoteColumns`.
- **dbt refuses**: `provenance`, and the Python properties `script` `readQueries` `csvSource`
  `extraColumns` `config` `secretRef` `modeType` `assets`.

## Python contract rules

[Python contracts](python-contracts.md) lists what a Python contract writes: properties, kinds,
columns and the upsert key. A missing property is an issue at `customProperties`: `missing_field`
for `schedule` and `criticality`, `kind_rule` for one the kind needs. A refused property or a bad
value is an issue at its item, a column's fault at its field
(`schema[0].properties[2].physicalType`). A `kind` that is not a Python kind is a `value` issue and
skips the kind rules.

## dbt contract rules

A dbt contract writes `service`, `kind`, `filePath` and `materialization`, else `missing_field` at
`customProperties`; a [dormant node](glossary.md#dormant-node) has no `schedule`. A bad value is a
`value` issue at the property's `value`:

- `service`, `filePath`: a string with text. `kind`: `dbt-model`, `dbt-seed` or `dbt-snapshot`.
- `materialization`: a mapping whose `materialized` is text (its issue is at `value.materialized`).
- `criticality`: `REGULATORY`, `CORE` or `SECONDARY`. `schedule`: a name, no whitespace, 50 at most.
- `timeoutMinutes`: an integer from 1 to 10080, not a boolean or a float.

## Binding values

The seven bindings that tie a contract to an [ontology](ontology.md#bind-a-contract) are checked
for their shape in both families, whatever the node's kind and whether or not the install uses an
ontology: a malformed binding is always refused. A binding on another object than the one it sits
on is `refused_property`. A malformed value is a `value` issue at the `value` of its item,
`<list>[i].value`, for a Python contract and a dbt contract alike. That differs from an ordinary
Python property, whose `value` issue is at the item. Each fault of one value is its own issue.

- `entity`: the name of a thing, a string with no whitespace, control character, dot or slash.
- `grain`: a list of at least one string with text.
- `fixed` and `filter`: a mapping of names, strings with text, to a string, a finite number or a
  boolean.
- `property`: `<Thing>.<field>`, two names around one dot.
- `local`: `true`. A column that is not local leaves the property out.
- `aggregate`: a mapping whose keys are among `from`, `agg`, `where` and `period`, with a `from` or
  a `period`. `from` is `<Thing>` or `<Thing>.<field>`, `agg` and `period` are strings with text,
  and `where` is a mapping.

A binding written twice in one list is checked at its first copy, and the repeat is a `value`
issue like any repeated property. Whether the thing or the field exists, and whether a column has
more than one binding, is for the [rules](ontology.md#the-rules), not for `validate`.

```python
>>> from continuo_contracts import dbt
>>> good = yaml_strict.load("""
... apiVersion: v3.2.0
... kind: DataContract
... id: analytics.kpis
... version: "0"
... team: {name: analytics-team}
... customProperties:
...   - {vendor: continuo, property: schedule, value: daily}
...   - {vendor: continuo, property: criticality, value: CORE}
...   - {vendor: continuo, property: script, value: scripts/kpis.py}
...   - {vendor: continuo, property: readQueries, value: {tx: select 1}}
... schema:
...   - name: kpis
...     physicalType: table
...     customProperties:
...       - {vendor: continuo, property: grain, value: []}
...     properties:
...       - name: x
...         physicalType: int
...         customProperties: [{vendor: continuo, property: local, value: false}]
... """)
>>> for issue in validate.validate_python_contract(good, path="kpis.odcs.yaml", service=None):
...     print(issue.path, issue.reason)
schema[0].customProperties[0].value value
schema[0].properties[0].customProperties[0].value value
>>> node = {"resource_type": "model", "name": "kpis", "schema": "analytics",
...     "fqn": ["shop", "kpis"], "original_file_path": "models/kpis.sql",
...     "config": {"materialized": "table", "meta": {"owner": "team", "continuo": {"grain": []}}},
...     "columns": {"x": {"name": "x", "meta": {"continuo": {"property": "x"}}}}}
>>> for issue in validate.validate_dbt_contract(dbt.generate(node, [], manifest={}).document):
...     print(issue.path, issue.reason)
schema[0].customProperties[0].value value
schema[0].properties[0].customProperties[0].value value

```

## What passing gives

`contract.normalise_python` and `python.node_from_odcs` expect a Python contract that passed. A dbt
contract that passes holds every field `dbt.run_facts` reads, in the shape it reads, so `run_facts`
never fails on it. The [lite check](glossary.md#lite-check) hashes only contracts that pass.
