# Fingerprint field reference

This file is generated from `continuo_contracts/fingerprints.yaml` by `scripts/render_fingerprints.py` and is not edited by hand.

The *In* column names the fingerprint an input moves. `content` moves `content_hash`, the fingerprint of a node's code, and the parts in brackets are the pieces of `content_hash` that the input is folded into: `source` is the node's own code or data, `shared` is code the node uses that other nodes share, `config` is the hashed configuration, `compiled` is the compiled SQL, and `identity` means the value names the node, so a change makes a new node with fresh hashes. `contract` moves `contract_hash`, the fingerprint of a node's contract. `both` moves both. `neither` moves neither. The *Part, or why neither* column names the part for a `content` or `both` row, names the contract fields the input fills for a `contract` row, and gives the reason the input moves nothing for a `neither` row. A clause after a semicolon names what the input also moves while its condition holds. In a dbt test row, `test node:` names the part of the test node's own `content_hash`, and `tested node` says the field also moves the `contract_hash` of the node under test.

The names in the *Inputs* column are these:

- dbt source part: `raw_code` (model, snapshot), `compiled_code` (model and snapshot: the compiled part; test: the source part), `seed_bytes` (the digest the compile extras carry for a seed), `seed_checksum` (dbt's checksum of a seed, used when a stored artifact has no compile extras).
- dbt relation: `relation.schema`, `relation.alias`.
- dbt config: `config.<key>` for a key of the hashed config, which `config.*` covers for every key outside the denylist, and `config.<key>` for each denylisted key. `config.contract.enforced` is the `enforced` key of the dbt contract settings.
- dbt enforced contract: `contract.columns`, `constraints`.
- dbt shared units: `macros`, `functions`, `unit_tests`, `on_run_start`, `on_run_end`, `project_flags`, `generate_schema_name`, `generate_alias_name`.
- Python: `projection.<key>` for each key of the code projection, `ci.source_hash` (the digest of the node's script bytes, computed by the producer's CI, and for a CSV node the digest of its URI), `ci.shared_code_hash` (the shared part for the in-repo files the script imports or lists as assets, folded path by path by `shared_hash`).
- Python contract: `odcs.<path>` for a field of the contract's stored form, written with its ODCS names as they are in the file and without array indexes, such as `odcs.team.name` or `odcs.schema.properties.logicalType`. A continuo custom property is `odcs.custom.<property>` on the contract (`odcs.custom.timeoutMinutes`) and `odcs.schema.custom.<property>` on the schema object (`odcs.schema.custom.reads`). `odcs.*` covers every standard ODCS field that no entry names, which a Python family classifies as contract, and no continuo custom property.

## Terms

- *re-baseline*: a continuo release of each service, run once after a change of the hashing code, that recomputes every node's stored hash with the new code and validates and reloads nothing.
- *compile extras*: the files continuo's compile step writes beside dbt's manifest: seed bytes, seed headers and column types, and similar inputs a stored manifest lacks.
- *code bundle*: the per-release file in which continuo stores each node's code.

## Lists the hashing code applies

- `hashed_config_denylist`: the dbt `config` keys that stay out of the hashed config: `access`, `description`, `docs`, `freshness`, `grants`, `group`, `meta`, `tags`.
- `projection_keys`: the keys of the Python code projection, in projection order: `schema`, `table`, `kind`, `script`, `reads`, `output_columns`, `extra_columns`, `config`, `secret_ref`, `mode_type`, `primary_key`, `assets`.
- `contract_hash_exclusions`: the root keys of the stored form that `contract_hash` leaves out: `apiVersion`, `kind`, `status`, `version`.

## Every family

| Field or input | Source | In | Part, or why neither | Inputs |
|---|---|---|---|---|
| Upstream edges, `upstream_unique_ids` | sqlglot over compiled SQL and Python read queries | neither | the topology artifact carries them as the graph's edges; DAG reads stay out of the contract | |
| Image tag, manifest metadata; library, runtime and Python versions | the release, the manifest, compile extras, the producer bundle | neither | provenance | |
| Install settings: hosts, database names, the engine | the chart | neither | never stored; a node's own database name is masked in compiled SQL | |

## dbt model

| Field or input | Source | In | Part, or why neither | Inputs |
|---|---|---|---|---|
| Schema | node `schema`, after `generate_schema_name` | both (config) | content: the resolved relation in `hashed_config`; contract: `id`. A new schema makes a new node | `relation.schema` |
| Name; a version's `<name>.v<version>` | node `name` and `version` | both (identity) | content: it keys the node, so a new name makes a new node with fresh hashes; contract: `id`, `name`, `schema[0].name` | |
| Resolved alias | node `alias`, after `generate_alias_name` | both (config) | content: the resolved relation in `hashed_config`; contract: `physicalName` | `relation.alias` |
| SQL text | `raw_code` | content (source) | source part: the SQL text with LF line endings and no outer ASCII whitespace, nothing else changed; code stays out of the contract | `raw_code` |
| What compiled SQL adds: `var()`, `env_var()`, `target.*`, `source()` definitions, upstream names through `ref()`, an inlined ephemeral upstream, `meta` read by SQL | `compiled_code` | content (compiled) | compiled part, masked; out of the contract | `compiled_code` |
| Run-specific values in compiled SQL: invocation id, run start, the node's database name | the manifest | neither | masked to fixed placeholders, so a run with no edit moves nothing | |
| Macros the team or its packages write in the closure, root overrides, macros called only inside `is_incremental()` | the manifest's `macros` | content (shared) | shared part, each folded with its id | `macros` |
| Built-in macros of dbt-core, dbt-adapters, dbt-common and the adapter | the manifest's `macros` | neither | left out of the closure; their versions are provenance, and pinned | |
| Bodies of the dbt functions the model calls | the manifest | content (shared) | shared part | `functions` |
| Unit tests | the manifest's `unit_tests` | content (shared) | shared part, by dbt's own checksum, and counted in `test_count`; code, not quality rules | `unit_tests` |
| Service units: `on-run-start`, `on-run-end`, the `flags:` block, `generate_schema_name`, `generate_alias_name` | the manifest, compile extras | content (shared) | shared part | `on_run_start`, `on_run_end`, `project_flags`, `generate_schema_name`, `generate_alias_name` |
| `materialized`, `unique_key`, `incremental_strategy`, `on_schema_change` | `config` | both (config) | content: `hashed_config`; contract: `materialization`, and `schema[0].physicalType` from `materialized` | `config.materialized`, `config.unique_key`, `config.incremental_strategy`, `config.on_schema_change` |
| `contract.enforced` | `config` | both (config) | content: `hashed_config`; contract: `contractEnforced` | `config.contract.enforced` |
| Every other config key outside the denylist: `persist_docs`, `sql_header`, `full_refresh`, `pre_hook`, `post_hook`, the rest of `contract`, adapter and microbatch keys | `config` | content (config) | `hashed_config`, which keeps every key outside its denylist; not a contract fact | `config.*` |
| Config keys with null values | `config` | neither | dropped: a dbt upgrade adds them with no meaning | |
| `tags` | node `tags` | contract | `tags`, and the first as `schedule`; outside `hashed_config`. A tag set in the SQL file's `config()` line moves the source part anyway | `config.tags` |
| `meta.owner`, `meta.criticality`, `meta.timeout_minutes` | `config.meta` | contract; both (compiled) while SQL reads it | contract: `team.name`, `criticality`, `timeoutMinutes`; `meta` is outside `hashed_config`. Content: the compiled part, while SQL reads the value | |
| `meta.continuo.readers`, `.reads`, `.tests`, `.classification` | `config.meta`, column `meta` | contract; both (compiled) while SQL reads it | contract: `roles`, `reads`, table tests as quality rules, `classification`, carried whether or not the install uses an ontology. Content: the compiled part, while SQL reads the value | |
| `meta.continuo` bindings: `entity`, `grain`, `fixed`, `filter`, a column's `property`, `local` and aggregate keys | `config.meta`, column `meta` | contract; both (compiled) while SQL reads it | contract: the ontology part, written only when the install uses an ontology. Content: the compiled part, while SQL reads the value | |
| Every other `meta` key | `config.meta` | neither; content (compiled) while SQL reads it | not in `hashed_config`, since `meta` is outside it, and not a contract fact, since no field holds it and the continuo vocabulary is closed. Content: the compiled part, while SQL reads the value | `config.meta` |
| `docs` | `config.docs` | neither | whether dbt's documentation site shows the node, and in which colour (`show`, `node_color`): not code, and no contract field | `config.docs` |
| `description` as a `config` key, if a project sets one | `config` | neither | dbt declares no such key; the denylist keeps it out of `hashed_config`, and the contract's description comes from the node's `description` | `config.description` |
| `access`, `group` | `config` | contract | `access`, left out when it is dbt's default, `protected`, and `group`; outside `hashed_config` | `config.access`, `config.group` |
| `freshness.build_after` | `config` | contract | `freshnessBuildAfter`; outside `hashed_config` | `config.freshness` |
| `grants` | `config` | contract | `roles`; outside `hashed_config` | `config.grants` |
| Declared columns: name and order | `schema.yml` | contract; both (config) under an enforced dbt contract | contract: `properties` in declared order; content: `hashed_config`, only when enforced | `contract.columns` |
| Column `data_type` | `schema.yml` | contract; both (config) under an enforced dbt contract | contract: `physicalType`, with the `logicalType` derived from it; content: `hashed_config`, only when enforced | |
| Column and model constraints | `schema.yml`, model `constraints` | neither; content (config) under an enforced dbt contract | `hashed_config`. A primary key also reaches the contract through the manifest's `primary_key`; no other constraint does, since `required` and `unique` come from tests | `constraints` |
| Column `quote` | `schema.yml` | neither; content (config) under an enforced dbt contract | `hashed_config`; it only decides how dbt quotes the name in DDL, so it has no contract field | |

## dbt seed

| Field or input | Source | In | Part, or why neither | Inputs |
|---|---|---|---|---|
| Schema, name, resolved alias | node `schema`, `name`, `alias` | both (config, identity) | as for a model | `relation.schema`, `relation.alias` |
| CSV bytes, any size, outer whitespace included | compile extras' `sha256` | content (source) | source part. A stored artifact with no compile extras keeps dbt's checksum as the source part (the file path for a seed over 1 MiB) | `seed_bytes`, `seed_checksum` |
| CSV header | the CSV, and compile extras' `header` | content (source) | through the bytes; the contract holds the declared columns instead, which the producer's CI check keeps equal to the header | |
| `seed_columns_hash`, `seed_columns` | compile extras' `header` and `column_types` | neither | topology fields that tell a column change from a row change; their inputs are already in `content_hash`, through the bytes and `hashed_config` | |
| Compiled part | none | neither | a seed has no compiled SQL; the part is `""` | |
| Seed materialization and loader macros the team overrides; package macros in the closure | the manifest's `macros` | content (shared) | shared part: closure roots and root overrides | `macros` |
| Built-in macros | the manifest's `macros` | neither | as for a model | |
| Service units | the manifest, compile extras | content (shared) | shared part, as for a model | `on_run_start`, `on_run_end`, `project_flags`, `generate_schema_name`, `generate_alias_name` |
| `materialized: seed` | `config` | both (config) | content: `hashed_config`; contract: `materialization`, and `schema[0].physicalType`, always `table` for a seed | `config.materialized` |
| `column_types` | `config` | both (config) | content: `hashed_config`; contract: `physicalType`, where it wins over `data_type`, with the `logicalType` derived from it | `config.column_types` |
| `quote_columns` | `config` | both (config) | content: `hashed_config`; contract: `quoteColumns` | `config.quote_columns` |
| `delimiter`, `full_refresh` and every other config key outside the denylist | `config` | content (config) | `hashed_config`; `full_refresh` also rides on the topology node | `config.*` |
| Config keys with null values | `config` | neither | dropped | |
| `tags` | node `tags` | contract | `tags`, and the first as `schedule`, which is `seed` when there is none | `config.tags` |
| `meta.owner`, `meta.criticality`, `meta.timeout_minutes`; `meta.continuo` keys and bindings | `config.meta`, column `meta` | contract | as for a model, except that a seed has no SQL to read a `meta` value, so none of them reaches `content_hash` | |
| Every other `meta` key, `docs` | `config` | neither | as for a model; a seed has no SQL to read a `meta` value | `config.meta`, `config.docs` |
| `grants` | `config` | contract | `roles` | `config.grants` |
| `group` | `config` | contract | `group`, as on a model; outside `hashed_config`. dbt declares `group` on every node's config | `config.group` |
| `access`, `freshness`, or `description` as a `config` key, if a project sets one on a seed | `config` | neither | dbt declares none of them on a seed's config (`access` and `freshness` are a model's); the denylist keeps them out of `hashed_config`, and the contract generator maps `access` and `freshness` for a model only | `config.access`, `config.freshness`, `config.description` |

## dbt snapshot

| Field or input | Source | In | Part, or why neither | Inputs |
|---|---|---|---|---|
| Schema, name, resolved alias | node `schema`, `name`, `alias` | both (config, identity) | as for a model | `relation.schema`, `relation.alias` |
| SQL text | the snapshot's own `raw_code` | content (source) | source part: the SQL text with LF line endings and no outer ASCII whitespace, nothing else changed; another snapshot block in the same file does not move it | `raw_code` |
| Compiled SQL | `compiled_code` | content (compiled) | compiled part, masked | `compiled_code` |
| Run-specific values in compiled SQL | the manifest | neither | masked | |
| The snapshot materialization, `snapshot_<strategy>_strategy`, the hash macros the team writes, root overrides; package macros in the closure | the manifest's `macros` | content (shared) | shared part | `macros` |
| Built-in macros | the manifest's `macros` | neither | as for a model | |
| Bodies of the dbt functions the snapshot calls | the manifest | content (shared) | shared part | `functions` |
| Service units | the manifest, compile extras | content (shared) | shared part | `on_run_start`, `on_run_end`, `project_flags`, `generate_schema_name`, `generate_alias_name` |
| `strategy`, `unique_key`, `updated_at`, `check_cols`, `hard_deletes`, `snapshot_meta_column_names`, `dbt_valid_to_current` | snapshot `config` | both (config) | content: `hashed_config`; contract: `snapshot` | `config.strategy`, `config.unique_key`, `config.updated_at`, `config.check_cols`, `config.hard_deletes`, `config.snapshot_meta_column_names`, `config.dbt_valid_to_current` |
| The columns dbt adds to the history table: `dbt_scd_id`, `dbt_updated_at`, `dbt_valid_from` and `dbt_valid_to`, and `dbt_is_deleted` when `hard_deletes` is `new_record`, under the names `snapshot_meta_column_names` sets | derived from the snapshot `config` | both (config) | contract: `properties`, added by the contract generator; content: through the snapshot config in `hashed_config` | |
| `materialized: snapshot` | `config` | both (config) | content: `hashed_config`; contract: `materialization`, and `schema[0].physicalType`, always `table` for a snapshot | `config.materialized` |
| Every other config key outside the denylist | `config` | content (config) | `hashed_config` | `config.*` |
| Config keys with null values | `config` | neither | dropped | |
| `tags`, `meta.owner`, `meta.criticality`, `meta.timeout_minutes`, `meta.continuo` keys and bindings | the node, `config.meta`, column `meta` | contract; both (compiled) while SQL reads it | as for a model | `config.tags` |
| Every other `meta` key, `docs` | `config` | neither; content (compiled) while SQL reads it | as for a model | `config.meta`, `config.docs` |
| `grants` | `config` | contract | `roles` | `config.grants` |
| `group` | `config` | contract | `group`, as on a model; outside `hashed_config`. dbt declares `group` on every node's config | `config.group` |
| `access`, `freshness`, or `description` as a `config` key, if a project sets one on a snapshot | `config` | neither | as for a seed | `config.access`, `config.freshness`, `config.description` |

## dbt test

| Field or input | Source | In | Part, or why neither | Inputs |
|---|---|---|---|---|
| Test id | dbt's test node id | content (test node: identity) | it keys the test node and its entry in the code bundle's `tests` map | |
| Name, namespace, `kwargs`, `column_name`, `attached_node` | the test node | content (test node: source), contract (tested node) | content: through the compiled SQL, the test's source part; contract: the rule's `id` and arguments, and the property it sits on | |
| Compiled SQL, generic or singular | `compiled_code` | content (test node: source) | source part, masked; the compiled part is `""`, since the source part already is the compiled SQL. Kept in the code bundle's `tests` map | `compiled_code` |
| The generic test's macro the team or a package writes; `get_test_sql` and `should_store_failures` overrides | the manifest's `macros` | content (test node: shared) | shared part | `macros` |
| Built-in generic test macros (`test_not_null` and its closure) | the manifest's `macros` | neither | left out of the closure, so a dbt upgrade does not move a test's hash | |
| Bodies of the dbt functions the test calls | the manifest | content (test node: shared) | shared part | `functions` |
| `severity` | `config` | content (test node: config), contract (tested node) | content: `hashed_config`, lower-cased; contract: the rule's `severity`, lower-cased, `warn` as `warning` | `config.severity` |
| `where` | `config`, rendered | content (test node: config), contract (tested node) | masked as compiled SQL is, on both sides; contract: the rule's `where` | `config.where` |
| `warn_if`, `error_if`, `fail_calc` | `config` | content (test node: config), contract (tested node) | content: `hashed_config`; contract: `warnIf`, `errorIf`, `failCalc` when not dbt's defaults | `config.warn_if`, `config.error_if`, `config.fail_calc` |
| `limit`, `store_failures` | `config` | content (test node: config) | `hashed_config`; code only | `config.limit`, `config.store_failures` |
| The schema and alias a test writes its failures to | the test node | content (test node: config) | the resolved relation in `hashed_config`; not a contract fact | `relation.schema`, `relation.alias` |
| Every other config key outside the denylist | `config` | content (test node: config) | `hashed_config` | `config.*` |
| Description, tags | the test's YAML | contract (tested node) | the rule's `description` and `tags`; outside `hashed_config` | `config.tags` |
| `meta` | `config.meta` | neither; content (test node: source) while SQL reads it | outside `hashed_config`, and no field of a quality rule holds it. A value the test's SQL reads moves its source part, which is the compiled SQL | `config.meta` |
| `group` | `config` | neither | outside `hashed_config`, and no field of a quality rule holds it. dbt declares `group` on a test's config too | `config.group` |
| Config keys with null values: `alias`, `database`, `group`, `static_analysis`, `store_failures`, `store_failures_as`, `sql_header`, `where` and `limit`, while a project leaves them unset | `config` | neither | dropped; dbt writes each as null until it is set | |
| `docs`, `grants`, `access`, `freshness`, or `description` as a `config` key, if a project sets one on a test | `config` | neither | dbt declares none of them on a test's config; the denylist keeps them out of `hashed_config`, and no field of a quality rule holds them | `config.docs`, `config.grants`, `config.access`, `config.freshness`, `config.description` |
| A test of a source, or of a node that continuo's topology-controller skips | the manifest | neither | continuo's topology-controller, which parses the dbt manifest, keeps only the tests of the nodes it tracks | |

## python-node

| Field or input | Source | In | Part, or why neither | Inputs |
|---|---|---|---|---|
| `id` | authored, lower case | both (config) | content: its schema part, the projection's `schema`; contract: `id` | `projection.schema`, `odcs.id` |
| The table, `schema[0].name`, and `name` | authored; `name` defaults to the table | both (config) | content: the projection's `table`; contract: `schema[0].name`, `name` | `projection.table`, `odcs.schema.name`, `odcs.name` |
| `physicalName` | defaulted to the table | contract | stored and hashed; any other value is refused, so it adds nothing beyond the table | `odcs.schema.physicalName` |
| `schema[0].physicalType` | authored, must be `table` | contract | a constant for a Python node | `odcs.schema.physicalType` |
| Script bytes | the script file | content (source) | source part, computed by the producer's CI | `ci.source_hash` |
| `script` | authored path | both (config) | content: the projection's `script`; contract: `script` | `projection.script`, `odcs.custom.script` |
| In-repo import closure | the files the script imports | content (shared) | shared part, each folded with its path, computed by the producer's CI | `ci.shared_code_hash` |
| `assets` | authored list | both (config, shared) | content: the list in the projection, the files' bytes in the shared part; contract: `assets` | `projection.assets`, `odcs.custom.assets` |
| `readQueries` | authored | both (config) | content: the projection's `reads`, each query with LF line endings and no outer ASCII whitespace; contract: `readQueries` | `projection.reads`, `odcs.custom.readQueries` |
| Columns: name and order | `schema[0].properties` | both (config) | content: the projection's `output_columns`; contract: `properties` in file order | `projection.output_columns`, `odcs.schema.properties.name` |
| Column `physicalType` | authored | both (config) | content and contract: the canonical type, so `INT` and `INTEGER` hash alike in both | `odcs.schema.properties.physicalType` |
| Column `required` | authored | both (config) | content: `nullable` in the projection; contract: `required` | `odcs.schema.properties.required` |
| Column `logicalType` and `logicalTypeOptions` | derived from `physicalType` | contract | stored and hashed; an authored `logicalType` is replaced and an authored `logicalTypeOptions` is refused | `odcs.schema.properties.logicalType`, `odcs.schema.properties.logicalTypeOptions` |
| Column `unique` | authored | contract | descriptive: the runtime creates no constraint | `odcs.schema.properties.unique` |
| `primaryKey`, `primaryKeyPosition` | authored | contract; both (config) on an `upsert` node | content: the projection's `primary_key`, `[]` unless `upsert`; contract: the key, descriptive on `replace` and `append`, and a key of one column is stored without its `primaryKeyPosition` | `projection.primary_key`, `odcs.schema.properties.primaryKey`, `odcs.schema.properties.primaryKeyPosition` |
| `kind` | authored, default `python-node` | both (config) | content: the projection's canonical kind; contract: `kind` | `projection.kind`, `odcs.custom.kind` |
| `modeType` | authored, default `replace` | both (config) | content: the projection's `mode_type`; contract: `modeType` | `projection.mode_type`, `odcs.custom.modeType` |
| `extraColumns` | authored, default `raise` | both (config) | content: the projection's `extra_columns`; contract: `extraColumns` | `projection.extra_columns`, `odcs.custom.extraColumns` |
| `config` | authored | both (config) | content: the projection's `config`; contract: `config` | `projection.config`, `odcs.custom.config` |
| `csvSource`, `secretRef` | refused on a `python-node` | neither | the projection's `reads` holds the read queries, and its `secret_ref` is `""` | `projection.secret_ref`, `odcs.custom.csvSource`, `odcs.custom.secretRef` |
| `team.name`, `schedule`, `criticality` | authored | contract | not code, so outside `content_hash` | `odcs.team.name`, `odcs.custom.schedule`, `odcs.custom.criticality` |
| Descriptions, `tags` | authored | contract | not code, so outside `content_hash` | `odcs.description.purpose`, `odcs.schema.description`, `odcs.schema.properties.description`, `odcs.tags`, `odcs.schema.tags`, `odcs.schema.properties.tags` |
| `timeoutMinutes` | authored | contract | not code, so outside `content_hash` | `odcs.custom.timeoutMinutes` |
| `service` | the release, or authored and equal | contract | not code, so outside `content_hash`; the stored form always carries it | `odcs.custom.service` |
| `reads` on the schema object | filled from the tables the read queries name | contract | the tables the read queries name, which topology-controller lists; never authored | `odcs.schema.custom.reads` |
| Quality rules | authored | contract | as written; the `where`, `warnIf`, `errorIf` and `failCalc` of a rule are `continuo` custom properties of the rule | `odcs.schema.quality.metric`, `odcs.schema.quality.mustBe`, `odcs.schema.quality.severity`, `odcs.schema.quality.custom.where`, `odcs.schema.quality.custom.warnIf`, `odcs.schema.quality.custom.errorIf`, `odcs.schema.quality.custom.failCalc`, `odcs.schema.properties.quality.metric`, `odcs.schema.properties.quality.mustBe`, `odcs.schema.properties.quality.severity`, `odcs.schema.properties.quality.custom.where`, `odcs.schema.properties.quality.custom.warnIf`, `odcs.schema.properties.quality.custom.errorIf`, `odcs.schema.properties.quality.custom.failCalc` |
| `relationships` | authored | contract | as written | `odcs.schema.relationships.from`, `odcs.schema.relationships.to`, `odcs.schema.properties.relationships.to` |
| `classification` | authored | contract | as written | `odcs.schema.properties.classification` |
| `roles` | authored | contract | as written | `odcs.roles.role`, `odcs.roles.access`, `odcs.roles.description` |
| `slaProperties`, `support`, `price` | authored | contract | hashed as written | `odcs.slaProperties.property`, `odcs.slaProperties.value`, `odcs.slaProperties.unit`, `odcs.slaProperties.description`, `odcs.support.channel`, `odcs.support.tool`, `odcs.support.url`, `odcs.support.description`, `odcs.price.priceAmount`, `odcs.price.priceCurrency`, `odcs.price.priceUnit` |
| Other vendors' custom properties | authored | contract | hashed as written | `odcs.customProperties`, `odcs.schema.customProperties`, `odcs.schema.properties.customProperties` |
| Every other standard ODCS field the author writes: `description.usage` and `.limitations`, `domain`, `context`, `slaDefaultElement`, `authoritativeDefinitions`, a schema object's or column's `businessName`, `synonyms` or `deprecated`, and a column's `examples`, `enum`, `criticalDataElement` or `transformLogic` | authored | contract | hashed as written; every standard field that no entry names is classified by this rule | `odcs.*` |
| The `continuo` properties that a Python node does not carry: `filePath`, `materialization`, `contractEnforced`, `access`, `group`, `deprecationDate`, `freshnessBuildAfter`, `snapshot`, `quoteColumns`, `provenance` and an authored `reads` | authored | neither | refused: the first nine describe how dbt builds or governs a node and nothing reads them on a Python node, `provenance` belongs to the published file and is never stored, and `reads` is filled from the read queries | `odcs.custom.filePath`, `odcs.custom.materialization`, `odcs.custom.contractEnforced`, `odcs.custom.access`, `odcs.custom.group`, `odcs.custom.deprecationDate`, `odcs.custom.freshnessBuildAfter`, `odcs.custom.snapshot`, `odcs.custom.quoteColumns`, `odcs.custom.provenance`, `odcs.custom.reads` |
| `servers`, `tenant`, `contractCreatedTs` | authored | neither | refused: the install decides where the table lives and which tenant owns it, and a clock value is no part of a contract | `odcs.servers`, `odcs.tenant`, `odcs.contractCreatedTs` |
| A column's nested `properties`, `items` and `map`, `partitioned: true` and a `partitionKeyPosition` other than -1 | authored | neither | refused: the column of a Python node has a name, a type and a nullability, and its table is not partitioned | `odcs.schema.properties.properties`, `odcs.schema.properties.items`, `odcs.schema.properties.map`, `odcs.schema.properties.partitioned`, `odcs.schema.properties.partitionKeyPosition` |
| A column's `physicalName` | authored | neither | left out of the stored form when it equals the column's `name`, and refused when it differs | `odcs.schema.properties.physicalName` |
| `version`, `status` as authored | authored | neither | ignored: the stored form always holds `version: "0"` and `status: active`, and `contract_hash` leaves both out | `odcs.version`, `odcs.status` |
| `apiVersion` and the ODCS `kind` | `v3.2.0` and `DataContract` | neither | format constants, the same in every stored form, which `contract_hash` leaves out | `odcs.apiVersion`, `odcs.kind` |
| The contract file's path and layout: comments, key order, quoting | the `.odcs.yaml` file | neither | the path is provenance, and the canonical form reads values, not text | |

## python-csv

| Field or input | Source | In | Part, or why neither | Inputs |
|---|---|---|---|---|
| `id`, the table and `name` | authored | both (config) | as for a `python-node` | `projection.schema`, `projection.table`, `odcs.id`, `odcs.schema.name`, `odcs.name` |
| `physicalName` | defaulted to the table | contract | as for a `python-node` | `odcs.schema.physicalName` |
| `schema[0].physicalType` | authored, must be `table` | contract | as for a `python-node` | `odcs.schema.physicalType` |
| CSV URI | authored `csvSource` | both (source, config) | content: the source part, `sha256(uri)` computed by the producer's CI, and the projection's `reads.csv`; contract: `csvSource` | `projection.reads`, `ci.source_hash`, `odcs.custom.csvSource` |
| The CSV's content at the URI | the object | neither | data, not a new node version | |
| `script`, `readQueries`, `secretRef`, `assets` | refused on a csv node | neither | the projection holds `script: ""`, `reads: {csv: <uri>}`, `secret_ref: ""` and `assets: []`, and the shared part is empty | `projection.script`, `projection.secret_ref`, `projection.assets`, `ci.shared_code_hash`, `odcs.custom.script`, `odcs.custom.readQueries`, `odcs.custom.secretRef`, `odcs.custom.assets` |
| Columns, each column's `physicalType` and `required` | authored | both (config) | as for a `python-node` | `projection.output_columns`, `odcs.schema.properties.name`, `odcs.schema.properties.physicalType`, `odcs.schema.properties.required` |
| Column `logicalType` and `logicalTypeOptions` | derived from `physicalType` | contract | as for a `python-node` | `odcs.schema.properties.logicalType`, `odcs.schema.properties.logicalTypeOptions` |
| Column `unique` | authored | contract | as for a `python-node` | `odcs.schema.properties.unique` |
| `primaryKey`, `primaryKeyPosition` | authored | contract | descriptive: a csv node is `replace` only, so the projection's `primary_key` is `[]`; a key of one column is stored without its `primaryKeyPosition` | `projection.primary_key`, `odcs.schema.properties.primaryKey`, `odcs.schema.properties.primaryKeyPosition` |
| `kind` | authored, `python-csv` | both (config) | as for a `python-node` | `projection.kind`, `odcs.custom.kind` |
| `modeType` | `replace` only, the default | both (config) | the projection's `mode_type` is always `replace` | `projection.mode_type`, `odcs.custom.modeType` |
| `extraColumns`, `config` | authored | both (config) | as for a `python-node` | `projection.extra_columns`, `projection.config`, `odcs.custom.extraColumns`, `odcs.custom.config` |
| `team.name`, `schedule`, `criticality` | authored | contract | as for a `python-node` | `odcs.team.name`, `odcs.custom.schedule`, `odcs.custom.criticality` |
| Descriptions, `tags` | authored | contract | as for a `python-node` | `odcs.description.purpose`, `odcs.schema.description`, `odcs.schema.properties.description`, `odcs.tags`, `odcs.schema.tags`, `odcs.schema.properties.tags` |
| `timeoutMinutes` | authored | contract | as for a `python-node` | `odcs.custom.timeoutMinutes` |
| `service` | the release, or authored and equal | contract | as for a `python-node` | `odcs.custom.service` |
| `reads` on the schema object | filled from the tables the read queries name | contract | as for a `python-node`; this kind has no read queries, so it lists no table | `odcs.schema.custom.reads` |
| Quality rules | authored | contract | as for a `python-node` | `odcs.schema.quality.metric`, `odcs.schema.quality.mustBe`, `odcs.schema.quality.severity`, `odcs.schema.quality.custom.where`, `odcs.schema.quality.custom.warnIf`, `odcs.schema.quality.custom.errorIf`, `odcs.schema.quality.custom.failCalc`, `odcs.schema.properties.quality.metric`, `odcs.schema.properties.quality.mustBe`, `odcs.schema.properties.quality.severity`, `odcs.schema.properties.quality.custom.where`, `odcs.schema.properties.quality.custom.warnIf`, `odcs.schema.properties.quality.custom.errorIf`, `odcs.schema.properties.quality.custom.failCalc` |
| `relationships` | authored | contract | as for a `python-node` | `odcs.schema.relationships.from`, `odcs.schema.relationships.to`, `odcs.schema.properties.relationships.to` |
| `classification` | authored | contract | as for a `python-node` | `odcs.schema.properties.classification` |
| `roles` | authored | contract | as for a `python-node` | `odcs.roles.role`, `odcs.roles.access`, `odcs.roles.description` |
| `slaProperties`, `support`, `price` | authored | contract | as for a `python-node` | `odcs.slaProperties.property`, `odcs.slaProperties.value`, `odcs.slaProperties.unit`, `odcs.slaProperties.description`, `odcs.support.channel`, `odcs.support.tool`, `odcs.support.url`, `odcs.support.description`, `odcs.price.priceAmount`, `odcs.price.priceCurrency`, `odcs.price.priceUnit` |
| Other vendors' custom properties | authored | contract | as for a `python-node` | `odcs.customProperties`, `odcs.schema.customProperties`, `odcs.schema.properties.customProperties` |
| Every other standard ODCS field the author writes: `description.usage` and `.limitations`, `domain`, `context`, `slaDefaultElement`, `authoritativeDefinitions`, a schema object's or column's `businessName`, `synonyms` or `deprecated`, and a column's `examples`, `enum`, `criticalDataElement` or `transformLogic` | authored | contract | hashed as written; every standard field that no entry names is classified by this rule | `odcs.*` |
| The `continuo` properties that a Python node does not carry: `filePath`, `materialization`, `contractEnforced`, `access`, `group`, `deprecationDate`, `freshnessBuildAfter`, `snapshot`, `quoteColumns`, `provenance` and an authored `reads` | authored | neither | as for a `python-node` | `odcs.custom.filePath`, `odcs.custom.materialization`, `odcs.custom.contractEnforced`, `odcs.custom.access`, `odcs.custom.group`, `odcs.custom.deprecationDate`, `odcs.custom.freshnessBuildAfter`, `odcs.custom.snapshot`, `odcs.custom.quoteColumns`, `odcs.custom.provenance`, `odcs.custom.reads` |
| `servers`, `tenant`, `contractCreatedTs` | authored | neither | as for a `python-node` | `odcs.servers`, `odcs.tenant`, `odcs.contractCreatedTs` |
| A column's nested `properties`, `items` and `map`, `partitioned: true` and a `partitionKeyPosition` other than -1 | authored | neither | as for a `python-node` | `odcs.schema.properties.properties`, `odcs.schema.properties.items`, `odcs.schema.properties.map`, `odcs.schema.properties.partitioned`, `odcs.schema.properties.partitionKeyPosition` |
| A column's `physicalName` | authored | neither | as for a `python-node` | `odcs.schema.properties.physicalName` |
| `version`, `status` as authored | authored | neither | as for a `python-node` | `odcs.version`, `odcs.status` |
| `apiVersion` and the ODCS `kind` | `v3.2.0` and `DataContract` | neither | as for a `python-node` | `odcs.apiVersion`, `odcs.kind` |
| The contract file's path and layout: comments, key order, quoting | the `.odcs.yaml` file | neither | as for a `python-node` | |

## python-api

| Field or input | Source | In | Part, or why neither | Inputs |
|---|---|---|---|---|
| `id`, the table and `name` | authored | both (config) | as for a `python-node` | `projection.schema`, `projection.table`, `odcs.id`, `odcs.schema.name`, `odcs.name` |
| `physicalName` | defaulted to the table | contract | as for a `python-node` | `odcs.schema.physicalName` |
| `schema[0].physicalType` | authored, must be `table` | contract | as for a `python-node` | `odcs.schema.physicalType` |
| Script bytes, the in-repo import closure, the asset files' bytes | the repository | content (source, shared) | source and shared parts, as for a `python-node` | `ci.source_hash`, `ci.shared_code_hash` |
| `script`, `assets` | authored | both (config) | as for a `python-node` | `projection.script`, `projection.assets`, `odcs.custom.script`, `odcs.custom.assets` |
| `readQueries`, `csvSource` | refused on an api node | neither | the projection's `reads` is `{}` | `projection.reads`, `odcs.custom.readQueries`, `odcs.custom.csvSource` |
| `secretRef` | authored, optional | both (config) | content: the projection's `secret_ref`, `""` when none; contract: `secretRef` | `projection.secret_ref`, `odcs.custom.secretRef` |
| The Secret's values | the cluster | neither | install data; continuo carries only the name | |
| What the script fetches at run time | the API | neither | data, not code or a promise | |
| Columns, each column's `physicalType` and `required` | authored | both (config) | as for a `python-node` | `projection.output_columns`, `odcs.schema.properties.name`, `odcs.schema.properties.physicalType`, `odcs.schema.properties.required` |
| Column `logicalType` and `logicalTypeOptions` | derived from `physicalType` | contract | as for a `python-node` | `odcs.schema.properties.logicalType`, `odcs.schema.properties.logicalTypeOptions` |
| Column `unique` | authored | contract | as for a `python-node` | `odcs.schema.properties.unique` |
| `primaryKey`, `primaryKeyPosition` | authored | contract; both (config) on an `upsert` node | as for a `python-node` | `projection.primary_key`, `odcs.schema.properties.primaryKey`, `odcs.schema.properties.primaryKeyPosition` |
| `kind` (`python-api`), `modeType`, `extraColumns`, `config` | authored or defaulted | both (config) | as for a `python-node` | `projection.kind`, `projection.mode_type`, `projection.extra_columns`, `projection.config`, `odcs.custom.kind`, `odcs.custom.modeType`, `odcs.custom.extraColumns`, `odcs.custom.config` |
| `team.name`, `schedule`, `criticality` | authored | contract | as for a `python-node` | `odcs.team.name`, `odcs.custom.schedule`, `odcs.custom.criticality` |
| Descriptions, `tags` | authored | contract | as for a `python-node` | `odcs.description.purpose`, `odcs.schema.description`, `odcs.schema.properties.description`, `odcs.tags`, `odcs.schema.tags`, `odcs.schema.properties.tags` |
| `timeoutMinutes` | authored | contract | as for a `python-node` | `odcs.custom.timeoutMinutes` |
| `service` | the release, or authored and equal | contract | as for a `python-node` | `odcs.custom.service` |
| `reads` on the schema object | filled from the tables the read queries name | contract | as for a `python-node`; this kind has no read queries, so it lists no table | `odcs.schema.custom.reads` |
| Quality rules | authored | contract | as for a `python-node` | `odcs.schema.quality.metric`, `odcs.schema.quality.mustBe`, `odcs.schema.quality.severity`, `odcs.schema.quality.custom.where`, `odcs.schema.quality.custom.warnIf`, `odcs.schema.quality.custom.errorIf`, `odcs.schema.quality.custom.failCalc`, `odcs.schema.properties.quality.metric`, `odcs.schema.properties.quality.mustBe`, `odcs.schema.properties.quality.severity`, `odcs.schema.properties.quality.custom.where`, `odcs.schema.properties.quality.custom.warnIf`, `odcs.schema.properties.quality.custom.errorIf`, `odcs.schema.properties.quality.custom.failCalc` |
| `relationships` | authored | contract | as for a `python-node` | `odcs.schema.relationships.from`, `odcs.schema.relationships.to`, `odcs.schema.properties.relationships.to` |
| `classification` | authored | contract | as for a `python-node` | `odcs.schema.properties.classification` |
| `roles` | authored | contract | as for a `python-node` | `odcs.roles.role`, `odcs.roles.access`, `odcs.roles.description` |
| `slaProperties`, `support`, `price` | authored | contract | as for a `python-node` | `odcs.slaProperties.property`, `odcs.slaProperties.value`, `odcs.slaProperties.unit`, `odcs.slaProperties.description`, `odcs.support.channel`, `odcs.support.tool`, `odcs.support.url`, `odcs.support.description`, `odcs.price.priceAmount`, `odcs.price.priceCurrency`, `odcs.price.priceUnit` |
| Other vendors' custom properties | authored | contract | as for a `python-node` | `odcs.customProperties`, `odcs.schema.customProperties`, `odcs.schema.properties.customProperties` |
| Every other standard ODCS field the author writes: `description.usage` and `.limitations`, `domain`, `context`, `slaDefaultElement`, `authoritativeDefinitions`, a schema object's or column's `businessName`, `synonyms` or `deprecated`, and a column's `examples`, `enum`, `criticalDataElement` or `transformLogic` | authored | contract | hashed as written; every standard field that no entry names is classified by this rule | `odcs.*` |
| The `continuo` properties that a Python node does not carry: `filePath`, `materialization`, `contractEnforced`, `access`, `group`, `deprecationDate`, `freshnessBuildAfter`, `snapshot`, `quoteColumns`, `provenance` and an authored `reads` | authored | neither | as for a `python-node` | `odcs.custom.filePath`, `odcs.custom.materialization`, `odcs.custom.contractEnforced`, `odcs.custom.access`, `odcs.custom.group`, `odcs.custom.deprecationDate`, `odcs.custom.freshnessBuildAfter`, `odcs.custom.snapshot`, `odcs.custom.quoteColumns`, `odcs.custom.provenance`, `odcs.custom.reads` |
| `servers`, `tenant`, `contractCreatedTs` | authored | neither | as for a `python-node` | `odcs.servers`, `odcs.tenant`, `odcs.contractCreatedTs` |
| A column's nested `properties`, `items` and `map`, `partitioned: true` and a `partitionKeyPosition` other than -1 | authored | neither | as for a `python-node` | `odcs.schema.properties.properties`, `odcs.schema.properties.items`, `odcs.schema.properties.map`, `odcs.schema.properties.partitioned`, `odcs.schema.properties.partitionKeyPosition` |
| A column's `physicalName` | authored | neither | as for a `python-node` | `odcs.schema.properties.physicalName` |
| `version`, `status` as authored | authored | neither | as for a `python-node` | `odcs.version`, `odcs.status` |
| `apiVersion` and the ODCS `kind` | `v3.2.0` and `DataContract` | neither | as for a `python-node` | `odcs.apiVersion`, `odcs.kind` |
| The contract file's path and layout: comments, key order, quoting | the `.odcs.yaml` file | neither | as for a `python-node` | |
