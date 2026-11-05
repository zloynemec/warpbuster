# 026 — Automatic reconstruction of coordinate-free FIT from GPX

## Scope and assumptions

When every original FIT record has neither latitude nor longitude, a supplied GPX
may provide the entire path. Assume one complete traversal of its only continuous
segment, from its first point to its last in stored order. This is a reconstruction
assumption, never evidence for the Integrity Detector.

Use qualified, positive cumulative record distance for relative progress. Accept
`abs(course_length - recorded_delta) / recorded_delta <= 0.10`; the dimensionless
threshold belongs to `CourseReconstructionConfig`. Do not fall back to uniform time
or speed when distance is unusable. Retain existing confidence policy: the candidate
is MEDIUM and the default process/web policy applies it; an explicit HIGH policy
still skips it. This special case does not require the missing-completion flag.

Preserve timestamps, recorded distance/speed, altitude, sensors, unknown/developer
fields and source files. Use the existing writer and FIT diff. Show the assumption
on the result page using the existing description and gap table. No new settings,
CLI parameters, direction inference, alternate start, multi-segment joining,
OSM routing or elevation reconstruction.

## Acceptance

- Default shared pipeline and `process FIT GPX` reconstruct eligible inputs without
  extra flags; dry-run previews without writing.
- A present coordinate component, including session/lap endpoints, disqualifies
  the automatic whole-course assumption. Partially missing tracks keep their rules.
- Missing GPX, multiple segments, unusable/zero/reset distance, continuity breaks,
  invalid timing, pause conflicts and physically impossible allocation refuse.
- Threshold boundary is inclusive and configurable; out-of-range/non-finite
  configuration is rejected. Recorded-distance offsets do not affect progression.
- Console/JSON/local HTML disclose the full-course assumption, confidence,
  allocation, both distances, discrepancy, preserved telemetry and planned/applied
  status. The public result page explains applied reconstruction in Russian above
  the metrics, including both distances and discrepancy. Old/ordinary/skipped
  reports retain their existing descriptions; no new controls are added.
- Synthetic regression tests cover planner, writer preservation, pipeline and CLI;
  private user files are inspected locally and never committed.

## Verification

`.venv/bin/python -m pytest tests/test_coordinate_free_reconstruction.py -q`

`.venv/bin/python -m pytest -q`

`.venv/bin/ruff check src tests`

`.venv/bin/mypy src/warpbuster`

## Verified

- Core: 843 passed, 6 skipped; web: 178 passed, 5 skipped.
- Ruff, formatting, mypy and `git diff --check` pass.
- The threshold tests reject an inverted comparison (in-memory mutation only).
- Synthetic 20,000-record integrity analysis + planning: 0.190 seconds locally.
- Private 2026-11-05 input: 5,110 coordinates filled, 7.515% distance discrepancy;
  10,220 coordinate fields changed, zero distance/summary/unexpected changes;
  timestamps, sensors, developer and unknown fields preserved. Artifacts remain
  ignored under `tests/private/coordinate-free-2026-11-05/`.
- Public JSON, local HTML and console expose the notice. The result page now
  renders a localized explanation in its existing description and labels the
  whole-activity gap as an assumed path.
- Result-page follow-up: `node --test web/tests/*.test.mjs` — 30 passed;
  `.venv/bin/python -m pytest web/tests/test_fit_only_backend.py::test_coordinate_free_course_reconstruction_public_report tests/test_coordinate_free_reconstruction.py -q`
  — 29 passed. Synthetic browser preview confirms the explanation is visible
  above the metrics, with no JavaScript console errors. No controls were added.
