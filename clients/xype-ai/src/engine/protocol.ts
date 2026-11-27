// The messages between the extension host and the sync worker (slice 11c), one shared
// module for both sides. The host only does VS Code work (secrets, settings, file events,
// notices); everything else runs in the worker. Messages carry ids, counts and fixed
// reasons, never a path, code or token; the client secret crosses only when the worker
// asks for it to log in, and is not kept.

import type { StopReason } from '@/session/session';
import type { ClientCredentials } from '@/session/tokens';

import type { IndexStats } from './indexer';

export interface RepositoryRoot {
  /** The registered repository's id (the folder's `xype.repositoryId` setting). */
  readonly id: string;
  /** Absolute path of its workspace folder. */
  readonly root: string;
}

/** The worker's `workerData`. */
export interface SyncConfig {
  readonly serverUrl: string;
  /** PEM of the server's CA; the system CAs when null. */
  readonly ca: string | null;
  readonly clientVersion: string;
  readonly workspaceId: string;
  readonly repositories: readonly RepositoryRoot[];
  /** The extension's `dist/`: grammars, the tree-sitter runtime, the scrub thread, the rules. */
  readonly assetsDir: string;
}

export type HostMessage =
  | { readonly kind: 'changed' }
  | {
      readonly kind: 'credentials';
      readonly requestId: number;
      readonly credentials: ClientCredentials | null;
    }
  | { readonly kind: 'stop' };

/** `failed`: the engine could not start or stopped on an error it does not name. */
export type EngineStop = StopReason | 'failed';

export type EngineMessage =
  | { readonly kind: 'credentials_needed'; readonly requestId: number }
  | { readonly kind: 'grammars_failed'; readonly languages: readonly string[] }
  | { readonly kind: 'indexed'; readonly stats: IndexStats }
  /** A walk failed; no detail, since an error's message may name a path. */
  | { readonly kind: 'index_failed' }
  | { readonly kind: 'stopped'; readonly reason: EngineStop };
