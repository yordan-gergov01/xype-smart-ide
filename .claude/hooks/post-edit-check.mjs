// PostToolUse(Edit|Write) check: enforce the 500-line source limit and run the formatter/linter
// for the edited file when the toolchain is installed. Exit 2 = feedback to Claude (stderr).
import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { PROJECT_DIR, loadSizeLimits, readStdinJson, relToProject } from './lib.mjs';

const TIMEOUT_MS = 20_000;
// The server's virtual environment (server/README.md); its layout differs per platform.
const SERVER_BIN = path.join(
  PROJECT_DIR,
  'server',
  '.venv',
  process.platform === 'win32' ? 'Scripts' : 'bin',
);

/**
 * @param {string} cmd
 * @param {string[]} args
 * @param {string} [cwd]
 */
function run(cmd, args, cwd = PROJECT_DIR) {
  const r = spawnSync(cmd, args, {
    cwd,
    encoding: 'utf8',
    timeout: TIMEOUT_MS,
    shell: false,
  });
  const reason = r.error ? `${r.error.message}\n` : '';
  return { ok: r.status === 0, out: `${reason}${r.stdout ?? ''}${r.stderr ?? ''}`.trim() };
}

/** @param {string} rel */
function sizeProblem(rel) {
  const limits = loadSizeLimits();
  if (!limits.extensions.has(path.extname(rel)) || limits.exempt.some((re) => re.test(rel)))
    return null;
  const lines = readFileSync(path.join(PROJECT_DIR, rel), 'utf8').split(/\r?\n/);
  const count = lines.at(-1) === '' ? lines.length - 1 : lines.length;
  return count > limits.maxFileLines
    ? `${rel} has ${count} lines (limit ${limits.maxFileLines}). Split it by responsibility.`
    : null;
}

/** @param {string} rel */
function lintProblem(rel) {
  const abs = path.join(PROJECT_DIR, rel);
  const ruff = path.join(SERVER_BIN, process.platform === 'win32' ? 'ruff.exe' : 'ruff');
  if (rel.startsWith('server/') && rel.endsWith('.py') && existsSync(ruff)) {
    const serverDir = path.join(PROJECT_DIR, 'server');
    run(ruff, ['format', abs], serverDir);
    const check = run(ruff, ['check', '--fix', abs], serverDir);
    return check.ok ? null : `ruff:\n${check.out}`;
  }
  const eslint = path.join(PROJECT_DIR, 'node_modules', 'eslint', 'bin', 'eslint.js');
  if (/\.(ts|tsx|js|jsx|mjs|cjs)$/.test(rel) && existsSync(eslint)) {
    const check = run(process.execPath, [eslint, '--fix', abs]);
    return check.ok ? null : `eslint:\n${check.out}`;
  }
  return null;
}

const input = readStdinJson();
const rel = relToProject(input?.tool_input?.file_path);
if (!rel || !existsSync(path.join(PROJECT_DIR, rel))) process.exit(0);

const problems = [sizeProblem(rel), lintProblem(rel)].filter(Boolean);
if (problems.length) {
  process.stderr.write(`post-edit-check: ${problems.join('\n')}\n`);
  process.exit(2);
}
process.exit(0);
