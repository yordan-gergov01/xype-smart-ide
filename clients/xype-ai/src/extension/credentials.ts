// The client credentials in VS Code's `SecretStorage` (the OS keychain), never in settings
// (ADR-0014). The session key is never stored anywhere (ADR-0015).

import * as vscode from 'vscode';

import type { ClientCredentials } from '@/session/tokens';

const CLIENT_ID = 'xype.clientId';
const CLIENT_SECRET = 'xype.clientSecret';
// The server's shape for a client id (`backend/api/auth.py`).
const CLIENT_ID_PATTERN = /^xc_[0-9a-f]{16}$/;
// `backend/services/auth/peppers.py`: `xcs_` and 32 random bytes as base64url.
const CLIENT_SECRET_PATTERN = /^xcs_[A-Za-z0-9_-]{43}$/;

export async function readCredentials(
  secrets: vscode.SecretStorage,
): Promise<ClientCredentials | null> {
  const [clientId, clientSecret] = await Promise.all([
    secrets.get(CLIENT_ID),
    secrets.get(CLIENT_SECRET),
  ]);
  return clientId === undefined || clientSecret === undefined ? null : { clientId, clientSecret };
}

/** Asks for the id and secret an admin issued; false when the user cancelled. */
export async function signIn(secrets: vscode.SecretStorage): Promise<boolean> {
  const clientId = await vscode.window.showInputBox({
    title: 'XYPE: client id',
    prompt: 'The client id your admin issued (xc_…)',
    ignoreFocusOut: true,
    validateInput: (value) =>
      CLIENT_ID_PATTERN.test(value) ? null : 'Expected xc_ and 16 hex digits',
  });
  if (clientId === undefined) return false;
  const clientSecret = await vscode.window.showInputBox({
    title: 'XYPE: client secret',
    prompt: 'The client secret, shown once when it was issued',
    password: true,
    ignoreFocusOut: true,
    validateInput: (value) =>
      CLIENT_SECRET_PATTERN.test(value) ? null : 'Expected xcs_ and 43 characters',
  });
  if (clientSecret === undefined) return false;
  await secrets.store(CLIENT_ID, clientId);
  await secrets.store(CLIENT_SECRET, clientSecret);
  return true;
}

export async function clearCredentials(secrets: vscode.SecretStorage): Promise<void> {
  await secrets.delete(CLIENT_ID);
  await secrets.delete(CLIENT_SECRET);
}
