// PostToolUse(Edit|Write) check: enforce the 500-line source limit and run the formatter/linter
// for the edited file when the toolchain is installed. Exit 2 = feedback to Claude (stderr).
import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { PROJECT_DIR, loadSizeLimits, readStdinJson, relToProject } from './lib.mjs';

const TIMEOUT_MS = 20_000;

function run(cmd, args) {
  const r = spawnSync(cmd, args, {
    cwd: PROJECT_DIR,
    encoding: 'utf8',
    timeout: TIMEOUT_MS,
    shell: false,
  });
  return {
    ok: r.status === 0,
    out: `${r.stdout ?? ''}${r.stderr ?? ''}`.trim(),
    missing: r.error?.code === 'ENOENT',
  };
}

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

function lintProblem(rel) {
  const abs = path.join(PROJECT_DIR, rel);
  if (rel.endsWith('.py') && existsSync(path.join(PROJECT_DIR, 'server', 'pyproject.toml'))) {
    run('uv', ['run', '--directory', 'server', 'ruff', 'format', abs]);
    const check = run('uv', ['run', '--directory', 'server', 'ruff', 'check', '--fix', abs]);
    return check.ok || check.missing ? null : `ruff:\n${check.out}`;
  }
  const eslint = path.join(PROJECT_DIR, 'node_modules', 'eslint', 'bin', 'eslint.js');
  if (/\.(ts|tsx|js|jsx|mjs|cjs)$/.test(rel) && existsSync(eslint) && !rel.startsWith('.claude/')) {
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
