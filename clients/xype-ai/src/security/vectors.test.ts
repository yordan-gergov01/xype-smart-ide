// The shared cross-language vectors (packages/crypto-vectors, written by the server's
// generator from raw primitives): the client must reproduce every one of them.

import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { innerHash } from '@/context/merkle';

import { DecryptionError } from './aead';
import { openPayload, type PayloadContext, payloadAad, sealPayload } from './cryptoBox';
import { frame } from './framing';
import {
  clientKeyPairForTests,
  directionKey,
  unwrapRepositoryKey,
  unwrapSessionKey,
  wrappingKey,
} from './keys';
import { hmacId, type IdDomain } from './obfuscation';

interface Vectors {
  frame: { fields: string[]; hex: string }[];
  hmac_id: { key_hex: string; domain: IdDomain; fields: string[]; id: string }[];
  direction_keys: Record<'session_key_hex' | 'c2s_hex' | 's2c_hex', string>;
  payload: {
    session_key_hex: string;
    context: Record<string, string | number>;
    counter: number;
    aad_hex: string;
    nonce_hex: string;
    plaintext_hex: string;
    ciphertext_hex: string;
  };
  session_key_exchange: Record<string, string>;
  repository_key_wrap: Record<string, string>;
  merkle_inner_hash: {
    children: { node_id: string; hash: string; is_leaf: boolean; lines: [number, number] | null }[];
    hash_hex: string;
  }[];
}

const vectors = JSON.parse(
  readFileSync(
    new URL('../../../../packages/crypto-vectors/vectors.json', import.meta.url),
    'utf8',
  ),
) as Vectors;
const bytes = (hex: string | undefined) => new Uint8Array(Buffer.from(hex ?? '', 'hex'));
const hex = (data: Uint8Array) => Buffer.from(data).toString('hex');

function context(raw: Record<string, string | number>): PayloadContext {
  return {
    keyId: String(raw['key_id']),
    direction: 'c2s',
    messageId: String(raw['message_id']),
    requestId: String(raw['request_id']),
    fieldNumber: Number(raw['field_number']),
    repositoryId: String(raw['repository_id']),
    obfuscatedFileId: String(raw['obfuscated_file_id']),
    chunkHash: String(raw['chunk_hash']),
    lineStart: Number(raw['line_start']),
    lineEnd: Number(raw['line_end']),
    leafNodeId: String(raw['leaf_node_id']),
  };
}

describe('shared crypto vectors', () => {
  it('frames fields', () => {
    for (const v of vectors.frame) expect(hex(frame(...v.fields))).toBe(v.hex);
  });

  it('derives obfuscated ids', () => {
    for (const v of vectors.hmac_id) {
      expect(hmacId(bytes(v.key_hex), v.domain, ...v.fields)).toBe(v.id);
    }
  });

  it('derives one key per direction', () => {
    const v = vectors.direction_keys;
    expect(hex(directionKey(bytes(v.session_key_hex), 'c2s'))).toBe(v.c2s_hex);
    expect(hex(directionKey(bytes(v.session_key_hex), 's2c'))).toBe(v.s2c_hex);
  });

  it('seals and opens a chunk payload', () => {
    const v = vectors.payload;
    const ctx = context(v.context);
    const key = directionKey(bytes(v.session_key_hex), 'c2s');
    expect(hex(payloadAad(ctx))).toBe(v.aad_hex);
    expect(hex(sealPayload(key, v.counter, bytes(v.plaintext_hex), ctx))).toBe(v.ciphertext_hex);
    const opened = openPayload(key, bytes(v.nonce_hex), bytes(v.ciphertext_hex), ctx);
    expect(hex(opened)).toBe(v.plaintext_hex);
  });

  it.each([
    ['repositoryId', 'repo-2'],
    ['leafNodeId', 'd'.repeat(64)],
    ['lineStart', 11],
  ] as const)('refuses a payload moved to another %s', (name, value) => {
    const v = vectors.payload;
    const key = directionKey(bytes(v.session_key_hex), 'c2s');
    const moved = { ...context(v.context), [name]: value };
    expect(() => openPayload(key, bytes(v.nonce_hex), bytes(v.ciphertext_hex), moved)).toThrow(
      DecryptionError,
    );
  });

  it('derives the wrapping key and unwraps the session and repository keys', () => {
    const v = vectors.session_key_exchange;
    const pair = clientKeyPairForTests(bytes(v['client_private_hex']));
    expect(hex(pair.publicKey)).toBe(v['client_public_hex']);
    const wrapping = wrappingKey(
      pair,
      bytes(v['server_public_hex']),
      String(v['key_id']),
      String(v['user_id']),
    );
    expect(hex(wrapping)).toBe(v['wrapping_key_hex']);
    const sealed = new Uint8Array([...bytes(v['wrap_nonce_hex']), ...bytes(v['wrapped_key_hex'])]);
    expect(hex(unwrapSessionKey(wrapping, String(v['key_id']), sealed))).toBe(v['session_key_hex']);
    expect(() => unwrapSessionKey(wrapping, 'key-2', sealed)).toThrow(DecryptionError);

    const r = vectors.repository_key_wrap;
    const repo = new Uint8Array([...bytes(r['wrap_nonce_hex']), ...bytes(r['wrapped_key_hex'])]);
    const ids = [String(r['repository_id']), String(r['hmac_key_id'])] as const;
    expect(hex(unwrapRepositoryKey(wrapping, ...ids, repo))).toBe(r['repository_key_hex']);
    expect(() => unwrapRepositoryKey(wrapping, ids[1], ids[0], repo)).toThrow(DecryptionError);
  });

  it('hashes Merkle inner nodes', () => {
    for (const v of vectors.merkle_inner_hash) {
      const children = v.children.map((c) => ({
        nodeId: c.node_id,
        hash: c.hash,
        isLeaf: c.is_leaf,
        lines: c.lines,
      }));
      expect(innerHash(children)).toBe(v.hash_hex);
    }
  });
});
