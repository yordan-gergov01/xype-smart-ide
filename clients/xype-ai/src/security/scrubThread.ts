// The scrub worker thread (ADR-0019, slice 11): runs `SecretScrubber` off the extension
// host's thread, one file at a time, so a regex that backtracks badly can be stopped by
// terminating the thread. Loaded by Node directly (type stripping), so its imports name
// their `.ts` files and use no `@/` alias. Replies never carry an error's message.

import { parentPort, workerData } from 'node:worker_threads';

import { parseRules, ScrubTimeoutError, SecretScrubber } from './scrubber.ts';

const port = parentPort;
if (port === null) throw new Error('scrubThread runs only as a worker thread');
const scrubber = new SecretScrubber(parseRules(workerData));

port.on('message', (text: string) => {
  try {
    const { text: scrubbed, findings } = scrubber.scrub(text);
    port.postMessage({ kind: 'scrubbed', text: scrubbed, findings });
  } catch (error) {
    port.postMessage({ kind: error instanceof ScrubTimeoutError ? 'timeout' : 'error' });
  }
});
port.postMessage({ kind: 'ready' });
