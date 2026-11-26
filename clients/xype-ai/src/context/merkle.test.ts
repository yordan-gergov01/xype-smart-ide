import { describe, expect, it } from 'vitest';

import { hmacId } from '@/security/obfuscation';

import { buildTree, type ChunkInput, type FileInput, innerHash, MerkleError } from './merkle';

const KEY = Uint8Array.from({ length: 32 }, (_, i) => i);
const OTHER_KEY = Uint8Array.from({ length: 32 }, (_, i) => 255 - i);

function chunk(symbol: string, lines: [number, number], content = `def ${symbol}(): pass\n`) {
  return {
    kind: 'function',
    symbol: `py:${symbol}`,
    occurrence: 0,
    content,
    lines,
  } satisfies ChunkInput;
}

const MAIN: FileInput = {
  path: 'src/app/main.py',
  chunks: [chunk('run', [1, 2]), chunk('stop', [4, 5])],
};
const UTIL: FileInput = { path: 'src/util.py', chunks: [chunk('help', [1, 1])] };
const FILES: FileInput[] = [MAIN, UTIL, { path: 'README.md', chunks: [] }];

describe('buildTree (ADR-0018)', () => {
  it('mirrors directories, files and chunks with keyed ids', () => {
    const tree = buildTree(KEY, FILES);
    const top = tree.children('');
    const srcId = hmacId(KEY, 'dir', 'src');
    expect(top.map((n) => n.nodeId).sort()).toEqual(
      [hmacId(KEY, 'file', 'README.md'), srcId].sort(),
    );
    const appId = hmacId(KEY, 'dir', 'src/app');
    const mainId = hmacId(KEY, 'file', 'src/app/main.py');
    const leaves = tree.children(mainId);
    expect(leaves.map((n) => n.nodeId)).toEqual(
      [
        hmacId(KEY, 'chunk-pos', 'src/app/main.py', 'function', 'py:run', '0'),
        hmacId(KEY, 'chunk-pos', 'src/app/main.py', 'function', 'py:stop', '0'),
      ].sort(),
    );
    expect(leaves.every((n) => n.isLeaf && n.parentId === mainId)).toBe(true);
    expect(leaves.find((n) => n.lines?.[0] === 1)?.hash).toBe(
      hmacId(KEY, 'chunk-content', 'def run(): pass\n'),
    );
    expect(tree.children(appId).map((n) => n.nodeId)).toEqual([mainId]);
  });

  it('chains inner hashes up to the root', () => {
    const tree = buildTree(KEY, FILES);
    const check = (parentId: string): string => {
      const children = tree.children(parentId);
      for (const child of children) if (!child.isLeaf) expect(child.hash).toBe(check(child.nodeId));
      return innerHash(children);
    };
    expect(tree.rootHash).toBe(check(''));
  });

  it('gives the same root whatever the file order, OS separator or Unicode form', () => {
    const reordered = [...FILES]
      .reverse()
      .map((f) => ({ ...f, path: f.path.replaceAll('/', '\\') }));
    expect(buildTree(KEY, reordered).rootHash).toBe(buildTree(KEY, FILES).rootHash);
    const nfd = buildTree(KEY, [{ path: 'café.py', chunks: [] }]);
    const nfc = buildTree(KEY, [{ path: './café.py', chunks: [] }]);
    expect(nfd.rootHash).toBe(nfc.rootHash);
  });

  it('changes the root when a chunk moves, and only its range', () => {
    const before = buildTree(KEY, FILES);
    const moved = buildTree(KEY, [
      { ...MAIN, chunks: [chunk('run', [2, 3]), chunk('stop', [5, 6])] },
      ...FILES.slice(1),
    ]);
    expect(moved.rootHash).not.toBe(before.rootHash);
    const mainId = hmacId(KEY, 'file', 'src/app/main.py');
    expect(moved.children(mainId).map((n) => n.hash)).toEqual(
      before.children(mainId).map((n) => n.hash),
    );
  });

  it('depends on the repository key', () => {
    expect(buildTree(OTHER_KEY, FILES).rootHash).not.toBe(buildTree(KEY, FILES).rootHash);
  });

  it('refuses duplicate chunks, duplicate files and bad ranges', () => {
    const run = chunk('run', [1, 2]);
    expect(() => buildTree(KEY, [{ path: 'a.py', chunks: [run, run] }])).toThrow(MerkleError);
    expect(() => buildTree(KEY, [UTIL, UTIL])).toThrow(MerkleError);
    for (const lines of [
      [0, 1],
      [3, 2],
      [1.5, 2],
    ] as [number, number][]) {
      expect(() => buildTree(KEY, [{ path: 'a.py', chunks: [chunk('x', lines)] }])).toThrow(
        MerkleError,
      );
    }
  });

  it('has an empty root for an empty repository', () => {
    expect(buildTree(KEY, []).rootHash).toBe(innerHash([]));
  });
});
