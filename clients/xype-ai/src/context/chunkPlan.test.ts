import { describe, expect, it } from 'vitest';

import type { ScrubbedText } from '@/security/scrubber';

import {
  type CallSite,
  MAX_CHUNK_BYTES,
  planChunks,
  splitLines,
  symbolName,
  type Unit,
  WINDOW_LINES,
} from './chunkPlan';

const unit = (
  kind: string,
  name: string,
  start: number,
  end: number,
  children: Unit[] = [],
): Unit => ({
  kind,
  name,
  start,
  end,
  children,
});
const plan = (prefix: string, text: string, units: Unit[], calls: CallSite[]) =>
  planChunks(prefix, text as ScrubbedText, units, calls);
const lines = (n: number) => Array.from({ length: n }, (_, i) => `l${String(i)}\n`).join('');
const shape = (chunks: ReturnType<typeof planChunks>) =>
  chunks.map((c) => [c.kind, c.symbol, c.occurrence, c.lines]);

describe('planChunks', () => {
  it('gives every line to at most one chunk', () => {
    // Two units on one line: the second starts on the next line; one inside is dropped.
    const units = [
      unit('function', 'a', 0, 2),
      unit('function', 'b', 2, 4),
      unit('function', 'c', 1, 1),
    ];
    expect(shape(plan('py', lines(5), units, []))).toEqual([
      ['function', 'py:a', 0, [1, 3]],
      ['function', 'py:b', 0, [4, 5]],
    ]);
  });

  it('numbers chunks of one kind and symbol in line order', () => {
    const units = [unit('key', 'name', 0, 0), unit('key', 'name', 2, 2)];
    const chunks = plan('yaml', lines(4), units, []);
    expect(shape(chunks)).toEqual([
      ['key', 'yaml:name', 0, [1, 1]],
      ['block', '', 0, [2, 2]],
      ['key', 'yaml:name', 1, [3, 3]],
      ['block', '', 1, [4, 4]],
    ]);
  });

  it('drops blank lines at the edges of a chunk and blank-only gaps', () => {
    const text = '\n\nx = 1\n\n\ndef f():\n    pass\n\n';
    expect(shape(plan('py', text, [unit('function', 'f', 5, 6)], []))).toEqual([
      ['block', '', 0, [3, 3]],
      ['function', 'py:f', 0, [6, 7]],
    ]);
  });

  it('splits by bytes as well as lines, and leaves out a line over the cap', () => {
    const wide = `${'x'.repeat(MAX_CHUNK_BYTES / 2)}\n`;
    const huge = `${'y'.repeat(MAX_CHUNK_BYTES + 1)}\n`;
    const chunks = plan('text', wide + wide + wide + huge + 'z\n', [], []);
    expect(chunks.map((c) => c.lines)).toEqual([
      [1, 1],
      [2, 2],
      [3, 3],
      [5, 5],
    ]);
    expect(chunks.every((c) => Buffer.byteLength(c.content) <= MAX_CHUNK_BYTES)).toBe(true);
  });

  it('measures UTF-8 bytes, not characters', () => {
    const text = `${'é'.repeat(MAX_CHUNK_BYTES / 2 - 1)}\n`.repeat(2);
    expect(plan('text', text, [], []).map((c) => c.lines)).toEqual([
      [1, 1],
      [2, 2],
    ]);
  });

  it('cuts a large unit without children into windows that keep its symbol', () => {
    const chunks = plan(
      'py',
      lines(WINDOW_LINES * 3),
      [unit('function', 'f', 0, WINDOW_LINES * 3 - 1)],
      [],
    );
    expect(shape(chunks)).toEqual([
      ['function', 'py:f', 0, [1, 60]],
      ['function', 'py:f', 1, [61, 120]],
      ['function', 'py:f', 2, [121, 180]],
    ]);
  });

  it('attributes each call to the chunk that owns its line', () => {
    const units = [unit('function', 'f', 0, 1), unit('function', 'g', 2, 3)];
    const calls = [
      { row: 1, name: 'g' },
      { row: 1, name: 'g' },
      { row: 3, name: 'h' },
      { row: 9, name: 'gone' },
    ];
    expect(plan('py', lines(4), units, calls).map((c) => [c.name, c.calls])).toEqual([
      ['f', ['g']],
      ['g', ['h']],
    ]);
  });

  it('has no chunks for an empty file', () => {
    expect(plan('py', '', [], [])).toEqual([]);
    expect(splitLines('')).toEqual([]);
    expect(splitLines('a\nb')).toEqual(['a\n', 'b']);
  });
});

describe('symbolName', () => {
  it('collapses whitespace and caps the length', () => {
    expect(symbolName('  my\n  key\t')).toBe('my key');
    expect(symbolName('k'.repeat(500))).toHaveLength(200);
  });
});
