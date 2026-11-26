// Secret and PII scrubbing with the shared rules in `packages/scrub-rules/rules.json`
// (ADR-0019), the same algorithm as the server's `common_lib.security.scrubber`: the
// shared corpus is the contract. The client scrubs whole files before chunking.
//
// Matches become the placeholder plus the newlines they spanned, so line numbers stay
// valid. The placeholder never matches a rule, so scrubbing is idempotent. Results carry
// counts per rule, never the matched text. Pure and synchronous: callers run it off the
// extension host thread.

export class ScrubTimeoutError extends Error {
  override name = 'ScrubTimeoutError';
}

export class RulesError extends Error {
  override name = 'RulesError';
}

export interface ScrubRule {
  readonly id: string;
  readonly pattern: RegExp;
  readonly group: number;
  // The rule runs only if the input contains one (lowercase when it ignores case).
  readonly keywords: readonly string[];
  readonly caseInsensitive: boolean;
}

export interface Threshold {
  readonly minLength: number;
  readonly bits: number;
}

export interface ScrubRules {
  readonly placeholder: string;
  readonly budgetMsPerMib: number;
  readonly minBudgetMs: number;
  readonly rules: readonly ScrubRule[];
  readonly entropy: ScrubRule;
  readonly hexThreshold: Threshold;
  readonly otherThresholds: readonly Threshold[];
  readonly otherRequires: readonly RegExp[];
  readonly allow: readonly RegExp[];
}

declare const scrubbed: unique symbol;

/**
 * Text the scrubber returned: the only text chunking and sealing accept. Produced only
 * in `src/security` (here and in the scrub worker); any other cast defeats ADR-0019.
 */
export type ScrubbedText = string & { readonly [scrubbed]: true };

export interface ScrubResult {
  readonly text: ScrubbedText;
  readonly findings: ReadonlyMap<string, number>;
}

type Json = Record<string, unknown>;

function field<T>(data: Json, key: string, check: (value: unknown) => value is T): T {
  const value = data[key];
  if (!check(value)) throw new RulesError(`rules: ${key} is missing or malformed`);
  return value;
}

const isString = (v: unknown): v is string => typeof v === 'string';
const isNumber = (v: unknown): v is number => typeof v === 'number';
const isObject = (v: unknown): v is Json => typeof v === 'object' && v !== null;
const isList = (v: unknown): v is unknown[] => Array.isArray(v);

function stringList(data: Json, key: string): string[] {
  const values = data[key] ?? [];
  if (!isList(values) || !values.every(isString)) throw new RulesError(`rules: ${key}`);
  return values;
}

function compileRule(entry: Json, patternKey = 'pattern'): ScrubRule {
  const caseInsensitive = entry['case_insensitive'] === true;
  const keywords = stringList(entry, 'keywords');
  return {
    id: field(entry, 'id', isString),
    // `d` gives the group's offsets; without `u`, `i` never folds non-ASCII to ASCII.
    pattern: new RegExp(field(entry, patternKey, isString), caseInsensitive ? 'dgi' : 'dg'),
    group: isNumber(entry['secret_group']) ? entry['secret_group'] : 0,
    keywords: caseInsensitive ? keywords.map((k) => k.toLowerCase()) : keywords,
    caseInsensitive,
  };
}

function threshold(entry: unknown): Threshold {
  if (!isObject(entry)) throw new RulesError('rules: a threshold is malformed');
  return {
    minLength: field(entry, 'min_length', isNumber),
    bits: field(entry, 'threshold', isNumber),
  };
}

/** `data` is the parsed `rules.json`. */
export function parseRules(data: unknown): ScrubRules {
  if (!isObject(data)) throw new RulesError('rules: not an object');
  const entropy = field(data, 'entropy', isObject);
  return {
    placeholder: field(data, 'placeholder', isString),
    budgetMsPerMib: field(data, 'time_budget_ms_per_mib', isNumber),
    minBudgetMs: field(data, 'min_time_budget_ms', isNumber),
    rules: field(data, 'rules', isList).map((r) => {
      if (!isObject(r)) throw new RulesError('rules: a rule is malformed');
      return compileRule(r);
    }),
    entropy: compileRule(entropy, 'candidate'),
    hexThreshold: threshold(field(entropy, 'hex', isObject)),
    otherThresholds: field(entropy, 'other', isList)
      .map(threshold)
      .sort((a, b) => a.minLength - b.minLength),
    otherRequires: stringList(entropy, 'other_requires').map((p) => new RegExp(p)),
    allow: stringList(entropy, 'allow').map((p) => new RegExp(p)),
  };
}

/** Bits per character of the token's own distribution; tokens are ASCII (the candidate class). */
export function shannonEntropy(token: string): number {
  const counts = new Map<string, number>();
  for (const ch of token) counts.set(ch, (counts.get(ch) ?? 0) + 1);
  const length = token.length;
  let bits = 0;
  for (const n of counts.values()) bits -= (n / length) * Math.log2(n / length);
  return bits;
}

const HEX = /^[0-9a-fA-F]+$/;
const encoder = new TextEncoder();

/** How long scrubbing `input` may take: the shared per-MiB budget, at least the minimum. */
export function scrubBudgetMs(rules: ScrubRules, input: string): number {
  const mib = encoder.encode(input).length / 2 ** 20;
  return Math.max(rules.budgetMsPerMib * mib, rules.minBudgetMs);
}

export class SecretScrubber {
  private readonly rules: ScrubRules;

  constructor(rules: ScrubRules) {
    this.rules = rules;
  }

  /** Throws `ScrubTimeoutError` when the text took longer than its budget. */
  scrub(input: string): ScrubResult {
    const started = performance.now();
    const budget = scrubBudgetMs(this.rules, input);
    const findings = new Map<string, number>();
    // Keywords are checked on the input: a replacement can remove one, never add one.
    const folded = input.toLowerCase();
    let text = input;
    for (const rule of [...this.rules.rules, this.rules.entropy]) {
      const haystack = rule.caseInsensitive ? folded : input;
      if (rule.keywords.length > 0 && !rule.keywords.some((k) => haystack.includes(k))) continue;
      const accept =
        rule === this.rules.entropy ? (t: string) => this.isHighEntropy(t) : () => true;
      text = this.replace(rule, text, findings, accept);
    }
    if (performance.now() - started > budget) {
      throw new ScrubTimeoutError('scrubbing exceeded its time budget');
    }
    return { text: text as ScrubbedText, findings };
  }

  private replace(
    rule: ScrubRule,
    text: string,
    findings: Map<string, number>,
    accept: (token: string) => boolean,
  ): string {
    let out = '';
    let last = 0;
    for (const match of text.matchAll(rule.pattern)) {
      const secret = match[rule.group];
      const span = match.indices?.[rule.group];
      if (secret === undefined || span === undefined || !accept(secret)) continue;
      findings.set(rule.id, (findings.get(rule.id) ?? 0) + 1);
      const newlines = secret.split('\n').length - 1;
      out += text.slice(last, span[0]) + this.rules.placeholder + '\n'.repeat(newlines);
      last = span[1];
    }
    return out + text.slice(last);
  }

  private isHighEntropy(token: string): boolean {
    const rules = this.rules;
    if (rules.allow.some((p) => p.test(token))) return false;
    let limit: Threshold | undefined;
    if (HEX.test(token)) {
      if (token.length < rules.hexThreshold.minLength) return false;
      limit = rules.hexThreshold;
    } else if (!rules.otherRequires.every((p) => p.test(token))) {
      // Identifiers and words ("DefaultEndpointsProtocol") are not keys.
      return false;
    } else {
      limit = rules.otherThresholds.findLast((t) => token.length >= t.minLength);
    }
    return limit !== undefined && shannonEntropy(token) >= limit.bits;
  }
}
