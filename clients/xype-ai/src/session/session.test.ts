// The session client against a scripted in-memory server: Hello, heartbeats, the Merkle
// sync, token renewal, the one key re-mint, backoff and the answer to a refused report.

import { create } from '@bufbuild/protobuf';
import { afterEach, describe, expect, it } from 'vitest';

import { planChunks } from '@/context/chunkPlan';
import { buildTree } from '@/context/merkle';
import { MerkleDeltaSchema, MerkleSyncRequestSchema } from '@/gen/xype/v1/index_pb';
import {
  HelloAckSchema,
  type SessionRequest,
  type SessionResponse,
  SessionResponseSchema,
  StreamError_Code,
  StreamErrorSchema,
  TokenRefreshRequiredSchema,
} from '@/gen/xype/v1/session_pb';
import { openPayload } from '@/security/cryptoBox';
import { directionKey } from '@/security/keys';
import type { ScrubbedText } from '@/security/scrubber';
import { CHUNKS_FIELD, MerkleResponder } from '@/sync/responder';

import {
  FakeClock,
  FakeKeyServer,
  FakeTokenApi,
  type Script,
  scriptedServer,
} from './testing/fakes';
import { KEY_UNUSABLE, SessionClient, type StopReason } from './session';
import { CredentialsMissingError, TokenSource } from './tokens';

const REPO = '0b9e6f1e-3f4a-4c7e-9a51-7d2c8e1f0a42';

type Body = SessionResponse['body'];
const response = (body: Body, correlationId = ''): SessionResponse =>
  create(SessionResponseSchema, { envelope: { messageId: 'm', correlationId }, body });
const helloAck = () =>
  response({ case: 'helloAck', value: create(HelloAckSchema, { heartbeatIntervalS: 20 }) });
const syncRequest = () =>
  response({
    case: 'merkleSyncRequest',
    value: create(MerkleSyncRequestSchema, { requestId: 'sync-1', repositoryId: REPO }),
  });
const streamError = (code: StreamError_Code, message: string, correlationId = '', retry = 0) =>
  response(
    { case: 'error', value: create(StreamErrorSchema, { code, message, retryAfterMs: retry }) },
    correlationId,
  );
const refreshRequired = () =>
  response({ case: 'tokenRefreshRequired', value: create(TokenRefreshRequiredSchema, {}) });

interface Harness {
  readonly session: SessionClient;
  readonly started: Promise<StopReason>;
  readonly keys: FakeKeyServer;
  readonly tokenApi: FakeTokenApi;
  readonly clock: FakeClock;
  readonly calls: SessionRequest[][];
  readonly tokens: string[];
  readonly done: Promise<void>;
  readonly responder: MerkleResponder;
}

let running: SessionClient | null = null;
afterEach(async () => {
  await running?.stop();
  running = null;
});

/** A session over one repository; the script calls `finish()` when the scenario is over. */
function harness(script: (finish: () => void) => Script, tokenSource?: TokenSource): Harness {
  const keys = new FakeKeyServer();
  const tokenApi = new FakeTokenApi();
  const clock = new FakeClock();
  const text = 'def f():\n    return 1\n' as ScrubbedText;
  const chunks = planChunks(
    'py',
    text,
    [{ kind: 'function', name: 'f', start: 0, end: 1, children: [] }],
    [],
  );
  const responder = new MerkleResponder(REPO);
  responder.setTree(buildTree(keys.repositoryKey, [{ path: 'app/a.py', chunks }]));
  let finish: () => void = () => undefined;
  const done = new Promise<void>((resolve) => (finish = resolve));
  const server = scriptedServer(script(finish));
  const session = new SessionClient({
    api: keys,
    tokens:
      tokenSource ??
      new TokenSource(tokenApi, () => Promise.resolve({ clientId: 'xc_1', clientSecret: 's' })),
    transport: server.transport,
    clock,
    clientVersion: '0.0.0',
    workspaceId: 'ws-1',
    repositories: new Map([[REPO, responder]]),
  });
  running = session;
  return { session, started: session.start(), keys, tokenApi, clock, ...server, done, responder };
}

function present<T>(value: T | undefined): T {
  if (value === undefined) throw new Error('missing');
  return value;
}

/** The server's view of the first uploaded chunk; its `aad` field stays empty. */
function serverOpen(keys: FakeKeyServer, sent: SessionRequest | undefined): Uint8Array {
  if (sent?.body.case !== 'chunkUpload') throw new Error('no upload');
  const chunk = present(sent.body.value.chunks[0]);
  const [ref, content, lines] = [
    present(chunk.ref),
    present(chunk.content),
    present(chunk.ref?.lines),
  ];
  expect(content.aad).toHaveLength(0);
  return openPayload(
    directionKey(present(keys.sessionKeys.get('key-1')), 'c2s'),
    content.nonce,
    content.ciphertext,
    {
      keyId: 'key-1',
      direction: 'c2s',
      messageId: present(sent.envelope).messageId,
      requestId: 'sync-1',
      fieldNumber: CHUNKS_FIELD,
      repositoryId: REPO,
      obfuscatedFileId: ref.obfuscatedFileId,
      chunkHash: ref.chunkHash,
      lineStart: lines.start,
      lineEnd: lines.end,
      leafNodeId: ref.leafNodeId,
    },
  );
}

const kinds = (requests: readonly SessionRequest[]) => requests.map((r) => r.body.case);

describe('SessionClient', () => {
  it('says Hello with its key and roots, then heartbeats at the announced interval', async () => {
    const h = harness((finish) => (request) => {
      if (request.body.case === 'heartbeat') finish();
      return request.body.case === 'hello' ? [helloAck()] : [];
    });
    await expect.poll(() => h.clock.intervals.length).toBe(1);
    expect(h.clock.intervals[0]?.ms).toBe(20_000);
    h.clock.intervals[0]?.tick();
    await h.done;
    const hello = h.calls[0]?.[0]?.body;
    expect(hello?.case).toBe('hello');
    if (hello?.case !== 'hello') return;
    expect(hello.value.sessionKeyId).toBe('key-1');
    expect(hello.value.repositoryIds).toEqual([REPO]);
    expect(hello.value.repositoryRoots).toEqual({ [REPO]: h.responder.rootReport()?.rootHash });
    expect(h.tokens[0]).toBe('Bearer access-login-1');
    await h.session.stop();
    expect(await h.started).toBe('stopped');
  });

  it('answers the sync and uploads what the delta asks for, sealed under its key', async () => {
    const h = harness((finish) => (request) => {
      switch (request.body.case) {
        case 'hello':
          return [helloAck(), syncRequest()];
        case 'merkleTreeReport': {
          const nodes = request.body.value.nodes;
          if (nodes.some((n) => !n.isLeaf)) {
            const ids = nodes.map((n) => n.nodeId);
            return [
              response({
                case: 'merkleSyncRequest',
                value: create(MerkleSyncRequestSchema, {
                  requestId: 'sync-1',
                  repositoryId: REPO,
                  nodeIds: ids,
                }),
              }),
            ];
          }
          const upload = nodes.map((n) => ({
            repositoryId: REPO,
            obfuscatedFileId: n.parentId,
            lines: n.lines,
            chunkHash: n.hash,
            leafNodeId: n.nodeId,
          }));
          return [
            response({
              case: 'merkleDelta',
              value: create(MerkleDeltaSchema, { requestId: 'sync-1', repositoryId: REPO, upload }),
            }),
          ];
        }
        case 'chunkUpload':
          finish();
          return [];
        default:
          return [];
      }
    });
    await h.done;
    const sent = h.calls[0]?.at(-1);
    const plain = serverOpen(h.keys, sent);
    expect(new TextDecoder().decode(plain)).toBe('def f():\n    return 1\n');
  });

  it('renews its token on TokenRefreshRequired and reopens at once with a new key', async () => {
    const h = harness((finish) => (request, call) => {
      if (request.body.case !== 'hello') return [];
      if (call === 0) return [helloAck(), refreshRequired()];
      finish();
      return [helloAck()];
    });
    await h.done;
    expect(h.tokens).toEqual(['Bearer access-login-1', 'Bearer access-refresh-1']);
    const second = h.calls[1]?.[0]?.body;
    expect(second?.case === 'hello' && second.value.sessionKeyId).toBe('key-2');
    expect(h.clock.sleeps).toEqual([]);
  });

  it('mints one new key when its key is refused, and stops on a second refusal', async () => {
    const h = harness(
      () => (request) =>
        request.body.case === 'hello'
          ? [streamError(StreamError_Code.INVALID_REQUEST, KEY_UNUSABLE)]
          : [],
    );
    expect(await h.started).toBe('too_many_windows');
    expect(h.keys.minted).toBe(2);
  });

  it('backs off with jitter, honours retry_after_ms, and keeps backing off past a quick drop', async () => {
    const h = harness((finish) => (request, call) => {
      if (request.body.case !== 'hello') return [];
      if (call === 0) return [streamError(StreamError_Code.UNAVAILABLE, 'unavailable', '', 5000)];
      if (call <= 2) return 'close';
      if (call === 3) return [helloAck(), streamError(StreamError_Code.UNAVAILABLE, 'unavailable')];
      finish();
      return [helloAck()];
    });
    await h.done;
    // Ceilings 1, 2, 4, 8 s at the jitter's midpoint; the first waits the server's 5 s. A
    // stream dropped right after Hello does not reset the backoff (no new key every second).
    expect(h.clock.sleeps).toEqual([5000, 1500, 3000, 6000]);
  });

  it('resets the backoff once a stream stayed up one heartbeat interval', async () => {
    const h = harness((finish) => (request, call) => {
      if (call === 0) return 'close';
      if (call === 2) finish();
      if (request.body.case === 'heartbeat')
        return [streamError(StreamError_Code.UNAVAILABLE, 'unavailable')];
      return request.body.case === 'hello' ? [helloAck()] : [];
    });
    await expect.poll(() => h.clock.intervals.length).toBe(1);
    h.clock.intervals[0]?.tick();
    await h.done;
    expect(h.clock.sleeps).toEqual([750, 750]);
  });

  it('stops when the server refuses the client credentials, without retrying in a loop', async () => {
    const h = harness(() => () => []);
    h.tokenApi.rejectLogin = true;
    expect(await h.started).toBe('credentials_rejected');
    expect(h.tokenApi.logins).toBeLessThanOrEqual(2);
  });

  it('stops as signed out when no credentials are stored, asking once', async () => {
    let asked = 0;
    const tokens = new TokenSource(new FakeTokenApi(), () => {
      asked += 1;
      return Promise.reject(new CredentialsMissingError('not signed in'));
    });
    const h = harness(() => () => [], tokens);
    expect(await h.started).toBe('signed_out');
    expect(asked).toBe(1);
  });

  it('stops when the server refuses its Hello', async () => {
    const h = harness(
      () => (request) =>
        request.body.case === 'hello'
          ? [streamError(StreamError_Code.INVALID_REQUEST, 'repository not accessible')]
          : [],
    );
    expect(await h.started).toBe('hello_refused');
  });

  it('waits before a second reopen without a wait, whatever its kind', async () => {
    const h = harness((finish) => (request, call) => {
      if (request.body.case !== 'hello') return [];
      if (call === 0) return [streamError(StreamError_Code.INVALID_REQUEST, KEY_UNUSABLE)];
      if (call === 1)
        return [streamError(StreamError_Code.UNAUTHENTICATED, 'access token expired')];
      finish();
      return [helloAck()];
    });
    await h.done;
    expect(h.clock.sleeps).toEqual([750]);
  });

  it('waits before renewing a second time in a row', async () => {
    const h = harness((finish) => (request, call) => {
      if (request.body.case !== 'hello') return [];
      if (call < 2) return [streamError(StreamError_Code.UNAUTHENTICATED, 'access token expired')];
      finish();
      return [helloAck()];
    });
    await h.done;
    expect(h.clock.sleeps).toEqual([750]);
  });

  it('reports its root again when the server refuses a tree report', async () => {
    const h = harness((finish) => (request) => {
      switch (request.body.case) {
        case 'hello':
          return [helloAck(), syncRequest()];
        case 'merkleTreeReport':
          return [
            streamError(
              StreamError_Code.INVALID_REQUEST,
              'malformed message',
              request.envelope?.messageId,
            ),
          ];
        case 'merkleRootReport':
          finish();
          return [];
        default:
          return [];
      }
    });
    await h.done;
    expect(kinds(h.calls[0] ?? [])).toEqual(['hello', 'merkleTreeReport', 'merkleRootReport']);
  });

  it('logs in again when a refresh fails, never reusing the spent refresh token', async () => {
    const h = harness((finish) => (request, call) => {
      if (request.body.case !== 'hello') return [];
      if (call === 0) return [helloAck(), refreshRequired()];
      finish();
      return [helloAck()];
    });
    h.tokenApi.failRefresh = true;
    await h.done;
    expect([h.tokenApi.refreshes, h.tokenApi.logins]).toEqual([1, 2]);
    expect(h.tokens[1]).toBe('Bearer access-login-2');
  });
});
