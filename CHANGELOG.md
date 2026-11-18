# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `continuo_contracts.dbt`: the gates and the names of a dbt manifest node. `skip_reason` gives the
  reason continuo leaves a model, seed or snapshot out (`no_owner`, `local_stub` or
  `installed_package`, checked in that order) or `None` for a node it keeps, `node_service`,
  `project_service`, `schedule`, `table_name` and `unique_id` give its service, schedule and id, and
  `tests_by_node` joins the tests of a manifest to the nodes they test. For every node of the five
  fixture manifests, `skip_reason` gives the skip reason continuo's parser recorded for that node,
  which `tests/fixtures/dbt/recorded_gates.json` holds. A field of a node in a shape these
  functions cannot read raises the new `DbtNodeError`, and so does, from `project_service` and
  `tests_by_node`, a manifest `metadata`, `nodes` or test field in a shape they cannot read.
- `continuo_contracts.dbt.generate` writes the ODCS contract of a kept dbt model, seed or snapshot
  from its manifest node and its tests:
  - the envelope, `id`, `name`, `description.purpose`, `team.name`, `tags`, `roles` (readers and
    grants; an empty or blank one raises `DbtNodeError`), the fourteen root `continuo` properties
    a dbt contract may carry, each only when it has a value, and one schema object with its
    `physicalName`, `physicalType` and declared `reads`. A `deprecationDate` is the `YYYY-MM-DD`
    date part of `deprecation_date` as written, read by a fixed pattern and checked as a calendar
    date, so it is the date the team wrote in every time zone; a week date, a basic date or an
    ordinal date raises `DbtNodeError`.
  - the schema object's `properties`: the declared columns in the order of the node's `columns`,
    each with its `physicalType` (a seed's `config.column_types` wins over `data_type`), its
    `logicalType`, description, tags, `classification` and a model's `primaryKey` (with
    `primaryKeyPosition` in a key of two or more columns), then a snapshot's history columns
    under the names `snapshot_meta_column_names` gives them.
  - a rule for each dbt test: `not_null`, `unique` and `accepted_values` are the library metrics
    `nullValues`, `duplicateValues` and `invalidValues`, and a strict test (severity `error`, no
    `where` and dbt's default thresholds) makes a column `required` or `unique`; an
    `accepted_values` rule's arguments are its `values` as `validValues` and its other kwargs as
    written (such as `quote`); a strict `relationships` test is a relationship to
    `<target id>.<field>`, and one that is not strict is a `custom` rule with its severity and
    `where`; any other generic test is a `custom` rule with `implementation: {test, arguments}`;
    a singular test is a `custom` rule on the schema object, as is each table test of
    `config.meta.continuo.tests` (`engine: data-quality`). A rule carries `severity` when it is
    not `error`, the test's description and tags, and `where`, `warnIf`, `errorIf` and
    `failCalc` when they are not dbt's defaults.

  It returns a `Generated` with the document, the contract's flags (`FLAGS`:
  `jinja_in_test_arguments`, `test_column_not_declared`, `primary_key_column_not_declared`) and
  `source_sha256`, the SHA-256 of exactly the manifest fields it reads: the node's, down to the
  keys of `config`, `config.meta` and `config.meta.continuo`, its columns in their order, its
  tests' and those of each relationship's target, so another `config` or `meta` key never moves
  it. The contract shares no list with the manifest node. A field of the node or of a test in a
  shape it cannot read, or a field it reads whose value canonical JSON cannot write, raises
  `DbtNodeError`, whose message names the node, then the test.
- `continuo_contracts.dbt.logical_type` gives the ODCS logical type of a declared column type, by
  its name before `(` in lower case (`float4` among the numbers), with `maxLength` for a string
  type that gives a length from 1 to 2**53 - 1 in ASCII digits, read with its leading zeros left
  out, or `None` for a type it does not know, an array type (with `[`), a type with a character
  outside ASCII or a type with text after its closing `)`, but `with time zone` or
  `without time zone` after a `timestamp(...)`, as in `timestamp(6) with time zone`, which is
  `timestamp`. Whitespace is a space or a tab only.
- `continuo_contracts.dbt.read_tree` names the fields `generate` reads, and `source_sha256`
  covers, of a node, a test and a relationship's target, as trees. The README writes down the
  structure `source_sha256` hashes, `{node, tests, targets}`, so another implementation can
  compute it.
- `continuo_contracts.dbt.run_facts` reads back from a generated or stored dbt contract what
  continuo runs the node with, as a `RunFacts`: its id, schema, table, physical name, service,
  kind, schedule (`""` for a dormant node), criticality, timeout (`None` when unset), file path,
  owner and materialization.
- `continuo_contracts.dbt.seed_header_issues(declared, header)` compares the column names a seed's
  contract declares with the names in its CSV header, and gives one message for each column the
  two disagree on: `<column> is in the CSV header but not declared`, in header order, then
  `<column> is declared but not in the CSV header`, in declared order. Names are compared exactly
  as written.
- Test fixtures for dbt nodes: `tests/fixtures/dbt/projects` holds the dbt projects `core`,
  `finance`, `marketing` and `service_1`, copied from continuo-demo, and a `cases` project with a
  local package `cases_pkg` that covers each mapping rule. `scripts/build_dbt_fixtures.py` parses
  each project with dbt 1.12.0b1 (Python 3.12, `TZ=Asia/Tokyo`) and writes
  `tests/fixtures/dbt/manifests/<project>.json.gz`; a rebuild with that pinned dbt writes the same
  bytes, which the test that the `dbt` pytest marker names checks when run by hand.
  `tests/dbt_fixtures.py` loads the manifests.
- `continuo_contracts.contract.normalise_dbt` writes the stored form of a dbt contract: the tables
  the node's compiled SQL names, which the caller passes, joined with its declared `reads` on the
  schema object, in lower case, sorted and listed once; the `primaryKeyPosition` of a key of one
  column dropped; then the same canonical form as a Python contract's. A table that is not
  `<schema>.<table>` raises `HashInputError`, and a value canonical JSON cannot write, which a
  stored contract passed back can hold, raises `CanonicalJsonError`. `contract_hash` hashes the
  result as it hashes a Python contract's.
- `continuo_contracts.validate.validate_dbt_contract` checks a dbt contract, generated or stored,
  against the ODCS schema and then against `dbt_rule_issues`, the rules of a dbt contract: the
  envelope; one schema object whose table has no slash, backslash or empty, `.` or `..` part
  between its dots; an `id` that is a schema part with no whitespace, dot or slash, a dot and the
  table lowered in the id's own string as `unique_id` lowers it; `team.name`; the `continuo`
  properties (a name the vocabulary does not know is `unknown_property`, one that dbt does not
  use, such as `script`, is `refused_property`, one written twice is a `value` issue);
  `service`, `kind`, `filePath` and `materialization` written; `service` and `filePath` text,
  `kind` one of the three dbt kinds, `materialization` a mapping whose `materialized` is text,
  `criticality` one of `REGULATORY`, `CORE` and `SECONDARY`, `schedule` at most 50 characters and
  `timeoutMinutes` a whole number from 1 to 10080. The value issues of a dbt contract sit at the
  `value` of the property, as in `customProperties[3].value`, where a Python contract's sit at
  `customProperties[3]`. A nested column is a `refused_field` issue, whose message names no node
  family. A contract that has no issue is one `dbt.run_facts` reads whole, which a test shows by
  deleting each piece of every golden contract and replacing it with a value of the wrong type,
  and a generated contract with no issue has a stored form with no issue. The checks of a Python
  contract do not change.
- The new vector file `vectors/dbt_contracts.json` pins the generated contract, the flags, the
  `source_sha256`, the stored form and the first `contract_hash` of nine dbt nodes, one for each
  rule of the generator, each with its node, tests and manifest pruned to the fields `generate`
  reads; a tenth case, `bad_criticality`, that pins the `source_sha256` and lists the issues
  `validate_dbt_contract` finds in the generated contract of a model whose criticality is `high`;
  and two cases built from fixture nodes the writer edits, which pin a namespaced generic test,
  `warnIf` and `failCalc`, an extra `accepted_values` kwarg, a `relationships` test that is not
  strict, a key of several columns, `maxLength` and tests and key columns the node does not
  declare. `tests/fixtures/dbt/golden/<project>/<id>.json` holds the generated contract, the
  flags, the `source_sha256`, the stored form and the hash of every kept node of the five fixture
  manifests, and `scripts/write_dbt_goldens.py` writes both, the same bytes on every run, with the
  public `dbt.read_tree` for the fields it keeps. No 0.2.0 vector file changes.
- `fingerprints.yaml` classifies every field of a dbt contract's stored form that
  `contract_hash` hashes (it leaves out the keys of `contract_hash_exclusions`) for `dbt-model`,
  `dbt-seed` and `dbt-snapshot`, in the `odcs.<path>` inputs the Python families use: the
  identity (`odcs.id`, `odcs.name`, `odcs.schema.name`, `odcs.schema.physicalName`), the
  `materialization` and `schema[0].physicalType`, the tags and `schedule`, the owner,
  `criticality` and `timeoutMinutes`, the `roles` of the readers and grants, the description,
  `service`, `kind` and `filePath`, `contractEnforced` (with `config.contract.enforced`, `both` in
  a model, a seed and a snapshot), the declared columns with their types, descriptions, tags and
  classification, a model's primary key, and the rules and relationships the tests give with
  their `severity`, `where` and thresholds. Each is `contract`, or `both` where it also moves
  `content_hash`, as the entry that names it says. A new entry `sql_reads` in each dbt family
  classifies `odcs.schema.custom.reads` as `contract`: the tables the compiled SQL names, with the
  declared reads. The `dbt-test` family classifies no `odcs.` input, since a test's facts land on
  the tested node's contract. The guard test walks the stored form of every golden contract and of
  the vector case that reads tables, and one generated contract of each dbt kind that sets every
  field the generator can write, and fails for a field no entry classifies; `docs/fingerprints.md`
  lists the new entries and inputs. No Python family changes and no hash moves.
- `continuo_contracts.lite`, a new module in a new layer between `cli` and `validate`: the lite
  check of a dbt service. `check_manifest(manifest, *, declared_service, read_header)` takes the
  manifest `dbt parse` writes and returns a `LiteReport` of `Line`s, ordered by node id and the
  same for any order of the manifest. A node continuo skips, a dormant node (a kept model or
  snapshot with no tag) and a kept seed of an installed package, whose CSV header is not checked
  since its CSV file is not in the project, give an `info` line. A node whose fields the library
  cannot read, each issue of `validate_dbt_contract`, a declared read that is not a table, a seed
  whose CSV cannot be read, each column a seed's header and declared columns disagree on, and a
  service whose every node is skipped give an `error` line. `contracts` maps the id of each
  contract that has no issue to its `contract_hash`, with no tables from compiled SQL; a contract
  with an issue has none. `BANNER` is the text that says passing the check does not guarantee
  that continuo will create the service's contracts at release time, and `RELEASE_ONLY` lists the
  six things only the release does. The module reads no file and prints and logs nothing.
- `continuo-contracts dbt check PROJECT_DIR [--dbt PATH] [--profiles-dir DIR] [--target NAME]
  [--manifest FILE] [--service NAME] [--json]`, the producer's lite check of a dbt project. It
  runs `<dbt> --write-json --log-path <temporary folder> parse --project-dir PROJECT_DIR
  --profiles-dir <DIR> --target-path <temporary folder>`, with `dbt` on `PATH` or the executable `--dbt` names, as a new process with no shell,
  and reads the `manifest.json` that dbt writes, so the command imports no dbt and runs on a
  Python of its own; a folder or a target with spaces or shell metacharacters reaches dbt as
  written. It gives the placeholder `continuo-lite-check` to each variable that `profiles.yml`
  reads with `env_var('NAME')`, with no default and a plain identifier as its name, and that the
  environment does not set, and changes no other variable. It reads the header of each seed of
  the project from the first row of the seed's CSV file in the project (UTF-8 with or without a
  byte order mark, the delimiter of the seed's `config`; a file it cannot read, a delimiter `csv`
  refuses or a path with a NUL is an error line), checks the manifest with `lite.check_manifest`,
  and logs the banner, each line of the report, the six things only the release does and the
  banner again, at INFO or, for an error, at ERROR. With `--json`, stdout is one document,
  `{"lite", "ok", "service", "lines", "contracts"}`. `--manifest` reads a manifest that already
  exists and runs no dbt. The exit status is 0 when the check finds no error, 1 for an error or a
  `dbt parse` that fails (the last 20 lines of its output, without its colour codes, are logged at
  ERROR), and 2 when the command cannot run: no such project folder, no `dbt` executable, or a
  manifest that cannot be read or whose `metadata` or tests are not the shape of a dbt manifest.
  `--service` is the service of the project when the manifest names none, by default the folder
  name with `_` written `-`.
- The import-linter contract `subprocess-is-confined-to-cli`: no module but `cli` imports
  `subprocess`. `tests/test_architecture.py` holds it to the code, and checks that a planted import
  breaks it. The `validate` and `hash` commands do not change.

### Changed

- `continuo_contracts.vocabulary`: `ContinuoProperty` has a fourth required field, `dbt`, that says
  what a dbt contract does with the property: `written` (the generator writes it), `filled`
  (continuo fills it, as it does `reads`) or `not_used`. A caller that builds a
  `ContinuoProperty` by hand passes the new `dbt` argument. A dbt contract may carry the fourteen
  root properties `service`, `kind`, `schedule`, `criticality`, `timeoutMinutes`, `filePath`,
  `materialization`, `contractEnforced`, `access`, `group`, `deprecationDate`,
  `freshnessBuildAfter`, `snapshot` and `quoteColumns`, the schema property `reads` and the four
  quality properties; nine properties are `not_used`. The `python` answers do not change.
- `fingerprints.yaml`: `config.contract.enforced` of a seed and a snapshot moves from `content`
  (through `other_config`) to an explicit `both` entry, because the contract carries it.

### Fixed

- `scripts/build_dbt_fixtures.py` drops every `POSTGRES_` variable of the caller's environment, as
  it drops every `DBT_` one, so a `POSTGRES_DB` set on the machine no longer changes the database
  names in a rebuilt fixture manifest.
- `continuo-contracts dbt check` runs `dbt --write-json --log-path <temporary folder> parse ...`:
  a `DBT_WRITE_JSON=false` in the environment, or `flags: write_json: false` in the project or
  profile, no longer leaves the check without a manifest (it was a usage error, exit 2, for a
  valid project), and dbt writes its log in the temporary folder, so it no longer writes a `logs`
  folder in the project and a project folder that cannot be written no longer fails the check.

## [0.2.0] - 2026-11-15

**TL;DR**

- Python ODCS contracts: continuo-contracts reads a Python node's ODCS v3.2.0 contract strictly
  (`yaml_strict`), checks it against the vendored ODCS schema and continuo's rules (`validate`,
  every problem an `Issue` with a closed reason code), and turns it into the `PythonNode` the
  0.1.0 code projection takes (`python.node_from_odcs`).
- The stored form and `contract_hash`: `contract.normalise_python()` writes one canonical form
  for files that mean the same thing, and `contract.contract_hash()` hashes it. The new vector
  files `vectors/contracts.json` and `vectors/strict_yaml.json` pin both.
- `continuo-contracts validate` and `continuo-contracts hash`, for producer CI and people.
- `jsonschema>=4.26` is a new runtime dependency, and the library requires Python 3.14.
- No hash of 0.1.0 moves: the four 0.1.0 vector files are byte for byte the same.

### Added

- `continuo_contracts.schema`: the ODCS v3.2.0 JSON schema ships in the wheel, and `load()` returns
  it after it checks that the file has the pinned SHA-256 (`SchemaFileError` otherwise).
- `continuo_contracts.yaml_strict`: `load()` reads a contract file as YAML 1.2 core, so `on`,
  `yes`, `NO`, `2026-11-14`, `1:20` and `1_000` are strings. It refuses aliases, merge keys,
  repeated keys, keys that are not strings, tags other than the core ones, more than one
  document, nesting deeper than `canonical.MAX_DEPTH` and values that `canonical_json` cannot
  write, with a `StrictYamlError` that names the line and the column. The new vector file
  `vectors/strict_yaml.json` pins it.
- `continuo_contracts.vocabulary`: `PROPERTIES` lists the custom properties of the `continuo`
  vendor with the object of a contract each sits on (`root`, `schema` or `quality`) and who may
  write it in a Python contract (`authored`, `filled` or `refused`). `continuo_values()` returns
  the values of the `continuo` items of a `customProperties` list (a name written twice keeps its
  last value), `VENDOR` is `continuo`, and `ContinuoProperty` is the row type.
- `continuo_contracts.validate`: `validate_python_contract()` checks the ODCS contract of a Python
  node and returns every problem as an `Issue` (a path, a reason from the closed `REASONS` and a
  message) in path order. `schema_issues()` runs the JSON schema and `rule_issues()` runs
  continuo's rules alone on a document the schema accepted, so a caller can cache the first.
  `schema_issues()` first scans the columns of every schema object and, when one nests `items`, a
  `map` or `properties`, returns the `refused_field` issues the rules give for them without
  running the JSON schema, whose check of a nested column takes exponential time.
- The rules of `continuo_contracts.validate` check the envelope, the identity (the id, `name` and
  `physicalName` agree with the table, the id is in lower case, and the file is named
  `<table>.odcs.yaml` or `<schema>.<table>.odcs.yaml`), `team.name`, `schedule` and
  `criticality`, and the `service` property, a string with text that names the service of the
  release when one is given. They refuse `servers`, `tenant`, `contractCreatedTs`, and a
  `continuo` property that is unknown, filled by continuo, not carried by a Python contract, on
  the wrong object or written twice.
- A name in a Python contract is not blank and has no whitespace at either end and no control
  character: the name of the table and the schema part of the id (`identity`, which also refuses
  whitespace, a dot or a slash anywhere in them), the name of a column and of a read query, and
  the `schedule` (`value`, which also refuses whitespace anywhere in it and a name longer than 50
  characters). `script` and each entry of `assets` are normalised POSIX paths inside the project
  (`value`): no empty, `.` or `..` part, no `\`, and no `/`, `~` or drive letter at the start, so
  `./a.json`, `a//b`, `a\..\etc` and `C:/x` are refused.
- The id and the table are compared with their ASCII letters in lower case and every other
  character as written, so a Python contract is accepted or refused alike whatever Unicode tables
  the Python that runs it has, which lower-case different letters.
- The rules also check what depends on the kind of the node (`kind_rule`): each property has a
  value of its kind (`timeoutMinutes` is 1 to 10080, `secretRef` is a `continuo-api-` Secret name
  of at most 253 characters, `csvSource` is `s3://<bucket>/<key>` or `https://<host>`,
  `readQueries` maps names to queries with text, and `assets` lists each path once), and a
  python-node, python-csv or python-api writes the properties its kind needs and none that it
  refuses.
- The rules check the columns and the key. A column has a name used once and a `physicalType` that
  `canonical_sql_type` accepts (`column_type`, with a `VARCHAR(n)` or `CHAR(n)` of length 1 to
  2**53 - 1), and no `physicalName` other than its name, partition, partition key position,
  nested `properties`, `items` or `map`, or `logicalTypeOptions` (`refused_field`). The key of an
  `upsert` node (`primary_key`) exists, each key column is `required` and not `DOUBLE PRECISION`,
  and each column of a key of several has its own whole-number `primaryKeyPosition` from 1. A
  custom quality rule has a non-empty `implementation`.
- `continuo_contracts.python.node_from_odcs()` returns the `PythonNode` that a Python node's ODCS
  contract declares, so its `code_projection()` and `projection_hash()` are those of the same node
  written as a wire entry.
- `continuo_contracts.contract`: `normalise_python()` builds the stored form of a Python contract
  (`version: "0"`, `status: active`, the table's names, the `kind` and `service` properties, the
  `reads` tables in lower case, sorted and listed once, and per column the `physicalType` in the
  spelling `canonical_sql_type()` writes and a `logicalType` from `logical_type()`), written in
  the canonical form: description text stripped, quality severity lower-cased, empty values, ODCS
  defaults, the `primaryKeyPosition` of a key of one column and continuo's default `modeType` and
  `extraColumns` dropped, whole-number floats stored as integers and the set-like lists sorted,
  with free-form values kept as written. Two files that mean the same thing, whatever their line
  endings, byte order mark, key order, quoting or spelling of a column type, store the same form.
  It raises `HashInputError` for a `service` that is empty or blank and for a `reads` entry that
  is not exactly `<schema>.<table>`: two parts joined by a dot, neither empty, with no whitespace
  and no control character. `contract_hash()` hashes the stored form without `apiVersion`, `kind`,
  `status` and `version`. `logical_type()` gives `None` for a type `canonical_sql_type()` refuses
  and for a `VARCHAR(n)` or `CHAR(n)` whose length is not from 1 to 2**53 - 1. The new vector file
  `vectors/contracts.json` pins the stored form and the `contract_hash` of eight contracts, two of
  them messy spellings of another that store alike, and the issues of nineteen refused ones,
  among them one for each name and path rule.
- `fingerprints.yaml` classifies every field of a Python contract's stored form for `python-node`,
  `python-csv` and `python-api`, in the new input pattern `odcs.<path>` (`odcs.team.name`,
  `odcs.schema.properties.logicalType`, `odcs.custom.timeoutMinutes`, `odcs.schema.custom.reads`),
  and the new rule `standard_field` classifies every standard ODCS field no entry names as
  `contract`; a `continuo` custom property that no entry names stays unclassified. The list
  `lists.contract_hash_exclusions` holds the root keys that `contract_hash` leaves out
  (`apiVersion`, `kind`, `status`, `version`), and `fingerprints.contract_hash_exclusions()` and
  `Fingerprints.contract_hash_exclusions` return it. The guard test walks the stored form of the
  demo, full and messy contracts and fails for a field no entry classifies.
- `continuo_contracts.cli` and the console script `continuo-contracts`. `validate PATH...` checks
  `*.odcs.yaml` files (a folder is read for them, without the folders below it) and logs every
  issue at ERROR as `<file>: <path>: <message>`. `hash FILE --service SERVICE [--read TABLE]...`
  checks one file and prints its `contract_hash`. With `--json`, stdout is one JSON document
  (`validate`: `valid` and the `issues` of each file, `hash`: `contract_hash` and the `stored`
  form), and the log goes to stderr in either mode. Exit status 0 is valid, 1 is an issue and 2 is
  a usage error (a `--service` that is empty or blank among them), a path that names no contract
  file, a path that cannot be looked up (one below a folder the process may not search), a folder
  or a file that cannot be read or a `--read` value that is not `<schema>.<table>`.
- `errors.SchemaFileError` and `errors.StrictYamlError`, each a `ContractsError`.

### Changed

- The library requires Python 3.14 or later (`requires-python` is `>=3.14`). Python 3.12 and 3.13
  are no longer supported.
- `jsonschema>=4.26` is a runtime dependency, next to PyYAML. The wheel stays pure Python.
- `parse_fingerprints()` refuses a file without `lists.contract_hash_exclusions`, and a `rule`
  entry is either `outside_denylist` (a dbt family) or `standard_field` (a Python family).
- The README says in Stability that a newer version accepts every contract an older one accepted,
  with the same stored form and `contract_hash`, and in Pinning that topology-controller refuses a
  producer bundle written by a newer version than the one it runs.

## [0.1.0] - 2026-11-11

**TL;DR**

- First release. continuo-contracts holds the code continuo and its producers share to fingerprint nodes.
- `canonical_json()` writes RFC 8785 canonical JSON (nesting deeper than 100 levels is refused); `normalise_text()` turns CRLF and CR into LF and strips the ends.
- `fold3()` and `fold4()` build `content_hash`; `shared_fold()` and `shared_hash()` fold each file's path or unit id with its digest, so two files that swap contents change the hash.
- `PythonNode`, `code_projection()` and `projection_hash()` give a Python node's config part; `canonical_sql_type()` spells `INT` as `INTEGER`.
- `fingerprints.yaml` says which input moves which fingerprint; `docs/fingerprints.md` shows it.
- Four vector files ship in the wheel, so every consumer can check that it computes the same bytes.
- Python 3.12 and later. Apache-2.0.

### Added

- `continuo_contracts.canonical`: `canonical_json()` writes RFC 8785 canonical JSON and names the
  JSON path of any value it refuses; `normalise_text()` turns CRLF and CR into LF and strips ASCII
  whitespace from both ends.
- `continuo_contracts.canonical.MAX_DEPTH` (100) limits nesting: `canonical_json()` and
  `projection_hash()` refuse a container on a deeper level with its JSON path, and no depth
  raises a bare `RecursionError`.
- `continuo_contracts.hashing`: `fold3()` and `fold4()` build `content_hash`; `shared_fold()` and
  `shared_hash()` fold each unit's id or path with its digest, so two files that swap contents
  change the hash; `sha256_hex()` returns the lowercase hex SHA-256 of bytes.
- `continuo_contracts.errors`: `CanonicalJsonError`, `HashInputError`, `ProjectionError` and
  `FingerprintsFileError` are each a `ContractsError`, which is a `ValueError`. `classify()` and
  `unclassified()` raise a plain `ValueError` for a family that is not one of the seven.
- `continuo_contracts.fingerprints`: reads and checks `fingerprints.yaml`; `classify()` and
  `unclassified()` say which entry classifies an input of a node family;
  `hashed_config_denylist()` and `projection_keys()` hand the hashing code its lists.
- `continuo_contracts.python`: `PythonNode`, `code_projection()` and `projection_hash()` give a
  Python node's config part with a fixed shape; `canonical_sql_type()` spells a type as the runtime
  does (`INT` is `INTEGER`). `vectors/projection.json` and `vectors/sql_types.json` pin both.
- `canonical_sql_type()` strips exactly 29 code points from both ends of a type, listed in the
  README and the docstring, and `vectors/sql_types.json` pins U+0085, U+2028, U+001F and the
  refusal of U+200B around a type.
- `fingerprints.yaml` classifies every input of `content_hash` for each node family, and the dbt
  `config` keys `hashed_config` leaves out; `docs/fingerprints.md` shows it.
- `parse_fingerprints()` refuses a key written twice in a mapping, in an entry, under `lists`,
  under `families` or at the top level, with a `FingerprintsFileError` that says `duplicate key`
  and names the key and its line, instead of keeping the last.
- `continuo_contracts.fingerprints.FIXED_INPUT_NAMES` lists the input names written out in full;
  a guard test ties it to the input names that `docs/fingerprints.md` shows.
- `vectors/canonical_json.json` and `vectors/hashing.json` pin `canonical_json()` (with its
  nesting limit), `fold3()`, `fold4()`, `shared_fold()` and `shared_hash()` for a consumer in any
  language, and two new cases of `vectors/projection.json` pin the UTF-16 order of `config` keys
  and the code point order of assets. The README lists the files under Vectors.
- The README ends the section of each module with its public names, one line each, and has an
  `errors` section for the error classes; a test fails for a public name that has no line.
- The README lists what `code_projection` refuses, one rule for each line, so a producer in
  another language can refuse the same inputs.
- A lone surrogate in the schema or the table of a node shows as its backslash escape in the
  `<schema>.<table>: ` prefix of a `ProjectionError`, so the message can be written to a UTF-8
  stream; every other character of the prefix stays as it is.
