// The one place where the sync's plain messages (`src/sync/messages.ts`) become the
// generated `xype.v1` types and back (ADR-0038). Every request gets an `Envelope` with a
// UUIDv7 message id; a `ChunkUpload` keeps the id its payloads' AAD binds.

import { create } from '@bufbuild/protobuf';
import { timestampNow } from '@bufbuild/protobuf/wkt';

import type { ChunkRef } from '@/gen/xype/v1/common_pb';
import {
  ChunkUploadSchema,
  MerkleRootReportSchema,
  MerkleTreeReportSchema,
} from '@/gen/xype/v1/index_pb';
import {
  HeartbeatSchema,
  HelloSchema,
  type SessionRequest,
  SessionRequestSchema,
} from '@/gen/xype/v1/session_pb';
import { uuid7 } from '@/sync/ids';
import type {
  ChunkRefMessage,
  MerkleDeltaMessage,
  MerkleRootReportMessage,
  MerkleSyncRequestMessage,
  MerkleTreeReportMessage,
  OutgoingUpload,
} from '@/sync/messages';

type Body = SessionRequest['body'];

export interface HelloFields {
  readonly clientVersion: string;
  readonly workspaceId: string;
  readonly sessionKeyId: string;
  readonly repositoryIds: readonly string[];
  readonly repositoryRoots: ReadonlyMap<string, string>;
}

function request(body: Body, messageId = uuid7(), correlationId = ''): SessionRequest {
  return create(SessionRequestSchema, {
    envelope: { messageId, correlationId, sentAt: timestampNow() },
    body,
  });
}

export function helloRequest(hello: HelloFields): SessionRequest {
  return request({
    case: 'hello',
    value: create(HelloSchema, {
      clientVersion: hello.clientVersion,
      workspaceId: hello.workspaceId,
      sessionKeyId: hello.sessionKeyId,
      repositoryIds: [...hello.repositoryIds],
      repositoryRoots: Object.fromEntries(hello.repositoryRoots),
      maxParallelTools: 0,
    }),
  });
}

export function heartbeatRequest(seq: bigint): SessionRequest {
  return request({ case: 'heartbeat', value: create(HeartbeatSchema, { seq }) });
}

export function rootReportRequest(report: MerkleRootReportMessage): SessionRequest {
  return request({
    case: 'merkleRootReport',
    value: create(MerkleRootReportSchema, { ...report }),
  });
}

export function treeReportRequest(report: MerkleTreeReportMessage): SessionRequest {
  const nodes = report.nodes.map((node) => ({
    nodeId: node.nodeId,
    hash: node.hash,
    isLeaf: node.isLeaf,
    parentId: node.parentId,
    ...(node.lines === null ? {} : { lines: { ...node.lines } }),
  }));
  return request(
    { case: 'merkleTreeReport', value: create(MerkleTreeReportSchema, { ...report, nodes }) },
    undefined,
    report.requestId,
  );
}

/** The envelope's message id is the one every payload's AAD was sealed with. */
export function chunkUploadRequest({ messageId, upload }: OutgoingUpload): SessionRequest {
  const chunks = upload.chunks.map(({ ref, content }) => ({
    ref: { ...ref, lines: { ...ref.lines } },
    // `aad` stays empty: the server rebuilds it and never trusts sent bytes.
    content: { keyId: content.keyId, nonce: content.nonce, ciphertext: content.ciphertext },
  }));
  return request(
    { case: 'chunkUpload', value: create(ChunkUploadSchema, { ...upload, chunks }) },
    messageId,
    upload.requestId,
  );
}

function chunkRef(ref: ChunkRef): ChunkRefMessage {
  return {
    repositoryId: ref.repositoryId,
    obfuscatedFileId: ref.obfuscatedFileId,
    lines: { start: ref.lines?.start ?? 0, end: ref.lines?.end ?? 0 },
    chunkHash: ref.chunkHash,
    leafNodeId: ref.leafNodeId,
  };
}

export function syncRequestOf(value: {
  readonly requestId: string;
  readonly repositoryId: string;
  readonly serverRootHash: string;
  readonly nodeIds: readonly string[];
}): MerkleSyncRequestMessage {
  return {
    requestId: value.requestId,
    repositoryId: value.repositoryId,
    serverRootHash: value.serverRootHash,
    nodeIds: [...value.nodeIds],
  };
}

export function deltaOf(value: {
  readonly requestId: string;
  readonly repositoryId: string;
  readonly upload: readonly ChunkRef[];
  readonly deletedNodeIds: readonly string[];
}): MerkleDeltaMessage {
  return {
    requestId: value.requestId,
    repositoryId: value.repositoryId,
    upload: value.upload.map(chunkRef),
    deletedNodeIds: [...value.deletedNodeIds],
  };
}
