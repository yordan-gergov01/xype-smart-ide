import { mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { parsePathRules } from '@/security/paths';

import { WalkError, walkRepository, type WalkedFile } from './walker';

const RULES = parsePathRules({ denied_paths: ['.env', 'secrets/'], max_file_bytes: 64 });

let base: string;
let root: string;

async function put(path: string, text: string | Uint8Array): Promise<void> {
  await mkdir(dirname(join(root, path)), { recursive: true });
  await writeFile(join(root, path), text);
}

async function walk(): Promise<WalkedFile[]> {
  const files: WalkedFile[] = [];
  for await (const file of walkRepository(root, RULES)) files.push(file);
  return files;
}

const summary = (files: WalkedFile[]) =>
  files.map((f) => [f.path, f.file.kind === 'text' ? 'text' : f.file.reason]);

beforeEach(async () => {
  base = await mkdtemp(join(tmpdir(), 'xype-walk-'));
  root = join(base, 'repo');
  await mkdir(root);
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe('walkRepository', () => {
  it('applies nested .gitignore files with git precedence, and skips .git', async () => {
    await put('.gitignore', 'build/\n*.log\n');
    await put('app/.gitignore', '!keep.log\nlocal.txt\n');
    await put('app/main.py', 'x = 1\n');
    await put('app/keep.log', 'kept\n');
    await put('app/debug.log', 'no\n');
    await put('app/local.txt', 'no\n');
    await put('build/out.js', 'no\n');
    await put('.git/config', 'no\n');
    await put('README.md', '# r\r\n');
    expect(summary(await walk())).toEqual([
      ['.gitignore', 'text'],
      ['README.md', 'text'],
      ['app/.gitignore', 'text'],
      ['app/keep.log', 'text'],
      ['app/main.py', 'text'],
    ]);
  });

  it('excludes what .xype/ignore names, which cannot re-include a git-ignored file', async () => {
    await put('.gitignore', 'gen/\n');
    await put('.xype/ignore', 'fixtures/\n!gen/\n');
    await put('fixtures/a.py', 'no\n');
    await put('gen/b.py', 'no\n');
    await put('src/c.py', 'yes\n');
    expect(summary(await walk()).map(([path]) => path)).toEqual([
      '.gitignore',
      '.xype/ignore',
      'src/c.py',
    ]);
  });

  it('reports denied, large, binary and non-UTF-8 files without their text', async () => {
    await put('.env', 'TOKEN=x\n');
    await put('secrets/key.txt', 'no\n');
    await put('big.txt', 'x'.repeat(65));
    await put('image.bin', Uint8Array.of(1, 0, 2));
    await put('latin1.txt', Uint8Array.of(0xe9));
    await put('ok.txt', 'a\r\nb\n');
    const files = await walk();
    expect(summary(files)).toEqual([
      ['.env', 'denied_path'],
      ['big.txt', 'too_large'],
      ['image.bin', 'binary'],
      ['latin1.txt', 'not_utf8'],
      ['ok.txt', 'text'],
      ['secrets/key.txt', 'denied_path'],
    ]);
    expect(files.at(-2)?.file).toEqual({ kind: 'text', text: 'a\nb\n' });
  });

  it('never follows a link out of the repository', async () => {
    const outside = join(base, 'outside');
    await mkdir(outside);
    await writeFile(join(outside, 'secret.py'), 'x = 1\n');
    await put('src/a.py', 'a = 1\n');
    // A junction needs no privileges on Windows; elsewhere it is a directory link.
    await symlink(outside, join(root, 'linked'), 'junction');
    expect(summary(await walk()).map(([path]) => path)).toEqual(['src/a.py']);
  });

  it('never follows a file link', async (context) => {
    const outside = join(base, 'secret.py');
    await writeFile(outside, 'x = 1\n');
    await put('a.py', 'a = 1\n');
    try {
      await symlink(outside, join(root, 'notes.py'), 'file');
    } catch {
      context.skip(); // file links need a privilege on Windows
    }
    expect(summary(await walk()).map(([path]) => path)).toEqual(['a.py']);
  });

  it('fails with a code only, never the path, when the root is missing', async () => {
    const missing = join(base, 'payments-ledger');
    await expect(walkRepository(missing, RULES).next()).rejects.toThrow(WalkError);
    await expect(walkRepository(missing, RULES).next()).rejects.not.toThrow('payments');
  });
});
