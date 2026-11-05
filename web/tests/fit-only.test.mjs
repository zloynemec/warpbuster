import assert from "node:assert/strict";
import { test } from "node:test";
import { coverageDetails, coverageMessage, coverageRefused, coordinateFreeMessage, endpointMessage, gapCells, openSubmittedResult, returnToUpload, finishResultNavigation } from "../dist/assets/fit-only.mjs";

test("full GPX reconstruction explains the assumption and preserved telemetry", () => {
  const report = { mode: "fit_with_course", outcome: "repaired", coordinate_free_reconstruction: {
    status: "applied", recorded_distance_m: 11481.08, course_distance_m: 12343.88,
    distance_ratio_error: 0.0751496586, message: "SECRET <script>untrusted()</script>",
  } };
  const message = coordinateFreeMessage(report);
  assert.match(message, /В исходном FIT не было координат/);
  assert.match(message, /всей записи восстановлены по GPX/);
  assert.match(message, /от первой до последней точки/);
  assert.match(message, /накопленной дистанции FIT/);
  assert.match(message, /11,48 км/);
  assert.match(message, /12,34 км/);
  assert.match(message, /7,51%/);
  assert.match(message, /высота и показания датчиков сохранены/);
  assert.match(message, /путь и направление прохождения не подтверждены/);
  assert.doesNotMatch(message, /SECRET|<script>/);
  for (const status of ["planned", "skipped", undefined]) {
    assert.equal(coordinateFreeMessage({ ...report, coordinate_free_reconstruction: {
      ...report.coordinate_free_reconstruction, status,
    } }), "");
  }
  assert.equal(coordinateFreeMessage({ ...report, outcome: "unresolved" }), "");
  assert.equal(coordinateFreeMessage({ ...report, mode: "fit_only" }), "");
  assert.equal(coordinateFreeMessage({ outcome: "repaired" }), "");
  const noDistances = coordinateFreeMessage({ ...report, coordinate_free_reconstruction: { status: "applied" } });
  assert.match(noDistances, /всей записи восстановлены по GPX/);
  assert.doesNotMatch(noDistances, /NaN|undefined|км|%/);
  assert.deepEqual(gapCells({ number: 1, kind: "whole_activity", provider: "gpx", action: "applied", filled_points: 100 }), [
    "1", "Вся запись", "Восстановлен по GPX", "100", "Полное восстановление по GPX; фактический путь не подтверждён",
  ]);
});

const uid = "a".repeat(43);
const refusal = status => ({ mode: "fit_only", observed_gps_coverage: {
  status, observed_gps_coverage_percent: status === "unavailable" ? null : 50.999,
  minimum_observed_gps_coverage_percent: 51, reason: "invalid_record_timestamps",
} });
function browser() {
  const b = { url: "/fix", reloads: 0 };
  b.history = { state: null, pushState(state, _, url) { this.state = state; b.url = url; }, replaceState(state, _, url) { this.state = state; if (url) b.url = url; } };
  b.location = { reload() { b.reloads++; } };
  return b;
}

test("refusal returns only its initiating navigation and retains message across reload", () => {
  for (const status of ["below_threshold", "unavailable"]) {
    const b = browser(); openSubmittedResult(uid, b);
    assert.equal(returnToUpload(refusal(status), uid, b), true);
    assert.equal(b.url, "/fix"); assert.equal(b.reloads, 2);
    assert.match(coverageMessage(b.history.state.warpbusterCoverageRefusal), /Выбери FIT повторно и добавь GPX/);
    assert.equal(JSON.stringify(b.history.state).includes(uid), false);
    assert.equal(returnToUpload(refusal(status), uid, b), false);
  }
});

test("separate visit, another job, old report, and partial success never redirect", () => {
  const b = browser();
  assert.equal(returnToUpload(refusal("below_threshold"), uid, b), false);
  openSubmittedResult(uid, b);
  assert.equal(returnToUpload(refusal("below_threshold"), "b".repeat(43), b), false);
  assert.equal(returnToUpload({ outcome: "unresolved" }, uid, b), false);
  assert.equal(returnToUpload({ mode: "fit_only", partial: true, outcome: "repaired", observed_gps_coverage: { status: "passed" } }, uid, b), false);
  finishResultNavigation(uid, b);
  assert.equal(b.history.state, null);
});

test("gate decision comes from Core, never rounded UI percentages", () => {
  assert.equal(coverageRefused(refusal("below_threshold")), true);
  assert.equal(coverageRefused({ ...refusal("below_threshold"), mode: "fit_with_course" }), false);
  assert.match(coverageDetails({ status: "not_applicable" }), /не применяется/);
  assert.match(coverageDetails(), /недоступны/);
  assert.doesNotMatch(coverageMessage(refusal("unavailable").observed_gps_coverage), /0%/);
  assert.match(coverageMessage(refusal("unavailable").observed_gps_coverage), /Временные метки/);
  assert.doesNotMatch(coverageMessage({ status: "unavailable", reason: "SECRET" }), /SECRET/);
});

test("endpoint and gap text distinguishes partial repairs and missing ends", () => {
  const report = { mode: "fit_only", gaps: [{ kind: "prefix", action: "unresolved" }, { kind: "internal", action: "applied" }, { kind: "suffix", action: "unresolved" }] };
  assert.match(endpointMessage(report), /Начало и конец/);
  assert.equal(endpointMessage({ ...report, mode: "fit_with_course" }), "");
  assert.equal(endpointMessage({ mode: "fit_only", gaps: [{ kind: "internal", action: "unresolved" }] }), "");
  assert.deepEqual(gapCells({ number: 2, kind: "internal", provider: "osm", action: "applied", filled_points: 51 }), ["2", "Внутренний участок", "Восстановлен по OSM", "51", "Вариант прошёл проверки"]);
  assert.doesNotMatch(gapCells({ reasons: ["SECRET"], action: "unresolved" }).join(" "), /SECRET/);
});
