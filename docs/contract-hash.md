# Contract hash

How is a contract turned into its stored form and hashed? The [stored form](glossary.md#stored-form)
writes a [contract](glossary.md#contract) in one canonical shape, so two files that mean the same
thing store the same document and get the same [contract hash](glossary.md#contract-hash).

## Hash basis

`contract.contract_hash(stored)` is `sha256:` and the hex SHA-256 of the [canonical
JSON](hashing.md#canonical-json) of the stored form without the root keys `apiVersion`, `kind`,
`status` and `version` (`contract_hash_exclusions` in `fingerprints.yaml`). That `kind` is
`DataContract`; the `continuo` property `kind` is hashed. `normalise_python` and `normalise_dbt`
build the stored form in four steps:

1. Copy the contract. A float that is a whole number within ±(2**53 - 1) becomes an integer.
2. [Fill](#fill) it.
3. When only one column of the schema object has `primaryKey: true`, drop its `primaryKeyPosition`.
4. [Walk](#walk) it.

```python
>>> import hashlib
>>> from continuo_contracts.contract import contract_hash, normalise_dbt
>>> stored = normalise_dbt({"kind": "DataContract", "id": "a.t", "tags": ["b", "a", "a"],
...     "schema": [{"name": "t", "properties": [{"name": "id", "physicalName": "id", "required":
...     False, "primaryKey": True, "primaryKeyPosition": 1.0, "description": " Key\r\n"}]}]},
...     reads=["Raw.Events"])
>>> basis = (b'{"id":"a.t","schema":[{"customProperties":[{"property":"reads","value":'
...     b'["raw.events"],"vendor":"continuo"}],"name":"t","properties":[{"description":"Key",'
...     b'"name":"id","primaryKey":true}]}],"tags":["a","b"]}')
>>> contract_hash(stored), hashlib.sha256(basis).hexdigest()
('sha256:6a9dd19354d1496618d5bcf46051e2b28fc9bf7b3422339f6dcff44d5007853d',
 '6a9dd19354d1496618d5bcf46051e2b28fc9bf7b3422339f6dcff44d5007853d')

```

## Fill

`reads` is the list of tables a [node](glossary.md#node) reads. For a dbt node it is the tables
declared in [`meta.continuo.reads`](glossary.md#dbt-words), which `dbt.generate` writes, plus those
its compiled SQL names. [continuo](glossary.md#continuo)'s [release](glossary.md#release) adds the
SQL-named ones; the [lite check](glossary.md#lite-check) cannot, so its hash differs from the
release's when the SQL reads a table `reads` does not declare. This package does not specify how to
find tables in SQL, so a port cannot reproduce the release's hash for those nodes.

A table is two non-empty parts around one dot, so `analytics.customer.v2` is refused. It holds no
control character (U+0000 to U+001F, U+007F to U+009F) and none of the 29 whitespace code points
of a [canonical SQL type](hashing.md#canonical-sql-type). Other entries, and `reads` as one
string, raise `HashInputError`. Tables are stored ASCII-lower-cased, sorted by code point, once.

`normalise_dbt(doc, reads=...)` first removes every `continuo` `reads` item of the schema object,
keeping the `value` of the last one that has one. It then joins that value's tables with `reads`
and, when there is a table, adds `{vendor: continuo, property: reads, value: <tables>}`.

`normalise_python(doc, service=..., reads=...)`:

- Sets `version: "0"`, `status: "active"` and the root `continuo` properties `service` and `kind`,
  overwriting. `kind` is the written one, else `python-node`, and `python-model` becomes
  `python-node`. An empty or blank `service` raises `HashInputError`.
- Sets an absent root `name` or schema object `physicalName` to the schema object's `name`.
- Removes each column's `logicalType` and `logicalTypeOptions`. When `canonical_sql_type` accepts
  its `physicalType`, it writes the canonical spelling and the logical type below.
- With a table in `reads` (those the read queries name), appends `{vendor: continuo, property:
  reads, value: <tables>}` to the schema object's `customProperties`.

| Canonical type | Logical type |
|---|---|
| `INTEGER`, `BIGINT` | `integer` |
| `NUMERIC(p,s)`, `DOUBLE PRECISION` | `number` |
| `TEXT`, `DATE`, `TIMESTAMP`, `BOOLEAN` | `string`, `date`, `timestamp`, `boolean` |
| `VARCHAR(n)`, `CHAR(n)`, `1 <= n <= 2**53 - 1` | `string`, `logicalTypeOptions: {maxLength: n}` |

## Walk

Each object, or each item of a list, gets the type its parent's type names for its key:

| Parent type | Key: type of the value |
|---|---|
| root | `schema`: schema object, `slaProperties`: service level |
| root | `description`: description object |
| schema object | `properties`: property, `quality`: quality rule, `relationships`: relationship |
| property | the keys of a schema object, and `items`: property, `map`: map, `enum`: enum entry |
| map | `key` and `value`: property |
| any type | `customProperties`: custom property, any other key: plain object |

Each type's defaults and free-form keys (data copied as written) follow. Other types have neither.

- schema object: defaults `deprecated: false`, `logicalType: "object"`.
- property: defaults `false` for `deprecated`, `primaryKey`, `required`, `unique`, `partitioned`,
  `criticalDataElement`, `-1` for `primaryKeyPosition`, `partitionKeyPosition`, `"column"` for
  `semanticType`. Free-form: `examples`.
- quality rule: default `type: "library"`. Free-form: `implementation`, `arguments`, and `mustBe`
  and `mustNotBe`, kept even when empty. Relationship: default `type: "foreignKey"`.
- custom property, enum entry: free-form `value`, kept even when empty. Service level: the same,
  and free-form `valueExt`.

**Empty** means null, `""`, `[]` or `{}`. For each key of an object, in order:

1. A free-form value is copied as in step 1 of the hash basis, and dropped when empty unless kept.
2. Any other value is walked: an object key by key, a list item by item. The list then drops each
   empty item. Under `customProperties` it drops an item whose `value` is missing or empty. There it
   also drops a `vendor: continuo` item whose `property` and `value` are `modeType` and
   `"replace"`, or `extraColumns` and `"raise"`.
3. A string under a `description` key, or directly in the description object, gets CRLF and CR
   turned into LF. It then loses the six ASCII whitespace characters (space, TAB, LF, VT, FF, CR)
   at both ends. A quality rule's `severity` gets its ASCII letters lower-cased.
4. The key is dropped when its value is empty, or is its type's default (`0` is not `false`).
5. A list under `customProperties`, `tags`, `roles`, `quality`, `relationships` or
   `authoritativeDefinitions` is sorted by the canonical JSON bytes of each item, each item once.
   Every other list, such as `properties`, keeps its order.
6. After the last key, a property drops its `physicalName` when it equals its `name`.
