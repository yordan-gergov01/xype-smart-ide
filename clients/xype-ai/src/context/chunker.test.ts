// Golden fixtures: the chunks every client must cut from the sample files. A change to
// the chunking rules changes the shared index, so it must show up here as a diff.

import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import type { Parser } from 'web-tree-sitter';
import { beforeAll, describe, expect, it } from 'vitest';

import type { ScrubbedText } from '@/security/scrubber';

import { chunkFile, languageOf, loadParsers, type Parsers } from './chunker';
import { splitLines } from './chunkPlan';
import { grammarFile, LANGUAGE_IDS, type LanguageId } from './grammars';
import { SAMPLES } from './samples';

const GRAMMAR_DIR = fileURLToPath(new URL('../../grammars', import.meta.url));
const missing = LANGUAGE_IDS.filter((id) => !existsSync(grammarFile(GRAMMAR_DIR, id)));
// Hostile 1 MiB inputs parse for seconds, longer on a loaded CI runner.
const HOSTILE_MS = 60_000;
const skip = missing.length > 0 && process.env['XYPE_REQUIRE_GRAMMARS'] !== '1';

type Row = [
  kind: string,
  symbol: string,
  occurrence: number,
  lines: [number, number],
  calls: string[],
];

const GOLDEN: Record<LanguageId, Row[]> = {
  python: [
    ['block', '', 0, [1, 1], []],
    ['function', 'py:load', 0, [4, 6], ['join', 'read']],
    ['class', 'py:Store', 0, [9, 13], ['load']],
    ['function', 'py:read', 0, [16, 16], ['open', 'read']],
    ['block', '', 1, [18, 18], ['main']],
  ],
  typescript: [
    ['block', '', 0, [1, 1], []],
    ['function', 'ts:fetchUser', 0, [3, 5], ['get', 'parse']],
    ['function', 'ts:parse', 0, [7, 7], ['User']],
    ['class', 'ts:User', 0, [9, 14], []],
    ['interface', 'ts:Options', 0, [16, 18], []],
    ['type', 'ts:Id', 0, [20, 20], []],
    ['function', 'ts:default', 0, [21, 21], []],
  ],
  tsx: [
    ['function', 'tsx:View', 0, [1, 3], ['Button', 'save']],
    ['function', 'tsx:Button', 0, [5, 5], []],
  ],
  javascript: [
    ['function', 'js:helper', 0, [1, 3], []],
    ['function', 'js:run', 0, [5, 7], ['helper']],
    ['block', '', 0, [9, 9], ['run']],
  ],
  yaml: [
    ['key', 'yaml:name', 0, [1, 1], []],
    ['key', 'yaml:services', 0, [2, 4], []],
    ['block', '', 0, [5, 5], []],
    ['key', 'yaml:name', 1, [6, 6], []],
  ],
  sql: [
    ['table', 'sql:users', 0, [1, 1], []],
    ['function', 'sql:total', 0, [3, 3], []],
    ['statement', '', 0, [5, 5], []],
    ['statement', '', 1, [6, 6], []],
  ],
  markdown: [
    ['section', '', 0, [1, 2], []],
    ['section', 'md:Guide', 0, [3, 9], []],
  ],
  dockerfile: [
    ['stage', 'docker:build', 0, [1, 2], []],
    ['stage', '', 0, [4, 5], []],
  ],
};

describe.skipIf(skip)('chunkFile (run `npm run grammars -w xype-ai` first)', () => {
  let parsers: Parsers;
  beforeAll(async () => {
    parsers = (await loadParsers(GRAMMAR_DIR)).parsers;
  });
  // A generous parse budget: the goldens must never fall back to windows under load.
  const cut = (path: string, text: string, budgetMs = HOSTILE_MS) =>
    chunkFile(parsers, path, text as ScrubbedText, budgetMs);

  it.each(LANGUAGE_IDS)('cuts the golden chunks of the %s sample', (id) => {
    const { path, text } = SAMPLES[id];
    const chunks = cut(path, text);
    expect(chunks.map((c) => [c.kind, c.symbol, c.occurrence, c.lines, c.calls])).toEqual(
      GOLDEN[id],
    );
    const lines = splitLines(text);
    for (const chunk of chunks) {
      // Content is exactly the lines of its range, so a fetch by range returns it.
      expect(chunk.content).toBe(lines.slice(chunk.lines[0] - 1, chunk.lines[1]).join(''));
    }
  });

  it('keeps a symbol chunk and its id when lines are inserted above it', () => {
    const { path, text } = SAMPLES.python;
    const before = cut(path, text).find((c) => c.symbol === 'py:Store');
    const after = cut(path, `# note\n# more\n${text}`).find((c) => c.symbol === 'py:Store');
    expect(after?.lines).toEqual([11, 15]);
    expect([after?.content, after?.occurrence]).toEqual([before?.content, before?.occurrence]);
  });

  it('splits a large class into its methods and keeps the rest under the class', () => {
    const methods = Array.from(
      { length: 60 },
      (_, i) => `    def m${String(i)}(self):\n        return ${String(i)}\n`,
    );
    const text = `class Big:\n    size = 1\n\n${methods.join('\n')}`;
    const chunks = cut('big.py', text);
    expect(chunks[0]).toMatchObject({ kind: 'class', symbol: 'py:Big', lines: [1, 2] });
    expect(chunks.filter((c) => c.symbol.startsWith('py:Big.m'))).toHaveLength(60);
    expect(chunks[1]).toMatchObject({ kind: 'function', symbol: 'py:Big.m0', lines: [4, 5] });
  });

  it('chunks a file with syntax errors by its tree, and other files by windows', () => {
    expect(cut('a.py', 'def ok():\n    pass\n\ndef broken(:\n').map((c) => c.symbol)).toContain(
      'py:ok',
    );
    const text = Array.from({ length: 130 }, (_, i) => `line ${String(i)}\n`).join('');
    const windows = cut('notes.txt', text);
    expect(windows.map((c) => [c.kind, c.occurrence, c.lines])).toEqual([
      ['block', 0, [1, 60]],
      ['block', 1, [61, 120]],
      ['block', 2, [121, 130]],
    ]);
  });

  it(
    'survives a node with 300k children',
    () => {
      const text = `export const xs = [${'0,'.repeat(300_000)}0];\nfunction f() {}\n`;
      expect(cut('big.js', text).map((c) => c.symbol)).toEqual(['js:f']);
    },
    HOSTILE_MS,
  );

  it(
    'caps nesting depth and symbol length for deeply nested definitions',
    () => {
      const depth = 2_000;
      const open = Array.from({ length: depth }, (_, i) => `namespace n${String(i)} {\n`).join('');
      const text = `${open}const x = 1;\n${'}\n'.repeat(depth)}`;
      const chunks = cut('deep.ts', text);
      expect(chunks.length).toBeGreaterThan(0);
      expect(Math.max(...chunks.map((c) => c.symbol.length))).toBeLessThanOrEqual(1000 + 3);
    },
    HOSTILE_MS,
  );

  it('falls back to windows when the parser fails', () => {
    const failing = new Map(parsers);
    const broken = {
      parse: () => {
        throw new Error('out of memory');
      },
    } as unknown as Parser;
    failing.set('python', broken);
    const text = 'def f():\n    pass\n' as ScrubbedText;
    expect(chunkFile(failing, 'a.py', text).map((c) => c.kind)).toEqual(['block']);
  });

  it(
    'cuts a file by windows when its parse runs past the budget, and parses again after',
    () => {
      const text = `export const xs = [${'0,'.repeat(300_000)}0];\nfunction f() {}\n`;
      expect(cut('big.js', text, 0).every((c) => c.kind === 'block')).toBe(true);
      expect(cut('small.js', 'function g() {}\n').map((c) => c.symbol)).toEqual(['js:g']);
    },
    HOSTILE_MS,
  );

  it(
    'never emits a chunk over the size cap, even for a one-line minified file',
    () => {
      const minified = `var a=${'1+'.repeat(512 * 1024)}1;\nexport const b = () => a;\n`;
      const chunks = cut('dist/app.min.js', minified);
      expect(chunks.map((c) => c.symbol)).toEqual(['js:b']);
      expect(chunks.every((c) => Buffer.byteLength(c.content) <= 16 * 1024)).toBe(true);
    },
    HOSTILE_MS,
  );
});

describe('loadParsers', () => {
  it('reports every grammar that did not load', async () => {
    const { parsers, failed } = await loadParsers(
      fileURLToPath(new URL('./none', import.meta.url)),
    );
    expect([parsers.size, failed]).toEqual([0, [...LANGUAGE_IDS]]);
  });
});

describe('languageOf', () => {
  it.each([
    ['a/b.py', 'python'],
    ['x.MTS', 'typescript'],
    ['v.tsx', 'tsx'],
    ['s.cjs', 'javascript'],
    ['c.jsx', 'javascript'],
    ['k.YAML', 'yaml'],
    ['README.markdown', 'markdown'],
    ['ops/Dockerfile', 'dockerfile'],
    ['Dockerfile.dev', 'dockerfile'],
    ['api.dockerfile', 'dockerfile'],
    ['q.sql', 'sql'],
    ['notes.txt', null],
    ['.py', null],
    ['Makefile', null],
  ])('%s is %s', (path, language) => {
    expect(languageOf(path)).toBe(language);
  });
});
