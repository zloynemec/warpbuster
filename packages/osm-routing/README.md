# WarpBuster OSM Routing

This package is the isolated boundary between immutable OSM Manager snapshots and
Valhalla. Task 010B validates a protocol v1 manifest, normalizes overlapping OSM files,
builds local tiles, and atomically publishes a verified content-addressed graph cache.
Task 010C adds a versioned request-time trail-running profile without changing graph
identity. Task 010D adds bounded audited snapping and a stable single-route API.

It does not read FIT, detect corruption, select reconstruction candidates, or modify an
activity. It performs no network requests; OSM Manager is responsible for acquiring and
publishing source snapshots.

## Prepare and reuse a graph

```bash
warpbuster-osm-route prepare /absolute/path/to/manifest.json
warpbuster-osm-route prepare /absolute/path/to/manifest.json --json
warpbuster-osm-route prepare /absolute/path/to/manifest.json --rebuild
```

The first call returns `READY`. A later call with the same snapshot, materializer,
Valhalla runtime and build profile verifies the artifacts and returns the same
`graph_id` as `CACHED`, without rebuilding tiles.

The default platform cache can be overridden with `--cache-dir`,
`WARPBUSTER_OSM_ROUTING_CACHE_DIR`, or `osm-routing.toml`. All limits and their defaults
are documented in `osm-routing.example.toml`.

## Inspect and maintain derived artifacts

```bash
warpbuster-osm-route list
warpbuster-osm-route inspect sha256:GRAPH_DIGEST
warpbuster-osm-route remove sha256:GRAPH_DIGEST
warpbuster-osm-route prune --dry-run
warpbuster-osm-route prune --apply
```

`inspect` verifies the canonical PBF, Valhalla config and every tile hash. `remove`
requires one exact graph ID. `prune` is a dry run unless `--apply` is explicit. These
commands operate only inside the routing package's derived cache and never remove OSM
Manager source blobs.

## Inspect the trail-running profile

```bash
warpbuster-osm-route profile show
warpbuster-osm-route profile show --json
```

`warpbuster-trail-running-v1` is compatible with Valhalla `>=3.8.3,<3.9`. Its canonical
JSON and SHA-256 cover every option deliberately controlled by WarpBuster. The profile
allows normal paths, tracks, unpaved surfaces, steps and `sac_scale` through T3; T4–T6
and explicit pedestrian prohibitions remain unavailable. Hills are not treated as an
error. Tracks are a preference only within a reasonable detour, and `use_ferry=0` is a
strong preference rather than a hard ferry ban.

The graph-time `valhalla-pedestrian-graph-v1` policy is separate. Its ID and config hash
are recorded in every graph manifest, while the verified config artifact contains the
options; these affect `graph_id`, while the request-time trail profile does not. Access
tagged `private` or `destination` retains Valhalla 3.8.3 endpoint semantics.

## Build one audited route

Prepare the current graph-manifest v2, retain its exact `graph_id`, and query it:

```bash
warpbuster-osm-route route \
  sha256:GRAPH_DIGEST \
  --from 44.614065,33.736355 \
  --to 44.604988,33.773734

warpbuster-osm-route route \
  sha256:GRAPH_DIGEST \
  --from 44.614065,33.736355 \
  --to 44.604988,33.773734 \
  --json
```

Each anchor is checked independently against the Manager coverage and Valhalla
candidates. WarpBuster calculates its own snap distance, rejects a best snap beyond
30 m, and refuses close non-equivalent alternatives as ambiguous. A successful route
is decoded, checked against both audited snaps and summary length, then audited with
`trace_attributes(shape_match=edge_walk)` to preserve an ordered edge/OSM-way trail.

`OUTSIDE_COVERAGE`, `NO_SNAP`, `AMBIGUOUS_SNAP`, and `NO_ROUTE` are normal domain
outcomes with exit code 1. A verified `READY` route exits 0; invalid cache/config,
incompatible legacy graphs, engine failures, and failed post-audits exit 2. Query
thresholds are named in `osm-routing.example.toml` and copied into JSON provenance.
Graph manifest v1 remains inspectable as `LEGACY_READY` but must be rebuilt from its
Manager manifest before it can route.

## Task 010A diagnostic spike

The original temporary end-to-end probe remains available for diagnostic comparisons:

```bash
warpbuster-osm-route spike \
  /absolute/path/to/manifest.json \
  --work-dir /tmp/warpbuster-valhalla-spike \
  --from 44.484310,33.639581 \
  --to 44.494138,33.600678 \
  --alternates 2 \
  --json
```

The spike work directory contains only derived artifacts and must not be used as the OSM
Manager cache. Reusing a non-empty directory requires `--overwrite`; new integrations
should use the stable `route` command instead.
