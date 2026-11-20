# continuo-contracts

continuo-contracts writes, checks and hashes the data contract of each table that
[continuo][g-continuo] runs. A team's CI and continuo's [release][g-release] run the same code, so
they write the same bytes. The [lite check][g-lite] in CI cannot see the tables a node's SQL reads
without declaring them, so for such a node its hash differs from the release's ([reads][g-reads]).

continuo is the platform that runs a team's dbt and Python nodes on schedules and stores their
contracts and fingerprints. A [node][g-node]'s [contract][g-contract] is an [ODCS][g-odcs] YAML
document: what its table holds, who owns it and what is checked on it. Its
[contract hash][g-chash] changes when the contract's meaning changes, not its formatting.

## Install

```bash
pip install continuo-contracts==<version>
```

Python 3.14 or later. Pin the version that your continuo's release uses: whoever runs continuo can
tell you. A hash compares only between runs of the same code ([stability][stab]).

## Check a dbt project in CI

The lite check runs `dbt parse` on the project folder, `shop` here, then writes, checks and hashes
each node's contract. `--service` is the [service][g-service], the `name:` in `dbt_project.yml`
with `_` written `-`. dbt must be on `PATH`, or pass `--dbt PATH`. Here the [seed][g-dbt] file
`seeds/countries.csv` starts with `code,label`, and `schema.yml` declares `code` and `name`:

```bash
$ continuo-contracts dbt check shop --service shop
INFO LITE CHECK. Passing it does not guarantee that continuo will create this service's contracts at release time.
ERROR seed.shop.countries: label is in the CSV header but not declared
ERROR seed.shop.countries: name is declared but not in the CSV header
INFO What only the release does:
...
```

The exit status is 1, for the error lines. With the header `code,name` the run prints no `ERROR`
line, only the banner, the list of what only the release does and the banner again, and exits 0.
Exit 1 also means that `dbt parse` failed, and 2 that the command could not run, such as no `dbt`.

## Validate a Python node's contract

A Python node's contract is a file the team writes, `<table>.odcs.yaml` or
`<schema>.<table>.odcs.yaml`. A strict YAML reader reads it: YAML 1.2, where `required: yes` is
the string `'yes'`, and nothing that JSON cannot hold. Here `kpis.odcs.yaml` has `required: yes`:

```bash
$ continuo-contracts validate contracts/
INFO this release checks Python contracts
ERROR contracts/kpis.odcs.yaml: schema[0].properties[0].required: 'yes' is not of type 'boolean'
INFO 2 contracts checked, 1 with issues
```

In the first line, "release" is the command's word for this package's version. The wheel is pure
Python (`py3-none-any`), and its two runtime dependencies are PyYAML and jsonschema.

## Check contracts against an ontology

An [ontology][g-ontology] file names your services, things and their properties. A contract's
[bindings][g-binding] tie each table and column to it. `ontology check` checks the file alone, and
`dbt check --ontology` also reads a project's bindings against it, with the rules that need no
other service. A `WARNING` line never fails the run, and an `ERROR` line exits 1:

```bash
$ continuo-contracts ontology check ontology.yaml
ERROR principals.services.web: domain 'web' is not declared
INFO 1 error in the ontology file
$ continuo-contracts dbt check shop --service shop --ontology ontology.yaml
INFO LITE CHECK. Passing it does not guarantee that continuo will create this service's contracts at release time.
WARNING model.shop.orders: column rate comes from FxRate, which Order does not reach through any reference: the join is not a declared relationship
INFO What only the release does:
...
```

Exit 2 means the command could not run, such as an ontology file it cannot read or that does not
load.

## How it works

```text
 dbt project --dbt parse--> manifest node --gates, generate--> contract --+
                                                                          |
 Python node: <table>.odcs.yaml --strict YAML reader---------> contract --+
                                                                          v
                  contract hash <--SHA-256-- stored form <--normalise-- validate
```

The [gates][g-gate] leave some nodes of the dbt [manifest][g-manifest] out. Each contract is
[validated][val], then normalised into its [stored form][g-stored], one shape for all contracts
that mean the same thing, and hashed. See [how it works][how] for how the package is built.

## Documentation

Every reader can start with the [walkthrough][walk]: What happens to one dbt model, from
`schema.yml` to its hash?

### Use it on a dbt project

| Page | Question it answers |
|---|---|
| [Check your project][check] | How do I run the lite check in CI and fix what it reports? |
| [dbt contracts][dbt] | How does a dbt setting become a contract field? Which nodes are skipped? |

### Use it on a Python node

| Page | Question it answers |
|---|---|
| [Python contracts][py] | How do I write and check the contract of a Python node? |
| [Validation][val] | Which rules does `validate` apply, and what does each issue mean? |

### Bind contracts to an ontology

| Page | Question it answers |
|---|---|
| [Ontology][onto] | How do I describe my data in an ontology file, bind each contract to it and check the bindings? |

### Build on it or change it

| Page | Question it answers |
|---|---|
| [How it works][how] | How is the package built: pipeline, modules, fingerprints, stored form? |
| [Hashing][hash] | What is hashed, and how are the bytes written? |
| [Contract hash][chash] | How is a contract turned into its stored form and hashed? |
| [source_sha256][src] | How is `source_sha256` of a dbt node built? |
| [Fingerprints][fp] | Which input moves which fingerprint? |
| [Test vectors][vec] | How do I check a port or a code change against the test vectors? |
| [Errors][err] | Which exceptions does the package raise, and when? |
| [Stability][stab] | What may a new version change, and which version should I pin? |
| [Glossary][gloss] | What does each term of these pages mean? |
| [Contributing][contrib] | How do I set up, test and change the package? |

Apache License 2.0: see [LICENSE][lic] and [NOTICE][notice]. Report a vulnerability as
[SECURITY.md][sec] describes.

[how]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/how-it-works.md
[walk]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/walkthrough.md
[dbt]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/dbt-contracts.md
[check]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/check-your-project.md
[py]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/python-contracts.md
[val]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/validation.md
[onto]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/ontology.md
[hash]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/hashing.md
[chash]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/contract-hash.md
[src]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/source-sha256.md
[fp]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/fingerprints.md
[vec]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/vectors.md
[err]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/errors.md
[stab]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/stability.md
[gloss]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md
[contrib]: https://github.com/carolsimone/continuo-contracts/blob/main/CONTRIBUTING.md
[sec]: https://github.com/carolsimone/continuo-contracts/blob/main/SECURITY.md
[lic]: https://github.com/carolsimone/continuo-contracts/blob/main/LICENSE
[notice]: https://github.com/carolsimone/continuo-contracts/blob/main/NOTICE
[g-continuo]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#continuo
[g-release]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#release
[g-node]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#node
[g-contract]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#contract
[g-chash]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#contract-hash
[g-fp]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#fingerprint
[g-service]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#service
[g-lite]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#lite-check
[g-gate]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#gate
[g-stored]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#stored-form
[g-reads]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#reads
[g-odcs]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#odcs
[g-manifest]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#manifest
[g-dbt]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#dbt-words
[g-ontology]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#ontology
[g-binding]: https://github.com/carolsimone/continuo-contracts/blob/main/docs/glossary.md#binding
