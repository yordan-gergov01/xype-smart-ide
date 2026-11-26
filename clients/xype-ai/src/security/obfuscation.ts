// Obfuscated identifiers: keyed HMAC-SHA256 per repository (ADR-0016). Every developer
// who opens a repository must produce the same ids, so paths are normalised first.

import { createHmac } from 'node:crypto';

import { frame } from './framing';

export const ID_DOMAINS = ['file', 'dir', 'symbol', 'chunk-pos', 'chunk-content'] as const;

export type IdDomain = (typeof ID_DOMAINS)[number];

export class PathError extends Error {
  override name = 'PathError';
}

/** 64 lowercase hex characters; the repository key is not wiped. */
export function hmacId(repoKey: Uint8Array, domain: IdDomain, ...fields: string[]): string {
  return createHmac('sha256', repoKey)
    .update(frame(domain, ...fields))
    .digest('hex');
}

/**
 * A path relative to the repository root as ADR-0016 hashes it: `/` separators, NFC,
 * case kept, no leading `./`. Absolute paths, a drive letter, `..` and empty segments
 * are refused, so two operating systems never give one file two ids.
 */
export function normalisePath(path: string): string {
  const segments = path.normalize('NFC').replaceAll('\\', '/').split('/');
  while (segments[0] === '.') segments.shift();
  const bad = segments.some((s) => s === '' || s === '.' || s === '..');
  const drive = /^[A-Za-z]:$/.test(segments[0] ?? '');
  if (segments.length === 0 || bad || drive) throw new PathError('not a repository-relative path');
  return segments.join('/');
}
