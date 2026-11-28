// The extension's sync worker without VS Code, for the end-to-end test (Phase 2 slice 12,
// server/tests/e2e/, docs/runbooks/e2e.md). It starts the bundled `dist/syncWorker.cjs`
// exactly as the extension host does, answers its credential requests from a file, and
// relays the engine's messages (ids, counts and fixed reasons only) to stdout as JSON
// lines. Commands come on stdin, one per line: `changed` (a file was saved) and `stop`.
//
//   node scripts/e2e-client.mjs --server https://127.0.0.1:18443 --ca <ca.crt> \
//     --credentials <file: {"clientId", "clientSecret"}> --repository <uuid> --root <dir>

import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { Worker } from 'node:worker_threads';

const dist = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist');
const { values } = parseArgs({
  options: {
    server: { type: 'string' },
    ca: { type: 'string' },
    credentials: { type: 'string' },
    repository: { type: 'string' },
    root: { type: 'string' },
  },
});

/** @param {string | undefined} value @param {string} name */
function required(value, name) {
  if (value === undefined || value === '') throw new Error(`--${name} is required`);
  return value;
}

/** @param {string} path */
function readCredentials(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    // V8's parse error quotes the input, which holds the client secret.
    throw new Error('the credentials file is not valid JSON');
  }
}

const credentials = readCredentials(required(values.credentials, 'credentials'));
const worker = new Worker(join(dist, 'syncWorker.cjs'), {
  workerData: {
    serverUrl: required(values.server, 'server'),
    ca: readFileSync(required(values.ca, 'ca'), 'utf8'),
    clientVersion: 'e2e',
    workspaceId: randomUUID(),
    repositories: [
      {
        id: required(values.repository, 'repository'),
        root: resolve(required(values.root, 'root')),
      },
    ],
    assetsDir: dist,
  },
});

/** @param {unknown} line */
function emit(line) {
  process.stdout.write(`${JSON.stringify(line)}\n`);
}

worker.on('message', (/** @type {{ kind: string, requestId?: number }} */ message) => {
  if (message.kind === 'credentials_needed') {
    worker.postMessage({ kind: 'credentials', requestId: message.requestId, credentials });
    emit({ kind: 'credentials_sent' });
    return;
  }
  emit(message);
  if (message.kind === 'stopped') void worker.terminate();
});
worker.on('error', () => {
  // The message may name a path (rule 08): only the fact is reported.
  emit({ kind: 'worker_error' });
  process.exitCode = 1;
});
worker.on('exit', () => {
  process.exit();
});

createInterface({ input: process.stdin }).on('line', (/** @type {string} */ line) => {
  const command = line.trim();
  if (command === 'changed' || command === 'stop') worker.postMessage({ kind: command });
});
