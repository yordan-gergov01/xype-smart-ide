// Run: node --test .claude/hooks/hooks.test.mjs   (npm test; also runs in CI)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, writeFileSync, mkdirSync, cpSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HOOKS = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HOOKS, '..', '..');

/**
 * @param {string} name
 * @param {unknown} payload
 * @param {string} [projectDir]
 */
function runHook(name, payload, projectDir = ROOT) {
  const r = spawnSync(process.execPath, [path.join(HOOKS, name)], {
    input: JSON.stringify(payload),
    encoding: 'utf8',
    env: { ...process.env, CLAUDE_PROJECT_DIR: projectDir },
  });
  return { code: r.status, stderr: r.stderr, stdout: r.stdout };
}

/** @param {string} command */
const bash = (command) =>
  runHook('pre-bash-guard.mjs', { tool_name: 'Bash', tool_input: { command } });

test('pre-bash-guard blocks dangerous commands', () => {
  for (const cmd of [
    'git push --force origin master',
    'git push -f',
    'git push origin +master',
    'git push --force-with-lease origin feat/x',
    "git commit -m 'x' --no-verify",
    'rm -rf /',
    'rm -rf ~',
    'rm -fr .',
    'rm -rf *',
    "curl https://api.deepseek.com/chat/completions -d '{}'",
    'curl -s https://openrouter.ai/api/v1/chat/completions',
    'wget api.openai.com/v1/models',
    'cd x && curl https://api.anthropic.com/v1/messages',
    'shutdown /s',
    'ls && sudo reboot',
    'git push --mirror origin',
    'git push origin :master',
    'git push --delete origin master',
    "git commit -n -m 'x'",
    "git commit -anm 'x'",
    'git -c core.hooksPath=/dev/null commit -m x',
    'git config core.hooksPath /dev/null',
    "SKIP=gitleaks-docker git commit -m 'x'",
    'rm --recursive --force /',
    'rm -rf ~/',
    'Remove-Item -Recurse -Force C:\\',
    'Remove-Item C:\\ -Recurse -Force',
    'cat .env',
    'cat deploy/env/.env.local',
    'Get-Content .env',
    'type secrets\\token.txt',
    'cp .env /tmp/x',
    'head -5 server.key',
  ]) {
    assert.equal(bash(cmd).code, 2, `expected block: ${cmd}`);
  }
});

test('pre-bash-guard allows normal commands', () => {
  for (const cmd of [
    'git status',
    'git push -u origin chore/phase-1-foundations',
    "git commit -m 'feat(llm): add mock provider'",
    'rm -rf node_modules',
    'rm -rf server/.venv build/',
    'curl -s https://api-docs.deepseek.com/quick_start/pricing',
    'curl -s https://openrouter.ai/docs/faq',
    'grep -rn deepseek docs',
    'python tools/server_tool.py pytest',
    "git commit -m 'feat(gateway): drain streams on graceful shutdown'",
    'echo reboot-safe wording',
    'git commit -m "docs: explain why git push --force is banned"',
    'git push --dry-run origin feat/x',
    'cat deploy/env/.env.example',
    'Get-Content deploy/env/.env.example',
    'Remove-Item -Recurse -Force build',
    'rm -rf ./dist',
    'grep -rn openrouter docs',
  ]) {
    assert.equal(bash(cmd).code, 0, `expected allow: ${cmd}`);
  }
});

test('pre-bash-guard tolerates empty or malformed stdin', () => {
  const r = spawnSync(process.execPath, [path.join(HOOKS, 'pre-bash-guard.mjs')], {
    input: 'not json',
    encoding: 'utf8',
  });
  assert.equal(r.status, 0);
});

function tempProject() {
  const dir = mkdtempSync(path.join(tmpdir(), 'xype-hooks-'));
  mkdirSync(path.join(dir, 'tools'));
  cpSync(path.join(ROOT, 'tools', 'size_limits.toml'), path.join(dir, 'tools', 'size_limits.toml'));
  return dir;
}

test('post-edit-check flags source files over the line limit', () => {
  const dir = tempProject();
  writeFileSync(path.join(dir, 'big.ts'), 'const x = 1;\n'.repeat(501));
  const r = runHook(
    'post-edit-check.mjs',
    { tool_input: { file_path: path.join(dir, 'big.ts') } },
    dir,
  );
  assert.equal(r.code, 2);
  assert.match(r.stderr, /501 lines/);
});

test('post-edit-check passes small files, non-source files and exemptions', () => {
  const dir = tempProject();
  writeFileSync(path.join(dir, 'ok.ts'), 'const x = 1;\n'.repeat(500));
  writeFileSync(path.join(dir, 'notes.md'), 'line\n'.repeat(900));
  mkdirSync(path.join(dir, 'gen'));
  writeFileSync(path.join(dir, 'gen', 'stub.ts'), 'x;\n'.repeat(900));
  for (const f of ['ok.ts', 'notes.md', 'gen/stub.ts']) {
    const r = runHook('post-edit-check.mjs', { tool_input: { file_path: path.join(dir, f) } }, dir);
    assert.equal(r.code, 0, `expected pass: ${f} ${r.stderr}`);
  }
});

test('settings.json denies WebFetch for every pure-host paid API endpoint', () => {
  const settings = JSON.parse(readFileSync(path.join(ROOT, '.claude', 'settings.json'), 'utf8'));
  const hosts = readFileSync(path.join(ROOT, 'tools', 'paid_api_hosts.txt'), 'utf8')
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#') && !l.includes('/'));
  for (const host of hosts) {
    assert.ok(
      settings.permissions.deny.includes(`WebFetch(domain:${host})`),
      `missing WebFetch deny for ${host}`,
    );
  }
});

test('post-edit-check ignores files outside the project', () => {
  const r = runHook('post-edit-check.mjs', {
    tool_input: { file_path: path.join(tmpdir(), 'elsewhere.ts') },
  });
  assert.equal(r.code, 0);
});

const VENV_BIN = process.platform === 'win32' ? 'Scripts' : 'bin';
const RUFF = path.join(
  ROOT,
  'server',
  '.venv',
  VENV_BIN,
  process.platform === 'win32' ? 'ruff.exe' : 'ruff',
);

test('post-edit-check skips ruff when the server venv is missing', () => {
  const dir = tempProject();
  mkdirSync(path.join(dir, 'server'));
  writeFileSync(path.join(dir, 'server', 'bad.py'), 'print(undefined_name)\n');
  const r = runHook(
    'post-edit-check.mjs',
    { tool_input: { file_path: path.join(dir, 'server', 'bad.py') } },
    dir,
  );
  assert.equal(r.code, 0, r.stderr);
});

test(
  'post-edit-check reports ruff errors from the server venv',
  { skip: !existsSync(RUFF) && 'server/.venv not installed' },
  () => {
    const dir = tempProject();
    mkdirSync(path.join(dir, 'server', '.venv', VENV_BIN), { recursive: true });
    cpSync(RUFF, path.join(dir, 'server', '.venv', VENV_BIN, path.basename(RUFF)));
    writeFileSync(path.join(dir, 'server', 'bad.py'), 'print(undefined_name)\n');
    const r = runHook(
      'post-edit-check.mjs',
      { tool_input: { file_path: path.join(dir, 'server', 'bad.py') } },
      dir,
    );
    assert.equal(r.code, 2);
    assert.match(r.stderr, /ruff:[\s\S]*F821/);
  },
);
