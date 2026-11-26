import { describe, expect, it } from 'vitest';

import type { ScrubbedText } from '@/security/scrubber';

import { buildCallGraph, type ChunkKey, type FileChunks, MAX_TARGETS } from './callGraph';
import type { Chunk } from './chunkPlan';

function chunk(symbol: string, calls: string[] = [], kind = 'function', occurrence = 0): Chunk {
  return {
    kind,
    symbol,
    occurrence,
    content: '' as ScrubbedText,
    lines: [1, 1],
    name: symbol.slice(Math.max(symbol.indexOf(':'), symbol.lastIndexOf('.')) + 1),
    calls,
  };
}

const key = (path: string, symbol: string, kind = 'function', occurrence = 0): ChunkKey => ({
  path,
  kind,
  symbol,
  occurrence,
});

describe('buildCallGraph', () => {
  const files: FileChunks[] = [
    {
      path: 'a.py',
      chunks: [chunk('py:load', ['read']), chunk('py:read'), chunk('', ['load'], 'block')],
    },
    { path: 'b.py', chunks: [chunk('py:Store.get', ['load'])] },
    { path: 'c.py', chunks: [chunk('py:main', ['get'])] },
    { path: 'web.ts', chunks: [chunk('ts:load', ['fetch'])] },
    { path: 'ui.tsx', chunks: [chunk('tsx:View', ['load'])] },
  ];
  const graph = buildCallGraph(files);

  it('finds the chunks that call a chunk, also from a block', () => {
    expect(graph.dependents(key('a.py', 'py:read'))).toEqual([key('a.py', 'py:load')]);
    expect(graph.dependents(key('a.py', 'py:load'))).toEqual([
      key('a.py', '', 'block'),
      key('b.py', 'py:Store.get'),
    ]);
  });

  it('follows callers transitively up to the depth, nearest first', () => {
    expect(graph.dependents(key('a.py', 'py:read'), 3)).toEqual([
      key('a.py', 'py:load'),
      key('a.py', '', 'block'),
      key('b.py', 'py:Store.get'),
      key('c.py', 'py:main'),
    ]);
  });

  it('keeps language families apart and prefers a definition in the same file', () => {
    expect(graph.dependents(key('web.ts', 'ts:load'))).toEqual([key('ui.tsx', 'tsx:View')]);
    const local = buildCallGraph([
      { path: 'x.py', chunks: [chunk('py:f', ['g']), chunk('py:g')] },
      { path: 'y.py', chunks: [chunk('py:g')] },
    ]);
    expect(local.dependents(key('y.py', 'py:g'))).toEqual([]);
    expect(local.dependents(key('x.py', 'py:g'))).toEqual([key('x.py', 'py:f')]);
  });

  it('ignores names defined too often to mean a dependency', () => {
    const many = Array.from({ length: MAX_TARGETS + 1 }, (_, i) => ({
      path: `m${String(i)}.py`,
      chunks: [chunk('py:run')],
    }));
    const noisy = buildCallGraph([...many, { path: 'z.py', chunks: [chunk('py:z', ['run'])] }]);
    expect(noisy.dependents(key('m0.py', 'py:run'))).toEqual([]);
  });

  it('has no self-edges and no edges for files it cannot place', () => {
    const graph2 = buildCallGraph([
      { path: 'r.py', chunks: [chunk('py:loop', ['loop'])] },
      { path: 'notes.txt', chunks: [chunk('', ['loop'], 'block')] },
    ]);
    expect(graph2.dependents(key('r.py', 'py:loop'))).toEqual([]);
  });
});
