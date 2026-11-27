// The extension's entry (slice 11c). `src/extension/` is the only code that imports
// `vscode`: credentials in `SecretStorage`, settings, file events and notices. Everything
// else runs in the sync worker (`src/engine/worker.ts`). Bundled to `dist/extension.cjs`.
// It is not enabled in untrusted workspaces (`capabilities` in package.json).

import * as vscode from 'vscode';

import { clearCredentials, readCredentials, signIn } from './credentials';
import { EngineHost } from './host';

/** Settings changes arrive in bursts; the engine restarts once they settle. */
const SETTINGS_SETTLE_MS = 500;
/** Changes there never reach the index, so they do not wake the worker. */
const UNWATCHED = /[\\/](?:\.git|node_modules)[\\/]/;

let host: EngineHost | null = null;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const engine = new EngineHost(context);
  host = engine;
  const watcher = vscode.workspace.createFileSystemWatcher('**/*');
  const changed = (uri: vscode.Uri) => {
    if (!UNWATCHED.test(uri.fsPath)) engine.changed();
  };
  let settle: NodeJS.Timeout | undefined;
  context.subscriptions.push(
    watcher,
    watcher.onDidChange(changed),
    watcher.onDidCreate(changed),
    watcher.onDidDelete(changed),
    vscode.commands.registerCommand('xype.signIn', async () => {
      if (await signIn(context.secrets)) await engine.restart();
    }),
    vscode.commands.registerCommand('xype.signOut', async () => {
      // The token family stays valid on the server until it expires: no revoke route yet.
      await clearCredentials(context.secrets);
      await engine.stop();
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (!event.affectsConfiguration('xype')) return;
      clearTimeout(settle);
      settle = setTimeout(() => void engine.restart(), SETTINGS_SETTLE_MS);
    }),
    {
      dispose: () => {
        clearTimeout(settle);
      },
    },
  );
  // Without stored credentials the engine would only stop again: ask first, without
  // holding up activation on the answer.
  if ((await readCredentials(context.secrets)) === null) {
    void vscode.window
      .showInformationMessage('XYPE: sign in to index this workspace.', 'Sign in')
      .then((choice) => {
        if (choice === 'Sign in') void vscode.commands.executeCommand('xype.signIn');
      });
    return;
  }
  // Not awaited: the binding confirmation must not hold up activation.
  void engine.restart();
}

export async function deactivate(): Promise<void> {
  await host?.stop();
  host = null;
}
