// The sync worker thread's entry (slice 11c), the engine's composition root: it builds the
// real adapters from `workerData` (a `SyncConfig`) and talks to the extension host through
// `parentPort` (`protocol.ts`). Bundled to `dist/syncWorker.cjs`.

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { type MessagePort, parentPort, workerData } from 'node:worker_threads';

import { loadParsers } from '@/context/chunker';
import { parsePathRules } from '@/security/paths';
import { ScrubWorker } from '@/security/scrubWorker';
import { AuthApi } from '@/session/api';
import { systemClock } from '@/session/clock';
import { type ClientCredentials, CredentialsMissingError, TokenSource } from '@/session/tokens';
import { grpcTransport, sessionTransport } from '@/session/transport';

import { SyncEngine } from './engine';
import type { EngineMessage, HostMessage, SyncConfig } from './protocol';

function portOf(): MessagePort {
  if (parentPort === null) throw new Error('the sync worker runs only as a worker thread');
  return parentPort;
}

const port = portOf();
const config = workerData as SyncConfig;
const post = (message: EngineMessage) => {
  port.postMessage(message);
};

/** Asks the host for the stored credentials on each login; nothing is kept here. */
function credentialSource(): () => Promise<ClientCredentials> {
  let next = 0;
  const waiting = new Map<number, (credentials: ClientCredentials | null) => void>();
  port.on('message', (message: HostMessage) => {
    if (message.kind !== 'credentials') return;
    waiting.get(message.requestId)?.(message.credentials);
    waiting.delete(message.requestId);
  });
  return () =>
    new Promise((resolve, reject) => {
      const requestId = (next += 1);
      waiting.set(requestId, (credentials) => {
        if (credentials === null) reject(new CredentialsMissingError('not signed in'));
        else resolve(credentials);
      });
      post({ kind: 'credentials_needed', requestId });
    });
}

async function main(): Promise<void> {
  const rulesData: unknown = JSON.parse(readFileSync(join(config.assetsDir, 'rules.json'), 'utf8'));
  const scrubber = new ScrubWorker(
    rulesData,
    pathToFileURL(join(config.assetsDir, 'scrubThread.cjs')),
  );
  const { parsers, failed } = await loadParsers(
    join(config.assetsDir, 'grammars'),
    config.assetsDir,
  );
  if (failed.length > 0) post({ kind: 'grammars_failed', languages: failed });
  const endpoint = { baseUrl: config.serverUrl, ...(config.ca === null ? {} : { ca: config.ca }) };
  const api = new AuthApi(endpoint);
  const engine = new SyncEngine({
    repositories: config.repositories,
    clientVersion: config.clientVersion,
    workspaceId: config.workspaceId,
    api,
    tokens: new TokenSource(api, credentialSource()),
    transport: sessionTransport(grpcTransport(endpoint)),
    clock: systemClock,
    pathRules: parsePathRules(rulesData),
    parsers,
    scrub: (text) => scrubber.scrub(text),
    post,
  });
  port.on('message', (message: HostMessage) => {
    if (message.kind === 'changed') engine.changed();
    if (message.kind === 'stop') void engine.stop();
  });
  try {
    post({ kind: 'stopped', reason: await engine.run() });
  } finally {
    await scrubber.close();
  }
}

// The reason only: an error's message may name a path (rule 08). Anything thrown outside
// `main` ends the thread the same way, never reaching the host's log.
const failed = () => {
  post({ kind: 'stopped', reason: 'failed' });
  process.exit(1);
};
process.on('uncaughtException', failed);
process.on('unhandledRejection', failed);
main().catch(failed);
