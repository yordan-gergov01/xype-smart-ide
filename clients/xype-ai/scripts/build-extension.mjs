// Bundles the extension into dist/ (slice 11c, ADR-0038): the extension host entry, the
// sync worker and the scrub thread as CommonJS (VS Code loads the extension with
// `require`; `vscode` is provided by the host), plus the assets the worker loads by path:
// the tree-sitter runtime, the grammars and the shared scrub rules. Run after `npm run gen`.

import { copyFile, cp, mkdir, readdir, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

import { build } from 'esbuild';

const root = fileURLToPath(new URL('..', import.meta.url));
const dist = `${root}dist`;
const modules = fileURLToPath(new URL('../../../node_modules/', import.meta.url));

await rm(dist, { recursive: true, force: true });
await build({
  absWorkingDir: root,
  entryPoints: {
    extension: 'src/extension/extension.ts',
    syncWorker: 'src/engine/worker.ts',
    scrubThread: 'src/security/scrubThread.ts',
  },
  bundle: true,
  platform: 'node',
  // The Node inside VS Code's Electron, below the repository's own Node 24.
  target: 'node22',
  format: 'cjs',
  outdir: dist,
  outExtension: { '.js': '.cjs' },
  external: ['vscode'],
  // The CommonJS build: the ES module one finds its files through `import.meta.url`,
  // which a CommonJS bundle does not have.
  alias: { 'web-tree-sitter': `${modules}web-tree-sitter/web-tree-sitter.cjs` },
  tsconfig: 'tsconfig.json',
  sourcemap: true,
  // `import.meta` is empty in CommonJS; every use is behind an injected path (scrubWorker).
  logOverride: { 'empty-import-meta': 'silent' },
  logLevel: 'warning',
});

await copyFile(`${modules}web-tree-sitter/web-tree-sitter.wasm`, `${dist}/web-tree-sitter.wasm`);
await copyFile(`${root}../../packages/scrub-rules/rules.json`, `${dist}/rules.json`);
await mkdir(`${dist}/grammars`);
const grammars = (await readdir(`${root}grammars`).catch(() => [])).filter((n) =>
  n.endsWith('.wasm'),
);
for (const name of grammars) {
  await cp(`${root}grammars/${name}`, `${dist}/grammars/${name}`);
}
// Without grammars every file is cut by lines, and the extension says so.
process.stdout.write(`dist/ built with ${String(grammars.length)} grammars\n`);
