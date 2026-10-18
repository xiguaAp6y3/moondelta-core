# Vendored dependency: mizchi/parquet

- Upstream: https://github.com/mizchi/parquet
- Version: 0.2.2 (published 2026-09-24)
- License: Apache-2.0 (see LICENSE in this directory)

## Why vendored

`mizchi/parquet@0.2.2` declares `mizchi/duckdb@0.6.4` as a module dependency. That
package still ships the legacy `moon.mod.json` manifest, which the current MoonBit
toolchain (`moonc >= 0.10.15`) rejects ("no longer supported"). The dependency is only
used by the upstream `src/cmd/duckdb_e2e` and `src/playground_bridge` sub-packages, so
this vendored copy drops those two sub-packages and all DuckDB-related code. Nothing in
the parquet reader/writer implementation itself was modified.

## Modifications vs upstream

1. Removed `src/cmd/duckdb_e2e` and `src/playground_bridge` (DuckDB interoperability demos).
2. No changes to reader/writer/encoding/metadata code.

## License compliance

This directory is a redistribution of Apache-2.0 licensed code; the original LICENSE is
kept as `LICENSE`. Attribution to the upstream project is kept in this NOTICE file and in
the project README.
