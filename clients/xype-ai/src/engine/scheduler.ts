// When the sync engine works (rule 05, slice 11c): after the workspace has been quiet for
// longer than the idle delay, every 10 minutes for the consistency check, and never more
// than its share of one CPU core. Pure: time comes from the `Clock` seam.

import type { Clock } from '@/session/clock';

/** A change starts work only after this long without another change (> 2 s, rule 05). */
export const IDLE_MS = 2500;
/** The consistency check: a full walk even without a reported change. */
export const CHECK_INTERVAL_MS = 10 * 60 * 1000;
/** The share of one core the engine may use (10–15%, rule 05). */
export const CPU_SHARE = 0.12;

/**
 * Keeps work at `share` of one core: after work that took `workMs`, it waits long enough
 * that work / (work + wait) ≤ share.
 */
export class DutyCycle {
  readonly #clock: Clock;
  readonly #share: number;

  constructor(clock: Clock, share = CPU_SHARE) {
    this.#clock = clock;
    this.#share = share;
  }

  async pace(workMs: number, signal: AbortSignal): Promise<void> {
    if (workMs <= 0) return;
    await this.#clock.sleep((workMs * (1 - this.#share)) / this.#share, signal);
  }
}

/** Runs `work` after the idle delay and on the check interval; never two at once. */
export class IdleScheduler {
  readonly #clock: Clock;
  readonly #work: (signal: AbortSignal) => Promise<void>;
  readonly #onError: (error: unknown) => void;
  readonly #stop = new AbortController();
  #debounce: AbortController | null = null;
  #running: Promise<void> | null = null;
  #again = false;
  #stopCheck: () => void = () => undefined;

  /** `onError` hears a failed run; the next request runs again. */
  constructor(
    clock: Clock,
    work: (signal: AbortSignal) => Promise<void>,
    onError: (error: unknown) => void,
  ) {
    this.#clock = clock;
    this.#work = work;
    this.#onError = onError;
  }

  /** Starts the periodic check and runs once now (the first index). */
  start(): void {
    this.#stopCheck = this.#clock.every(CHECK_INTERVAL_MS, () => {
      this.#request();
    });
    this.#request();
  }

  /** Something in the workspace changed: work after the next quiet period. */
  changed(): void {
    this.#debounce?.abort();
    const debounce = new AbortController();
    this.#debounce = debounce;
    void this.#clock.sleep(IDLE_MS, debounce.signal).then(() => {
      if (!debounce.signal.aborted) this.#request();
    });
  }

  async stop(): Promise<void> {
    this.#stop.abort();
    this.#debounce?.abort();
    this.#stopCheck();
    await this.#running;
  }

  /** Coalesces requests: one more run after the current one, however many arrived. */
  #request(): void {
    if (this.#stop.signal.aborted) return;
    if (this.#running !== null) {
      this.#again = true;
      return;
    }
    this.#running = this.#loop().finally(() => {
      this.#running = null;
    });
  }

  async #loop(): Promise<void> {
    do {
      this.#again = false;
      try {
        await this.#work(this.#stop.signal);
      } catch (error) {
        this.#onError(error);
      }
    } while (this.#wantsAgain());
  }

  /** A method, not a field read: requests arrive while `work` runs. */
  #wantsAgain(): boolean {
    return this.#again && !this.#stop.signal.aborted;
  }
}
