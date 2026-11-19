# Hashing

What is hashed, and how are the bytes written? Every rule is exact, for a [port](glossary.md#port).

## What to implement

1. A JSON reader for every [node](glossary.md#node), then [canonical JSON](#canonical-json).
2. Python: read and check the contract file ([python-contracts](python-contracts.md)).
3. dbt: generate the [contract](glossary.md#contract) from the [manifest](glossary.md#manifest)
   node. The tables of [dbt-contracts](dbt-contracts.md) and the `dbt_contracts.json` [test
   vector](glossary.md#test-vector) specify this step, not byte rules.
4. dbt: its [`source_sha256`](source-sha256.md).
5. Both: the [stored form](glossary.md#stored-form) and [`contract_hash`](contract-hash.md).
6. Python: the code [projection](glossary.md#projection) and [`projection_hash`](#projection_hash).
7. Both: the [content hash](glossary.md#content-hash), which [`fold3` or
   `fold4`](#sha-256-and-the-folds) joins from the node's parts. This package computes only
   `projection_hash`. continuo computes a dbt node's parts, and the team's CI hashes a Python
   node's script and in-repo import closure ([fingerprints](fingerprints.md#python-node)).

The JSON reader keeps integers exact and apart from floats (numbers with a point or an exponent)
and booleans, and object keys in file order. It accepts at least 101 levels of nesting. A lone
surrogate (`"\ud800"`) or nesting deeper than 100 levels must end in an error, which canonical JSON
gives; the reader never repairs it. The [vectors](vectors.md) ship in the wheel, under
`continuo_contracts/vectors/`: `pip download continuo-contracts==<version> --no-deps`, then unzip.

[Validation](validation.md) uses `continuo_contracts/odcs-json-schema-v3.2.0.json` (ODCS v3.2.0,
JSON Schema draft 2019-09) and [continuo](glossary.md#continuo)'s rules. The hashes are defined on a
contract that passed it. `normalise_*` checks only `service` and the `reads` tables, so a port that
hashes only contracts the package accepted need not validate.

A dbt node's `id` is `<schema>.<table>` lower-cased as one string by Python 3.14's `str.lower`
(Unicode 16.0): a table `Σ` gives `analytics.ς` (no vector covers this). Its `reads`, the tables
it reads, are those declared in [`meta.continuo.reads`](glossary.md#dbt-words) plus those its
compiled SQL names, which this package does not find ([contract-hash](contract-hash.md#fill)).

`contract_hash`, `fold3` and `fold4` give `sha256:` and 64 lowercase hex digits. `source_sha256`,
`projection_hash`, `sha256_hex`, `shared_fold` and `shared_hash` give the 64 digits with no prefix
(`shared_fold` and `shared_hash` give `""` for no units).

## Canonical JSON

`canonical.canonical_json` writes RFC 8785 canonical JSON, UTF-8 encoded, with no whitespace. It
takes `null`, `true`, `false`, integers from -(2**53 - 1) to 2**53 - 1, finite floats, strings,
arrays and objects with string keys. A boolean is not a number.

- Keys: sorted by UTF-16 code units (compare the UTF-16BE bytes), so U+1F600 sorts before U+FF5E.
- Strings: `"` is `\"` and `\` is `\\`. U+0008, U+0009, U+000A, U+000C, U+000D are `\b`, `\t`,
  `\n`, `\f`, `\r`. Any other code point below U+0020 is `\u00xx` (lowercase hex). No other escapes.
- Floats: ECMAScript's `Number::toString`, with no range limit. `0.0` and `-0.0` are `0`, and a
  negative float is `-` then its absolute value. Take the shortest digits `d1..dk` that read back
  as the same double, and `n` with value `0.d1..dk * 10**n`. The first row that holds applies:

| When | Written | Example |
|---|---|---|
| `k <= n <= 21` | the digits, then `n - k` zeros | `1e20`: `100000000000000000000` |
| `0 < n <= 21` | the digits, a point after digit `n` | `1.5`: `1.5` |
| `-6 < n <= 0` | `0.`, then `-n` zeros, then the digits | `1e-6`: `0.000001` |
| otherwise | `d1`, `.d2..dk` if `k > 1`, `e`, `+` or `-`, `abs(n - 1)` | `1e21`: `1e+21` |

`CanonicalJsonError`, whose message starts with the JSON path (`$.a[0]`), refuses another type,
a key that is not a string, an integer out of range, NaN and an infinity. It also refuses a lone
surrogate, a container inside itself, and one deeper than `MAX_DEPTH`, 100 levels (outermost: 1).

## SHA-256 and the folds

`fold3` and `fold4` join their parts with `|`, hash the UTF-8 bytes and add `sha256:`. A part is
a bare hex digest or `""`, unchecked. `fold3(source, shared, config)` is a Python node's
`content_hash`, with its `projection_hash` as `config`. `fold4(source, shared, config, compiled)`
is a dbt node's: a [seed](glossary.md#dbt-words) or a test passes `compiled=""` (last `|` stays).

```text
shared_fold(units):      # key (an id or a repo-relative POSIX path) -> 64 lowercase hex digits
  if units is empty: return ""
  HashInputError for the first key, by code point, that is "", holds NUL or LF, cannot be
    UTF-8 encoded, or whose digest is not 64 lowercase hex digits
  lines = [key + NUL + units[key] for each key, sorted by code point]
  return sha256_hex(UTF-8 of the lines joined by LF, with no final LF)
shared_hash(units) = shared_fold({key: sha256_hex(data) for each key -> data of units})
```

## projection_hash

`python.projection_hash` is the digest of the canonical JSON of `python.code_projection`: the 12
keys of `projection_keys` in `fingerprints.yaml`, in that order. The node comes from:

- a wire entry, the node as one mapping (`PythonNode.from_mapping`). It needs `schema` and `table`
  and ignores other keys. An absent field takes its default: `kind` `python-node`, `config` `{}`,
  `reads` `{}`, `extra_columns` `raise`, `mode_type` `replace`, `script` and `secret_ref` `""`.
  `output_columns`, `primary_key` and `assets` default to `[]`, a column's `nullable` to `true`;
- or a contract (`python.node_from_odcs`). `schema` is the id without its final `.<table>`
  (compared ignoring ASCII case), `table` the schema object's name. `kind`, `script`, `config`,
  `assets`, `extra_columns`, `secret_ref` and `mode_type` are the `continuo` properties of those
  names in camelCase, else the defaults. `reads` is `readQueries`, or `{"csv": csvSource}` on
  `python-csv`. A column is its `name` and `physicalType`, nullable unless `required`.
  `primary_key` lists the `primaryKey: true` columns by `primaryKeyPosition` (a whole number from
  1; columns without one come last), ties in file order.

The projection writes each key as follows; any other value raises `ProjectionError`.

- `kind`: `python-node`, `python-csv` or `python-api`, with `python-model` written `python-node`.
  `extra_columns`: `raise` or `warn`. `mode_type`: `replace`, `append` or `upsert`.
- Non-empty strings: `schema`, `table`, column names, `assets`, and the `primary_key` entries of
  an `upsert`. On other modes `primary_key` is `[]`. Every other string may be empty.
- `python-csv` needs `script` `""`, `reads` exactly `{"csv": <uri>}`, `mode_type` `replace` and no
  `assets`. `python-api` needs `reads` `{}`, and the other kinds need `secret_ref` `""`.
- On `python-node` each SQL of `reads` gets LF line ends and loses the six ASCII whitespace
  characters (space, TAB, LF, VT, FF, CR) at both ends. A column is `{"name", "type",
  "nullable"}`, with a [canonical SQL type](#canonical-sql-type) and a boolean. `assets` are
  sorted by code point; a repeated asset raises `ProjectionError`.
- `config` is a mapping with string keys, as written. It nests at most 99 levels, itself level 1,
  because the projection holds it one level down and canonical JSON stops at 100.
- Accepted: an upsert with an empty `primary_key`, a repeated key entry, or two columns of one
  name. No other string is trimmed, case-changed or Unicode-normalised.

### Canonical SQL type

`python.canonical_sql_type` strips these 29 code points at both ends: U+0009 to U+000D, U+001C to
U+001F, U+0020, U+0085, U+00A0, U+1680, U+2000 to U+200A, U+2028, U+2029, U+202F, U+205F, U+3000. It
then matches, ignoring ASCII case, `BIGINT`, `INT`, `INTEGER`, `DOUBLE PRECISION` (one space),
`TEXT`, `TIMESTAMP`, `DATE`, `BOOLEAN`, `NUMERIC(p,s)`, `DECIMAL(p,s)`, `VARCHAR(n)` and `CHAR(n)`.
`p`, `s` and `n` are ASCII digits, and a run of the six ASCII whitespace characters may follow the
comma.

It writes upper case, `INT` as `INTEGER`, `DECIMAL` as `NUMERIC`, numbers without leading zeros or
spaces. `NUMERIC` needs `1 <= p <= 38` and `0 <= s <= p`. Anything else raises `ProjectionError`.
