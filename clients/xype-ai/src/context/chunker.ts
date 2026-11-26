// Chunking a scrubbed file (ADR-0017, slice 10): by its syntax tree for the seven
// languages, by line windows for any other file, a language whose grammar did not load,
// or a parse that returned no tree or ran past its time budget. tree-sitter recovers
// from syntax errors, so a tree with errors is still chunked by AST. Pure and
// synchronous once the parsers are loaded.

import type { Parser, Tree } from 'web-tree-sitter';

import type { ScrubbedText } from '@/security/scrubber';

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

export interface LoadedParsers {
  readonly parsers: Parsers;
  /** Languages whose grammar did not load: chunked by windows, and reported. */
  readonly failed: readonly LanguageId[];
}

/** Loads every grammar it can; a language whose grammar fails is chunked by windows. */
export async function loadParsers(grammarDir: string): Promise<LoadedParsers> {
  const parsers = new Map<LanguageId, Parser>();
  const failed: LanguageId[] = [];
  for (const id of LANGUAGE_IDS) {
    try {
      parsers.set(id, await createParser(grammarDir, id));
    } catch {
      failed.push(id);
    }
  }
  return { parsers, failed };
}

/** How long one file may parse before it is cut by line windows instead. */
export const PARSE_BUDGET_MS = 2000;

/** The tree, or null when the parse failed or ran past its budget. */
function parseWithin(parser: Parser, text: string, budgetMs: number): Tree | null {
  const deadline = performance.now() + budgetMs;
  const state = { cancelled: false };
  const tree = parser.parse(text, null, {
    // Returning true cancels the parse (web-tree-sitter's documented contract).
    progressCallback: () => (state.cancelled = performance.now() > deadline),
  });
  if (!state.cancelled) return tree;
  // A cancelled parse would otherwise resume on the next call.
  parser.reset();
  tree?.delete();
  return null;
}

/** The chunks of one file; `text` is scrubbed (ADR-0019) with LF line endings. */
export function chunkFile(
  parsers: Parsers,
  path: string,
  text: ScrubbedText,
  parseBudgetMs = PARSE_BUDGET_MS,
): Chunk[] {
  const language = languageOf(path);
  const parser = language === null ? undefined : parsers.get(language);
  if (language === null || parser === undefined) return planChunks('text', text, [], []);
  const prefix = SPECS[language].prefix;
  let tree: Tree | null = null;
  let units: Unit[] = [];
  let calls: CallSite[] = [];
  try {
    tree = parseWithin(parser, text, parseBudgetMs);
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
