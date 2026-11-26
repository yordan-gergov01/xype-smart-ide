// Chunking a scrubbed file (ADR-0017, slice 10): by its syntax tree for the seven
// languages, by line windows for any other file, a language whose grammar did not load,
// or a parse that returned no tree. tree-sitter recovers from syntax errors, so a tree
// with errors is still chunked by AST. Pure and synchronous once the parsers are loaded.

import type { Parser, Tree } from 'web-tree-sitter';

import { type CallSite, type Chunk, planChunks, type Unit } from './chunkPlan';
import { extractCalls, extractUnits, SPECS } from './extract';
import { createParser, LANGUAGE_IDS, type LanguageId } from './grammars';

const BY_EXTENSION: Record<string, LanguageId> = {
  py: 'python',
  ts: 'typescript',
  mts: 'typescript',
  cts: 'typescript',
  tsx: 'tsx',
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  jsx: 'javascript',
  sql: 'sql',
  yml: 'yaml',
  yaml: 'yaml',
  md: 'markdown',
  markdown: 'markdown',
  dockerfile: 'dockerfile',
};

/** The language of a repository-relative path, or null for line windows. */
export function languageOf(path: string): LanguageId | null {
  const base = path.slice(path.lastIndexOf('/') + 1).toLowerCase();
  if (base === 'dockerfile' || base.startsWith('dockerfile.')) return 'dockerfile';
  const dot = base.lastIndexOf('.');
  return dot <= 0 ? null : (BY_EXTENSION[base.slice(dot + 1)] ?? null);
}

export type Parsers = ReadonlyMap<LanguageId, Parser>;

/** Loads every grammar it can; a language whose grammar fails is chunked by windows. */
export async function loadParsers(grammarDir: string): Promise<Parsers> {
  const parsers = new Map<LanguageId, Parser>();
  for (const id of LANGUAGE_IDS) {
    try {
      parsers.set(id, await createParser(grammarDir, id));
    } catch {
      // The caller sees the gap through `parsers.has(id)`; chunking still works.
    }
  }
  return parsers;
}

/** The chunks of one file; `text` is already scrubbed (ADR-0019) with LF line endings. */
export function chunkFile(parsers: Parsers, path: string, text: string): Chunk[] {
  const language = languageOf(path);
  const parser = language === null ? undefined : parsers.get(language);
  if (language === null || parser === undefined) return planChunks('text', text, [], []);
  const prefix = SPECS[language].prefix;
  let tree: Tree | null = null;
  let units: Unit[] = [];
  let calls: CallSite[] = [];
  try {
    tree = parser.parse(text);
    if (tree !== null) {
      units = extractUnits(language, tree.rootNode);
      calls = extractCalls(language, tree.rootNode);
    }
  } catch {
    // The WebAssembly parser failed (out of memory, an internal error): line windows.
    units = [];
    calls = [];
  } finally {
    tree?.delete(); // trees live in WebAssembly memory
  }
  return planChunks(prefix, text, units, calls);
}
