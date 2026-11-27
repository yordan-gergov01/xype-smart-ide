// Which settings the extension trusts (slice 11c). The server and its CA decide who
// receives the client credentials and mints the session key that decrypts every upload,
// so only the user's own settings may name them: a repository's `.vscode/settings.json`
// is ignored for them (the settings are also `machine`-scoped, and the extension does not
// run in untrusted workspaces). Pure, so it is tested without VS Code.

/** The shape of `WorkspaceConfiguration.inspect`. */
export interface Inspected<T> {
  readonly globalValue?: T;
  readonly workspaceValue?: T;
  readonly workspaceFolderValue?: T;
}

/** The user's (or machine's) own value; any workspace or folder value is ignored. */
export function userValue(inspected: Inspected<string> | undefined): string {
  return inspected?.globalValue ?? '';
}
