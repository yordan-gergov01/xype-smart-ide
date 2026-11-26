// The shared scrub corpus and ReDoS inputs (packages/scrub-rules, ADR-0019): the client
// must give the server's results exactly, and stay linear on V8's regex engine too.
// Secrets in the corpus are stored as fragments so scanners never see a whole token.

import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { parseRules, ScrubTimeoutError, SecretScrubber, type ScrubRules } from './scrubber';

const dir = new URL('../../../../packages/scrub-rules/', import.meta.url);
const read = (name: string): unknown => JSON.parse(readFileSync(new URL(name, dir), 'utf8'));
const RULES = parseRules(read('rules.json'));

interface Case {
  name: string;
  input: string[];
  expected: string[];
  findings: Record<string, number>;
}
const corpus = read('corpus.json') as { cases: Case[] };

interface Adversarial {
  size_bytes: number;
  max_seconds: number;
  shapes: string[];
  pumps: string[];
  triggers: Record<string, string[][]>;
}
const adversarial = read('adversarial.json') as Adversarial;

describe('shared scrub corpus', () => {
  const scrubber = new SecretScrubber(RULES);

  it.each(corpus.cases.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    const result = scrubber.scrub(c.input.join(''));
    expect(result.text).toBe(c.expected.join(''));
    expect(Object.fromEntries(result.findings)).toEqual(c.findings);
    const again = scrubber.scrub(result.text);
    expect([again.text, again.findings.size]).toEqual([result.text, 0]);
  });

  it('keeps the line count', () => {
    for (const c of corpus.cases) {
      const lines = (text: string) => text.split('\n').length;
      expect(lines(scrubber.scrub(c.input.join('')).text)).toBe(lines(c.input.join('')));
    }
  });

  it('has no pattern that matches the empty string', () => {
    // Python and V8 step past empty matches differently; none may exist.
    for (const rule of [...RULES.rules, RULES.entropy]) {
      expect(new RegExp(rule.pattern.source).test('')).toBe(false);
    }
  });

  it('fails closed past the time budget', () => {
    const slow: ScrubRules = { ...RULES, budgetMsPerMib: 0, minBudgetMs: -1 };
    expect(() => new SecretScrubber(slow).scrub('x = 1\n')).toThrow(ScrubTimeoutError);
  });
});

function only(ruleId: string): SecretScrubber {
  const isEntropy = ruleId === RULES.entropy.id;
  return new SecretScrubber({
    ...RULES,
    budgetMsPerMib: 1e9,
    rules: RULES.rules.filter((r) => r.id === ruleId),
    entropy: isEntropy ? RULES.entropy : { ...RULES.entropy, keywords: ['\u0000never'] },
  });
}

function* inputs(ruleId: string): Generator<string> {
  const size = adversarial.size_bytes;
  for (const fragments of adversarial.triggers[ruleId] ?? []) {
    for (const pump of adversarial.pumps) {
      const trigger = fragments.join('');
      const shapes: Record<string, string> = {
        trigger_then_pump: trigger + pump.repeat(size),
        trigger_and_40_pumps_repeated: (trigger + pump.repeat(40)).repeat(
          Math.floor(size / trigger.length),
        ),
        trigger_and_pump_repeated: (trigger + pump).repeat(size),
      };
      for (const shape of adversarial.shapes) yield (shapes[shape] ?? '').slice(0, size);
    }
  }
}

// The quadratic self-test takes seconds by design (about 6 s on a CI runner).
const SLOW_TEST_MS = 60_000;

describe('ReDoS check on V8', () => {
  const ids = [...RULES.rules.map((r) => r.id), RULES.entropy.id];

  it('has inputs for every rule', () => {
    expect(Object.keys(adversarial.triggers).sort()).toEqual([...ids].sort());
  });

  it(
    'catches a rule that backtracks',
    () => {
      // A bounded rule of the portable subset can still be quadratic: this one is.
      const slowRule = { ...RULES.entropy, id: 'slow', pattern: /[a-z]{0,20000}x/dg, keywords: [] };
      const slow = new SecretScrubber({
        ...RULES,
        budgetMsPerMib: 1e9,
        rules: [slowRule],
        entropy: { ...RULES.entropy, keywords: ['\u0000never'] },
      });
      const started = performance.now();
      slow.scrub('a'.repeat(adversarial.size_bytes));
      expect((performance.now() - started) / 1000).toBeGreaterThan(adversarial.max_seconds);
    },
    SLOW_TEST_MS,
  );

  it.each(ids)('%s stays linear', (ruleId) => {
    const scrubber = only(ruleId);
    for (const text of inputs(ruleId)) {
      const started = performance.now();
      scrubber.scrub(text);
      const seconds = (performance.now() - started) / 1000;
      // The input is not shown: it may look like a secret to a log scanner.
      expect(seconds, `${ruleId} on a ${String(text.length)}-character input`).toBeLessThan(
        adversarial.max_seconds,
      );
    }
  });
});
