# Glossary

What does each term of these pages mean? An entry links to the page that explains it.

### continuo
The platform that runs a team's dbt and Python nodes on schedules and stores their contracts and
fingerprints. Its [release](#release) builds each node's [contract](#contract) with this package.
### release
continuo's release step. It builds, checks and hashes every contract, and can reject the project.
### service
The name continuo knows a project by: for dbt, the `name:` in `dbt_project.yml` with `_` written
`-`, so `my_shop` is `my-shop` ([check your project](check-your-project.md)).
### declared service
The service a release or `--service` gives. A [kept node](#kept-node) of another service makes the
release reject the manifest as `invalid_artifact`.
### manifest
dbt's `target/manifest.json`, the project as `dbt parse` reads it ([walkthrough](walkthrough.md)).
### dbt words
- model: a SQL `select` in `models/` that dbt builds as a table or a view.
- seed: a CSV file in `seeds/` that dbt loads as a table.
- snapshot: a table in which dbt keeps the history of another table's rows.
- `ref`: `{{ ref('orders') }}`, the Jinja call by which a model names another node.
- generic test: a named test, such as `not_null`, declared on a model or column in `schema.yml`.
- singular test: a SQL file in `tests/` that fails when it returns rows.
- `schema.yml`: a YAML file beside the models that declares their columns, tests and `meta`.
- `profiles.yml`: dbt's connection settings, one target per database.
- macro: a Jinja function, in `macros/` or in an installed package.
- `fqn`: a node's fully qualified name in the manifest, a list that starts with the project name.
- `meta`: free keys under a node's config that dbt copies into the manifest as `config.meta`.
- tag: a label on a node, as in `tags: [daily]`.
### node
A dbt model, seed or snapshot, or a Python node, that this package builds a contract for. A test
or another manifest entry is not a node. A script, a CSV file or an API call fills a Python node.
### contract
The [ODCS](#odcs) document of one node's table: columns, tests, owner, schedule. A dbt node's is
generated ([dbt](dbt-contracts.md)) and a Python node's written ([Python](python-contracts.md)).
### ODCS
The Open Data Contract Standard, a YAML format for a table's schema, quality rules and owners.
This package checks contracts against the ODCS v3.2.0 JSON schema, which it ships.
### quality rule
An item of a contract's `quality` list. A dbt test becomes one ([dbt contracts](dbt-contracts.md)).
### ontology
A YAML file that names who acts on data (domains, services, teams, roles, people) and what the data
is about ([things](#thing)). Contracts are bound to it and checked against it
([ontology](ontology.md)).
### thing
An entry under `entities` of the [ontology](#ontology): what data can be about, such as a customer,
with an owner, a key of named parts, properties and access rules.
### binding
One of the seven `continuo` custom properties that tie a contract to the ontology: `entity`,
`grain`, `fixed` and `filter` on the table, `property`, `local` and `aggregate` on a column
([ontology](ontology.md#bind-a-contract)).
### copy
A contract of a service that the ontology declares, as the rules read it: the table that holds some
rows and fields of a [thing](#thing), or an aggregate. A rule finds a copy by its physical relation,
`<schema>.<table>`, which another contract's [`reads`](#reads) names.
### resolution
What `ontology.resolve` does: it fills a [stored form](#stored-form) with what the
[ontology](#ontology) knows, such as the domain, the links, the types and the key, and never
replaces a value ([ontology](ontology.md#resolve)).
### ontology part
What `ontology.strip` removes from a stored form: the seven [bindings](#binding) and the
`authoritativeDefinitions` entries whose `url` starts with `continuo-ontology:`.
### finding
What an ontology rule reports: a severity (`error`, `warning` or `info`), the rule, a subject and a
message ([ontology](ontology.md#the-rules)).
### per-service rule
An ontology rule that needs no other service: `contract`, `coverage`, `quality` or `privacy`. The
[lite check](#lite-check) runs them. `access`, `order` and `drift` read the contracts of every
service, so only the [release](#release) runs them.
### reads
The tables a node reads. For dbt: those `meta.continuo.reads` declares, which the library writes,
plus those the compiled SQL names, which the release adds. The [lite check](#lite-check) cannot
add them, so its hash differs from the release's when the SQL reads an undeclared table.
### schedule
The `continuo` property that names what a node runs on. A Python node's author writes it. A dbt
node's is its first tag, `seed` for a seed with no tag, and none for a dormant node.
### run facts
What `dbt.run_facts` reads back from a dbt contract, such as its schedule, criticality and
materialization. A contract and its stored form give the same facts, and none is [reads](#reads).
### gate
A rule that keeps a dbt node or leaves it out. Three are tried in order: no `config.meta.owner`,
the tag `local_stub`, an installed package's node with no tag ([dbt contracts](dbt-contracts.md)).
### kept node
A node every [gate](#gate) lets through: it gets a contract ([dbt contracts](dbt-contracts.md)).
### skipped node
A node that a gate leaves out: no contract, and an `INFO` line of the [lite check](#lite-check).
### dormant node
A kept model or snapshot with no tag, hence no [schedule](#schedule). It runs only by hand.
### canonical JSON
The one text that RFC 8785 gives a JSON value: keys sorted, no whitespace, one form per number.
Two processes that hold the same value write the same bytes ([hashing](hashing.md)).
### stored form
The contract after `normalise_dbt` or `normalise_python`, such as sorted tags and `int` written
`INTEGER`: one shape for all that mean the same. [`contract_hash`](contract-hash.md) hashes it.
### contract hash
`contract_hash`: `sha256:` and the SHA-256 of the [stored form](#stored-form) as canonical JSON,
minus the root keys `apiVersion`, `kind` (`DataContract`), `status` and `version`. The `continuo`
property `kind` is hashed. A changed contract hash creates a new contract [version](#version).
### content hash
`content_hash`: the hash of a node's code and configuration ([how it works](how-it-works.md)).
### fingerprint
A hash that moves with what it covers. A node has two ([fingerprints](fingerprints.md)).
### lite check
`continuo-contracts dbt check`, run in the team's CI: it runs `dbt parse` and does for each node
what the release does with it. Passing it does not guarantee that the release accepts the project.
### re-baseline
After a version of this package moves a hash, continuo's release runs once for each service. It
recomputes every node's stored hash with the new code, and validates and reloads nothing.
### port
An implementation in another language that gives the same bytes ([hashing](hashing.md)).
### test vector
A JSON case in `vectors/`: an input and the exact output a function gives ([vectors](vectors.md)).
### projection
A Python node's code projection: what decides what it computes, in one JSON shape. Its hash is
the config part of the content hash ([hashing](hashing.md#projection_hash)).
### version
Three things. This package's own number, `continuo_contracts.__version__`. The ODCS root key
`version`, a string. A contract version in continuo, which a changed contract hash creates.
