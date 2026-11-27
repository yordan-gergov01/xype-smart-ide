import { readFileSync } from 'node:fs';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { parsePathRules } from '@/security/paths';
import { parseRules, SecretScrubber } from '@/security/scrubber';
import type { WorkerScrub } from '@/security/scrubWorker';
import { FakeClock } from '@/session/testing/fakes';

import { RepositoryIndexer } from './indexer';
import { DutyCycle } from './scheduler';

const RULES_DATA: unknown = JSON.parse(
  readFileSync(new URL('../../../../packages/scrub-rules/rules.json', import.meta.url), 'utf8'),
);
const scrubber = new SecretScrubber(parseRules(RULES_DATA));
// Synthetic, split so secret scanners never see a whole token.
const SECRET = ['ghp_', 'R7mQ2xLk9pW4vN8tY3cB6dF1hJ5sA0eZ', 'uX2i'].join('');
const KEY = new Uint8Array(32).fill(4);

let root: string;
let scrubbed: string[];
let clock: FakeClock;

function indexer(scrub?: (text: string) => Promise<WorkerScrub>) {
  clock = new FakeClock();
  return new RepositoryIndexer({
    root,
    pathRules: parsePathRules(RULES_DATA),
    parsers: new Map(),
    scrub:
      scrub ??
      ((text) => {
        scrubbed.push(text);
        return Promise.resolve({ kind: 'scrubbed', ...scrubber.scrub(text) });
      }),
    clock,
    cycle: new DutyCycle(clock),
  });
}

const signal = () => new AbortController().signal;

/** Every chunk's content in the tree, by walking it from the top. */
function contents(tree: Awaited<ReturnType<RepositoryIndexer['build']>>): string[] {
  const out: string[] = [];
  const queue = [''];
  for (let parent = queue.pop(); parent !== undefined; parent = queue.pop()) {
    for (const node of tree?.tree.children(parent) ?? []) {
      if (node.isLeaf) out.push(tree?.tree.leaf(node.nodeId)?.chunk.content ?? '');
      else queue.push(node.nodeId);
    }
  }
  return out;
}

beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), 'xype-index-'));
  scrubbed = [];
  await mkdir(join(root, 'src'));
  await writeFile(join(root, 'src', 'a.txt'), `token = "${SECRET}"\n`);
  await writeFile(join(root, 'src', 'b.txt'), 'plain\n');
  await writeFile(join(root, '.env'), 'X=1\n');
});

afterEach(async () => {
  await rm(root, { recursive: true, force: true });
});

describe('RepositoryIndexer', () => {
  it('builds the tree from scrubbed files and counts what it excluded', async () => {
    const result = await indexer().build(KEY, signal());
    expect(result?.stats).toMatchObject({ files: 2, reused: 0, chunks: 2 });
    expect(result?.stats.excluded).toEqual({ denied_path: 1 });
    expect(contents(result).join('')).not.toContain(SECRET);
    expect(contents(result).join('')).toContain('<SECRET_REMOVED>');
  });

  it('reuses the chunks of unchanged files and forgets removed ones', async () => {
    const index = indexer();
    const first = await index.build(KEY, signal());
    await writeFile(join(root, 'src', 'b.txt'), 'changed\n');
    await rm(join(root, 'src', 'a.txt'));
    const second = await index.build(KEY, signal());
    expect(second?.stats).toMatchObject({ files: 1, reused: 0 });
    const third = await index.build(KEY, signal());
    expect(third?.stats).toMatchObject({ files: 1, reused: 1 });
    expect(third?.tree.rootHash).toBe(second?.tree.rootHash);
    expect(first?.tree.rootHash).not.toBe(second?.tree.rootHash);
    expect(scrubbed).toHaveLength(3); // a and b, then the changed b only
  });

  it('excludes a file whose scrub ran past its limit', async () => {
    const result = await indexer(() =>
      Promise.resolve({ kind: 'excluded', reason: 'scrub_timeout' }),
    ).build(KEY, signal());
    expect(result?.stats).toMatchObject({
      files: 0,
      excluded: { denied_path: 1, scrub_timeout: 2 },
    });
  });

  it('paces its work to the CPU share and stops when asked', async () => {
    const index = indexer((text) => {
      clock.time += 12; // the scrub "took" 12 ms
      return Promise.resolve({ kind: 'scrubbed', ...scrubber.scrub(text) });
    });
    await index.build(KEY, signal());
    expect(clock.sleeps.map(Math.round)).toEqual([88, 88]);
    const stop = new AbortController();
    stop.abort();
    expect(await index.build(KEY, stop.signal)).toBeNull();
  });
});
