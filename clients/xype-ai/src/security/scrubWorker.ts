// Scrubbing in a worker thread with a hard limit (ADR-0019, slice 11). The scrubber's own
// budget is checked only after a rule returns, so a rule that backtracks badly would block
// forever; here the thread is terminated past the hard limit and the file is excluded,
// never sent unscrubbed. Files are scrubbed one at a time; a new thread starts on demand.

import { Worker } from 'node:worker_threads';

import { parseRules, type ScrubbedText, scrubBudgetMs, type ScrubRules } from './scrubber';

/** The hard limit is this many times the scrubber's own budget, and at least the minimum. */
export const HARD_LIMIT_FACTOR = 2;
export const HARD_LIMIT_MIN_MS = 1000;

export type WorkerScrub =
  | {
      readonly kind: 'scrubbed';
      readonly text: ScrubbedText;
      readonly findings: ReadonlyMap<string, number>;
    }
  | { readonly kind: 'excluded'; readonly reason: 'scrub_timeout' };

export class ScrubWorkerError extends Error {
  override name = 'ScrubWorkerError';
}

type Reply =
  | { kind: 'ready' | 'timeout' | 'error' }
  | { kind: 'scrubbed'; text: string; findings: Map<string, number> };

const EXCLUDED: WorkerScrub = { kind: 'excluded', reason: 'scrub_timeout' };
const THREAD = new URL('./scrubThread.ts', import.meta.url);

/** Waits for the thread's first message, so start-up never counts against a file's limit. */
function started(rulesData: unknown): Promise<Worker> {
  const worker = new Worker(THREAD, { workerData: rulesData });
  return new Promise((resolve, reject) => {
    const fail = () => {
      void worker.terminate();
      reject(new ScrubWorkerError('the scrub worker did not start'));
    };
    worker.once('message', (reply: Reply) => {
      worker.off('error', fail);
      worker.off('exit', fail);
      if (reply.kind === 'ready') resolve(worker);
      else fail();
    });
    worker.once('error', fail);
    worker.once('exit', fail);
  });
}

export class ScrubWorker {
  readonly #rulesData: unknown;
  readonly #rules: ScrubRules;
  #worker: Promise<Worker> | null = null;
  #queue: Promise<unknown> = Promise.resolve();

  /** `rulesData` is the parsed `rules.json`; it is checked here before any thread starts. */
  constructor(rulesData: unknown) {
    this.#rules = parseRules(rulesData);
    this.#rulesData = rulesData;
  }

  /** The scrubbed text, or `excluded` when it ran past the scrubber's budget or the hard limit. */
  scrub(text: string): Promise<WorkerScrub> {
    const run = this.#queue.then(() => this.#run(text));
    this.#queue = run.catch(() => undefined);
    return run;
  }

  async close(): Promise<void> {
    const worker = this.#worker;
    this.#worker = null;
    if (worker !== null) await (await worker).terminate();
  }

  async #run(text: string): Promise<WorkerScrub> {
    this.#worker ??= started(this.#rulesData);
    const worker = await this.#worker.catch((error: unknown) => {
      this.#worker = null;
      throw error;
    });
    const limit = Math.max(scrubBudgetMs(this.#rules, text) * HARD_LIMIT_FACTOR, HARD_LIMIT_MIN_MS);
    return new Promise((resolve, reject) => {
      const done = () => {
        clearTimeout(timer);
        worker.off('message', onReply);
        worker.off('error', onStop);
        worker.off('exit', onStop);
      };
      const onReply = (reply: Reply) => {
        done();
        if (reply.kind === 'scrubbed') {
          // The thread ran `SecretScrubber.scrub`: this is its output.
          resolve({ kind: 'scrubbed', text: reply.text as ScrubbedText, findings: reply.findings });
        } else if (reply.kind === 'timeout') resolve(EXCLUDED);
        else reject(new ScrubWorkerError('scrubbing failed'));
      };
      const onStop = () => {
        done();
        this.#discard(worker);
        reject(new ScrubWorkerError('the scrub worker stopped'));
      };
      const timer = setTimeout(() => {
        done();
        this.#discard(worker);
        resolve(EXCLUDED);
      }, limit);
      worker.on('message', onReply);
      worker.once('error', onStop);
      worker.once('exit', onStop);
      worker.postMessage(text);
    });
  }

  /** A thread stuck in a regex is terminated; the next file starts a new one. */
  #discard(worker: Worker): void {
    this.#worker = null;
    void worker.terminate();
  }
}
