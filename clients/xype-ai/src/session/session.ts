// The extension's side of the `Session` stream (ADR-0013, slice 11b). One stream at a
// time, each with its own session key and `ClientCryptoBox` (counters restart at 1 and
// the key is bound to one stream). It answers the Merkle sync for its repositories and
// reconnects:
// - on `TokenRefreshRequired` or an expired token: renew the token, reopen at once;
// - on `session key unknown, expired or not yours`: mint one new key; a second refusal
//   in a row stops the session (more windows than the 8-key cap, ADR-0015);
// - on `CODE_UNAVAILABLE`, a failed call or a close: exponential backoff with jitter,
//   at least the server's `retry_after_ms`.
// A refused tree report is answered with a new root report, so the server starts over.

import { Code, ConnectError } from '@connectrpc/connect';

import { type SessionResponse, type StreamError, StreamError_Code } from '@/gen/xype/v1/session_pb';
import type { MerkleDeltaMessage, MerkleSyncRequestMessage } from '@/sync/messages';
import type { MerkleResponder } from '@/sync/responder';

import { ApiError, type KeyApi } from './api';
import type { Clock } from './clock';
import { mintSessionKey, type SessionKeys } from './mint';
import { CredentialsMissingError, type TokenSource } from './tokens';
import type { SessionStream, SessionTransport } from './transport';
import {
  chunkUploadRequest,
  deltaOf,
  heartbeatRequest,
  helloRequest,
  rootReportRequest,
  syncRequestOf,
  treeReportRequest,
} from './wire';

/** The server's fixed refusal for a key it does not know (`KEY_UNUSABLE` in
 * `server/backend/services/session/gate.py`); a change there must change this. */
export const KEY_UNUSABLE = 'session key unknown, expired or not yours';
export const BACKOFF_BASE_MS = 1000;
export const BACKOFF_MAX_MS = 60_000;

/**
 * Why the session ended. All but `stopped` need the user: no stored credentials, more
 * windows than the key cap,
 * client credentials the server refuses, or a Hello it refuses (a repository the team no
 * longer has, or too many).
 */
export type StopReason =
  'stopped' | 'signed_out' | 'too_many_windows' | 'credentials_rejected' | 'hello_refused';

export interface SessionOptions {
  readonly api: KeyApi;
  readonly tokens: TokenSource;
  readonly transport: SessionTransport;
  readonly clock: Clock;
  readonly clientVersion: string;
  readonly workspaceId: string;
  readonly repositories: ReadonlyMap<string, MerkleResponder>;
  /** Each new key's repository HMAC keys; the receiver owns and wipes them. */
  readonly onRepositoryKeys?: (keys: ReadonlyMap<string, Uint8Array>) => void;
}

type Outcome =
  | { readonly kind: 'refresh' }
  | { readonly kind: 'key_unusable' }
  | { readonly kind: 'hello_refused' }
  | { readonly kind: 'signed_out' }
  | { readonly kind: 'backoff'; readonly retryAfterMs: number }
  | { readonly kind: 'stopped' };

const BACKOFF: Outcome = { kind: 'backoff', retryAfterMs: 0 };

/** Outcomes that end the session at once. */
const STOPS: Partial<Record<Outcome['kind'], StopReason>> = {
  stopped: 'stopped',
  hello_refused: 'hello_refused',
  signed_out: 'signed_out',
};

/** What a `StreamError` means for this stream; null keeps it open. */
function errorOutcome(error: StreamError): Outcome | null {
  switch (error.code) {
    case StreamError_Code.UNAUTHENTICATED:
      return { kind: 'refresh' };
    case StreamError_Code.INVALID_REQUEST:
      return error.message === KEY_UNUSABLE ? { kind: 'key_unusable' } : null;
    case StreamError_Code.UNAVAILABLE:
    case StreamError_Code.INTERNAL:
    case StreamError_Code.TIMEOUT:
      return { kind: 'backoff', retryAfterMs: error.retryAfterMs };
    default:
      return null;
  }
}

/** A failed call or REST request: an expired token is renewed, anything else backs off. */
function failureOutcome(error: unknown): Outcome {
  if (error instanceof CredentialsMissingError) return { kind: 'signed_out' };
  const unauthenticated =
    (error instanceof ConnectError && error.code === Code.Unauthenticated) ||
    (error instanceof ApiError && error.status === 401);
  return unauthenticated ? { kind: 'refresh' } : BACKOFF;
}

/** Drops the remembered tree reports of one repository. */
function forget(reports: Map<string, string>, repositoryId: string): void {
  for (const [messageId, repository] of reports) {
    if (repository === repositoryId) reports.delete(messageId);
  }
}

/** One open stream: its key, what it sent, and whether Hello was accepted. */
interface Live {
  readonly stream: SessionStream;
  readonly keys: SessionKeys;
  /** Message id of each tree report → its repository, to answer a refusal. */
  readonly reports: Map<string, string>;
  accepted: boolean;
  stopHeartbeat: () => void;
}

export class SessionClient {
  readonly #options: SessionOptions;
  readonly #abort = new AbortController();
  #live: Live | null = null;
  #userId: string | null = null;
  #attempt = 0;
  #running: Promise<StopReason> | null = null;

  constructor(options: SessionOptions) {
    this.#options = options;
  }

  /** Runs until `stop()` or a stop the user must see; resolves with why it ended. */
  start(): Promise<StopReason> {
    this.#running ??= this.#run();
    return this.#running;
  }

  async stop(): Promise<void> {
    this.#abort.abort();
    this.#live?.stream.close();
    await this.#running;
  }

  /** After a repository's tree was (re)built: the server compares roots and syncs. */
  reportRoot(repositoryId: string): void {
    const live = this.#live;
    const report = this.#options.repositories.get(repositoryId)?.rootReport();
    if (live?.accepted === true && report != null) live.stream.send(rootReportRequest(report));
  }

  async #run(): Promise<StopReason> {
    let previous: Outcome['kind'] | null = null;
    // Reopens without a wait (a new key, a renewed token) in a row: from the second on,
    // whatever their kind, they wait like any failure, so a misbehaving server cannot
    // make the client mint keys in a loop.
    let immediate = 0;
    while (!this.#stopped()) {
      const outcome = await this.#connect();
      const repeated = previous === outcome.kind;
      previous = outcome.kind;
      const stop = STOPS[outcome.kind];
      if (stop !== undefined) return stop;
      switch (outcome.kind) {
        case 'key_unusable':
          if (repeated) return 'too_many_windows';
          if (immediate++ > 0) await this.#backoff(0);
          break;
        case 'refresh': {
          if (immediate++ > 0) await this.#backoff(0);
          const renewed = await this.#renew();
          if (renewed !== null) return renewed;
          break;
        }
        case 'backoff':
          immediate = 0;
          await this.#backoff(outcome.retryAfterMs);
          break;
        default:
          return 'stopped';
      }
    }
    return 'stopped';
  }

  /** Null when renewed, or stopped for a transient failure; credentials the server refuses stop. */
  async #renew(): Promise<StopReason | null> {
    try {
      await this.#options.tokens.renew();
    } catch (error) {
      if (error instanceof CredentialsMissingError) return 'signed_out';
      if (error instanceof ApiError && [400, 401, 403].includes(error.status)) {
        return 'credentials_rejected';
      }
      await this.#backoff(0);
    }
    return null;
  }

  async #backoff(retryAfterMs: number): Promise<void> {
    const ceiling = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** this.#attempt);
    this.#attempt = Math.min(this.#attempt + 1, 16);
    const jittered = ceiling * (0.5 + this.#options.clock.random() / 2);
    await this.#options.clock.sleep(Math.max(jittered, retryAfterMs), this.#abort.signal);
  }

  async #connect(): Promise<Outcome> {
    let keys: SessionKeys;
    let access: string;
    try {
      ({ keys, access } = await this.#mint());
    } catch (error) {
      return failureOutcome(error);
    }
    if (this.#stopped()) {
      keys.box.wipe();
      return { kind: 'stopped' };
    }
    let stream: SessionStream;
    try {
      stream = this.#options.transport.open(access);
    } catch (error) {
      keys.box.wipe();
      return failureOutcome(error);
    }
    return this.#serve(stream, keys);
  }

  async #serve(stream: SessionStream, keys: SessionKeys): Promise<Outcome> {
    const live: Live = {
      stream,
      keys,
      reports: new Map(),
      accepted: false,
      stopHeartbeat: () => undefined,
    };
    this.#live = live;
    try {
      stream.send(this.#hello(keys));
      for await (const response of stream.responses) {
        const outcome = this.#handle(live, response);
        if (outcome !== null) return outcome;
      }
      return this.#stopped() ? { kind: 'stopped' } : BACKOFF;
    } catch (error) {
      return this.#stopped() ? { kind: 'stopped' } : failureOutcome(error);
    } finally {
      this.#live = null;
      live.stopHeartbeat();
      stream.close();
      keys.box.wipe();
    }
  }

  /** A method, not a property read: the signal changes while a stream is open. */
  #stopped(): boolean {
    return this.#abort.signal.aborted;
  }

  async #mint(): Promise<{ keys: SessionKeys; access: string }> {
    const { api, tokens, repositories, onRepositoryKeys } = this.#options;
    const access = await tokens.access();
    this.#userId ??= (await api.me(access)).userId;
    const keys = await mintSessionKey(api, access, this.#userId, [...repositories.keys()]);
    onRepositoryKeys?.(keys.repositoryKeys);
    return { keys, access };
  }

  #hello(keys: SessionKeys) {
    const roots = new Map<string, string>();
    for (const [id, responder] of this.#options.repositories) {
      const report = responder.rootReport();
      if (report !== null) roots.set(id, report.rootHash);
    }
    return helloRequest({
      clientVersion: this.#options.clientVersion,
      workspaceId: this.#options.workspaceId,
      sessionKeyId: keys.box.keyId,
      repositoryIds: [...this.#options.repositories.keys()],
      repositoryRoots: roots,
    });
  }

  #handle(live: Live, response: SessionResponse): Outcome | null {
    const body = response.body;
    switch (body.case) {
      case 'helloAck':
        this.#accepted(live, body.value.heartbeatIntervalS);
        return null;
      case 'tokenRefreshRequired':
        return { kind: 'refresh' };
      case 'merkleSyncRequest':
        this.#answerSync(live, syncRequestOf(body.value));
        return null;
      case 'merkleDelta':
        this.#answerDelta(live, deltaOf(body.value));
        return null;
      case 'error':
        return this.#refused(live, body.value, response.envelope?.correlationId ?? '');
      default:
        return null;
    }
  }

  /** Hello was accepted: heartbeats start. */
  #accepted(live: Live, heartbeatIntervalS: number): void {
    live.accepted = true;
    let seq = 0n;
    const intervalMs = Math.max(heartbeatIntervalS, 1) * 1000;
    live.stopHeartbeat = this.#options.clock.every(intervalMs, () => {
      // Only a stream that stayed up one interval resets the backoff: a server that
      // accepts Hello and drops the stream must not get a new key every second.
      this.#attempt = 0;
      live.stream.send(heartbeatRequest((seq += 1n)));
    });
  }

  #answerDelta(live: Live, delta: MerkleDeltaMessage): void {
    const responder = this.#options.repositories.get(delta.repositoryId);
    for (const upload of responder?.uploads(live.keys.box, delta) ?? []) {
      live.stream.send(chunkUploadRequest(upload));
    }
  }

  #answerSync(live: Live, request: MerkleSyncRequestMessage): void {
    // Only the latest level of a repository's sync can be refused.
    forget(live.reports, request.repositoryId);
    const responder = this.#options.repositories.get(request.repositoryId);
    for (const report of responder?.treeReports(request) ?? []) {
      const message = treeReportRequest(report);
      live.reports.set(message.envelope?.messageId ?? '', request.repositoryId);
      live.stream.send(message);
    }
  }

  /**
   * A refusal before Hello was accepted ends the stream, and the same Hello would be
   * refused again: stop. A refused tree report: report the root again, so the server
   * starts a new sync. Other refusals (an upload) are left to the server's re-request.
   */
  #refused(live: Live, error: StreamError, correlationId: string): Outcome | null {
    const outcome = errorOutcome(error);
    if (outcome === null && !live.accepted && error.code === StreamError_Code.INVALID_REQUEST) {
      return { kind: 'hello_refused' };
    }
    const repositoryId = live.reports.get(correlationId);
    if (outcome === null && repositoryId !== undefined) {
      forget(live.reports, repositoryId);
      const report = this.#options.repositories.get(repositoryId)?.rootReport();
      if (report != null) live.stream.send(rootReportRequest(report));
    }
    return outcome;
  }
}
