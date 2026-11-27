// The sync engine (slice 11c): one session and one index per registered repository of the
// window, all in the sync worker. The repository HMAC keys arrive with each session key;
// a tree is (re)built when a key first arrives or changes, after the workspace was quiet,
// and on the 10-minute check. A new tree is reported to the server, which then syncs.

import { timingSafeEqual } from 'node:crypto';

import type { Parsers } from '@/context/chunker';
import type { PathRules } from '@/security/paths';
import type { WorkerScrub } from '@/security/scrubWorker';
import type { KeyApi } from '@/session/api';
import type { Clock } from '@/session/clock';
import { SessionClient, type StopReason } from '@/session/session';
import type { TokenSource } from '@/session/tokens';
import type { SessionTransport } from '@/session/transport';
import { MerkleResponder } from '@/sync/responder';

import { RepositoryIndexer } from './indexer';
import type { EngineMessage, RepositoryRoot } from './protocol';
import { DutyCycle, IdleScheduler } from './scheduler';

export interface EngineDeps {
  readonly repositories: readonly RepositoryRoot[];
  readonly clientVersion: string;
  readonly workspaceId: string;
  readonly api: KeyApi;
  readonly tokens: TokenSource;
  readonly transport: SessionTransport;
  readonly clock: Clock;
  readonly pathRules: PathRules;
  readonly parsers: Parsers;
  readonly scrub: (text: string) => Promise<WorkerScrub>;
  readonly post: (message: EngineMessage) => void;
}

interface Repository {
  readonly responder: MerkleResponder;
  readonly indexer: RepositoryIndexer;
  key: Uint8Array | null;
  /** The key changed since the last tree: rebuild even if no file did. */
  stale: boolean;
}

export class SyncEngine {
  readonly #deps: EngineDeps;
  readonly #repositories = new Map<string, Repository>();
  readonly #session: SessionClient;
  readonly #scheduler: IdleScheduler;

  constructor(deps: EngineDeps) {
    this.#deps = deps;
    const cycle = new DutyCycle(deps.clock);
    for (const { id, root } of deps.repositories) {
      const indexer = new RepositoryIndexer({ ...deps, root, cycle });
      this.#repositories.set(id, {
        responder: new MerkleResponder(id),
        indexer,
        key: null,
        stale: false,
      });
    }
    this.#session = new SessionClient({
      api: deps.api,
      tokens: deps.tokens,
      transport: deps.transport,
      clock: deps.clock,
      clientVersion: deps.clientVersion,
      workspaceId: deps.workspaceId,
      repositories: new Map([...this.#repositories].map(([id, r]) => [id, r.responder])),
      onRepositoryKeys: (keys) => {
        this.#keys(keys);
      },
    });
    this.#scheduler = new IdleScheduler(
      deps.clock,
      (signal) => this.#index(signal),
      // A failed walk is retried on the next change or check; nothing about it is named.
      () => {
        deps.post({ kind: 'index_failed' });
      },
    );
  }

  /** Runs until stopped, or a stop the user must see. */
  async run(): Promise<StopReason> {
    this.#scheduler.start();
    try {
      return await this.#session.start();
    } finally {
      await this.#scheduler.stop();
      for (const repository of this.#repositories.values()) repository.key?.fill(0);
    }
  }

  changed(): void {
    this.#scheduler.changed();
  }

  async stop(): Promise<void> {
    await this.#session.stop();
  }

  /** Keeps one copy of each repository's key; a new or different one marks its tree stale. */
  #keys(keys: ReadonlyMap<string, Uint8Array>): void {
    let changed = false;
    for (const [id, key] of keys) {
      const repository = this.#repositories.get(id);
      const held = repository?.key ?? null;
      if (repository === undefined || (held !== null && sameKey(held, key))) {
        key.fill(0);
        continue;
      }
      held?.fill(0);
      repository.key = key;
      repository.stale = true;
      changed = true;
    }
    if (changed) this.#scheduler.changed();
  }

  async #index(signal: AbortSignal): Promise<void> {
    for (const [id, repository] of this.#repositories) {
      if (repository.key === null) continue;
      // A copy: a new key arriving mid-walk wipes the held one, never the one in use.
      const key = repository.key.slice();
      let built;
      try {
        built = await repository.indexer.build(key, signal);
      } finally {
        key.fill(0);
      }
      if (built === null) return;
      const previous = repository.responder.rootReport()?.rootHash;
      repository.responder.setTree(built.tree);
      if (repository.stale || previous !== built.tree.rootHash) this.#session.reportRoot(id);
      repository.stale = false;
      this.#deps.post({ kind: 'indexed', stats: built.stats });
    }
  }
}

function sameKey(a: Uint8Array, b: Uint8Array): boolean {
  return a.length === b.length && timingSafeEqual(a, b);
}
