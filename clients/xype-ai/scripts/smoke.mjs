// The Extension Development Host smoke test (slice 11c): starts a local VS Code with the
// built extension (`npm run build` first) and runs scripts/smoke-suite.cjs inside it.
// It uses an installed VS Code, never a download: set XYPE_VSCODE_EXECUTABLE to its
// executable, or have `code` on the PATH (Windows). A VS Code window opens while it runs.

import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { runTests } from '@vscode/test-electron';

const root = fileURLToPath(new URL('..', import.meta.url));

function executable() {
  const configured = process.env['XYPE_VSCODE_EXECUTABLE'];
  if (configured !== undefined && configured !== '') return configured;
  if (process.platform !== 'win32') return null;
  const cli = execFileSync('where', ['code.cmd'], { encoding: 'utf8' }).split(/\r?\n/)[0] ?? '';
  const exe = join(dirname(dirname(cli)), 'Code.exe');
  return existsSync(exe) ? exe : null;
}

const vscodeExecutablePath = executable();
if (vscodeExecutablePath === null) {
  process.stderr.write('smoke: set XYPE_VSCODE_EXECUTABLE to an installed VS Code\n');
  process.exit(1);
}
const workspace = await mkdtemp(join(tmpdir(), 'xype-smoke-'));
const userData = await mkdtemp(join(tmpdir(), 'xype-smoke-user-'));
try {
  // runTests rejects with the host's exit code; its stdout and stderr are already shown.
  await runTests({
    vscodeExecutablePath,
    extensionDevelopmentPath: root,
    extensionTestsPath: join(root, 'scripts', 'smoke-suite.cjs'),
    launchArgs: [workspace, '--disable-extensions', `--user-data-dir=${userData}`],
  });
} finally {
  await rm(workspace, { recursive: true, force: true });
  await rm(userData, { recursive: true, force: true });
}
