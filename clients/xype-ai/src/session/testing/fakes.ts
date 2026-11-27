// Fakes for the session's seams (rule 03), used by its tests: the server's half of the
// key exchange, a scripted `SessionService` on Connect's in-memory router, token calls
// that count, and a clock that never waits. Never wired into the extension.

import { createPublicKey, diffieHellman, generateKeyPairSync, randomBytes } from 'node:crypto';

import { type ConnectRouter, createRouterTransport } from '@connectrpc/connect';

import {
  type SessionRequest,
  type SessionResponse,
  SessionService,
} from '@/gen/xype/v1/session_pb';
import { aesGcmSeal } from '@/security/aead';
import { frame } from '@/security/framing';
import { hkdf } from '@/security/keys';

import { ApiError, type KeyApi, type MintedKey, type TokenApi, type Tokens } from '@/session/api';
import type { Clock } from '@/session/clock';
import { sessionTransport, type SessionTransport } from '@/session/transport';

const SPKI_PREFIX = Buffer.from('302a300506032b656e032100', 'hex');

function seal(key: Uint8Array, plaintext: Uint8Array, aad: Uint8Array): Uint8Array {
  const nonce = new Uint8Array(randomBytes(12));
  return new Uint8Array(Buffer.concat([nonce, aesGcmSeal(key, nonce, plaintext, aad)]));
}

/** Mints keys as the server does (ADR-0015) and keeps a copy to open payloads with. */
export class FakeKeyServer implements KeyApi {
  readonly userId = 'user-1';
  readonly sessionKeys = new Map<string, Uint8Array>();
  readonly repositoryKey = new Uint8Array(32).fill(7);
  minted = 0;

  me(): Promise<{ userId: string; teamId: string }> {
    return Promise.resolve({ userId: this.userId, teamId: 'team-1' });
  }

  sessionKey(
    _token: string,
    clientPublic: Uint8Array,
    repositoryIds: readonly string[],
  ): Promise<MintedKey> {
    this.minted += 1;
    const keyId = `key-${String(this.minted)}`;
    const server = generateKeyPairSync('x25519');
    const serverPublic = new Uint8Array(
      server.publicKey.export({ format: 'der', type: 'spki' }).subarray(SPKI_PREFIX.length),
    );
    const client = createPublicKey({
      key: Buffer.concat([SPKI_PREFIX, clientPublic]),
      format: 'der',
      type: 'spki',
    });
    const shared = diffieHellman({ privateKey: server.privateKey, publicKey: client });
    const info = frame('xype session-key v1', clientPublic, serverPublic, this.userId);
    const wrapping = hkdf(shared, info, new TextEncoder().encode(keyId));
    const sessionKey = new Uint8Array(randomBytes(32));
    this.sessionKeys.set(keyId, sessionKey.slice());
    return Promise.resolve({
      keyId,
      serverPublicKey: serverPublic,
      wrappedKey: seal(wrapping, sessionKey, frame('session', keyId)),
      repositoryKeys: repositoryIds.map((repositoryId) => ({
        repositoryId,
        hmacKeyId: 'hk-1',
        wrappedKey: seal(wrapping, this.repositoryKey, frame('repo-hmac', repositoryId, 'hk-1')),
      })),
    });
  }
}

export class FakeTokenApi implements TokenApi {
  logins = 0;
  refreshes = 0;
  failRefresh = false;
  rejectLogin = false;

  token(): Promise<Tokens> {
    this.logins += 1;
    if (this.rejectLogin) return Promise.reject(new ApiError(401, true));
    return Promise.resolve(this.#tokens(`login-${String(this.logins)}`));
  }

  refresh(): Promise<Tokens> {
    this.refreshes += 1;
    if (this.failRefresh) return Promise.reject(new Error('lost'));
    return Promise.resolve(this.#tokens(`refresh-${String(this.refreshes)}`));
  }

  #tokens(name: string): Tokens {
    return {
      accessToken: `access-${name}`,
      refreshToken: `refresh-token-${name}`,
      expiresInS: 900,
    };
  }
}

/** What the scripted server answers to one request; `close` ends the call. */
export type Script = (
  request: SessionRequest,
  call: number,
) => readonly SessionResponse[] | 'close';

export interface ScriptedService {
  /** Registers the scripted service on a Connect router (in memory or over HTTP/2). */
  readonly routes: (router: ConnectRouter) => void;
  /** Every request, per call. */
  readonly calls: SessionRequest[][];
  /** Each call's `authorization` metadata. */
  readonly tokens: string[];
}

/** A `SessionService` that answers by script and records every request per call. */
export function scriptedService(script: Script): ScriptedService {
  const calls: SessionRequest[][] = [];
  const tokens: string[] = [];
  const routes = (router: ConnectRouter) => {
    router.service(SessionService, {
      async *session(requests, context) {
        const call = calls.length;
        const seen: SessionRequest[] = [];
        calls.push(seen);
        tokens.push(context.requestHeader.get('authorization') ?? '');
        for await (const request of requests) {
          seen.push(request);
          const out = script(request, call);
          if (out === 'close') return;
          yield* out;
        }
      },
    });
  };
  return { routes, calls, tokens };
}

/** The scripted service on Connect's in-memory router. */
export function scriptedServer(script: Script): ScriptedService & { transport: SessionTransport } {
  const service = scriptedService(script);
  return { ...service, transport: sessionTransport(createRouterTransport(service.routes)) };
}

/** Sleeps return at once and are recorded; intervals tick only when the test says so. */
export class FakeClock implements Clock {
  readonly sleeps: number[] = [];
  time = 0;

  now(): number {
    return this.time;
  }
  readonly intervals: { ms: number; tick: () => void; active: boolean }[] = [];

  sleep(ms: number): Promise<void> {
    this.sleeps.push(ms);
    this.time += ms;
    return Promise.resolve();
  }

  every(ms: number, tick: () => void): () => void {
    const interval = { ms, tick, active: true };
    this.intervals.push(interval);
    return () => {
      interval.active = false;
    };
  }

  random(): number {
    return 0.5;
  }
}

/** Sleeps wait until the test advances the time past them, or their signal aborts. */
export class ManualClock implements Clock {
  time = 0;
  readonly #sleepers: { until: number; wake: () => void }[] = [];
  readonly #intervals: { next: number; ms: number; tick: () => void; active: boolean }[] = [];

  now(): number {
    return this.time;
  }

  sleep(ms: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve) => {
      const sleeper = { until: this.time + ms, wake: resolve };
      this.#sleepers.push(sleeper);
      signal.addEventListener('abort', () => {
        resolve();
      });
    });
  }

  every(ms: number, tick: () => void): () => void {
    const interval = { next: this.time + ms, ms, tick, active: true };
    this.#intervals.push(interval);
    return () => {
      interval.active = false;
    };
  }

  random(): number {
    return 0.5;
  }

  /** Moves the time on, waking sleepers and firing intervals that fall due. */
  async advance(ms: number): Promise<void> {
    this.time += ms;
    for (const interval of this.#intervals) {
      while (interval.active && interval.next <= this.time) {
        interval.next += interval.ms;
        interval.tick();
      }
    }
    for (const sleeper of this.#sleepers.filter((s) => s.until <= this.time)) {
      this.#sleepers.splice(this.#sleepers.indexOf(sleeper), 1);
      sleeper.wake();
    }
    // Let woken work run before the test looks.
    for (let i = 0; i < 20; i++) await Promise.resolve();
  }
}
