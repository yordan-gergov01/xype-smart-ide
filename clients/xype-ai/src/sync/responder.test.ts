// The Merkle sync responder and the privacy of what it sends: every outgoing message is
// serialised and searched for the plaintext paths, symbols, names, calls and code.

import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { type Chunk, planChunks } from '@/context/chunkPlan';
import { buildTree, innerHash } from '@/context/merkle';
import { ClientCryptoBox, counterOf, openPayload } from '@/security/cryptoBox';
import { directionKey } from '@/security/keys';
import { parseRules, type ScrubbedText, SecretScrubber } from '@/security/scrubber';

import type {
  ChunkRefMessage,
  MerkleSyncRequestMessage,
  MerkleTreeReportMessage,
  OutgoingUpload,
} from './messages';
import {
  CHUNK_OVERHEAD_BYTES,
  CHUNKS_FIELD,
  MAX_NODES_PER_REPORT,
  MAX_UPLOAD_BYTES,
  MerkleResponder,
} from './responder';

const REPO = '0b9e6f1e-3f4a-4c7e-9a51-7d2c8e1f0a42';
const REPO_KEY = new Uint8Array(32).fill(7);
const RULES = parseRules(
  JSON.parse(
    readFileSync(new URL('../../../../packages/scrub-rules/rules.json', import.meta.url), 'utf8'),
  ),
);
// Synthetic, and split on purpose so secret scanners never see a whole token.
const SECRET = ['ghp_', 'R7mQ2xLk9pW4vN8tY3cB6dF1hJ5sA0eZ', 'uX2i'].join('');
const PATH = 'payments/internal/refundLedger.py';
const SOURCE = [
  'def reconcileRefundLedger(entry):',
  `    token = "${SECRET}"`,
  '    return notifyTreasuryDesk(entry)',
  '',
].join('\n');

let ids = 0;
const nextId = () => `msg-${String(++ids)}`;

function chunks(path: string, text: string): Chunk[] {
  const scrubbed = new SecretScrubber(RULES).scrub(text).text;
  const unit = { kind: 'function', name: 'reconcileRefundLedger', start: 0, end: 2, children: [] };
  return planChunks('py', scrubbed, [unit], [{ row: 2, name: 'notifyTreasuryDesk' }]).map((c) => ({
    ...c,
    // `path` only tells the files apart in the tree.
    symbol: path === PATH ? c.symbol : `${c.symbol}.${path}`,
  }));
}

function responder(files: readonly string[] = [PATH]) {
  const tree = buildTree(
    REPO_KEY,
    files.map((path) => ({ path, chunks: chunks(path, SOURCE) })),
  );
  const sync = new MerkleResponder(REPO, nextId);
  sync.setTree(tree);
  return { sync, tree };
}

const request = (nodeIds: string[], serverRootHash = ''): MerkleSyncRequestMessage => ({
  requestId: 'req-1',
  repositoryId: REPO,
  serverRootHash,
  nodeIds,
});

/** Every report of a full descent, as the server asks for each inner node. */
function descend(sync: MerkleResponder): MerkleTreeReportMessage[] {
  const reports: MerkleTreeReportMessage[] = [];
  const queue = [''];
  for (let parent = queue.pop(); parent !== undefined; parent = queue.pop()) {
    for (const report of sync.treeReports(request(parent === '' ? [] : [parent])) ?? []) {
      reports.push(report);
      queue.push(...report.nodes.filter((n) => !n.isLeaf).map((n) => n.nodeId));
    }
  }
  return reports;
}

/** The refs the server's `MerkleDelta` names for the reported leaves. */
function leafRefs(reports: readonly MerkleTreeReportMessage[]): ChunkRefMessage[] {
  return reports.flatMap((report) =>
    report.nodes
      .filter((node) => node.isLeaf)
      .map((node) => ({
        repositoryId: REPO,
        obfuscatedFileId: node.parentId,
        lines: node.lines ?? { start: 0, end: 0 },
        chunkHash: node.hash,
        leafNodeId: node.nodeId,
      })),
  );
}

/** The server's view: it rebuilds the AAD from the plain fields (ADR-0015). */
function serverOpen(key: Uint8Array, out: OutgoingUpload): Uint8Array {
  const chunk = out.upload.chunks[0];
  if (chunk === undefined) throw new Error('no chunk');
  const { ref, content } = chunk;
  return openPayload(key, content.nonce, content.ciphertext, {
    keyId: content.keyId,
    direction: 'c2s',
    messageId: out.messageId,
    requestId: out.upload.requestId,
    fieldNumber: CHUNKS_FIELD,
    repositoryId: REPO,
    obfuscatedFileId: ref.obfuscatedFileId,
    chunkHash: ref.chunkHash,
    lineStart: ref.lines.start,
    lineEnd: ref.lines.end,
    leafNodeId: ref.leafNodeId,
  });
}

describe('MerkleResponder', () => {
  it('answers nothing before its tree is built, and nothing for another repository', () => {
    const sync = new MerkleResponder(REPO, nextId);
    expect([sync.treeReports(request([])), sync.rootReport()]).toEqual([null, null]);
    const { sync: built } = responder();
    expect(built.treeReports({ ...request([]), repositoryId: 'other' })).toBeNull();
  });

  it('reports children with their parent and hashes the server can verify', () => {
    const { sync, tree } = responder();
    const reports = descend(sync);
    expect(reports.every((r) => r.lastPage && r.rootHash === tree.rootHash)).toBe(true);
    // Every level's nodes hash to the node the server asked for (`''` is the root).
    const hashes = new Map([['', tree.rootHash]]);
    for (const node of reports.flatMap((r) => r.nodes)) hashes.set(node.nodeId, node.hash);
    expect(reports).toHaveLength(4); // root, two directories, the file
    for (const report of reports) {
      const children = report.nodes.map((n) => ({
        ...n,
        lines: n.lines === null ? null : ([n.lines.start, n.lines.end] as const),
      }));
      expect(innerHash(children)).toBe(hashes.get(report.nodes[0]?.parentId ?? ''));
    }
  });

  it('pages a level of more than 2000 nodes and sets lastPage only on the last page', () => {
    const files = Array.from({ length: MAX_NODES_PER_REPORT + 1 }, (_, i) => `f${String(i)}.py`);
    const { sync } = responder(files);
    const reports = sync.treeReports(request([])) ?? [];
    expect(reports.map((r) => [r.nodes.length, r.lastPage])).toEqual([
      [MAX_NODES_PER_REPORT, false],
      [1, true],
    ]);
  });

  it('seals the requested chunks so the server opens them under the rebuilt AAD', () => {
    const { sync, tree } = responder();
    const sessionKey = new Uint8Array(32).fill(9);
    const serverKey = directionKey(sessionKey, 'c2s');
    const box = new ClientCryptoBox('key-1', sessionKey);
    const refs = leafRefs(descend(sync));
    const stale = { ...refs[0], chunkHash: 'f'.repeat(64) } as ChunkRefMessage;
    const delta = { requestId: 'req-2', repositoryId: REPO, upload: [...refs, stale] };
    const [out, more] = sync.uploads(box, { ...delta, deletedNodeIds: [] });
    expect(more).toBeUndefined();
    expect(out?.upload).toMatchObject({
      requestId: 'req-2',
      newRootHash: tree.rootHash,
      isInitial: true,
    });
    expect(out?.upload.chunks.map((c) => counterOf(c.content.nonce))).toEqual([1]);
    if (out === undefined) throw new Error('no upload');
    expect(out.upload.chunks[0]?.ref).toEqual(refs[0]);
    const plain = serverOpen(serverKey, out);
    const text = new TextDecoder().decode(plain);
    expect(text).toContain('<SECRET_REMOVED>');
    expect(text).not.toContain(SECRET);
  });

  it('cuts uploads under the byte cap and seals them in order', () => {
    const big = (i: number): Chunk => ({
      kind: 'block',
      symbol: '',
      occurrence: i,
      content: `${String(i).padEnd(15 * 1024, 'x')}
` as ScrubbedText,
      lines: [i + 1, i + 1],
      name: '',
      calls: [],
    });
    const tree = buildTree(REPO_KEY, [
      { path: 'gen.txt', chunks: Array.from({ length: 300 }, (_, i) => big(i)) },
    ]);
    const sync = new MerkleResponder(REPO, nextId);
    sync.setTree(tree);
    const box = new ClientCryptoBox('key-1', new Uint8Array(32).fill(5));
    const delta = { requestId: 'r', repositoryId: REPO, deletedNodeIds: [] };
    const uploads = sync.uploads(box, { ...delta, upload: leafRefs(descend(sync)) });
    expect(uploads.length).toBeGreaterThan(5);
    for (const { upload } of uploads) {
      const bytes = upload.chunks.reduce(
        (n, c) => n + c.content.ciphertext.length + CHUNK_OVERHEAD_BYTES,
        0,
      );
      expect(bytes).toBeLessThanOrEqual(MAX_UPLOAD_BYTES);
    }
    const counters = uploads.flatMap((u) => u.upload.chunks.map((c) => counterOf(c.content.nonce)));
    expect(counters).toEqual(Array.from({ length: 300 }, (_, i) => i + 1));
  });

  it('sends no plaintext path, symbol, name, call or code in any message', () => {
    const { sync } = responder();
    const box = new ClientCryptoBox('key-1', new Uint8Array(32).fill(3));
    const reports = descend(sync);
    const delta = { requestId: 'r', repositoryId: REPO, deletedNodeIds: [] };
    const uploads = sync.uploads(box, { ...delta, upload: leafRefs(reports) });
    expect(uploads).toHaveLength(1);
    const wire = JSON.stringify([sync.rootReport(), ...reports, ...uploads], (_, v: unknown) =>
      v instanceof Uint8Array ? Buffer.from(v).toString('hex') : v,
    );
    const plaintext = ['payments', 'refundLedger', 'reconcileRefundLedger', 'notifyTreasuryDesk'];
    for (const plain of [...plaintext, 'entry', 'token', SECRET]) {
      expect(wire).not.toContain(plain);
    }
  });
});
