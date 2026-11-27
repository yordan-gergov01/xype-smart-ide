// Runs inside the Extension Development Host (scripts/smoke.mjs): the extension activates
// and registers its commands, the bundled scrub thread scrubs a file under VS Code's own
// Node, and the bundled sync worker starts, loads every grammar, and stops cleanly when
// no credentials are stored. No server is needed.

const assert = require('node:assert/strict');
const { mkdtempSync, rmSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { Worker } = require('node:worker_threads');

const vscode = require('vscode');

const dist = join(__dirname, '..', 'dist');
// Synthetic, split so secret scanners never see a whole token.
const SECRET = ['ghp_', 'R7mQ2xLk9pW4vN8tY3cB6dF1hJ5sA0eZ', 'uX2i'].join('');

function firstMessages(worker, count, timeoutMs) {
  return new Promise((resolve, reject) => {
    const seen = [];
    const timer = setTimeout(
      () => reject(new Error(`timed out after ${seen.length} messages`)),
      timeoutMs,
    );
    worker.on('message', (message) => {
      seen.push(message);
      if (seen.length === count) {
        clearTimeout(timer);
        resolve(seen);
      }
    });
    worker.on('error', reject);
  });
}

async function activates() {
  const extension = vscode.extensions.getExtension('xype.xype-ai');
  assert.ok(extension, 'the extension is installed in the host');
  await extension.activate();
  const commands = await vscode.commands.getCommands(true);
  assert.ok(commands.includes('xype.signIn') && commands.includes('xype.signOut'));
}

async function scrubs() {
  const rules = JSON.parse(require('node:fs').readFileSync(join(dist, 'rules.json'), 'utf8'));
  const thread = new Worker(join(dist, 'scrubThread.cjs'), { workerData: rules });
  try {
    const [ready] = await firstMessages(thread, 1, 10_000);
    assert.equal(ready.kind, 'ready');
    const reply = firstMessages(thread, 1, 10_000);
    thread.postMessage(`token = "${SECRET}"\n`);
    const [scrubbed] = await reply;
    assert.equal(scrubbed.kind, 'scrubbed');
    assert.ok(!scrubbed.text.includes(SECRET) && scrubbed.text.includes('<SECRET_REMOVED>'));
  } finally {
    await thread.terminate();
  }
}

async function syncWorkerStarts() {
  const folder = mkdtempSync(join(tmpdir(), 'xype-smoke-repo-'));
  writeFileSync(join(folder, 'a.py'), 'def f():\n    return 1\n');
  const worker = new Worker(join(dist, 'syncWorker.cjs'), {
    workerData: {
      serverUrl: 'https://127.0.0.1:1',
      ca: null,
      clientVersion: '0.0.0',
      workspaceId: 'smoke',
      repositories: [{ id: '0b9e6f1e-3f4a-4c7e-9a51-7d2c8e1f0a42', root: folder }],
      assetsDir: dist,
    },
  });
  try {
    worker.on('message', (message) => {
      if (message.kind === 'credentials_needed') {
        worker.postMessage({
          kind: 'credentials',
          requestId: message.requestId,
          credentials: null,
        });
      }
    });
    const seen = await firstMessages(worker, 2, 30_000);
    // Grammars load (no `grammars_failed`), then the missing credentials stop it.
    assert.deepEqual(
      seen.map((m) => m.kind),
      ['credentials_needed', 'stopped'],
    );
    assert.equal(seen[1].reason, 'signed_out');
  } finally {
    await worker.terminate();
    rmSync(folder, { recursive: true, force: true });
  }
}

exports.run = async function run() {
  await activates();
  await scrubs();
  await syncWorkerStarts();
};
