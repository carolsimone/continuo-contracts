# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `continuo_contracts.canonical`: `canonical_json()` writes RFC 8785 canonical JSON and names the
  JSON path of any value it refuses; `normalise_text()` turns CRLF and CR into LF and strips ASCII
  whitespace from both ends.
