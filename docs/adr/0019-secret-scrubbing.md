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
- A unit test (`server/tests/unit/common_lib/test_scrubber.py`) rejects patterns outside the subset, and adjacent quantified parts whose character classes overlap (`[A-Za-z]{0,64}[A-Za-z0-9]{0,64}`), which backtrack super-linearly.
- A rule may list `keywords`; it runs only if the text contains one (`-----END` for key blocks, `.eyJ` for JWTs). This keeps repeated rule prefixes cheap.
- Each file has a time budget (`time_budget_ms_per_mib` 1 000, at least `min_time_budget_ms` 50), measured on the input size. Measured on 2026-11-10 in Python, per MiB: about 0.37 s of real code; up to about 0.7 s for the 1 MiB pathological tests; and up to about 2 s for hostile input built to defeat the keyword filter (repeated key headers ending in one `-----END`, repeated `eyJ` ending in `.eyJ`). All are linear. A file over its budget is treated as denied and never sent: scrubbing fails closed. Python cannot interrupt a running match, so the bounded subset is the real protection and the budget is the last check. The server re-check runs per chunk, which the indexer caps at 64 KiB (slice 8), in a child process that is killed when it runs past its time (section 3). Headroom for real code is under 3× the budget, so a heavily loaded machine could deny a large legitimate file; the chunk cap keeps the server side far from it, and a client denial is reported, not silent.

**Coverage of the first rule set:**
- private key blocks (`-----BEGIN … PRIVATE KEY-----` to `END`, with a bounded body);
- cloud access keys (AWS, GCP service-account JSON fields, Azure connection strings);
- version-control and CI tokens (GitHub, GitLab), Slack, Stripe, and the paid LLM keys of `tools/paid_api_hosts.txt`;
- JWTs;
- URLs with userinfo (`scheme://user:pass@`);
- `password`, `secret`, `token`, `api_key`, `private_key` and similar assignments in code, YAML, `.env` and Dockerfile syntax, including typed ones (`password: str = …`, `const token: string = …`);
- `Bearer` and `Token` credentials in headers;
- private keys inside JSON strings (`\n` escapes, as in service-account files);
- email addresses and international phone numbers (PII).

**Entropy.**
- A candidate is a run of at least 20 characters from `[A-Za-z0-9+/=_-]` inside an assignment or a string literal.
- It is scrubbed above a threshold that depends on its alphabet and length. The maximum possible entropy is log2(min(length, alphabet size)), so fixed high thresholds would never fire.
- Starting values:
  - hex: ≥ 3.0 bits per character for 32 characters or more;
  - base64-like: ≥ 3.5 for 20–39 characters, ≥ 4.0 for 40 or more.
- Thresholds are tuned only against the corpus.
- A non-hex candidate must contain both a digit and a letter, so identifiers and words in strings (`"DefaultEndpointsProtocol"`) are not scrubbed.
- An allow-list applies to entropy matches only: lockfile integrity hashes (`sha512-…`), UUIDs, and paths whose segments are words with at most two trailing digits (`src/components/Header-v2`). Keyword rules such as `api_key = <uuid>` run first and take precedence over it.

**Output.**
- Each match is replaced with `<SECRET_REMOVED>` and the line count is kept, so line ranges stay valid.
- The placeholder is never a finding: `scrub(scrub(x)) == scrub(x)` with zero findings.
- The result has a findings count per rule id, never the matched text. Logs and metrics carry only counts (`xype_scrub_findings_total{rule}`).

### 3. Server re-check (indexer, after decryption)

- The indexer runs the same rules on each decrypted chunk. A chunk with findings is not embedded.
- It is counted, and reported back as rejected with a fixed reason. The Merkle sync treats it as settled, so it is not re-uploaded in a loop (ADR-0018).
- The same check runs before any provider adapter call (Phase 3 on).
- The rejection is stored on the leaf (`merkle_nodes.rejection_reason`, slice 8c2). Accepted residual: someone with database access learns which leaf (an HMAC id, its file's HMAC id and its line range) had a secret in the client's working copy, never the secret. The client learns the same from `IndexAck.rejected`. The reason never appears in logs or metric labels per leaf.
- The check runs in child processes (`indexer.scan`, slice 8c1), two at a time, because a thread cannot be stopped. The chunk goes to the child over its stdin pipe, in memory only. The child gets only the variables the interpreter needs to start, never the KEK or a password, and it does not log. A scan still running after 2 s is killed, the chunk settles as `unscannable` (fail closed), and a fresh child takes its place. A child is ready only once it has loaded its rules, so its start never counts against a chunk's time. A killed child is not replaced at once: the next scan starts one when none is free, so a start that fails is a retry of that chunk (`/readyz` turns false), never a pool that shrinks for good or a scan that hangs. A child found dead while idle is replaced without blaming the next chunk. The container has no core files (`ulimits: core: 0`). The children run as the worker's own user: an environment without credentials keeps them from inheriting secrets, but a child that ran hostile code could still read the parent's `/proc/<pid>/environ`. Only a separate user would close that, and a child only runs the rules on text. The indexer settings `SCAN_TIMEOUT_S` (2 s) and `SCAN_WORKERS` (2, about 31 MiB each) tune it.
- It catches differences between the client and server implementations, and chunks from a client that skipped scrubbing. It cannot catch a secret format that the rules themselves miss.

### Consequences

- Good: one rule file, one corpus, two implementations held equal by tests.
- Good: whole-file, line-preserving scrubbing keeps `ChunkRef` line ranges and Merkle ids stable.
- Bad: our own rules cover fewer formats than a large community set. New formats are added as rules together with corpus cases.
- Bad: bounded repetitions can miss a very long secret's tail. The bound is set well above real key sizes.
- Bad: entropy gives false positives on minified code or embedded data. Those chunks lose some context but leak nothing.
- Bad: known PII gaps: IP addresses, personal names and postal addresses are not detected (`docs/privacy.md`).

### Confirmation

- A shared corpus in `packages/scrub-rules/corpus.json` holds positive and negative cases. All of them are synthetic, never a real secret, and each input and expected text is stored as fragments of at most 8 characters that the tests join, so no secret scanner (gitleaks, GitHub push protection) sees a whole token and no allow-list is needed.
- The corpus includes:
  - secrets that straddle chunk boundaries;
  - hex keys of 32 and 40 characters, and a UUID used as a key;
  - already-scrubbed text;
  - pathological inputs (an unterminated `BEGIN`, long runs of one character) with a time budget.
- The vitest and pytest suites must give identical output for every case.
- A ReDoS check (`server/tests/unit/common_lib/test_scrub_redos.py`, in CI with the unit tests) runs each rule alone on the adversarial inputs of `packages/scrub-rules/adversarial.json`. Each input is a trigger for that rule followed by a pump character, in three shapes, cut to 64 KiB, and each must scan in under 1 s. Every rule id needs triggers there, so a new rule cannot skip the check. A self-test shows that the check catches a bounded but quadratic pattern (`[a-z]{0,20000}x`). Measured on 2026-11-17: the slowest input takes about 0.17 s (the email rule); 1 MiB of the worst shape takes about 0.45 s, linear.
- The client (slice 9, `clients/xype-ai/src/security/`) passes the whole corpus, idempotence included, and runs the same adversarial inputs on V8 in vitest, with the same self-test for a quadratic pattern. Measured on 2026-11-25 on the developer machine: the whole ReDoS suite takes about 2 s.
- `denied_paths` (client, `paths.ts`) uses a small gitignore-like syntax matched case-insensitively on the normalised path, so a Windows file system cannot let `.ENV` through: `name/` denies every file under a directory of that name, a pattern with an inner `/` matches the end of the path, any other pattern is a basename glob (`*` within one segment), and `!pattern` allows again (the last match wins; `!.env.example`). A `*` anywhere else is refused when the rules load, since it would match only literally. A path the client cannot normalise (`..`, an absolute path) is excluded as `bad_path`, never fatal to the walk. Files over `max_file_bytes`, with a NUL byte or not valid UTF-8 are excluded too, since a lossy decode would change what is hashed; CRLF becomes LF before scrubbing (ADR-0016). `.gitignore` and `.xype/ignore` are applied by the workspace walker (slice 11).
- The workspace walker (slice 11a, `clients/xype-ai/src/sync/walker.ts`) reads a file only after `.gitignore` (every directory, git's precedence, through the `ignore` package), `.xype/ignore` (it excludes, and cannot bring back what git ignores), `denied_paths` and the size from `fstat` all passed. It never follows a symbolic link or junction and skips `.git`. Tested with temporary repositories.
- The client scrubs in a worker thread (`scrubWorker.ts`). The scrubber's own budget is checked only after a rule returns, so the thread is terminated past a hard limit (twice the budget, at least 1 s) and the file is excluded (`scrub_timeout`), never sent unscrubbed. Tested with a rule that backtracks exponentially.
- Only the scrubber produces `ScrubbedText`. `chunkFile` and `ClientCryptoBox.sealText` accept only it, so unscrubbed text cannot reach chunking or sealing by mistake. ESLint refuses an `as ScrubbedText` cast and a raw `seal` call outside `src/security`, the chunk planner and tests.
- The walker checks after each open that the handle and the path name the same regular file and that the path still resolves inside the repository, and lists a directory only while it does, so a file or directory swapped for a link during the walk is not read. File system errors become a `WalkError` with only the error code, since Node's messages name paths. Accepted: a hard link to a file outside the repository cannot be told apart from the file.
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
