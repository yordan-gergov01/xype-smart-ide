// Path exclusion before a file is read (ADR-0019 section 1): the `denied_paths` of the
// shared rule file, files over `max_file_bytes`, binary files and files that are not
// UTF-8. An excluded file never produces a chunk or a `ChunkRef`. `.gitignore` and
// `.xype/ignore` are applied by the workspace walker (slice 11).
//
// `denied_paths` uses a small gitignore-like syntax, matched case-insensitively (a
// Windows file system would otherwise let `.ENV` through) on the normalised path:
// - `name/` denies every file under a directory of that name, anywhere;
// - a pattern with an inner `/` matches the end of the path (`.docker/config.json`);
// - any other pattern is a basename glob where `*` matches within one segment;
// - `!pattern` allows again what an earlier pattern denied; the last match wins.

import { normalisePath, PathError } from './obfuscation';

export type Exclusion = 'bad_path' | 'denied_path' | 'too_large' | 'binary' | 'not_utf8';

export class PathRulesError extends Error {
  override name = 'PathRulesError';
}

export type FileText =
  | { readonly kind: 'text'; readonly text: string }
  | { readonly kind: 'excluded'; readonly reason: Exclusion };

interface PathPattern {
  readonly allow: boolean;
  readonly test: (path: string) => boolean;
}

export interface PathRules {
  readonly patterns: readonly PathPattern[];
  readonly maxFileBytes: number;
}

function globSegment(glob: string): RegExp {
  const body = glob
    .split('*')
    .map((part) => part.replace(/[.+?^${}()|[\]\\]/g, '\\$&'))
    .join('[^/]*');
  return new RegExp(`^${body}$`, 'i');
}

function compilePattern(raw: string): PathPattern {
  const allow = raw.startsWith('!');
  const pattern = (allow ? raw.slice(1) : raw).toLowerCase();
  if (pattern.includes('*') && pattern.includes('/')) {
    // Would match only literally, so a new rule like this would never deny anything.
    throw new PathRulesError('denied_paths: `*` works only in a basename pattern');
  }
  if (pattern.endsWith('/')) {
    const dir = pattern.slice(0, -1);
    return { allow, test: (path) => path.toLowerCase().split('/').slice(0, -1).includes(dir) };
  }
  if (pattern.includes('/')) {
    return {
      allow,
      test: (path) => {
        const lower = path.toLowerCase();
        return lower === pattern || lower.endsWith(`/${pattern}`);
      },
    };
  }
  const glob = globSegment(pattern);
  return { allow, test: (path) => glob.test(path.slice(path.lastIndexOf('/') + 1)) };
}

/** `data` is the parsed `rules.json`. */
export function parsePathRules(data: unknown): PathRules {
  const record = (typeof data === 'object' && data !== null ? data : {}) as Record<string, unknown>;
  const denied = record['denied_paths'];
  const max = record['max_file_bytes'];
  if (!Array.isArray(denied) || !denied.every((p) => typeof p === 'string')) {
    throw new PathRulesError('rules: denied_paths is missing or malformed');
  }
  if (typeof max !== 'number') throw new PathRulesError('rules: max_file_bytes is missing');
  return { patterns: denied.map(compilePattern), maxFileBytes: max };
}

/** `path` is repository-relative; it is normalised first (ADR-0016). */
export function isDeniedPath(rules: PathRules, path: string): boolean {
  const normalised = normalisePath(path);
  let denied = false;
  for (const pattern of rules.patterns) {
    if (pattern.allow === denied && pattern.test(normalised)) denied = !pattern.allow;
  }
  return denied;
}

const decoder = new TextDecoder('utf-8', { fatal: true });

/**
 * The file's text with LF line endings, as ADR-0016 hashes it, or why it is excluded.
 * A lossy decode would change what is hashed, so invalid UTF-8 is refused.
 */
export function fileText(rules: PathRules, path: string, bytes: Uint8Array): FileText {
  let denied: boolean;
  try {
    denied = isDeniedPath(rules, path);
  } catch (error) {
    // One odd file name must not stop the whole repository from being indexed.
    if (error instanceof PathError) return { kind: 'excluded', reason: 'bad_path' };
    throw error;
  }
  if (denied) return { kind: 'excluded', reason: 'denied_path' };
  if (bytes.length > rules.maxFileBytes) return { kind: 'excluded', reason: 'too_large' };
  if (bytes.includes(0)) return { kind: 'excluded', reason: 'binary' };
  try {
    return { kind: 'text', text: decoder.decode(bytes).replaceAll('\r\n', '\n') };
  } catch {
    return { kind: 'excluded', reason: 'not_utf8' };
  }
}
