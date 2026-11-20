# dbt contracts

How does a dbt setting become a contract field? Which nodes are skipped?

The first section shows what to write in `schema.yml`. The rest specifies, in tables, how
`dbt.generate` writes a dbt [node](glossary.md#node)'s [contract](glossary.md#contract) from the
[manifest](glossary.md#manifest) alone. The [glossary](glossary.md#dbt-words) explains dbt words
such as seed, snapshot, `meta`, `fqn`, `ref`, macro and generic test.

## What to write in `schema.yml`

```yaml
models:
  - name: orders
    config:
      alias: orders_v                   # physicalName
      tags: [daily]                     # first tag: the schedule; dbt_project.yml +tags go first
      meta:
        owner: team-orders              # no owner, no contract
        criticality: CORE               # default SECONDARY
        timeout_minutes: 90             # timeoutMinutes
        continuo:
          readers: [finance]            # roles with access: read
          reads: [raw.payments]         # tables it reads; the release adds those its SQL names
          tests: [{name: row_count, granularity: daily, params: {min: 1}}]
    columns:
      - name: id
        data_tests: [not_null, unique]  # at dbt's default settings: required and unique
      - {name: email, meta: {continuo: {classification: personal}}}
```

The library reads a node's `config.meta`. dbt fills it from `meta:` under the model, from
`config: meta:` and from `+meta:` in `dbt_project.yml`, so each spelling gives the same contract.
A column's `meta:` and its `config: meta:` are one value too. `data_tests:` is dbt's key for
tests, and dbt still reads the older spelling `tests:`.

## Which nodes get a contract

Three [gates](glossary.md#gate), tried in this order, decide whether a model, seed or snapshot is
[skipped](glossary.md#skipped-node). The first that applies wins (`dbt.skip_reason`):

| Reason | The node is skipped when |
|---|---|
| `no_owner` | it has no `config.meta.owner`, or an empty one |
| `local_stub` | it has the tag `local_stub` |
| `installed_package` | it has no tag and its service is not the project's service |

A node's service is its `service` property. The project's service is `metadata.project_name`
with `_` replaced by `-`, else the [declared service](glossary.md#declared-service) (`--service`).
With neither, no node is skipped as `installed_package`.

## Contract fields

A field with no value (null, `""`, `[]` or `{}`) is left out: its key is absent. A field in a
shape the generator cannot read raises an error, `DbtNodeError`, that names the node and the field.

| Contract field | dbt source |
|---|---|
| `apiVersion`, `kind`, `version`, `status` | always `v3.2.0`, `DataContract`, `"0"`, `active` |
| `id` | `<schema>.<table>`, lowered with Python's `str.lower`; the alias is not part of it |
| `name`, `schema[0].name` | the table: `name`, or `<name>.v<version>` for a versioned model |
| `schema[0].physicalName` | `alias`, else the table |
| `schema[0].physicalType` | `config.materialized` when `view` or `ephemeral`, else `table` |
| `description.purpose` | `description`, trimmed; also the schema object's `description` |
| `team.name` | `config.meta.owner` |
| `tags` | the node's tags in dbt's order: the project's, then the model's |
| `roles` | each of `config.meta.continuo.readers` as `{role, access: read}`, then the grants |
| `roles` (grants) | `{role: <grantee>, access: <privilege>}`, privileges sorted by name |
| `schema[0]` property `reads` | `config.meta.continuo.reads` |
| property `service` | the first entry of `fqn`, `_` written `-` |
| property `kind` | `dbt-model`, `dbt-seed` or `dbt-snapshot` |
| property `schedule` | the first tag, else `seed` for a seed, else none: the node runs by hand |
| property `criticality` | `config.meta.criticality`, else `SECONDARY` |
| property `timeoutMinutes` | `config.meta.timeout_minutes` |
| property `filePath` | `original_file_path` |
| property `materialization` | `{materialized, uniqueKey, incrementalStrategy, onSchemaChange}` |
| property `contractEnforced` | `true` when `config.contract.enforced` is `true`; [validation](validation.md#dbt-contract-rules) refuses it on a snapshot |
| property `access` | a model's `config.access`, left out when `protected` |
| property `group` | `config.group` |
| property `deprecationDate` | the `YYYY-MM-DD` that starts `deprecation_date`, else an error |
| property `freshnessBuildAfter` | `{count, period}` of a model's `config.freshness.build_after` |
| property `snapshot` | a snapshot's settings, below |
| property `quoteColumns` | a seed's `config.quote_columns` |
| `schema[0]` properties `entity`, `grain`, `fixed`, `filter` | the same keys of `config.meta.continuo`: [bindings](#bind-to-an-ontology) |

A property is a root `{vendor: continuo, property, value}` item, written in this order when it has
a value. `service` is left out with no `fqn`, which validation reports as `missing_field`.
`materialization` leaves out `onSchemaChange: ignore`, and `snapshot` holds `strategy`,
`unique_key`, `updated_at`, `check_cols`, `hard_deletes` in camelCase, then
`snapshot_meta_column_names` as `metaColumnNames` and `dbt_valid_to_current` as `validToCurrent`.

`reads` is the list of tables a node reads: those declared in `meta.continuo.reads` plus those
its compiled SQL names. The library writes the declared ones, and continuo's
[release](glossary.md#release) adds the SQL-named ones. The [lite check](glossary.md#lite-check)
cannot, so its hash differs from the release's when the SQL reads a table `reads` does not declare.

## Columns

The `properties` are the declared columns in dbt's `columns` order. A snapshot then adds each
history column it does not declare as `{name}`: `dbt_scd_id`, `dbt_updated_at`, `dbt_valid_from`,
`dbt_valid_to`, and `dbt_is_deleted` for `hard_deletes: new_record`. `snapshot_meta_column_names`
may rename each. dbt fills a model's `primary_key` from a primary-key constraint or from a column
with both `not_null` and `unique` tests.

| Column field | dbt source |
|---|---|
| `name`, `description`, `tags` | the column's own; the description trimmed |
| `physicalType` | `data_type`, trimmed; on a seed, a non-blank `config.column_types` entry wins |
| `logicalType` | from `physicalType`, by the list below |
| `classification` | the column's `meta.continuo.classification` |
| properties `property`, `local`, `aggregate` | the column's `meta.continuo.property`, `.local`, and `.from`, `.agg`, `.where`, `.period` as one `aggregate`: [bindings](#bind-to-an-ontology) |
| `primaryKey` | `true` when a model's `primary_key` lists it; an undeclared entry is dropped |
| `primaryKeyPosition` | its place in `primary_key` from 1 (dropped entries count), in a key of 2+ |

**Trimmed**: a description has CRLF and CR turned into LF and loses space, TAB, LF, VT, FF and CR
at both ends. A type loses the 29 [whitespace code points](hashing.md#canonical-sql-type) instead.

`dbt.logical_type` reads a type as `<name>` or `<name>(<inside>)`, then only spaces or tabs.
Neither part holds a parenthesis, and only `timestamp(<inside>)` may add `with time zone` or
`without time zone`. So `numeric(10, 2)` is `number` and `varchar(64) not null` has none. The name
is read in lower case, each run of spaces or tabs as one space, and maps by this list:

- `integer`: `int`, `integer`, `int4`, `bigint`, `int8`, `smallint`, `int2`
- `number`: `numeric`, `decimal`, `double precision`, `float`, `float4`, `float8`, `real`, `double`
- `string`: `text`, `string`, `varchar`, `character varying`, `char`, `character`
- `timestamp`: `timestamp`, `timestamptz`, `timestamp with[out] time zone`, `datetime`
- `date`: `date`; `boolean`: `boolean`, `bool`

Another name, a `[` or a non-ASCII character gives none. A string type whose `<inside>` is digits
from 1 to 2**53 - 1, leading zeros ignored, gives `logicalTypeOptions.maxLength`.

## Bind to an ontology

A node and its columns may carry the seven [bindings](glossary.md#binding) that tie a contract to an
[ontology](ontology.md). A team writes them under `meta.continuo`, and `dbt.generate` writes them
as custom properties of the `continuo` vendor:

```yaml
models:
  - name: daily_sales
    config:
      meta:
        owner: team-shop
        continuo:
          entity: Sale                  # the table is a copy of the thing Sale
          fixed: {channel: web}         # it holds only this value of a key part
          filter: {status: paid}        # and only the rows with this value of a field
    columns:
      - {name: sale_id, meta: {continuo: {property: Sale.sale_id}}}
      - {name: note, meta: {continuo: {local: true}}}
      - name: total
        meta: {continuo: {from: Sale.amount, agg: sum, where: {channel: web}}}
      - {name: day, meta: {continuo: {period: day}}}
```

| `meta.continuo` key | Where | Becomes |
|---|---|---|
| `entity`, `grain`, `fixed`, `filter` | the node's `config.meta` | the property of that name on the schema object, after `reads` |
| `property` | a column's `meta` | the column's property `property`, `<Thing>.<field>` |
| `local` | a column's `meta` | the column's property `local`, written only when `true` |
| `from`, `agg`, `where`, `period` | a column's `meta` | the one column property `aggregate`, a mapping of the keys the column writes |

A key that is absent or null writes nothing, nor does `local: false`. A value is copied as the
manifest holds it, whatever its shape, so a wrong one reaches `validate`, which refuses it with a
`value` issue ([validation](validation.md#binding-values)). A node's `source_sha256` covers these
keys ([source_sha256](source-sha256.md#read-trees)). The [ontology](ontology.md) page says what each
binding means and which rules check it.

```python
>>> from continuo_contracts import dbt, validate
>>> node = {
...     "resource_type": "model", "name": "daily_sales", "schema": "analytics",
...     "fqn": ["shop", "daily_sales"], "tags": ["daily"],
...     "original_file_path": "models/daily_sales.sql",
...     "config": {"materialized": "table", "meta": {"owner": "team-shop", "continuo": {
...         "entity": "Sale", "fixed": {"channel": "web"}, "filter": {"status": "paid"}}}},
...     "columns": {
...         "sale_id": {"name": "sale_id", "meta": {"continuo": {"property": "Sale.sale_id"}}},
...         "note": {"name": "note", "meta": {"continuo": {"local": True}}},
...         "total": {"name": "total", "meta": {"continuo": {
...             "from": "Sale.amount", "agg": "sum", "where": {"channel": "web"}}}},
...         "day": {"name": "day", "meta": {"continuo": {"period": "day"}}}}}
>>> doc = dbt.generate(node, [], manifest={}).document
>>> {p["property"]: p["value"] for p in doc["schema"][0]["customProperties"]}
{'entity': 'Sale', 'fixed': {'channel': 'web'}, 'filter': {'status': 'paid'}}
>>> for column in doc["schema"][0]["properties"]:
...     [item] = column["customProperties"]
...     print(column["name"], item["property"], item["value"])
sale_id property Sale.sale_id
note local True
total aggregate {'agg': 'sum', 'from': 'Sale.amount', 'where': {'channel': 'web'}}
day aggregate {'period': 'day'}
>>> validate.validate_dbt_contract(doc)
[]

```

## Tests and quality rules

A node's tests are the manifest's enabled `test` entries whose `attached_node` is the node, or,
with no `attached_node`, that list it in `depends_on.nodes`. A test is **built-in** when it is
dbt's `not_null`, `unique`, `accepted_values` or `relationships`: namespace `dbt`, or none and
`macro.dbt.test_<name>` first in `depends_on.macros`. A project's own `test_<name>` macro makes it
an other generic test, but overriding dbt's macro through `dispatch` does not.

A test is **strict** when it has severity `error`, no `where`, and dbt's defaults
`warn_if: "!= 0"`, `error_if: "!= 0"` and `fail_calc: count(*)`.

| dbt test | Quality rule | A strict one also sets |
|---|---|---|
| `not_null` | `{id, metric: nullValues, mustBe: 0}` | the column's `required: true` |
| `unique` | `{id, metric: duplicateValues, mustBe: 0}` | the column's `unique: true` |
| `accepted_values` | `{id, metric: invalidValues, mustBe: 0, arguments}` | - |
| `relationships` | none when strict with a target, else as other generic | `relationships` |
| other generic | `{id, name, type: custom, engine: dbt, implementation: {test, arguments}}` | - |
| singular | `{id, name, type: custom, engine: dbt, implementation: <name>}` | - |
| `config.meta.continuo.tests` | `{type: custom, engine: data-quality, name, implementation}` | - |

- `id` and `name` are the test's name. `test` is the generic test's name, `<namespace>.` first.
- `arguments` are the kwargs without `model` and `column_name`; `not_null` and `unique` keep none.
  `accepted_values` writes its `values` as `validValues`, then its other kwargs, such as `quote`.
- A rule from a test carries `severity: warning` for `warn`, its `description` and `tags`, and
  `where`, `warnIf`, `errorIf`, `failCalc` as custom properties when not dbt's defaults.
- A table test's `implementation` is `{enabled, granularity, params}`; `enabled` defaults true.
- Column rules and schema-object test rules sort by [canonical JSON](hashing.md#canonical-json);
  table tests follow as written.

**The column a test checks** is its `column_name` kwarg when that differs from the test's own
`column_name`, else the test's own. A name in one pair of SQL quotes (`"id"`, `` `id` ``, `[id]`)
names the declared column `id` when none is called `"id"`, in `primary_key` too. A singular test,
a generic test with no column, and a test on an undeclared column or an expression give rules on
the schema object. The last kind keeps its column as the `column_name` argument.

**A relationship** is `{to: "<target id>.<field>"}`, such as `analytics.customers.id`: `field` is
the test's `field` kwarg as written. The target is the one other node or source in the test's
`depends_on.nodes`, else the tested node ([all cases](source-sha256.md#relationship-targets)). Its
id is the node's `id`, or a source's `<schema>.<identifier or name>` in lower case.

`dbt.generate` also returns flags for a person to review, which the lite check does not print.
`jinja_in_test_arguments` marks `{{` or `{%` in a test's kwargs or `where`.
`test_column_not_declared` marks a test on an undeclared column or an expression, and
`primary_key_column_not_declared` a `primary_key` entry that names no declared column.
