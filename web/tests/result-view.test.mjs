import assert from "node:assert/strict";
import { test } from "node:test";
import { formatDuration, formatPace, splitLabel } from "../dist/assets/run-summary.mjs";
import { trackSegments } from "../dist/assets/result-map.mjs";

test("formats elapsed time and pace without clock-time leakage or rounding errors", () => {
  assert.equal(formatDuration(3661), "1:01:01");
  assert.equal(formatDuration(59.8), "1:00");
  assert.equal(formatPace(359.8), "6:00");
  assert.equal(formatPace(3600), "60:00");
  assert.equal(formatPace(null), "—");
  assert.equal(formatDuration(NaN), "—");
});

test("labels the final partial kilometre by its actual distance range", () => {
  assert.equal(splitLabel({ index: 3, complete_kilometre: true }), "3");
  assert.equal(splitLabel({ index: 3, complete_kilometre: false, start_distance_m: 2000, end_distance_m: 2500 }), "2–2,5");
});

test("map preserves segment breaks and never bridges unavailable coordinates", () => {
  assert.deepEqual(trackSegments([[[55, 37], [null, 38], [56, 38]], [[57, 39], [91, 1], [58, 40]]]),
    [[[55, 37]], [[56, 38]], [[57, 39]], [[58, 40]]]);
  assert.deepEqual(trackSegments(undefined), []);
  assert.deepEqual(trackSegments([[[0, 0], [1, 1]]]), [[[0, 0], [1, 1]]]);
});
