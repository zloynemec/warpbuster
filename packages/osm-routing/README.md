# WarpBuster OSM Routing

This package is the isolated boundary between immutable OSM Manager snapshots and
Valhalla. Task 010B validates a protocol v1 manifest, normalizes overlapping OSM files,
builds local tiles, and atomically publishes a verified content-addressed graph cache.
Task 010C adds a versioned request-time trail-running profile without changing graph
identity. Task 010D adds bounded audited snapping and a stable single-route API.
Task 010E adds opt-in audited alternatives and advisory pairwise comparisons.
Task 010F requires the graph build and query runtime to use the exact same Valhalla version.

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

Both single-route and alternatives compare `cache_key.runtime.valhalla` with the
installed `valhalla.__version__` before creating an Actor. Exact equality, including
version suffixes, is required even within the trail profile's supported version range.
A mismatch returns `GRAPH_ENGINE_MISMATCH`, exit 2, with both versions and the graph ID.
Run `prepare` on the source snapshot manifest in the current environment and use the
returned `graph_id`. There is no automatic rebuild, deletion or migration; `list` and
`inspect` still work with graphs built by another runtime.

Missing/invalid build version identity is `CACHE_CORRUPT`; missing/invalid installed
version identity is `VALHALLA_REQUEST_FAILED`. Identity must be a nonempty string with
no whitespace. This is a reproducibility policy, not a claim that every different
version is binary-incompatible. Osmium versions are not compared at query time.

## Compare audited alternatives

`--alternates N` requests **N additional paths**, not counting the engine primary.
The default is `0`, preserving the existing `operation=route` JSON contract. Use `1`
or `2` to request the independent `operation=route_alternatives`, `protocol_version=1`
contract. A current graph can be reused without rebuilding.

```bash
warpbuster-osm-route route sha256:GRAPH_DIGEST \
  --from 44.540049,33.690680 \
  --to 44.528600,33.725055 \
  --alternates 2

warpbuster-osm-route route sha256:GRAPH_DIGEST \
  --from 44.540049,33.690680 \
  --to 44.528600,33.725055 \
  --alternates 2 --json > routes.json
```

JSON stdout contains one document; shell redirection writes it to your chosen file.
Console output includes the snapping decisions, requested/returned/unique counts and
a route comparison table. The engine may return no alternatives or fewer than requested.

Python callers keep `RouteService.route(RouteRequest)` for a single path and use:

```python
from warpbuster_osm_routing import (
    GeoPoint,
    RouteAlternativesRequest,
    RouteService,
    RoutingCacheConfig,
)

service = RouteService(RoutingCacheConfig.load())
result = service.alternatives(
    RouteAlternativesRequest(
        graph_id="sha256:GRAPH_DIGEST",
        start=GeoPoint(44.540049, 33.690680),
        end=GeoPoint(44.528600, 33.725055),
        alternates=2,
    )
)
for candidate in result.candidates:
    print(candidate.route_id, candidate.role, len(candidate.coordinates))
document = result.as_dict()
```

The frozen candidates expose coordinates and detached audit dictionaries via
`as_dict()`. JSON consumers dispatch on **operation + protocol_version**, not version
alone. The new operation has these fields:

| Field | Meaning |
|---|---|
| `request`, `graph`, `profile`, `query_policy`, `snapping` | Original anchors and complete 010D provenance/audits |
| `alternatives_policy` | Version/hash, effective limits, metric definition and sorting policy |
| `primary_route_id` | Engine primary ID; not a selected repair route |
| `routes` | Unique audited routes: stable ID, role, summary, geometry, edges, warnings, `vs_primary` |
| `comparisons` | Every pair, keyed by `route_a_id`/`route_b_id` |
| `route_choice` | `SINGLE_CANDIDATE`, `MULTIPLE_CANDIDATES`, or `NOT_EVALUATED` |
| `search` | Executed flag, requested/engine/unique counts, duplicate count, reasons, `exhaustive=false` |
| `engine_diagnostics` | Raw engine slot mapping and duplicate audit; excluded from semantic ordering guarantees |

Each path is independently audited against the **same** accepted anchors/profile using
`edge_walk`, not `map_snap`. All shape segments must belong to exactly one audited edge
span. If any engine candidate fails a mandatory check, the entire query returns exit 2
and an error with `engine_slot`/check diagnostics; no partial route set is published.
This does not change Core's partial FIT repair policy.

Only exact geometry + ordered directed traversal duplicates are removed. WarpBuster
rejects conflicting audit data on such duplicates as `ROUTE_AUDIT_FAILED` rather than
silently choosing one representation. The engine primary stays first. Other paths are
sorted by calculated length rounded to millimetres,
then their content-derived ID. This is presentation order, **not** repair confidence.
IDs are scoped to the graph and profile. Equal requests on the same runtime/graph/policy
have stable semantic output; raw `engine_diagnostics` may track backend ordering.

### Interpreting comparisons

- `length_delta_m` is B minus A; `distance_ratio` is B/A, using calculated 2D geometry.
  An alternative can be shorter than the primary chosen by the costing profile.
- `shared_edge_weight_m` sums minimum travelled length per shared directed edge.
  `overlap_a` and `overlap_b` divide this weight by each path's length.
- `diversity_ratio` is `1 - shared_weight / min(length_a, length_b)`.
- These are `directed_edge_weighted_v1` metrics, **not exact spatial overlap**. Different
  partial spans on one edge can overestimate shared weight; repeated traversals retain
  length but lose ordering in this metric. Opposite directed edges do not overlap.
- Diversity below `0.10` warns `LOW_DIVERSITY`; candidate/primary distance above `1.50`
  warns `LARGE_DETOUR`. Neither warning hides a distinct path or resolves ambiguity.
- `REPEATED_EDGE_TRAVERSAL`, `COINCIDENT_GEOMETRY_DIFFERENT_EDGES`, `FERRY_USED` and
  `DESTINATION_ONLY_SNAP` remain visible. Engine estimated time is not the runner's pace;
  absent engine cost stays `null` and is not fabricated from length/time.

**One returned candidate does not prove a unique path.** All successful searches remain
non-exhaustive. More candidates mean `READY + MULTIPLE_CANDIDATES`, exit 0, not an error.
No alternatives means `READY + SINGLE_CANDIDATE`, also exit 0. Negative snap/path outcomes
still exit 1, with empty routes and `NOT_EVALUATED`; malformed responses, engine errors
and exceeded limits exit 2. There is no automatic reconstruction decision.

All new policy values are in `osm-routing.example.toml`. At most two additional paths,
one native route request and three trace audits are allowed; each response is bounded to
8 MiB before JSON parsing. Aggregate shape/edge limits include duplicates (48,000 each),
and the existing per-route limits still apply. These are adapter resource bounds, not
a hard C++ memory limit or wall-clock timeout.
[Task 010F](../../tasks/010f-minimal-integration-readiness.md) covers only the graph/runtime
version guard, not packaging smoke or runtime workers. Hard query deadlines are deferred hardening,
to revisit before unattended batch/server use or after an observed hang.

Restrictions follow the pinned engine's normalized data, not an independent raw OSM tag
index. The integration tests verify `foot=no`, T4 and `impassable=yes` bypass exclusion;
the audit also rejects normalized engine `surface=impassable`. A raw OSM tag with that
same spelling is not assumed to produce the same normalized value.

No multi-route GPX/HTML export, DEM, activity evidence, extra acquisition or FIT writing
is included. Selecting candidates for reconstruction belongs to Task 011.

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
