import { readFileSync } from 'node:fs';

import { afterEach, describe, expect, it } from 'vitest';

import { ScrubWorker } from './scrubWorker';

const RULES: Record<string, unknown> = JSON.parse(
  readFileSync(new URL('../../../../packages/scrub-rules/rules.json', import.meta.url), 'utf8'),
) as Record<string, unknown>;
// A rule that backtracks exponentially: V8 cannot interrupt it, only a terminated thread.
const HANGING = {
  ...RULES,
  rules: [...(RULES['rules'] as unknown[]), { id: 'hang', pattern: '^(a+)+$', keywords: [] }],
};
const SECRET = ['AKIA', 'IOSFODNN7', 'EXAMPLE'].join('');

let worker: ScrubWorker | null = null;
afterEach(async () => {
  await worker?.close();
  worker = null;
});

describe('ScrubWorker', () => {
  it('scrubs in a worker thread with the shared rules', async () => {
    worker = new ScrubWorker(RULES);
    const result = await worker.scrub(`key = "${SECRET}"\n`);
    expect(result.kind).toBe('scrubbed');
    if (result.kind !== 'scrubbed') return;
    expect(result.text).not.toContain(SECRET);
    expect(result.text).toContain('<SECRET_REMOVED>');
  });

  it('excludes a file whose scrub hangs, then scrubs the next one in a new thread', async () => {
    worker = new ScrubWorker(HANGING);
    const started = performance.now();
    expect(await worker.scrub(`${'a'.repeat(40)}!`)).toEqual({
      kind: 'excluded',
      reason: 'scrub_timeout',
    });
    expect(performance.now() - started).toBeLessThan(10_000);
    expect((await worker.scrub('plain text\n')).kind).toBe('scrubbed');
  }, 30_000);

  it('refuses malformed rules before any thread starts', () => {
    expect(() => new ScrubWorker({})).toThrow('rules');
  });
});
