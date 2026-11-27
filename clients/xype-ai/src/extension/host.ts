// The sync worker's lifecycle in the extension host (slice 11c). Starts and stops run one
// at a time, so two quick changes never leave two engines (two streams, two keys); a stop
// lets the engine close its stream and wipe its keys before the thread is terminated.
// Notices are fixed texts; nothing from the worker is logged.

import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { Worker } from 'node:worker_threads';

import * as vscode from 'vscode';

import type { EngineMessage, EngineStop, HostMessage, SyncConfig } from '@/engine/protocol';

import { readCredentials } from './credentials';
import { userValue } from './settings';

const WORKSPACE_ID_KEY = 'xype.workspaceId';
const BINDING_KEY = 'xype.binding:';
// A registered repository's id; anything else is never shown or sent.
const REPOSITORY_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** How long a stopping engine may take to close its stream and wipe its keys. */
const STOP_GRACE_MS = 2000;
/** Failed walks in a row before the user is told. */
const FAILURES_BEFORE_NOTICE = 3;

const STOP_NOTICES: Partial<Record<EngineStop, string>> = {
  signed_out: 'XYPE: sign in (XYPE: Sign in) to index this workspace.',
  credentials_rejected: 'XYPE: sign in again; the server refused the client credentials.',
  too_many_windows: 'XYPE: too many windows are open; close one and reload this window.',
  hello_refused: 'XYPE: the server refused a repository of this window; check xype.repositoryId.',
  failed: 'XYPE: indexing stopped on an error; reload the window to retry.',
};

/**
 * A folder's `xype.repositoryId` may come from the repository itself, which could point
 * this folder at another of the team's repositories. Each binding is confirmed once.
 */
async function confirmed(
  context: vscode.ExtensionContext,
  folder: vscode.WorkspaceFolder,
  id: string,
): Promise<boolean> {
  const key = BINDING_KEY + folder.uri.toString();
  if (context.globalState.get<string>(key) === id) return true;
  const choice = await vscode.window.showWarningMessage(
    `XYPE: index the folder "${folder.name}" into the registered repository ${id}?`,
    { modal: true },
    'Index',
  );
  if (choice !== 'Index') return false;
  await context.globalState.update(key, id);
  return true;
}

async function syncConfig(context: vscode.ExtensionContext): Promise<SyncConfig | null> {
  const settings = vscode.workspace.getConfiguration('xype');
  const serverUrl = userValue(settings.inspect<string>('serverUrl'));
  const caFile = userValue(settings.inspect<string>('caFile'));
  const repositories = [];
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    const id = vscode.workspace
      .getConfiguration('xype', folder.uri)
      .get<string>('repositoryId', '');
    if (REPOSITORY_ID.test(id) && (await confirmed(context, folder, id))) {
      repositories.push({ id, root: folder.uri.fsPath });
    }
  }
  if (serverUrl === '' || repositories.length === 0) return null;
  let workspaceId = context.workspaceState.get<string>(WORKSPACE_ID_KEY);
  if (workspaceId === undefined) {
    workspaceId = randomUUID();
    await context.workspaceState.update(WORKSPACE_ID_KEY, workspaceId);
  }
  const version = (context.extension.packageJSON as { version?: unknown }).version;
  return {
    serverUrl,
    ca: caFile === '' ? null : await readFile(caFile, 'utf8'),
    clientVersion: typeof version === 'string' ? version : '0.0.0',
    workspaceId,
    repositories,
    assetsDir: vscode.Uri.joinPath(context.extensionUri, 'dist').fsPath,
  };
}

export class EngineHost {
  readonly #context: vscode.ExtensionContext;
  #worker: Worker | null = null;
  /** Starts and stops in order: each waits for the one before. */
  #queue: Promise<void> = Promise.resolve();
  #failures = 0;

  constructor(context: vscode.ExtensionContext) {
    this.#context = context;
  }

  /** (Re)starts the engine with the current settings; nothing runs without them. */
  restart(): Promise<void> {
    return this.#serial(async () => {
      await this.#stop();
      const config = await syncConfig(this.#context).catch(() => null);
      if (config !== null) this.#start(config);
    });
  }

  stop(): Promise<void> {
    return this.#serial(() => this.#stop());
  }

  changed(): void {
    this.#post({ kind: 'changed' });
  }

  #serial(step: () => Promise<void>): Promise<void> {
    this.#queue = this.#queue.then(step, step);
    return this.#queue;
  }

  #post(message: HostMessage): void {
    this.#worker?.postMessage(message);
  }

  #start(config: SyncConfig): void {
    const thread = vscode.Uri.joinPath(this.#context.extensionUri, 'dist', 'syncWorker.cjs');
    const worker = new Worker(thread.fsPath, { workerData: config });
    worker.on('message', (message: EngineMessage) => {
      if (this.#worker !== worker) return;
      if (message.kind === 'stopped') {
        // The engine ended on its own: the idle thread goes too, without a second notice.
        this.#worker = null;
        void worker.terminate();
      }
      void this.#onMessage(worker, message);
    });
    // An error's message may name a path (rule 08): only the fixed notice is shown.
    const ended = () => {
      if (this.#worker !== worker) return;
      this.#worker = null;
      void vscode.window.showWarningMessage(STOP_NOTICES.failed ?? '');
    };
    worker.on('error', ended);
    worker.on('exit', ended);
    this.#failures = 0;
    this.#worker = worker;
  }

  /** Asks the engine to stop, then terminates the thread after its grace period. */
  async #stop(): Promise<void> {
    const worker = this.#worker;
    this.#worker = null;
    if (worker === null) return;
    const stopped = new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, STOP_GRACE_MS);
      worker.on('message', (message: EngineMessage) => {
        if (message.kind !== 'stopped') return;
        clearTimeout(timer);
        resolve();
      });
    });
    worker.postMessage({ kind: 'stop' } satisfies HostMessage);
    await stopped;
    await worker.terminate();
  }

  async #onMessage(worker: Worker, message: EngineMessage): Promise<void> {
    switch (message.kind) {
      case 'credentials_needed': {
        const credentials = await readCredentials(this.#context.secrets);
        // Only to the worker that asked, and only while it is still the current one.
        if (this.#worker !== worker) return;
        const reply: HostMessage = {
          kind: 'credentials',
          requestId: message.requestId,
          credentials,
        };
        worker.postMessage(reply);
        return;
      }
      case 'grammars_failed':
        void vscode.window.showWarningMessage(
          `XYPE: ${String(message.languages.length)} grammars did not load; those files are indexed by lines.`,
        );
        return;
      case 'indexed':
        this.#failures = 0;
        return;
      case 'index_failed':
        this.#failures += 1;
        if (this.#failures === FAILURES_BEFORE_NOTICE) {
          void vscode.window.showWarningMessage('XYPE: indexing keeps failing; check the folder.');
        }
        return;
      case 'stopped': {
        const notice = STOP_NOTICES[message.reason];
        if (notice !== undefined) void vscode.window.showWarningMessage(notice);
        return;
      }
    }
  }
}
