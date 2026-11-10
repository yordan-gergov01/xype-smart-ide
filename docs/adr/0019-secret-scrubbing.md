# 0019 — Secret and PII scrubbing: path exclusion, then whole-file regex and entropy rules from one shared file, re-checked on the server

- Status: Proposed
- Date: 2026-11-10
- Deciders: project owner
- Brief reference: §3.4.2, §3.7

## Context and problem statement

Code often contains credentials, tokens, connection strings and personal data such as emails. They must be replaced with `<SECRET_REMOVED>` on the client **before** encryption and sending, and a scrubbed secret must never reach a provider adapter (brief §3.7 test). Some files should never leave the client at all (`.env`, private keys). Both the TypeScript client and the Python server need the same rules; if the two disagree, a secret slips through.

## Decision drivers

- Files that exist to hold secrets are never chunked: minimum context (rule 07).
- One source of truth for the rules, tested against one shared corpus on both sides.
- Few false negatives on common secret formats. False positives are tolerable, since a scrubbed hash costs only a little context.
- Deterministic and idempotent output, so chunk hashes (ADR-0016) stay stable and the server re-check finds nothing in clean output.
- Bounded run time: no regex blow-up on large or hostile files.

## Considered options

1. **A path deny-list, then our own JSON rule file (`packages/scrub-rules/rules.json`) applied to the whole file, using regexes in a portable, bounded subset plus an entropy check. The client applies it; the server re-checks it after decryption.**
2. Import the full gitleaks rule set.
3. Entropy only.

## Decision outcome

Chosen option: **1**.

### 1. Path exclusion (client, before reading the file)

These files are never chunked and never produce a `ChunkRef`:
- files ignored by `.gitignore` and by the user's `.xype/ignore`;
- a built-in deny-list: `.env*` (except `.env.example`), `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `.npmrc`, `.pypirc`, `.netrc`, `kubeconfig` and `.kube/`, `credentials.json`, `*.tfstate*`, `.aws/`, `.ssh/`, `*.jks`, `*.keystore`, `.git-credentials`, `.docker/config.json`, `secrets.yaml`, `secrets.yml`;
- files over 1 MiB, and binary files.

The list lives in the same rule file and is tested on both sides.

### 2. Whole-file scrubbing (client, before chunking)

The scrubber runs over the whole file and preserves line count. Chunking (ADR-0017) and hashing (ADR-0016) then work on the scrubbed file, so a key block or JSON credential that spans a chunk boundary is still caught.

**Rules.**
- Each rule has an `id`, a `pattern`, an optional `secret_group` (the part to replace), optional `keywords` (a prefilter) and an optional `case_insensitive`; the loader turns the last into each engine's own flag.
- Patterns stay inside a portable subset:
  - ASCII-explicit classes (`[A-Za-z0-9_]`, never `\w`, `\s` or `.`);
  - no lookbehind, backreferences or inline flags;
  - every repetition bounded (`{0,4096}`, never `*` or `+` over a class that can overlap its neighbour).
- A repo check rejects patterns outside the subset, and adjacent quantified parts whose character classes overlap (`[A-Za-z]{0,64}[A-Za-z0-9]{0,64}`), which backtrack super-linearly.
- Each file has a time budget (100 ms per MiB). A file that exceeds it is treated as denied and never sent: scrubbing fails closed.

**Coverage of the first rule set:**
- private key blocks (`-----BEGIN … PRIVATE KEY-----` to `END`, with a bounded body);
- cloud access keys (AWS, GCP service-account JSON fields, Azure connection strings);
- version-control and CI tokens (GitHub, GitLab), Slack, Stripe, and the paid LLM keys of `tools/paid_api_hosts.txt`;
- JWTs;
- URLs with userinfo (`scheme://user:pass@`);
- `password`, `secret`, `token` and `api_key` assignments in code, YAML, `.env` and Dockerfile syntax;
- email addresses and international phone numbers (PII).

**Entropy.**
- A candidate is a run of at least 20 characters from `[A-Za-z0-9+/=_-]` inside an assignment or a string literal.
- It is scrubbed above a threshold that depends on its alphabet and length. The maximum possible entropy is log2(min(length, alphabet size)), so fixed high thresholds would never fire.
- Starting values:
  - hex: ≥ 3.0 bits per character for 32 characters or more;
  - base64-like: ≥ 3.5 for 20–39 characters, ≥ 4.0 for 40 or more.
- Thresholds are tuned only against the corpus.
- An allow-list (lockfile integrity hashes, git SHAs in known positions, UUIDs outside an assignment) applies to entropy matches only. Keyword rules such as `api_key = <uuid>` take precedence over it.

**Output.**
- Each match is replaced with `<SECRET_REMOVED>` and the line count is kept, so line ranges stay valid.
- The placeholder is never a finding: `scrub(scrub(x)) == scrub(x)` with zero findings.
- The result has a findings count per rule id, never the matched text. Logs and metrics carry only counts (`xype_scrub_findings_total{rule}`).

### 3. Server re-check (indexer, after decryption)

- The indexer runs the same rules on each decrypted chunk. A chunk with findings is not embedded.
- It is counted, and reported back as rejected with a fixed reason. The Merkle sync treats it as settled, so it is not re-uploaded in a loop (ADR-0018).
- The same check runs before any provider adapter call (Phase 3 on).
- It catches differences between the client and server implementations, and chunks from a client that skipped scrubbing. It cannot catch a secret format that the rules themselves miss.

### Consequences

- Good: one rule file, one corpus, two implementations held equal by tests.
- Good: whole-file, line-preserving scrubbing keeps `ChunkRef` line ranges and Merkle ids stable.
- Bad: our own rules cover fewer formats than a large community set. New formats are added as rules together with corpus cases.
- Bad: bounded repetitions can miss a very long secret's tail. The bound is set well above real key sizes.
- Bad: entropy gives false positives on minified code or embedded data. Those chunks lose some context but leak nothing.
- Bad: known PII gaps: IP addresses, personal names and postal addresses are not detected (`docs/privacy.md`).

### Confirmation

- A shared corpus in `packages/scrub-rules/corpus/` holds positive and negative cases. All of them are synthetic, never a real secret, and gitleaks allow-lists that path only.
- The corpus includes:
  - secrets that straddle chunk boundaries;
  - hex keys of 32 and 40 characters, and a UUID used as a key;
  - already-scrubbed text;
  - pathological inputs (an unterminated `BEGIN`, long runs of one character) with a time budget.
- The vitest and pytest suites must give identical output for every case.
- A test that a denied path never produces a `ChunkRef`.
- A privacy test that a chunk with a planted secret never reaches the `Embedder` fake or the mock provider.

## Pros and cons of the options

### 2. The full gitleaks rule set

- Good: broad coverage, maintained.
- Bad: it is written for Go's RE2 dialect. Many patterns behave differently in JavaScript or Python, so translating and testing hundreds of rules is a project of its own.
- Bad: it is tuned for scanning repositories, not for chunks with PII.

### 3. Entropy only

- Good: no rules to maintain.
- Bad: misses low-entropy secrets (passwords, emails) and flags many hashes.

## More information

- `common_lib/secret_bytes.py` (wiping after the check), ADR-0015 (server re-check in the indexer), ADR-0016 (hash over the scrubbed text), ADR-0018 (rejected chunks settle).
- A linear-time engine (RE2 bindings on both sides) is the fallback if the bounded subset proves insufficient. It would mean a native dependency on the client.
