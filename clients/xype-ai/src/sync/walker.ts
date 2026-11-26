// The workspace walker (ADR-0019, slice 11): the files of one repository that may be
// indexed, each read only after its path passed every check. `.gitignore` files (in
// every directory, with git's precedence) and the repository's `.xype/ignore` both
// exclude: a file either one ignores is never read, and `.xype/ignore` cannot bring back
// what git ignores. Then `denied_paths` and the size are checked before the file is
// read. Symbolic links (and Windows junctions) are never followed, so nothing outside
// the repository is read; `.git` is skipped. Errors carry only a code, never a path.

import { constants, type Dirent } from 'node:fs';
import { type FileHandle, lstat, open, readdir, realpath } from 'node:fs/promises';
import { join } from 'node:path';

import ignore, { type Ignore } from 'ignore';

import { PathError } from '@/security/obfuscation';
import { type FileText, fileText, isDeniedPath, type PathRules } from '@/security/paths';

export interface WalkedFile {
  /** Repository-relative, `/`-separated. */
  readonly path: string;
  readonly file: FileText;
}

/** A file system error the walk cannot skip; only its code, since messages name paths. */
export class WalkError extends Error {
  override name = 'WalkError';
  readonly code: string;

  constructor(code: string) {
    super(`the workspace walk failed: ${code}`);
    this.code = code;
  }
}

interface Scope {
  /** The directory the ignore file sits in, `''` for the root. */
  readonly base: string;
  readonly rules: Ignore;
}

interface Pending {
  readonly dir: string;
  readonly scopes: readonly Scope[];
}

type Bytes =
  { readonly kind: 'bytes'; readonly bytes: Uint8Array } | { readonly kind: 'too_large' };

/** Larger ignore files are not read (and exclude nothing). */
const MAX_IGNORE_BYTES = 1024 * 1024;
// Windows has neither flag; there links are caught by the listing and the checks after open.
// O_NONBLOCK: a FIFO swapped in for a file must not block the walk.
const OPEN_FLAGS =
  process.platform === 'win32'
    ? constants.O_RDONLY
    : constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK;
const VANISHED = new Set(['ENOENT', 'EACCES', 'EPERM', 'ELOOP', 'EISDIR', 'ENOTDIR', 'ENXIO']);

/** Null for an error that only means the entry is gone or unreadable; else a `WalkError`. */
function skippable(error: unknown): null {
  const code = (error as NodeJS.ErrnoException | undefined)?.code;
  if (typeof code === 'string' && VANISHED.has(code)) return null;
  throw new WalkError(typeof code === 'string' ? code : 'unknown');
}

/** git's precedence: the deepest ignore file that matches decides, `!` included. */
function gitIgnored(scopes: readonly Scope[], path: string, isDir: boolean): boolean {
  let ignored = false;
  for (const scope of scopes) {
    const relative = scope.base === '' ? path : path.slice(scope.base.length + 1);
    const result = scope.rules.test(isDir ? `${relative}/` : relative);
    if (result.ignored) ignored = true;
    else if (result.unignored) ignored = false;
  }
  return ignored;
}

class Walk {
  readonly #root: string;
  readonly #rules: PathRules;
  #xype: Ignore | null = null;
  #realRoot = '';

  constructor(root: string, rules: PathRules) {
    this.#root = root;
    this.#rules = rules;
  }

  async init(): Promise<void> {
    try {
      this.#realRoot = await realpath(this.#root);
    } catch (error) {
      skippable(error);
      throw new WalkError('ENOENT');
    }
    this.#xype = await this.#ignoreRules('.xype/ignore');
  }

  /** What to do with an entry: links and other non-files are skipped, as are ignored ones. */
  classify(scopes: readonly Scope[], entry: Dirent, path: string): 'skip' | 'dir' | 'file' {
    // `.git` is a directory, or a file naming one (submodules, worktrees).
    if (entry.isSymbolicLink() || entry.name === '.git') return 'skip';
    const isDir = entry.isDirectory();
    if ((!isDir && !entry.isFile()) || this.#skipped(scopes, path, isDir)) return 'skip';
    return isDir ? 'dir' : 'file';
  }

  async scopes(pending: Pending): Promise<readonly Scope[]> {
    const file = pending.dir === '' ? '.gitignore' : `${pending.dir}/.gitignore`;
    const rules = await this.#ignoreRules(file);
    return rules === null ? pending.scopes : [...pending.scopes, { base: pending.dir, rules }];
  }

  /** A directory that now resolves outside its place (swapped for a link) lists nothing. */
  async entries(dir: string): Promise<Dirent[]> {
    try {
      if (dir !== '' && !(await this.#inPlace(dir))) return [];
      const entries = await readdir(join(this.#root, dir), { withFileTypes: true });
      return entries.sort((a, b) => (a.name < b.name ? -1 : 1));
    } catch (error) {
      skippable(error);
      return [];
    }
  }

  /** The file's text or why it is excluded; null when it vanished or is not a file. */
  async read(path: string): Promise<FileText | null> {
    try {
      if (isDeniedPath(this.#rules, path)) return { kind: 'excluded', reason: 'denied_path' };
    } catch (error) {
      if (error instanceof PathError) return { kind: 'excluded', reason: 'bad_path' };
      throw error;
    }
    const read = await this.#bytes(path, this.#rules.maxFileBytes);
    if (read === null) return null;
    if (read.kind === 'too_large') return { kind: 'excluded', reason: 'too_large' };
    return fileText(this.#rules, path, read.bytes);
  }

  /** An odd name the ignore rules cannot test is skipped. */
  #skipped(scopes: readonly Scope[], path: string, isDir: boolean): boolean {
    try {
      const forXype = isDir ? `${path}/` : path;
      return gitIgnored(scopes, path, isDir) || this.#xype?.ignores(forXype) === true;
    } catch {
      return true;
    }
  }

  async #ignoreRules(path: string): Promise<Ignore | null> {
    const read = await this.#bytes(path, MAX_IGNORE_BYTES);
    return read?.kind === 'bytes' ? ignore().add(Buffer.from(read.bytes).toString('utf8')) : null;
  }

  /** Whether the path still resolves to itself under the root, through no link. */
  async #inPlace(path: string): Promise<boolean> {
    return (await realpath(join(this.#root, path))) === join(this.#realRoot, path);
  }

  /** At most `max` bytes of a regular file that is still the one listed, never a link. */
  async #bytes(path: string, max: number): Promise<Bytes | null> {
    const full = join(this.#root, path);
    let handle: FileHandle | null = null;
    try {
      handle = await open(full, OPEN_FLAGS);
      const [info, entry] = await Promise.all([handle.stat(), lstat(full)]);
      // The open handle and the path name the same regular file, which resolves in place.
      const same = info.dev === entry.dev && info.ino === entry.ino;
      if (!info.isFile() || !entry.isFile() || !same || !(await this.#inPlace(path))) return null;
      if (info.size > max) return { kind: 'too_large' };
      const bytes = new Uint8Array(info.size);
      const { bytesRead } = await handle.read(bytes, 0, info.size, 0);
      return { kind: 'bytes', bytes: bytes.subarray(0, bytesRead) };
    } catch (error) {
      return skippable(error);
    } finally {
      await handle?.close();
    }
  }
}

/** Every file of the repository at `root` that no ignore file excludes, in path order. */
export async function* walkRepository(root: string, rules: PathRules): AsyncGenerator<WalkedFile> {
  const walk = new Walk(root, rules);
  await walk.init();
  const stack: Pending[] = [{ dir: '', scopes: [] }];
  for (let pending = stack.pop(); pending !== undefined; pending = stack.pop()) {
    const scopes = await walk.scopes(pending);
    const dirs: string[] = [];
    for (const entry of await walk.entries(pending.dir)) {
      const path = pending.dir === '' ? entry.name : `${pending.dir}/${entry.name}`;
      const kind = walk.classify(scopes, entry, path);
      if (kind === 'dir') dirs.push(path);
      if (kind !== 'file') continue;
      const file = await walk.read(path);
      if (file !== null) yield { path, file };
    }
    // Reversed, so the stack visits directories in name order.
    for (const dir of dirs.reverse()) stack.push({ dir, scopes });
  }
}
