# Contracts for Python nodes

How do I write and check the contract of a Python node?

A Python node is a table that [continuo](glossary.md#continuo) fills with a script, a CSV file or an
API call. You write its [contract](glossary.md#contract): one [ODCS](glossary.md#odcs) (Open Data
Contract Standard) YAML file, `<schema>.<table>.odcs.yaml` or `<table>.odcs.yaml`.

## A minimal contract

The quoted text is the file `analytics.daily_kpis.odcs.yaml`: a script reads a table, by day.

```python
>>> from continuo_contracts import validate, yaml_strict
>>> text = """\
... apiVersion: v3.2.0
... kind: DataContract
... id: analytics.daily_kpis
... version: "0"
... team: {name: finance}
... customProperties:
...   - {vendor: continuo, property: schedule, value: daily}
...   - {vendor: continuo, property: criticality, value: CORE}
...   - {vendor: continuo, property: script, value: scripts/daily_kpis.py}
...   - {vendor: continuo, property: readQueries, value: {tx: select * from analytics.transactions}}
... schema:
...   - name: daily_kpis
...     physicalType: table
...     properties:
...       - {name: day, physicalType: date, required: true}
...       - {name: tx_count, physicalType: int, required: true}
...       - {name: total_eur, physicalType: "decimal(12, 2)"}
... """
>>> path = "contracts/analytics.daily_kpis.odcs.yaml"
>>> validate.validate_python_contract(yaml_strict.load(text), path=path, service="finance")
[]

```

An empty list means the contract is valid; [validation](validation.md) lists every issue reason. The
`id` is `<schema>.<table>` in lower case, the one schema object is the table, `team.name` is
required, and `name` and `physicalName`, when written, equal the table. ODCS requires `version`, but
the [stored form](glossary.md#stored-form) sets it to `"0"`, so any string works. `servers` and
`tenant` are refused because continuo decides them, `contractCreatedTs` because a clock is not part
of a contract.

## Properties

continuo reads its settings from the root `customProperties` items `{vendor: continuo, property:
<name>, value: <value>}`, each property written once.

| Property | Value | Written |
|---|---|---|
| `schedule` | a schedule name, no whitespace, at most 50 characters | always |
| `criticality` | `REGULATORY`, `CORE` or `SECONDARY` | always |
| `kind` | `python-node` (default), `python-csv` or `python-api` | optional |
| `service` | the [declared service](glossary.md#declared-service) | optional |
| `timeoutMinutes` | an integer from 1 to 10080 (`90.0` is refused) | optional |
| `modeType` | `replace` (default), `append` or `upsert` | optional |
| `extraColumns` | `raise` (default) or `warn` | optional |
| `config` | a mapping of settings for the script | optional |
| `script` | a project path | by kind |
| `readQueries` | the read queries: a mapping of query name to SQL text, at least one | by kind |
| `csvSource` | `s3://<bucket>/<key>` or `https://<host>[/<path>]` | by kind |
| `secretRef` | a Kubernetes Secret: `continuo-api-`, then `a-z`, `0-9` and `-` | by kind |
| `assets` | project paths of the files the script reads, each listed once | by kind |

A **project path** is relative and separated by `/`. It does not start with `/`, `~` or a drive
letter such as `C:`, and has no empty, `.` or `..` part: `./a.py`, `a//b` and `a\b` are refused.
`secretRef` ends in a letter or a digit and has at most 253 characters.

| Kind | Fills its table from | Needs | Refuses |
|---|---|---|---|
| `python-node` | a script that reads tables | `script`, `readQueries` | `csvSource`, `secretRef` |
| `python-csv` | a CSV file | `csvSource` | `script`, `readQueries`, `secretRef`, `assets` |
| `python-api` | a script that calls an API | `script` | `readQueries`, `csvSource` |

A `python-csv` node only replaces its table, and `python-model` reads as `python-node`. continuo
fills `reads` on the schema object with the tables the read queries name (`hash --read` passes
them), so you never write it. Properties that only dbt contracts carry, such as `materialization`,
are refused, and so are `provenance` and unknown names. A quality rule may carry `where`,
`warnIf`, `errorIf` and `failCalc`, and its `implementation`, if written, is not `""` or `{}`.

## Columns

The columns are the `properties` of the schema object: at least one, each name used once. Each
has one of these SQL types in `physicalType`, in any case and with any outer whitespace:

| Written | Stored as |
|---|---|
| `int`, `integer`, `bigint` | `INTEGER`, `INTEGER`, `BIGINT` |
| `text`, `date`, `timestamp`, `boolean`, `double precision` | the same in upper case |
| `decimal(12, 2)`, `numeric(012,2)` | `NUMERIC(12,2)`: precision 1 to 38, scale 0 up to it |
| `varchar(64)`, `char(3)` | `VARCHAR(64)`, `CHAR(3)`: length from 1 to 2**53 - 1 |

Every other type is refused, such as `FLOAT`, `VARCHAR`, `NUMERIC(10)` and `TIMESTAMPTZ`. A column
is nullable unless it has `required: true`. It nests nothing (`items`, `map`, `properties`), is not
partitioned, and writes no `logicalTypeOptions` and no `physicalName` but its name.

An `upsert` node needs a key: `primaryKey: true` on one column or more, each `required: true` and
not `DOUBLE PRECISION`. In a key of two or more columns, each has its own `primaryKeyPosition`, a
whole number from 1 (`1.0` counts as 1). On `replace` and `append` the key only describes the table.

## The `validate` and `hash` commands

`continuo-contracts validate PATH... [--service SERVICE] [--json]` checks each file, and each
`*.odcs.yaml` file directly inside a folder, and logs each issue at ERROR as
`<file>: <path>: <message>`. `hash` checks one file and prints its
[contract hash](glossary.md#contract-hash), which covers the service:
`continuo-contracts hash FILE --service SERVICE [--read TABLE]... [--json]`. Each `--read` is a
table the read queries name, as `<schema>.<table>`: pass the tables that continuo's
[release](glossary.md#release) passes to get the hash it stores.

```bash
$ continuo-contracts validate contracts
INFO this release checks Python contracts
INFO 1 contract checked, 0 with issues
$ continuo-contracts hash contracts/analytics.daily_kpis.odcs.yaml --service finance \
    --read analytics.transactions
INFO this release checks Python contracts
sha256:87fc212d4be5ed48feaf69521cfc172f93e8fda263ae09f65357c74d674d3739
```

Logs go to stderr, the result to stdout. In the first log line, "release" means this package's
version. With `--json`, `hash` prints `{"contract_hash", "stored"}` and `validate` prints
`{"valid", "files": [{"path", "issues": [{"path", "reason", "message"}]}]}`.

The exit status is 0 when every contract is valid and 1 when a contract has an issue. It is 2
when the command could not run: a bad argument, such as a blank `--service` or a `--read` that is
not `<schema>.<table>`. No `*.odcs.yaml` file to check gives 2 too, and so does a path or file that
cannot be read or is not UTF-8.

## Strict YAML

Both commands read a file with `yaml_strict.load`: YAML 1.2 core, so one file means one thing to
every reader. A refusal is the one issue `strict_yaml` at `$`, its message
`line <n>, column <n>: <reason>` with the reason below.

| Written | Read as |
|---|---|
| a quoted scalar, or one after `!!str` or a lone `!` | a string |
| `~`, `null`, `Null`, `NULL`, or nothing | null |
| `true`, `True`, `TRUE`, `false`, `False`, `FALSE` | a boolean |
| `12`, `+1`, `-3`, `007` (7), `0o17` (15), `0x1F` (31) | an integer |
| `1.5`, `-.5`, `.5`, `1.`, `1e3` (1000.0), `1.5e-3` | a float |
| `-0x1F`, `0X1F`, `0b101`, `1_000`, `on`, `yes`, `tRue`, `2026-11-14`, `12:30` | a string |
| a block scalar: `\|` keeps line breaks, `>` folds them into spaces | a string |
| flow style, such as `{a: 1}` or `[1, 2]` | a mapping or a list, as block style |
| an anchor `&name` that no alias uses | the value; the anchor is ignored |
| a leading byte order mark, CRLF | dropped; read as LF |
| `!!int`, `!!float`, `!!bool`, `!!null`, `!!seq`, `!!map` on a value of that form | that type |
| a core tag on another form, such as `!!int abc` | refused: `'abc' is not an integer` |
| an alias `*name` | refused: `YAML aliases are not allowed` |
| a merge key `<<` | refused: `merge keys are not allowed` |
| a repeated key | refused: `duplicate key 'a'` |
| a key that is not a string | refused: `mapping key 1 is not a string`, or `mapping keys must be strings` |
| a second document | refused: `expected a single document in the stream, but found another document` |
| any other tag, such as `!!binary` or `!custom` | refused: `the tag !!binary is not allowed` |
| `.nan`, `.inf`, `-.inf`, `1e999` | refused: `$.a: NaN and the infinities are not JSON numbers` |
| an integer beyond ±(2**53 - 1) | refused: `$.a: integer is outside -(2**53 - 1) to 2**53 - 1` |
| a lone surrogate, such as `"\ud800"` | refused: `$.a: string holds a lone surrogate` |
| a container 101 levels down | refused: `nesting is too deep, a container may be at most 100 levels down` |

A value refused with a `$.a` path has no place in the text: line 0, column 0. Aliases are refused
because a few lines could expand into any size, merge and repeated keys because readers disagree.
