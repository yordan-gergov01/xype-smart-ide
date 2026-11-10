#!/usr/bin/env node
// Builds the web-tree-sitter grammars listed in grammars.json into grammars/ (ADR-0017).
//
// Trust: npm tarballs are pinned by exact version and sha512, the git source by full
// commit, and the tree-sitter-cli binary by sha256 per platform. Only the generated
// parser that a source ships (src/parser.c, plus its scanner) is compiled; the build
// never runs upstream JavaScript (`grammar.js`, install scripts). The toolchain that
// tree-sitter-cli downloads (wasi-sdk, binaryen) is not verified by us (ADR-0017).
// A grammar whose output matches its manifest entry and this script is skipped.

import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  existsSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT = fileURLToPath(import.meta.url);
const PACKAGE_DIR = join(dirname(SCRIPT), '..');
const OUTPUT_DIR = join(PACKAGE_DIR, 'grammars');
const WORK_DIR = join(PACKAGE_DIR, '.grammar-work');
const STAMP_FILE = join(OUTPUT_DIR, 'stamp.json');
const REGISTRY = 'https://registry.npmjs.org';
const FETCH_TIMEOUT_MS = 120_000;
const ID = /^[a-z0-9_-]+$/;
const NPM_NAME = /^(@[a-z0-9-~][a-z0-9-._~]*\/)?[a-z0-9-~][a-z0-9-._~]*$/;
const VERSION = /^\d+\.\d+\.\d+$/;
const COMMIT = /^[0-9a-f]{40}$/;
const GITHUB_REPO = /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+$/;
// A relative path inside a source: plain segments only, so no `..`, drive or root.
const SOURCE_PATH = /^(\.|[\w-]+(\/[\w-]+)*)$/;
// An empty directory for core.hooksPath, so no hook can run.
const NO_HOOKS = join(WORK_DIR, 'no-hooks');
// Hardened git: https only, no hooks, no LFS or other filters, no symlinks on checkout.
const GIT = [
  ...['-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always'],
  ...['-c', `core.hooksPath=${NO_HOOKS}`, '-c', 'core.symlinks=false'],
  ...['-c', 'filter.lfs.smudge=', '-c', 'filter.lfs.process=', '-c', 'filter.lfs.required=false'],
];

/**
 * @typedef {{ npm: string, version: string, integrity: string }} NpmSource
 * @typedef {{ git: string, commit: string }} GitSource
 * @typedef {{ id: string, source: NpmSource | GitSource, path: string, license: string }} Grammar
 * @typedef {{ version: string, sha256: Record<string, string> }} Cli
 * @typedef {{ cli: Cli, grammars: Grammar[] }} Manifest
 */

class GrammarBuildError extends Error {}

/**
 * Runs a command without a shell and returns its stdout; fails on any error. With
 * `live`, output goes straight to the terminal (long builds and the first toolchain
 * download show progress and cannot overflow a capture buffer).
 * @param {string} command @param {string[]} args
 * @param {{ cwd?: string, input?: Buffer, live?: boolean }} [options]
 */
function run(command, args, { live = false, ...options } = {}) {
  const env = { ...process.env, GIT_LFS_SKIP_SMUDGE: '1', GIT_TERMINAL_PROMPT: '0' };
  const stdio = live ? 'inherit' : 'pipe';
  const result = spawnSync(command, args, { ...options, env, stdio, encoding: 'utf8' });
  const step = `${command} ${args.find((a) => !a.startsWith('-') && !a.includes('=')) ?? ''}`;
  if (result.error) throw new GrammarBuildError(`${step}: ${result.error.message}`);
  if (result.status !== 0) {
    if (!live) process.stderr.write(result.stderr);
    throw new GrammarBuildError(`${step} failed (${String(result.signal ?? result.status)})`);
  }
  return live ? '' : result.stdout.trim();
}

/** @param {string} file */
function readJson(file) {
  try {
    return JSON.parse(readFileSync(file, 'utf8'));
  } catch (error) {
    throw new GrammarBuildError(`${file} is not valid JSON`, { cause: error });
  }
}

/** @param {Grammar} grammar */
function validateGrammar(grammar) {
  const { id, path, source } = grammar;
  const pathOk = SOURCE_PATH.test(path);
  const sourceOk =
    'npm' in source
      ? NPM_NAME.test(source.npm) &&
        VERSION.test(source.version) &&
        String(source.integrity).startsWith('sha512-')
      : GITHUB_REPO.test(source.git) && COMMIT.test(source.commit);
  if (!ID.test(id) || !pathOk || !sourceOk) {
    throw new GrammarBuildError(`grammars.json: invalid entry ${JSON.stringify(id)}`);
  }
}

/** @returns {Manifest} */
function readManifest() {
  /** @type {Manifest} */
  const manifest = readJson(join(PACKAGE_DIR, 'grammars.json'));
  if (!VERSION.test(String(manifest.cli?.version))) {
    throw new GrammarBuildError('grammars.json: invalid cli.version');
  }
  manifest.grammars.forEach(validateGrammar);
  return manifest;
}

/**
 * The installed package's directory and version, looked up in node_modules the way
 * Node resolves it; tree-sitter-cli has neither an entry point nor an export map.
 * @param {string} name
 */
function installedPackage(name) {
  for (let base = PACKAGE_DIR; dirname(base) !== base; base = dirname(base)) {
    const dir = join(base, 'node_modules', name);
    if (existsSync(join(dir, 'package.json'))) {
      return { dir, version: String(readJson(join(dir, 'package.json')).version) };
    }
  }
  throw new GrammarBuildError(`${name} is not installed`);
}

/** Checks the CLI and runtime versions and the CLI binary's hash; returns the binary. */
function checkToolchain(/** @type {Cli} */ cli) {
  const runtime = installedPackage('web-tree-sitter');
  const tool = installedPackage('tree-sitter-cli');
  for (const [name, version] of [
    ['web-tree-sitter', runtime.version],
    ['tree-sitter-cli', tool.version],
  ]) {
    if (version !== cli.version) {
      throw new GrammarBuildError(
        `${String(name)} is ${String(version)}, grammars.json needs ${cli.version}`,
      );
    }
  }
  const platform = `${process.platform}-${process.arch}`;
  const expected = cli.sha256[platform];
  if (expected === undefined) {
    throw new GrammarBuildError(`grammars.json has no tree-sitter-cli hash for ${platform}`);
  }
  const binary = join(tool.dir, process.platform === 'win32' ? 'tree-sitter.exe' : 'tree-sitter');
  if (sha256(readFileSync(binary)) !== expected) {
    throw new GrammarBuildError('the tree-sitter-cli binary does not match its pinned hash');
  }
  return binary;
}

/** @param {Buffer | string} data */
function sha256(data) {
  return createHash('sha256').update(data).digest('hex');
}

/** A fresh directory that is guaranteed to lie inside the work directory. */
function workDir(/** @type {string} */ name) {
  const dir = resolve(WORK_DIR, name);
  if (!dir.startsWith(resolve(WORK_DIR) + sep)) {
    throw new GrammarBuildError(`work path escapes the work directory: ${name}`);
  }
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  return dir;
}

/** Fails on any symlink or hard link in an extracted or checked-out source. */
function assertPlainTree(/** @type {string} */ dir) {
  for (const entry of readdirSync(dir, { recursive: true, withFileTypes: true })) {
    const stat = lstatSync(join(entry.parentPath, entry.name));
    if (stat.isSymbolicLink() || (stat.isFile() && stat.nlink > 1)) {
      throw new GrammarBuildError(`source contains a link: ${entry.name}`);
    }
  }
}

/** @param {NpmSource} source @returns {Promise<string>} the extracted package directory */
async function fetchNpm(source) {
  const base = source.npm.split('/').pop() ?? source.npm;
  const url = `${REGISTRY}/${source.npm}/-/${base}-${source.version}.tgz`;
  const response = await fetch(url, {
    redirect: 'error',
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new GrammarBuildError(`download of ${source.npm}@${source.version} failed`);
  }
  const tarball = Buffer.from(await response.arrayBuffer());
  const integrity = `sha512-${createHash('sha512').update(tarball).digest('base64')}`;
  if (integrity !== source.integrity) {
    throw new GrammarBuildError(`integrity mismatch for ${source.npm}@${source.version}`);
  }
  // Named by the integrity hash, so two packages never share a directory. The verified
  // bytes go to tar on stdin; nothing is re-read from disk.
  const dir = workDir(sha256(source.integrity));
  run('tar', ['-xzf', '-', '--no-same-owner'], { cwd: dir, input: tarball });
  assertPlainTree(dir);
  return join(dir, 'package');
}

/** @param {GitSource} source @returns {string} the checked-out directory */
function fetchGit(source) {
  const dir = workDir(source.commit);
  const at = { cwd: dir };
  mkdirSync(NO_HOOKS, { recursive: true });
  run('git', [...GIT, 'init', '--quiet', '--template='], at);
  run('git', [...GIT, 'fetch', '--quiet', '--depth', '1', '--', source.git, source.commit], at);
  run('git', [...GIT, '-c', 'advice.detachedHead=false', 'checkout', '--quiet', 'FETCH_HEAD'], at);
  if (run('git', [...GIT, 'rev-parse', 'HEAD'], at) !== source.commit) {
    throw new GrammarBuildError(`checked-out commit of ${source.git} does not match the pin`);
  }
  rmSync(join(dir, '.git'), { recursive: true, force: true });
  assertPlainTree(dir);
  return dir;
}

/**
 * Fetches each source once per run (TypeScript and TSX share one).
 * @param {Map<string, Promise<string>>} fetched @param {Grammar['source']} source
 */
function fetchSource(fetched, source) {
  const key = 'npm' in source ? source.integrity : source.commit;
  let root = fetched.get(key);
  if (root === undefined) {
    root = 'npm' in source ? fetchNpm(source) : Promise.resolve(fetchGit(source));
    fetched.set(key, root);
  }
  return root;
}

/** @param {Grammar} grammar @param {string} cli @param {Map<string, Promise<string>>} fetched */
async function buildGrammar(grammar, cli, fetched) {
  const dir = resolve(await fetchSource(fetched, grammar.source), grammar.path);
  if (!existsSync(join(dir, 'src', 'parser.c'))) {
    throw new GrammarBuildError(`${grammar.id}: the source ships no generated src/parser.c`);
  }
  const wasm = join(OUTPUT_DIR, `tree-sitter-${grammar.id}.wasm`);
  run(cli, ['build', '--wasm', '-o', wasm, dir], { live: true });
}

async function main() {
  const manifest = readManifest();
  const cli = checkToolchain(manifest.cli);
  mkdirSync(OUTPUT_DIR, { recursive: true });
  /** @type {Record<string, string>} */
  const stamps = existsSync(STAMP_FILE) ? readJson(STAMP_FILE) : {};
  const script = sha256(readFileSync(SCRIPT));
  const fetched = new Map();
  for (const grammar of manifest.grammars) {
    const print = sha256(JSON.stringify({ grammar, cli: manifest.cli, script }));
    if (
      stamps[grammar.id] === print &&
      existsSync(join(OUTPUT_DIR, `tree-sitter-${grammar.id}.wasm`))
    ) {
      process.stdout.write(`${grammar.id}: up to date\n`);
      continue;
    }
    process.stdout.write(`${grammar.id}: building\n`);
    await buildGrammar(grammar, cli, fetched);
    stamps[grammar.id] = print;
    writeFileSync(STAMP_FILE, `${JSON.stringify(stamps, null, 2)}\n`);
  }
}

try {
  await main();
} catch (error) {
  if (!(error instanceof GrammarBuildError)) throw error;
  process.stderr.write(`build-grammars: ${error.message}\n`);
  process.exitCode = 1;
}
