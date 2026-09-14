export const POLL_INTERVAL_MS = 5000;

// A new timer is scheduled only after the previous request/render finishes.
export function createResultPoller({ getStatus, onReady, onState, onError,
  schedule = setTimeout, cancel = clearTimeout }) {
  let timer;
  let stopped = true;
  let running = false;
  let generation = 0;
  let failures = 0;
  const stop = () => { stopped = true; generation += 1; cancel(timer); };
  const tick = async () => {
    if (stopped || running) return;
    running = true;
    const current = generation;
    try {
      const status = await getStatus();
      if (stopped || current !== generation) return;
      if (status.status === "ready") {
        await onReady(status);
        if (current === generation) stop();
      } else if (["failed", "expired"].includes(status.status)) {
        onState(status);
        stop();
      } else if (["uploading", "queued", "processing"].includes(status.status)) {
        failures = 0;
        onState(status);
      } else {
        throw new Error("Сервер вернул неизвестный статус.");
      }
    } catch (error) {
      if (stopped || current !== generation) return;
      failures += 1;
      const terminal = [400, 403, 404, 410].includes(error.status) || failures >= 3;
      onError(error, terminal);
      if (terminal) stop();
    } finally {
      running = false;
      if (!stopped) timer = schedule(tick, POLL_INTERVAL_MS);
    }
  };
  return {
    start() { if (!stopped) return; stopped = false; failures = 0; generation += 1; tick(); },
    stop,
  };
}
