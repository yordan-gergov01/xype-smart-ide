import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  createParser,
  GrammarError,
  grammarFile,
  LANGUAGE_IDS,
  type LanguageId,
} from '@/context/grammars';

const GRAMMAR_DIR = fileURLToPath(new URL('../../grammars', import.meta.url));

// One small, valid sample per language, and the node type each root must have.
const SAMPLES: Record<LanguageId, { source: string; root: string }> = {
  python: { source: 'def f(x):\n    return x\n', root: 'module' },
  typescript: {
    source: 'export function f(x: number): number {\n  return x;\n}\n',
    root: 'program',
  },
  tsx: { source: 'const A = () => <div>a</div>;\n', root: 'program' },
  javascript: { source: 'class A {\n  m() {\n    return 1;\n  }\n}\n', root: 'program' },
  yaml: { source: 'a:\n  b: 1\n', root: 'stream' },
  sql: { source: 'CREATE TABLE t (id int);\nSELECT id FROM t WHERE id = 1;\n', root: 'program' },
  markdown: { source: '# Title\n\nText.\n\n## Sub\n\nMore.\n', root: 'document' },
  dockerfile: {
    source: 'FROM python:3.12 AS base\nRUN pip install x\nCOPY . /app\n',
    root: 'source_file',
  },
};

const missing = LANGUAGE_IDS.filter((id) => !existsSync(grammarFile(GRAMMAR_DIR, id)));
// CI builds the grammars first and must never skip; locally the toolchain is optional.
const required = process.env['XYPE_REQUIRE_GRAMMARS'] === '1';
const skip = missing.length > 0 && !required;

describe.skipIf(skip)('grammars (run `npm run grammars -w xype-ai` first)', () => {
  it('has a built grammar for every language', () => {
    expect(missing).toEqual([]);
  });

  it.each(LANGUAGE_IDS)('parses a %s sample without errors', async (id) => {
    const parser = await createParser(GRAMMAR_DIR, id);
    const tree = parser.parse(SAMPLES[id].source);
    expect(tree?.rootNode.type).toBe(SAMPLES[id].root);
    expect(tree?.rootNode.hasError).toBe(false);
  });
});

describe('manifest', () => {
  it('builds exactly the languages the context engine chunks', () => {
    const manifest = JSON.parse(
      readFileSync(fileURLToPath(new URL('../../grammars.json', import.meta.url)), 'utf8'),
    ) as { grammars: { id: string }[] };
    expect(manifest.grammars.map((g) => g.id).sort()).toEqual([...LANGUAGE_IDS].sort());
  });
});

describe('loading', () => {
  it('raises a GrammarError for a missing grammar', async () => {
    await expect(
      createParser(fileURLToPath(new URL('./no-such-dir', import.meta.url)), 'python'),
    ).rejects.toThrow(GrammarError);
  });
});
