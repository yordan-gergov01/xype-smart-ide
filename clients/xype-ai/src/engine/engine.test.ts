// The engine end to end on the client: a temporary repository, the real walker, scrubber,
// chunker and tree, and the session against an in-memory scripted server.

import { readFileSync } from 'node:fs';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { create } from '@bufbuild/protobuf';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  HelloAckSchema,
  type SessionRequest,
  SessionResponseSchema,
} from '@/gen/xype/v1/session_pb';
import { parsePathRules } from '@/security/paths';
import { parseRules, SecretScrubber } from '@/security/scrubber';
import {
  FakeKeyServer,
  FakeTokenApi,
  ManualClock,
  type Script,
  scriptedServer,
} from '@/session/testing/fakes';
import { TokenSource } from '@/session/tokens';

import { SyncEngine } from './engine';
import type { EngineMessage } from './protocol';
import { IDLE_MS } from './scheduler';

const REPO = '0b9e6f1e-3f4a-4c7e-9a51-7d2c8e1f0a42';
const RULES_DATA: unknown = JSON.parse(
  readFileSync(new URL('../../../../packages/scrub-rules/rules.json', import.meta.url), 'utf8'),
);
const scrubber = new SecretScrubber(parseRules(RULES_DATA));

let root: string;
let engine: SyncEngine | null = null;

beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), 'xype-engine-'));
  await writeFile(join(root, 'notes.txt'), 'hello\n');
});

afterEach(async () => {
  await engine?.stop();
  engine = null;
  await rm(root, { recursive: true, force: true });
});

const helloAck = () =>
  create(SessionResponseSchema, {
    body: { case: 'helloAck', value: create(HelloAckSchema, { heartbeatIntervalS: 20 }) },
  });

function start(script: Script) {
  const clock = new ManualClock();
  const server = scriptedServer(script);
  const posted: EngineMessage[] = [];
  const keys = new FakeKeyServer();
  engine = new SyncEngine({
    repositories: [{ id: REPO, root }],
    clientVersion: '0.0.0',
    workspaceId: 'ws-1',
    api: keys,
    tokens: new TokenSource(new FakeTokenApi(), () =>
      Promise.resolve({ clientId: 'xc_1', clientSecret: 's' }),
    ),
    transport: server.transport,
    clock,
    pathRules: parsePathRules(RULES_DATA),
    parsers: new Map(),
    scrub: (text) => Promise.resolve({ kind: 'scrubbed', ...scrubber.scrub(text) }),
    post: (message) => posted.push(message),
  });
  const running = engine.run();
  return { clock, server, posted, running };
}

const kinds = (requests: readonly SessionRequest[] | undefined) =>
  (requests ?? []).map((r) => r.body.case);

describe('SyncEngine', () => {
  it('indexes once the repository key arrives and the workspace is quiet, then reports its root', async () => {
    const { clock, server, posted, running } = start((request) =>
      request.body.case === 'hello' ? [helloAck()] : [],
    );
    await expect.poll(() => kinds(server.calls[0])).toEqual(['hello']);
    const hello = server.calls[0]?.[0]?.body;
    // The first Hello has no roots: trees need the key that comes with the first mint.
    expect(hello?.case === 'hello' && hello.value.repositoryRoots).toEqual({});
    await clock.advance(IDLE_MS);
    await expect.poll(() => kinds(server.calls[0])).toEqual(['hello', 'merkleRootReport']);
    expect(posted).toContainEqual({
      kind: 'indexed',
      stats: { files: 1, reused: 0, chunks: 1, excluded: {} },
    });
    await engine?.stop();
    expect(await running).toBe('stopped');
  });

  it('reports a failed walk without naming anything, and keeps running', async () => {
    const parent = root;
    root = join(parent, 'missing');
    const { clock, server, posted } = start((request) =>
      request.body.case === 'hello' ? [helloAck()] : [],
    );
    root = parent; // cleaned up after the test
    await expect.poll(() => kinds(server.calls[0])).toEqual(['hello']);
    await clock.advance(IDLE_MS);
    await expect.poll(() => posted).toContainEqual({ kind: 'index_failed' });
    expect(kinds(server.calls[0])).toEqual(['hello']);
  });

  it('does not report again after a change that left the tree as it was', async () => {
    const { clock, server } = start((request) =>
      request.body.case === 'hello' ? [helloAck()] : [],
    );
    await expect.poll(() => kinds(server.calls[0])).toEqual(['hello']);
    await clock.advance(IDLE_MS);
    await expect.poll(() => kinds(server.calls[0])).toContain('merkleRootReport');
    engine?.changed();
    await clock.advance(IDLE_MS);
    await writeFile(join(root, 'notes.txt'), 'changed\n');
    engine?.changed();
    await clock.advance(IDLE_MS);
    await expect
      .poll(() => kinds(server.calls[0]).filter((k) => k === 'merkleRootReport').length)
      .toBe(2);
  });
});
