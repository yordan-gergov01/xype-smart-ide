// The call graph over a repository's chunks (brief §3.4.2, slice 10): when chunk A
// changes, its dependents (the chunks that call A) are worth putting into the context,
// because vector search misses dependencies that share no text. It lives on the client.
// Nothing sends it yet; whether its edges go to the server's `chunk_edges` (as keyed
// `symbol` ids, ADR-0016) is decided when completion and chat use dependents.
//
// Calls are resolved by simple name within a language family (Python, or TypeScript,
// TSX and JavaScript together). A name defined in the caller's own file resolves only
// there; otherwise to every definition of that name in the family, unless there are more
// than `MAX_TARGETS` of them (`get`, `run`), which says nothing about a dependency.

import type { Chunk } from './chunkPlan';
import { languageOf } from './chunker';
import type { LanguageId } from './grammars';

export const MAX_TARGETS = 8;

/** A chunk's identity within its repository, as the Merkle leaf id is built from it. */
export interface ChunkKey {
  readonly path: string;
  readonly kind: string;
  readonly symbol: string;
  readonly occurrence: number;
}

export interface FileChunks {
  readonly path: string;
  readonly chunks: readonly Chunk[];
}

export interface CallGraph {
  /** The chunks that call `key`, up to `depth` calls away, nearest first. */
  dependents(key: ChunkKey, depth?: number): readonly ChunkKey[];
}

const FAMILY: Partial<Record<LanguageId, string>> = {
  python: 'python',
  typescript: 'script',
  tsx: 'script',
  javascript: 'script',
};

const keyOf = (path: string, chunk: Chunk): ChunkKey => ({
  path,
  kind: chunk.kind,
  symbol: chunk.symbol,
  occurrence: chunk.occurrence,
});

const idOf = (key: ChunkKey) =>
  [key.path, key.kind, key.symbol, String(key.occurrence)].join('\u0000');

interface Definition {
  readonly key: ChunkKey;
  readonly family: string;
}

interface Located {
  readonly file: FileChunks;
  readonly family: string;
}

function located(files: readonly FileChunks[]): Located[] {
  return files.flatMap((file) => {
    const language = languageOf(file.path);
    const family = language === null ? undefined : FAMILY[language];
    return family === undefined ? [] : [{ file, family }];
  });
}

function definitions(files: readonly Located[]): Map<string, Definition[]> {
  const byName = new Map<string, Definition[]>();
  for (const { file, family } of files) {
    for (const chunk of file.chunks.filter((c) => c.name !== '')) {
      const list = byName.get(chunk.name) ?? [];
      list.push({ key: keyOf(file.path, chunk), family });
      byName.set(chunk.name, list);
    }
  }
  return byName;
}

function resolve(defs: readonly Definition[], family: string, path: string): Definition[] {
  const same = defs.filter((d) => d.family === family);
  const local = same.filter((d) => d.key.path === path);
  if (local.length > 0) return local;
  return same.length > MAX_TARGETS ? [] : same;
}

/** Callers by the id of the chunk they call. */
function callersOf(files: readonly Located[]): Map<string, ChunkKey[]> {
  const byName = definitions(files);
  const callers = new Map<string, Map<string, ChunkKey>>();
  for (const { file, family } of files) {
    for (const chunk of file.chunks) {
      const caller = keyOf(file.path, chunk);
      const targets = chunk.calls.flatMap((name) =>
        resolve(byName.get(name) ?? [], family, file.path),
      );
      for (const target of targets) {
        const targetId = idOf(target.key);
        if (targetId === idOf(caller)) continue;
        const list = callers.get(targetId) ?? new Map<string, ChunkKey>();
        list.set(idOf(caller), caller);
        callers.set(targetId, list);
      }
    }
  }
  return new Map([...callers].map(([target, list]) => [target, [...list.values()]]));
}

export function buildCallGraph(files: readonly FileChunks[]): CallGraph {
  const callers = callersOf(located(files));
  return {
    dependents(key, depth = 1) {
      const seen = new Set([idOf(key)]);
      const out: ChunkKey[] = [];
      let frontier = [key];
      for (let level = 0; level < depth && frontier.length > 0; level++) {
        const next: ChunkKey[] = [];
        for (const caller of frontier.flatMap((k) => callers.get(idOf(k)) ?? [])) {
          if (seen.has(idOf(caller))) continue;
          seen.add(idOf(caller));
          next.push(caller);
          out.push(caller);
        }
        frontier = next;
      }
      return out;
    },
  };
}
