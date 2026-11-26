// The sync's `xype.v1` messages as plain values, field for field with `index.proto` and
// `common.proto` (camelCase, as generated TypeScript names them). Slice 11b's gRPC client
// maps them to the generated types. Plaintext fields carry only ids, hashes and line
// ranges; code travels only as an `EncryptedPayload`.

import type { EncryptedPayload } from '@/security/cryptoBox';

export interface LineRange {
  /** 1-based, inclusive. */
  readonly start: number;
  readonly end: number;
}

export interface ChunkRefMessage {
  readonly repositoryId: string;
  readonly obfuscatedFileId: string;
  readonly lines: LineRange;
  readonly chunkHash: string;
  readonly leafNodeId: string;
}

export interface ChunkContentMessage {
  readonly ref: ChunkRefMessage;
  readonly content: EncryptedPayload;
}

export interface ChunkUploadMessage {
  readonly requestId: string;
  readonly repositoryId: string;
  readonly chunks: readonly ChunkContentMessage[];
  readonly newRootHash: string;
  readonly isInitial: boolean;
}

/** A `ChunkUpload` and its envelope's `message_id`, which every payload's AAD binds. */
export interface OutgoingUpload {
  readonly messageId: string;
  readonly upload: ChunkUploadMessage;
}

export interface MerkleRootReportMessage {
  readonly repositoryId: string;
  readonly rootHash: string;
}

export interface MerkleSyncRequestMessage {
  readonly requestId: string;
  readonly repositoryId: string;
  readonly serverRootHash: string;
  /** Empty asks for the top level. */
  readonly nodeIds: readonly string[];
}

export interface MerkleNodeMessage {
  readonly nodeId: string;
  readonly hash: string;
  readonly isLeaf: boolean;
  /** Only for leaves. */
  readonly lines: LineRange | null;
  /** The requested node this child belongs to, `''` for the top level. */
  readonly parentId: string;
}

export interface MerkleTreeReportMessage {
  readonly requestId: string;
  readonly repositoryId: string;
  readonly rootHash: string;
  readonly nodes: readonly MerkleNodeMessage[];
  readonly lastPage: boolean;
}

export interface MerkleDeltaMessage {
  readonly requestId: string;
  readonly repositoryId: string;
  readonly upload: readonly ChunkRefMessage[];
  readonly deletedNodeIds: readonly string[];
}
