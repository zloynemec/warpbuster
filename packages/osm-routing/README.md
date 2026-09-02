# WarpBuster OSM Routing

This package is the isolated boundary between immutable OSM Manager snapshots and
Valhalla. Task 010B validates a protocol v1 manifest, normalizes overlapping OSM files,
builds local tiles, and atomically publishes a verified content-addressed graph cache.

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
Manager cache. Reusing a non-empty directory requires `--overwrite`. The stable route
query contract itself is intentionally deferred to Tasks 010C–010E.
