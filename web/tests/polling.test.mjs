import assert from "node:assert/strict";
import { test } from "node:test";
import { createResultPoller, POLL_INTERVAL_MS } from "../dist/assets/polling.mjs";

const settle = () => new Promise(resolve => setImmediate(resolve));
function harness(getStatus, onReady = async () => {}) {
  const timers = new Map();
  const events = [];
  let id = 0;
  const poller = createResultPoller({
    getStatus, onReady: async status => { events.push("ready"); await onReady(status); },
    onState: status => events.push(status.status),
    onError: (error, terminal) => events.push({ error: error.message, terminal }),
    schedule: (callback, delay) => { assert.equal(delay, 5000); timers.set(++id, callback); return id; },
    cancel: key => timers.delete(key),
  });
  return { poller, timers, events, async next() {
    assert.equal(timers.size, 1);
    const [key, callback] = timers.entries().next().value;
    timers.delete(key); await callback(); await settle();
  } };
}

test("polls every five seconds until ready and stops", async () => {
  assert.equal(POLL_INTERVAL_MS, 5000);
  const states = ["queued", "processing", "ready"];
  const h = harness(async () => ({ status: states.shift() }));
  h.poller.start(); await settle(); await h.next(); await h.next();
  assert.deepEqual(h.events, ["queued", "processing", "ready"]);
  assert.equal(h.timers.size, 0);
});

test("does not overlap a pending request and ignores its response after stopping", async () => {
  let resolve, calls = 0;
  const h = harness(() => { calls++; return new Promise(done => { resolve = done; }); });
  h.poller.start(); h.poller.start();
  assert.equal(calls, 1); assert.equal(h.timers.size, 0);
  h.poller.stop(); resolve({ status: "ready" }); await settle();
  assert.deepEqual(h.events, []); assert.equal(h.timers.size, 0);
});

test("failed processing and HTTP 404/410 stop automatic retries", async () => {
  for (const state of ["failed", "expired"]) {
    const h = harness(async () => ({ status: state }));
    h.poller.start(); await settle();
    assert.deepEqual(h.events, [state]); assert.equal(h.timers.size, 0);
  }
  for (const status of [404, 410]) {
    const h = harness(async () => { throw Object.assign(new Error("gone"), { status }); });
    h.poller.start(); await settle();
    assert.equal(h.events[0].terminal, true); assert.equal(h.timers.size, 0);
  }
});

test("temporary failures retry, stop after three failures and allow manual retry", async () => {
  let failing = true;
  const h = harness(async () => { if (failing) throw new Error("offline"); return { status: "ready" }; });
  h.poller.start(); await settle(); await h.next(); await h.next();
  assert.deepEqual(h.events.map(event => event.terminal), [false, false, true]);
  assert.equal(h.timers.size, 0);
  failing = false; h.poller.start(); await settle();
  assert.equal(h.events.at(-1), "ready"); assert.equal(h.timers.size, 0);
});

test("a failed report fetch retries and never starts parallel status polling", async () => {
  let attempts = 0, resolve;
  const h = harness(async () => ({ status: "ready" }), async () => {
    attempts++;
    if (attempts === 1) throw new Error("report unavailable");
    await new Promise(done => { resolve = done; });
  });
  h.poller.start(); await settle();
  assert.equal(h.events[1].terminal, false);
  const next = h.next(); await settle();
  assert.equal(h.timers.size, 0);
  resolve(); await next; assert.equal(attempts, 2); assert.equal(h.timers.size, 0);
});
