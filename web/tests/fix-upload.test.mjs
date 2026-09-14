import assert from "node:assert/strict";
import { test } from "node:test";
import { formatFileSize, validateSelection } from "../dist/assets/fix-upload.mjs";

test("accepts exported FIT and GPX names regardless of extension case or MIME", () => {
  assert.equal(validateSelection([{ name: "Пробежка.FIT", size: 3500, type: "" }], "fit"), "");
  assert.equal(validateSelection([{ name: "route.GpX", size: 400, type: "text/xml" }], "gpx"), "");
});

test("rejects switched files, disguised suffixes, and archives", () => {
  for (const [name, extension] of [["route.gpx", "fit"], ["run.fit", "gpx"], ["run.fit.exe", "fit"], ["run.fit.zip", "fit"]]) {
    assert.match(validateSelection([{ name, size: 100 }], extension), /Проверь формат/);
  }
});

test("rejects empty files and more or fewer than one file per picker", () => {
  assert.match(validateSelection([{ name: "run.fit", size: 0 }], "fit"), /пустой/);
  assert.match(validateSelection([], "gpx"), /один файл/);
  assert.match(validateSelection([{ name: "a.fit", size: 1 }, { name: "b.fit", size: 2 }], "fit"), /один файл/);
});

test("formats metadata sizes without reading file contents", () => {
  assert.equal(formatFileSize(1), "1 КБ");
  assert.equal(formatFileSize(1024), "1 КБ");
  assert.equal(formatFileSize(1025), "2 КБ");
  assert.equal(formatFileSize(1024 * 1024), "1 МБ");
  assert.equal(formatFileSize(1.5 * 1024 * 1024), "1,5 МБ");
});

test("enforces server-provided per-file limits before submission", () => {
  assert.equal(validateSelection([{ name: "run.fit", size: 100 }], "fit", 100), "");
  assert.notEqual(validateSelection([{ name: "run.fit", size: 101 }], "fit", 100), "");
});
