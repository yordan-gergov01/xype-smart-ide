// The session's time seam, so tests run reconnects and heartbeats without waiting.

export interface Clock {
  /** Resolves after `ms`, or early when `signal` aborts. */
  sleep(ms: number, signal: AbortSignal): Promise<void>;
  /** Calls `tick` every `ms` until the returned function is called. */
  every(ms: number, tick: () => void): () => void;
  /** In [0, 1): the backoff's jitter. */
  random(): number;
}

export const systemClock: Clock = {
  sleep: (ms, signal) =>
    new Promise((resolve) => {
      const timer = setTimeout(done, ms);
      function done() {
        clearTimeout(timer);
        signal.removeEventListener('abort', done);
        resolve();
      }
      signal.addEventListener('abort', done, { once: true });
    }),
  every: (ms, tick) => {
    const timer = setInterval(tick, ms);
    return () => {
      clearInterval(timer);
    };
  },
  random: () => Math.random(),
};
