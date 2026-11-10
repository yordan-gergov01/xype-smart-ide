# 0017 — AST chunking with web-tree-sitter: all grammars built to WebAssembly from pinned sources

- Status: Proposed
- Date: 2026-11-10
- Deciders: project owner
- Brief reference: §1 (web-tree-sitter), §3.4.2 (AST chunking for Python, TypeScript/TSX, JS, SQL, YAML, Dockerfile, Markdown)

## Context and problem statement

The client context engine chunks code along its syntax tree (functions, classes, blocks) with `web-tree-sitter`, which loads one WebAssembly grammar per language. The grammars must load under the pinned runtime, ship inside the extension, and come from sources we can audit. Risk R4 (`docs/plan.md`) expected that some grammars would have no usable prebuilt `.wasm`.

Spike (2026-11-10) with web-tree-sitter **0.27.1**:
- **Prebuilt in npm.** Python 0.25.0, TypeScript and TSX 0.23.2, JavaScript 0.25.0 and YAML 0.7.1 ship `.wasm` files. Each loads, and its sample parses without errors (ABI 14 or 15).
- **No `.wasm` in npm.** SQL (`@derekstride/tree-sitter-sql` 0.3.12) and Markdown (`@tree-sitter-grammars/tree-sitter-markdown` 0.3.2) ship only the generated C parser.
- **Placeholder package.** The npm name `tree-sitter-dockerfile` is a security placeholder that npm left in place of the original package. The real grammar is `camdencheek/tree-sitter-dockerfile` on GitHub, which is not on npm.
- **Built from source.** `tree-sitter build --wasm` (tree-sitter-cli 0.27.1) built SQL, Markdown and Dockerfile. All three load and parse their samples without errors (ABI 14 or 15).
- **npm grammar packages as dependencies fail.** Their optional `tree-sitter` peer ranges conflict, so `npm install` fails with ERESOLVE. Each one also runs a native `node-gyp-build` install script that we do not need.

The owner chose to build the missing grammars from source (2026-11-10).

## Decision drivers

- All seven languages chunk by AST, as the brief requires.
- Sources are pinned and verified: exact versions with checksums, or full commit hashes.
- The build runs no upstream JavaScript (`grammar.js`, grammar install scripts), and no peer conflicts enter the monorepo.
- The extension loads every grammar the same way.
- CI does not download the compiler toolchain on every run.

## Considered options

1. **Build every grammar to `.wasm` from pinned sources with tree-sitter-cli, through one script and one manifest.**
2. Use the prebuilt `.wasm` from npm grammar packages where it exists, and build only the rest.
3. Simple non-AST chunkers for SQL (statements), Markdown (headings) and Dockerfile (instructions).

## Decision outcome

Chosen option: **1**.

### Manifest

`clients/xype-ai/grammars.json` lists, for each grammar:
- its id;
- the directory inside the source that holds `src/parser.c` (TypeScript and TSX share one source; Markdown uses only its block parser);
- its licence;
- its source: an npm tarball pinned by exact version and `sha512` integrity, or a git repository pinned by full commit.

It also names the toolchain version (`0.27.1`), which must match both the installed `tree-sitter-cli` and `web-tree-sitter`, and the `sha256` of the tree-sitter-cli binary per platform (`linux-x64`, `win32-x64`).

| Grammar | Source | Pin | Licence |
|---|---|---|---|
| Python | npm `tree-sitter-python` | 0.25.0 + sha512 | MIT |
| TypeScript, TSX | npm `tree-sitter-typescript` | 0.23.2 + sha512 | MIT |
| JavaScript | npm `tree-sitter-javascript` | 0.25.0 + sha512 | MIT |
| YAML | npm `@tree-sitter-grammars/tree-sitter-yaml` | 0.7.1 + sha512 | MIT |
| SQL | npm `@derekstride/tree-sitter-sql` | 0.3.12 + sha512 | MIT |
| Markdown | npm `@tree-sitter-grammars/tree-sitter-markdown` | 0.3.2 + sha512 | MIT |
| Dockerfile | git `camdencheek/tree-sitter-dockerfile` | `868e44ce…` (tag v0.2.0) | MIT (`LICENSE`; its `package.json` says ISC, the `LICENSE` file is taken as authoritative) |

### Build (`npm run grammars -w xype-ai`, `scripts/build-grammars.mjs`)

- **Manifest checks.** Ids, package names, versions, commits and paths are validated (no absolute paths, no `..`), and every work directory must lie inside `.grammar-work/`.
- **npm sources.** The script downloads the tarball from the registry, without redirects and with a timeout, and checks its `sha512` against the manifest. The verified bytes go to `tar` on stdin. Nothing is installed, so no install script runs.
- **No links.** After extraction or checkout, any symlink or hard link in the source fails the build.
- **The git source.** Only `https://github.com/<owner>/<repo>` URLs are accepted. Git runs with `protocol.allow=never` except https, no hooks, no LFS or other filters and no symlinks. The source is fetched by commit hash, not by tag, after a `--` separator, and `HEAD` is checked against the pin after checkout.
- **What is compiled.** The script requires the generated `src/parser.c` that a source ships. It never runs `tree-sitter generate`, which would execute the upstream `grammar.js`.
- **Output.** `tree-sitter build --wasm` writes the files to `clients/xype-ai/grammars/`, which git ignores.
- **Skipping.** A stamp file records a fingerprint of each manifest entry, the toolchain and the build script itself. Up-to-date grammars are skipped.

### Toolchain

Three downloads are trusted:
- **The tree-sitter-cli binary.** Its npm install script downloads it from the tree-sitter GitHub release on every `npm ci`, without a checksum. The build script checks it against the `sha256` pinned in `grammars.json` before running it, so a replaced binary is refused at build time, but the install script itself has already run by then.
- **wasi-sdk 34 (≈ 590 MB) and binaryen 132 (≈ 108 MB).** tree-sitter-cli downloads them on first use from their GitHub releases into the user cache (`%LOCALAPPDATA%\tree-sitter` on Windows, `~/.cache/tree-sitter` on Linux). We do not verify them. Pinning their hashes is a follow-up.

The compiled parsers include each grammar's hand-written `src/scanner.c`. They run inside the WebAssembly sandbox of web-tree-sitter in the extension, over the developer's code, which is why every source is pinned.

### CI

- The `node` job restores `clients/xype-ai/grammars` (≈ 17 MB) from a cache keyed on `grammars.json`, the build script and `package-lock.json`, and saves it right after a build. A cache hit skips every build, so the compiler toolchain is fetched only when one of those changes. Release builds (Phase 8) build the grammars fresh and do not use this cache.
- The grammar test runs with `XYPE_REQUIRE_GRAMMARS=1`, so a missing grammar fails CI instead of skipping. Locally the grammar tests are reported as skipped (their title names `npm run grammars`) when the grammars have not been built, so `npm run check` does not need the toolchain. A test also checks that `grammars.json` and `LANGUAGE_IDS` list the same languages.

### Runtime

`src/context/grammars.ts` initialises `web-tree-sitter` once and loads a grammar by language id. A load failure raises `GrammarError`. Chunking (slice 10) keeps a line-window fallback for files that fail to parse.

### Consequences

- Good: all seven languages are AST-based; one loader and one build path cover them.
- Good: every source and the CLI binary are pinned and verified, no upstream JavaScript runs, and the monorepo gains no peer conflicts and no grammar install scripts.
- Bad: tree-sitter-cli's own install script downloads its binary on every `npm ci`; the hash check happens only when grammars are built.
- Bad: the first local build downloads about 700 MB of toolchain, and the first CI run after a pin change does too.
- Bad: the SQL grammar is 12.5 MB of WebAssembly. Packaging (Phase 8) may load it lazily.
- Bad: a grammar update means editing the manifest (version, checksum or commit) and rebuilding.
- Bad: Phase 8 bundling must add third-party notices for the seven grammars and for web-tree-sitter.

### Confirmation

- `clients/xype-ai/src/context/grammars.test.ts` loads all eight grammar files (seven languages, with TypeScript and TSX separate) and parses one sample each with no error nodes. It is required in CI.
- The build script refuses a checksum mismatch, a CLI binary with another hash, an invalid manifest entry (for example a git URL starting with `--`), a git checkout that does not match its pin, a source with links or without `src/parser.c`, and a toolchain version that differs from the manifest. Each refusal was checked by hand on 2026-11-10.
- Follow-ups: pin wasi-sdk and binaryen hashes; a network guard for vitest like the server's (ADR-0012); a package entry point (`src/index.ts` and `exports`) when the second module arrives.

## Pros and cons of the options

### 2. Prebuilt `.wasm` where available

- Good: no toolchain for five of the seven.
- Bad: npm grammar packages as dependencies bring ERESOLVE peer conflicts and native install scripts. Extracting their tarballs avoids that but still leaves two kinds of grammar sources. The toolchain is still needed for the other three.

### 3. Simple chunkers for SQL, Markdown and Dockerfile

- Good: no toolchain at all.
- Bad: it deviates from the brief's "AST for all seven", and SQL with functions and procedures is chunked poorly.

## More information

- Spike scripts and outputs: not kept (scratch). The result is recorded here and in `grammars.test.ts`.
- Risk R4 in `docs/plan.md`; ADR-0008 (npm workspaces, vitest added with this first TypeScript package).
