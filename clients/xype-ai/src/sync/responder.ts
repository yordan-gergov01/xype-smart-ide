// The client's side of the Merkle sync (ADR-0018, slice 11): answers the server's
// `MerkleSyncRequest`s from the repository's current tree and turns a `MerkleDelta` into
// sealed `ChunkUpload`s. Pure: the stream (slice 11b) sends what it returns. Each `Chunk`
// is mapped to the wire field by field, so its symbol, name and calls never leave the
// client, and only scrubbed content is sealed (ADR-0019).

import type { Chunk } from '@/context/chunkPlan';
import type { MerkleLeaf, MerkleNode, MerkleTree } from '@/context/merkle';
import type { ClientCryptoBox } from '@/security/cryptoBox';

import { uuid7 } from './ids';
import type {
  ChunkContentMessage,
  ChunkRefMessage,
  MerkleDeltaMessage,
  MerkleNodeMessage,
  MerkleRootReportMessage,
  MerkleSyncRequestMessage,
  MerkleTreeReportMessage,
  OutgoingUpload,
} from './messages';

/** The server's limits (`common_lib.merkle`); a larger message is refused. */
export const MAX_NODES_PER_REPORT = 2000;
export const MAX_CHUNKS_PER_UPLOAD = 256;
/** `ChunkUpload.chunks`: part of every payload's AAD (ADR-0015). */
export const CHUNKS_FIELD = 3;
/**
 * The server receives messages of at most 1 MiB (`MAX_MESSAGE_BYTES` in
 * `server/backend/grpc/server.py`); an upload stays well below it, counting each chunk's
 * content, GCM tag and a generous allowance for its ref and envelope fields.
 */
export const MAX_UPLOAD_BYTES = 768 * 1024;
export const CHUNK_OVERHEAD_BYTES = 512;

const encoder = new TextEncoder();

function nodeMessage(node: MerkleNode): MerkleNodeMessage {
  return {
    nodeId: node.nodeId,
    hash: node.hash,
    isLeaf: node.isLeaf,
    lines: node.lines === null ? null : { start: node.lines[0], end: node.lines[1] },
    parentId: node.parentId,
  };
}

type Leaf = MerkleLeaf<Chunk>;

/** Upload pages within both the count and the byte cap; one chunk (≤ 16 KiB) always fits. */
function uploadPages(leaves: readonly Leaf[]): Leaf[][] {
  const out: Leaf[][] = [];
  let page: Leaf[] = [];
  let bytes = 0;
  for (const leaf of leaves) {
    const size = encoder.encode(leaf.chunk.content).length + CHUNK_OVERHEAD_BYTES;
    if (
      page.length > 0 &&
      (page.length === MAX_CHUNKS_PER_UPLOAD || bytes + size > MAX_UPLOAD_BYTES)
    ) {
      out.push(page);
      page = [];
      bytes = 0;
    }
    page.push(leaf);
    bytes += size;
  }
  if (page.length > 0) out.push(page);
  return out;
}

/** Tree-report pages by count; uploads use `uploadPages`, which also caps bytes. */
function reportPages<T>(items: readonly T[], size: number): T[][] {
  const out: T[][] = [];
  for (let start = 0; start < items.length; start += size) {
    out.push(items.slice(start, start + size));
  }
  return out.length === 0 ? [[]] : out;
}

export class MerkleResponder {
  readonly repositoryId: string;
  #tree: MerkleTree<Chunk> | null = null;
  /**
   * Whether the server had no tree when it last asked. Informational: the server does not
   * read `is_initial`, and a re-requested delta (slice 8c3c) may arrive without a request.
   */
  #initial = false;
  readonly #newId: () => string;

  constructor(repositoryId: string, newId: () => string = uuid7) {
    this.repositoryId = repositoryId;
    this.#newId = newId;
  }

  /** The repository's tree after a (re)build; until then the responder answers nothing. */
  setTree(tree: MerkleTree<Chunk>): void {
    this.#tree = tree;
  }

  /** After a change, or after the server refused a level: the server then starts a sync. */
  rootReport(): MerkleRootReportMessage | null {
    if (this.#tree === null) return null;
    return { repositoryId: this.repositoryId, rootHash: this.#tree.rootHash };
  }

  /**
   * The requested nodes' children, in pages; only the last sets `lastPage`. Null before
   * the tree is built: an empty answer would delete the server's tree.
   */
  treeReports(request: MerkleSyncRequestMessage): MerkleTreeReportMessage[] | null {
    const tree = this.#tree;
    if (tree === null || request.repositoryId !== this.repositoryId) return null;
    this.#initial = request.serverRootHash === '';
    const parents = request.nodeIds.length === 0 ? [''] : request.nodeIds;
    const nodes = parents.flatMap((parent) => tree.children(parent).map(nodeMessage));
    const all = reportPages(nodes, MAX_NODES_PER_REPORT);
    return all.map((page, index) => ({
      requestId: request.requestId,
      repositoryId: this.repositoryId,
      rootHash: tree.rootHash,
      nodes: page,
      lastPage: index === all.length - 1,
    }));
  }

  /**
   * The chunks the delta asks for, sealed in order (each takes the next counter). A ref
   * whose leaf changed since the tree was reported is skipped: the next root report
   * starts a new sync for it.
   */
  uploads(box: ClientCryptoBox, delta: MerkleDeltaMessage): OutgoingUpload[] {
    const tree = this.#tree;
    if (tree === null || delta.repositoryId !== this.repositoryId) return [];
    const refs = delta.upload.flatMap((ref) => {
      const leaf = tree.leaf(ref.leafNodeId);
      return leaf === undefined || leaf.node.hash !== ref.chunkHash ? [] : [leaf];
    });
    return uploadPages(refs).map((page) => {
      const messageId = this.#newId();
      const chunks = page.map((leaf) => this.#sealed(box, delta.requestId, messageId, leaf));
      return {
        messageId,
        upload: {
          requestId: delta.requestId,
          repositoryId: this.repositoryId,
          chunks,
          newRootHash: tree.rootHash,
          isInitial: this.#initial,
        },
      };
    });
  }

  #sealed(
    box: ClientCryptoBox,
    requestId: string,
    messageId: string,
    leaf: Leaf,
  ): ChunkContentMessage {
    const { node, chunk } = leaf;
    const ref: ChunkRefMessage = {
      repositoryId: this.repositoryId,
      obfuscatedFileId: node.parentId,
      lines: { start: chunk.lines[0], end: chunk.lines[1] },
      chunkHash: node.hash,
      leafNodeId: node.nodeId,
    };
    const content = box.sealText(chunk.content, {
      messageId,
      requestId,
      fieldNumber: CHUNKS_FIELD,
      repositoryId: ref.repositoryId,
      obfuscatedFileId: ref.obfuscatedFileId,
      chunkHash: ref.chunkHash,
      lineStart: ref.lines.start,
      lineEnd: ref.lines.end,
      leafNodeId: ref.leafNodeId,
    });
    return { ref, content };
  }
}
