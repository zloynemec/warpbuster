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

// Exercise the actual page handlers without a browser-specific file-picker API.
function element() {
  return { hidden: true, disabled: false, value: "", textContent: "", dataset: {}, handlers: {},
    addEventListener(type, fn) { this.handlers[type] = fn; },
    setAttribute() {}, removeAttribute() {}, focus() {}, contains() { return false; } };
}
async function page(requestOverride) {
  const { initUploadPage } = await import("../dist/assets/fix-upload.mjs");
  const ids = new Map();
  const get = id => { if (!ids.has(id)) ids.set(id, element()); return ids.get(id); };
  const cards = Object.fromEntries(["fit", "gpx"].map(ext => {
    const parts = new Map();
    return [ext, { dataset: {}, querySelector(selector) { if (!parts.has(selector)) parts.set(selector, element()); return parts.get(selector); } }];
  }));
  const root = { getElementById: get, querySelector: selector => cards[selector.includes('"fit"') ? "fit" : "gpx"],
    querySelectorAll: () => [], addEventListener() {} };
  const jobs = [];
  const browser = { history: { state: null, pushState(state, _, url) { this.state = state; browser.url = url; } }, location: { reload() { browser.reloaded = true; } } };
  const request = requestOverride || (async (url, options) => {
    if (url === "/api/session") return { file_limit_bytes: 1000, retention_days: 7 };
    jobs.push(options); return { uid: "a".repeat(43) };
  });
  initUploadPage(root, { request, browser });
  await Promise.resolve();
  const choose = (ext, files) => cards[ext].querySelector(".drop-zone").handlers.drop({ preventDefault() {}, dataTransfer: { files } });
  const remove = ext => cards[ext].querySelector("[data-remove]").handlers.click();
  return { get, choose, remove, jobs, browser };
}
const fit = () => new File(["synthetic"], "run.fit");
const gpx = () => new File(["synthetic"], "route.gpx");

test("FIT alone enables submission and sends no course part", async () => {
  const p = await page();
  assert.equal(p.get("analyze-button").disabled, true);
  p.choose("fit", [fit()]);
  assert.equal(p.get("analyze-button").disabled, false);
  await p.get("analyze-button").handlers.click();
  assert.deepEqual([...p.jobs[0].body.keys()], ["activity"]);
  assert.equal(p.browser.url, `/res/${"a".repeat(43)}`);
  assert.equal(p.browser.reloaded, true);
});

test("GPX alone is insufficient; adding and removing GPX preserves FIT readiness", async () => {
  const p = await page();
  p.choose("gpx", [gpx()]); assert.equal(p.get("analyze-button").disabled, true);
  p.choose("fit", [fit()]); assert.equal(p.get("analyze-button").disabled, false);
  p.remove("gpx"); assert.equal(p.get("analyze-button").disabled, false);
  p.choose("gpx", [gpx()]);
  await p.get("analyze-button").handlers.click();
  assert.deepEqual([...p.jobs[0].body.keys()], ["activity", "course"]);
  p.remove("fit"); assert.equal(p.get("analyze-button").disabled, true);
});

test("invalid optional GPX blocks silent fallback until explicitly removed", async () => {
  const p = await page(); p.choose("fit", [fit()]);
  for (const bad of [new File([], "empty.gpx"), new File(["x"], "bad.txt"), new File(["x".repeat(1001)], "large.gpx")]) {
    p.choose("gpx", [bad]); assert.equal(p.get("analyze-button").disabled, true);
    assert.equal(p.get("gpx-error").hidden, false);
    p.remove("gpx"); assert.equal(p.get("analyze-button").disabled, false);
  }
});

test("connection failure keeps submission disabled", async () => {
  const p = await page(async () => { throw new Error("offline"); });
  p.choose("fit", [fit()]);
  assert.equal(p.get("analyze-button").disabled, true);
  assert.equal(p.get("service-retry").hidden, false);
});
