// The client's Merkle tree over the directory structure (ADR-0018): directories → files →
// chunks. Ids are keyed HMACs of the normalised path (ADR-0016), so every developer of a
// repository builds the same tree; a leaf's hash covers its scrubbed content and its
// parent's inner hash covers its line range, so a moved chunk changes its path's hashes.

import { createHash } from 'node:crypto';

import { hmacId, normalisePath } from '@/security/obfuscation';

/** A chunk of one file, its content already scrubbed with LF line endings. */
export interface ChunkInput {
  readonly kind: string;
  /** `language:qualified.name`, or empty for a chunk without a symbol. */
  readonly symbol: string;
  /** Tells apart chunks with the same kind and symbol in one file, from 0. */
  readonly occurrence: number;
  readonly content: string;
  readonly lines: readonly [number, number];
}

export interface FileInput {
  readonly path: string;
  readonly chunks: readonly ChunkInput[];
}

export interface MerkleNode {
  readonly nodeId: string;
  /** The empty string for a top-level node, as on the wire. */
  readonly parentId: string;
  readonly hash: string;
  readonly isLeaf: boolean;
  readonly lines: readonly [number, number] | null;
}

export interface MerkleTree {
  readonly rootHash: string;
  /** The children of `parentId` (`''` for the top level), sorted by node id. */
  children(parentId: string): readonly MerkleNode[];
}

export class MerkleError extends Error {
  override name = 'MerkleError';
}

const MAX_LINE = 2 ** 31 - 1;

function byNodeId(a: { readonly nodeId: string }, b: { readonly nodeId: string }): number {
  return a.nodeId < b.nodeId ? -1 : 1;
}

/** SHA-256 over the children sorted by node id (ADR-0018, shared vectors). */
export function innerHash(children: readonly Omit<MerkleNode, 'parentId'>[]): string {
  const digest = createHash('sha256');
  for (const child of [...children].sort(byNodeId)) {
    digest.update(child.nodeId + child.hash, 'ascii');
    digest.update(Uint8Array.of(child.isLeaf ? 1 : 0));
    if (child.lines !== null) {
      const lines = new DataView(new ArrayBuffer(8));
      lines.setUint32(0, child.lines[0]);
      lines.setUint32(4, child.lines[1]);
      digest.update(new Uint8Array(lines.buffer));
    }
  }
  return digest.digest('hex');
}

function checkChunk(chunk: ChunkInput): void {
  const [start, end] = chunk.lines;
  const valid = Number.isInteger(start) && Number.isInteger(end) && end <= MAX_LINE;
  if (!valid || start < 1 || start > end) throw new MerkleError('invalid line range');
  if (!Number.isInteger(chunk.occurrence) || chunk.occurrence < 0) {
    throw new MerkleError('invalid occurrence');
  }
}

class Builder {
  readonly byParent = new Map<string, MerkleNode[]>();
  private readonly dirs = new Map<string, string>(); // directory path → node id
  private readonly dirIds = new Set<string>();

  private readonly repoKey: Uint8Array;

  constructor(repoKey: Uint8Array) {
    this.repoKey = repoKey;
  }

  addFile(file: FileInput): void {
    const path = normalisePath(file.path);
    const fileId = hmacId(this.repoKey, 'file', path);
    const leaves = file.chunks.map((chunk) => this.leaf(path, fileId, chunk));
    for (const leaf of leaves) this.add(leaf);
    const hash = innerHash(leaves);
    this.add({ nodeId: fileId, parentId: this.parentOf(path), hash, isLeaf: false, lines: null });
  }

  /** A directory's hash needs its children's first, so directories hash bottom-up. */
  hashDirectory(nodeId: string): string {
    const children = (this.byParent.get(nodeId) ?? []).map((child) =>
      this.dirIds.has(child.nodeId) ? { ...child, hash: this.hashDirectory(child.nodeId) } : child,
    );
    this.byParent.set(nodeId, children);
    return innerHash(children);
  }

  private add(node: MerkleNode): void {
    const siblings = this.byParent.get(node.parentId) ?? [];
    if (siblings.some((s) => s.nodeId === node.nodeId)) {
      throw new MerkleError('two nodes have the same id');
    }
    siblings.push(node);
    this.byParent.set(node.parentId, siblings);
  }

  /** The node id of the directory holding `path`, creating directories on the way. */
  private parentOf(path: string): string {
    const cut = path.lastIndexOf('/');
    if (cut < 0) return '';
    const dir = path.slice(0, cut);
    const known = this.dirs.get(dir);
    if (known !== undefined) return known;
    const nodeId = hmacId(this.repoKey, 'dir', dir);
    this.dirs.set(dir, nodeId);
    this.dirIds.add(nodeId);
    this.add({ nodeId, parentId: this.parentOf(dir), hash: '', isLeaf: false, lines: null });
    return nodeId;
  }

  private leaf(path: string, fileId: string, chunk: ChunkInput): MerkleNode {
    checkChunk(chunk);
    const fields = [path, chunk.kind, chunk.symbol, String(chunk.occurrence)];
    return {
      nodeId: hmacId(this.repoKey, 'chunk-pos', ...fields),
      parentId: fileId,
      hash: hmacId(this.repoKey, 'chunk-content', chunk.content),
      isLeaf: true,
      lines: [chunk.lines[0], chunk.lines[1]],
    };
  }
}

/** Builds the tree of one repository; paths are repository-relative (ADR-0016). */
export function buildTree(repoKey: Uint8Array, files: readonly FileInput[]): MerkleTree {
  const builder = new Builder(repoKey);
  for (const file of files) builder.addFile(file);
  const rootHash = builder.hashDirectory('');
  const byParent = builder.byParent;
  for (const siblings of byParent.values()) siblings.sort(byNodeId);
  return { rootHash, children: (parentId) => byParent.get(parentId) ?? [] };
}
