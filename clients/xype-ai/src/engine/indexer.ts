// One repository's index on the client (slice 11c): every walk goes through the
// workspace walker, so the ignore files, `denied_paths`, the size check and no-follow
// always apply (ADR-0019). Then each file is scrubbed in the scrub worker, chunked and put
// into the Merkle tree. A file whose text did not change since the last walk reuses its
// chunks; the work on each file is paced to the engine's CPU share.

import { createHash } from 'node:crypto';

import type { Chunk } from '@/context/chunkPlan';
import { chunkFile, type Parsers } from '@/context/chunker';
import { buildTree, type MerkleTree } from '@/context/merkle';
import type { PathRules } from '@/security/paths';
import type { WorkerScrub } from '@/security/scrubWorker';
import type { Clock } from '@/session/clock';
import { walkRepository } from '@/sync/walker';

import type { DutyCycle } from './scheduler';

/** Counts only: never a path or a name (rule 08). */
export interface IndexStats {
  readonly files: number;
  readonly reused: number;
  readonly chunks: number;
  readonly excluded: Readonly<Record<string, number>>;
}

export interface IndexerDeps {
  readonly root: string;
  readonly pathRules: PathRules;
  readonly parsers: Parsers;
  readonly scrub: (text: string) => Promise<WorkerScrub>;
  readonly clock: Clock;
  readonly cycle: DutyCycle;
}

interface Cached {
  /** SHA-256 of the file's text, in memory only. */
  readonly hash: string;
  readonly chunks: readonly Chunk[];
}

export class RepositoryIndexer {
  readonly #deps: IndexerDeps;
  #cache = new Map<string, Cached>();

  constructor(deps: IndexerDeps) {
    this.#deps = deps;
  }

  /** The repository's tree under `repoKey`, or null when `signal` stopped the walk. */
  async build(
    repoKey: Uint8Array,
    signal: AbortSignal,
  ): Promise<{ tree: MerkleTree<Chunk>; stats: IndexStats } | null> {
    const next = new Map<string, Cached>();
    const excluded: Record<string, number> = {};
    let reused = 0;
    for await (const { path, file } of walkRepository(this.#deps.root, this.#deps.pathRules)) {
      if (signal.aborted) return null;
      if (file.kind === 'excluded') {
        excluded[file.reason] = (excluded[file.reason] ?? 0) + 1;
        continue;
      }
      const started = this.#deps.clock.now();
      const hash = createHash('sha256').update(file.text).digest('hex');
      const known = this.#cache.get(path);
      const entry = known?.hash === hash ? known : await this.#chunk(path, file.text, hash);
      if (entry === known) reused += 1;
      if (entry === null) excluded['scrub_timeout'] = (excluded['scrub_timeout'] ?? 0) + 1;
      else next.set(path, entry);
      await this.#deps.cycle.pace(this.#deps.clock.now() - started, signal);
    }
    // Files gone since the last walk leave the cache with it.
    this.#cache = next;
    const files = [...next].map(([path, entry]) => ({ path, chunks: entry.chunks }));
    const chunks = files.reduce((n, f) => n + f.chunks.length, 0);
    const stats = { files: files.length, reused, chunks, excluded };
    return { tree: buildTree(repoKey, files), stats };
  }

  /** Scrubbed and chunked, or null when the scrub ran past its limit (the file is excluded). */
  async #chunk(path: string, text: string, hash: string): Promise<Cached | null> {
    const scrubbed = await this.#deps.scrub(text);
    if (scrubbed.kind === 'excluded') return null;
    return { hash, chunks: chunkFile(this.#deps.parsers, path, scrubbed.text) };
  }
}
