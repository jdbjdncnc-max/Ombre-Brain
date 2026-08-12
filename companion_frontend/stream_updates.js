export function createStreamUpdateCoordinator({
  onRender,
  onPersist,
  renderDelayMs = 80,
  persistDelayMs = 1200,
  setTimer = setTimeout,
  clearTimer = clearTimeout
}) {
  let latestValue;
  let renderTimer = null;
  let persistTimer = null;

  function schedule(value) {
    latestValue = value;
    if (renderTimer === null) {
      renderTimer = setTimer(() => {
        renderTimer = null;
        onRender(latestValue);
      }, renderDelayMs);
    }
    if (persistTimer === null) {
      persistTimer = setTimer(() => {
        persistTimer = null;
        onPersist(latestValue);
      }, persistDelayMs);
    }
  }

  function flush(value = latestValue) {
    latestValue = value;
    if (renderTimer !== null) {
      clearTimer(renderTimer);
      renderTimer = null;
    }
    if (persistTimer !== null) {
      clearTimer(persistTimer);
      persistTimer = null;
    }
    if (latestValue !== undefined) {
      onRender(latestValue);
      onPersist(latestValue);
    }
  }

  function cancel() {
    if (renderTimer !== null) {
      clearTimer(renderTimer);
      renderTimer = null;
    }
    if (persistTimer !== null) {
      clearTimer(persistTimer);
      persistTimer = null;
    }
    latestValue = undefined;
  }

  return { schedule, flush, cancel };
}
