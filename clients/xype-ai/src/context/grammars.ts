// Grammars for AST chunking (brief §3.4.2, ADR-0017). The .wasm files are built from
// pinned sources by `npm run grammars` (scripts/build-grammars.mjs) into grammars/.

import { join } from 'node:path';

import { Language, Parser } from 'web-tree-sitter';

export const LANGUAGE_IDS = [
  'python',
  'typescript',
  'tsx',
  'javascript',
  'yaml',
  'sql',
  'markdown',
  'dockerfile',
] as const;

export type LanguageId = (typeof LANGUAGE_IDS)[number];

export class GrammarError extends Error {
  override name = 'GrammarError';
}

let runtimeReady: Promise<void> | undefined;

/** Initialises the web-tree-sitter runtime once per process; a failure can be retried. */
function initRuntime(): Promise<void> {
  runtimeReady ??= Parser.init().catch((error: unknown) => {
    runtimeReady = undefined;
    throw new GrammarError('the web-tree-sitter runtime could not start', { cause: error });
  });
  return runtimeReady;
}

export function grammarFile(grammarDir: string, id: LanguageId): string {
  return join(grammarDir, `tree-sitter-${id}.wasm`);
}

/** Loads one grammar from `grammarDir`; the caller keeps it for reuse. */
export async function loadGrammar(grammarDir: string, id: LanguageId): Promise<Language> {
  await initRuntime();
  try {
    return await Language.load(grammarFile(grammarDir, id));
  } catch (error) {
    throw new GrammarError(`grammar ${id} could not be loaded`, { cause: error });
  }
}

/** A parser bound to one grammar. */
export async function createParser(grammarDir: string, id: LanguageId): Promise<Parser> {
  const language = await loadGrammar(grammarDir, id);
  const parser = new Parser();
  parser.setLanguage(language);
  return parser;
}
