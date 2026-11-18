import assert from "node:assert/strict";
import { test } from "node:test";
import { formatDuration, formatMetric, formatWholeMetric, formatPace, splitLabel } from "../dist/assets/run-summary.mjs";
import { trackSegments } from "../dist/assets/result-map.mjs";
import { altitudeMessage, osmFailureMessage, resultMessage } from "../dist/assets/fit-only.mjs";

test("OSM acquisition failure with no FIT is a technical failure, not partial success", () => {
  const report = { outcome: "unresolved", partial: true, osm: {
    status: "unavailable", stage: "acquisition", error_code: "acquisition_failed",
  }, altitude_completion: { status: "not_needed" } };
  const [title, description] = resultMessage(report);
  assert.match(title, /ошибка OSM/);
  assert.match(description, /получить данные OSM/);
  assert.match(description, /Исправленный FIT не создавался/);
  assert.match(description, /исходный трек/);
  assert.doesNotMatch(description, /Часть участков|больше уверенности/);
  assert.match(osmFailureMessage(report), /Изменения не записаны/);
  assert.doesNotMatch(osmFailureMessage(report), /восстановление сохранено/);
  assert.equal(altitudeMessage(report), "");
});

test("partial repairs preserve their success when OSM or DEM fails", () => {
  const report = { outcome: "repaired", partial: true, osm: {
    status: "unavailable", stage: "prepare",
  }, altitude_completion: { status: "unavailable", reason: "SECRET" } };
  const [title, description] = resultMessage(report);
  assert.match(title, /частично/);
  assert.match(description, /Проверенные изменения сохранены/);
  assert.match(description, /остались невосстановленными/);
  assert.match(osmFailureMessage(report), /подготовить карту OSM/);
  assert.match(osmFailureMessage(report), /Проверенные изменения сохранены/);
  assert.doesNotMatch(altitudeMessage(report), /SECRET|восстановление координат сохранено/);
  assert.match(altitudeMessage(report), /высоты FIT сохранены/);
});

test("ordinary, legacy, coverage and whole-course results retain accurate messages", () => {
  assert.match(resultMessage({ outcome: "unchanged", partial: true })[0], /Без изменений/);
  assert.match(resultMessage({ outcome: "unresolved" })[0], /больше уверенности/);
  const gate = { mode: "fit_only", outcome: "unresolved", osm: { status: "unavailable" },
    observed_gps_coverage: { status: "below_threshold" } };
  assert.match(resultMessage(gate)[0], /без GPX не запущено/);
  assert.equal(osmFailureMessage({}), "");
  assert.equal(altitudeMessage({}), "");
  assert.equal(altitudeMessage({ altitude_completion: { status: "disabled" } }), "");
  assert.match(altitudeMessage({ altitude_completion: { status: "applied", completed_records: 10 } }), /10 записей/);
  const full = { mode: "fit_with_course", outcome: "repaired", coordinate_free_reconstruction: { status: "applied" } };
  assert.match(resultMessage(full)[1], /Координаты всей записи восстановлены по GPX/);
});

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

test("rounds split elevation, heart rate and cadence to whole numbers, preserving missing values", () => {
  assert.equal(formatWholeMetric(12.4), "12");
  assert.equal(formatWholeMetric(12.5), "13");
  assert.equal(formatWholeMetric(149.8), "150");
  assert.equal(formatWholeMetric(173.2), "173");
  assert.equal(formatWholeMetric(0), "0");
  for (const value of [null, undefined, NaN, Infinity]) assert.equal(formatWholeMetric(value), "—");
  assert.equal(formatMetric(12.4), "12,4");
});

test("map preserves segment breaks and never bridges unavailable coordinates", () => {
  assert.deepEqual(trackSegments([[[55, 37], [null, 38], [56, 38]], [[57, 39], [91, 1], [58, 40]]]),
    [[[55, 37]], [[56, 38]], [[57, 39]], [[58, 40]]]);
  assert.deepEqual(trackSegments(undefined), []);
  assert.deepEqual(trackSegments([[[0, 0], [1, 1]]]), [[[0, 0], [1, 1]]]);
});
