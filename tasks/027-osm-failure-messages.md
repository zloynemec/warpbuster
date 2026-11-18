# 027 — Explain OSM failures and retain private acquisition diagnostics

## Scope

Fix result-page messages for a technical OSM failure with no output FIT, a partial
repair, and altitude completion that was not needed. Preserve the existing report
schema, confidence policy, detector, reconstruction and FIT writer.

Keep bounded acquisition-error details and chained cause in the existing private
OSM audit, never the public report. Investigate the reported production failure
using synthetic network requests; do not overwrite the user's result or send its
geographic bounds to Overpass without explicit permission.

## Acceptance

- An unresolved result with OSM unavailable explicitly reports a technical failure,
  no corrected FIT and the original track on the map.
- A repaired partial result says that verified changes were saved and other gaps
  remain. A skipped result never claims that coordinate repair was saved.
- Disabled/not-needed altitude completion is silent; other altitude messages do
  not imply that coordinates were repaired.
- Coverage-gate refusals, ordinary results, full-course reconstruction and older
  reports keep appropriate messages. No new settings or controls.
- Acquisition errors retain their code, bounded details and cause in the existing
  private audit. Public JSON contains only the established safe error code.
- Synthetic regressions cover UI branches and audit privacy; no private tracks
  enter public fixtures or version control.

## Verification

`node --test web/tests/*.test.mjs`

`.venv/bin/python -m pytest -q web/tests/test_osm_pipeline.py`

`.venv/bin/ruff check src tests web/backend web/tests`

`.venv/bin/mypy src/warpbuster`

Production investigation: preserve the existing result; compare the actual HTTP
client and isolated worker on synthetic public data before attributing the cause.

## Results (2026-11-18)

- 917 core tests, 190 web tests and 33 JS tests pass; Ruff, mypy, syntax and
  whitespace checks pass. Browser preview shows the technical-failure heading,
  original-track explanation and no misleading altitude/partial-success claim.
- Synthetic production probes reproduced read timeouts with the same client both
  inside and outside the worker's 2 GiB resource limit. DNS resolved normally.
  Later probes returned HTTP 406 from the configured Overpass status endpoint and
  HTTP 502 from its interpreter through the real `OverpassClient`.
- OpenStreetMap's main capabilities API returned HTTP 200 from the same container.
  This establishes a current failure contacting the configured Overpass endpoint;
  it does not prove the exact exception of the historical 3.91-second attempt.
- The original job retained only `acquisition_failed`; the new private audit
  preserves bounded error details and cause for future failures. Its publication
  and file permissions are covered by a regression test.
- Automatic safety review rejected replaying the user's geographic bounding box
  to Overpass without explicit location-sharing consent. Only synthetic public
  requests were sent. Original input, result, shared OSM cache and production
  configuration were not modified. No endpoint fallback or retry-policy change.

## Authorized replay follow-up (2026-11-18)

The user subsequently explicitly authorized sending this activity's geographic
bounds to `overpass-api.de`. The unchanged production client replayed the two
coverage batches (two cells, 96.877 km²) into temporary files, with its normal
90-second acquisition budget and two retries.

The first batch received HTTP 502 twice. The response explicitly reported that
all Overpass query workers were busy and the request queue was full. The third
attempt returned HTTP 200 (2,130,027 bytes, 40.724 seconds), and the second batch
returned HTTP 200 (4,755,831 bytes, 35.545 seconds). Both responses passed OSM
validation within the acquisition budget. This reproduces a transient upstream
capacity failure and establishes that the generated requests are accepted when
capacity becomes available. The exact historical HTTP response remains unknown.

The original FIT hash was rechecked unchanged. No routing, FIT writing, published
result changes or shared-cache publication occurred. A private replay summary is
stored under ignored `tests/private/osm-diagnostics-vyp088/`.
