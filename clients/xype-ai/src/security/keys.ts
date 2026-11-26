// The client's half of ADR-0015: the X25519 session-key exchange, unwrapping the session
// key and the repository HMAC keys (ADR-0016), and the per-direction payload keys.
// Every derived or unwrapped key is a `Uint8Array` the caller wipes when done. A session
// key is never persisted: reusing one after a restart would reuse its nonces.

import {
  createPrivateKey,
  createPublicKey,
  diffieHellman,
  generateKeyPairSync,
  hkdfSync,
  type KeyObject,
} from 'node:crypto';

import { aesGcmOpen, DecryptionError } from './aead';
import { frame } from './framing';

export const KEY_BYTES = 32;
export const DIRECTIONS = ['c2s', 's2c'] as const;
export type Direction = (typeof DIRECTIONS)[number];

export class KeyExchangeError extends Error {
  override name = 'KeyExchangeError';
}

const EXCHANGE_INFO = 'xype session-key v1';
const WRAP_NONCE_BYTES = 12;
const WRAPPED_KEY_BYTES = WRAP_NONCE_BYTES + KEY_BYTES + 16;
// DER headers of a raw X25519 key (RFC 8410), so raw bytes go in and out of Node.
const PKCS8_PREFIX = Buffer.from('302e020100300506032b656e04220420', 'hex');
const SPKI_PREFIX = Buffer.from('302a300506032b656e032100', 'hex');
const encoder = new TextEncoder();

/** HKDF-SHA256 to a 32-byte key; no salt means the RFC 5869 zero salt. */
export function hkdf(key: Uint8Array, info: Uint8Array, salt?: Uint8Array): Uint8Array {
  return new Uint8Array(hkdfSync('sha256', key, salt ?? new Uint8Array(), info, KEY_BYTES));
}

/** Each direction encrypts only with its own key (no reflection). */
export function directionKey(sessionKey: Uint8Array, direction: Direction): Uint8Array {
  return hkdf(sessionKey, frame('xype payload', direction));
}

export interface ClientKeyPair {
  readonly publicKey: Uint8Array;
  readonly privateKey: KeyObject;
}

/** A fresh ephemeral pair per `/session-key` request. */
export function clientKeyPair(): ClientKeyPair {
  return pairOf(generateKeyPairSync('x25519').privateKey);
}

/** A pair from a fixed private key: test vectors only, never exported by the package. */
export function clientKeyPairForTests(privateRaw: Uint8Array): ClientKeyPair {
  const der = Buffer.concat([PKCS8_PREFIX, privateRaw]);
  try {
    return pairOf(createPrivateKey({ key: der, format: 'der', type: 'pkcs8' }));
  } finally {
    der.fill(0);
  }
}

function pairOf(privateKey: KeyObject): ClientKeyPair {
  const spki = createPublicKey(privateKey).export({ format: 'der', type: 'spki' });
  return { publicKey: new Uint8Array(spki.subarray(SPKI_PREFIX.length)), privateKey };
}

/** The key that wraps this exchange's session key and repository keys. */
export function wrappingKey(
  pair: ClientKeyPair,
  serverPublicKey: Uint8Array,
  keyId: string,
  userId: string,
): Uint8Array {
  if (serverPublicKey.length !== KEY_BYTES) {
    throw new KeyExchangeError('the server public key is malformed');
  }
  const server = createPublicKey({
    key: Buffer.concat([SPKI_PREFIX, serverPublicKey]),
    format: 'der',
    type: 'spki',
  });
  const shared = sharedSecret(pair.privateKey, server);
  try {
    const info = frame(EXCHANGE_INFO, pair.publicKey, serverPublicKey, userId);
    return hkdf(shared, info, encoder.encode(keyId));
  } finally {
    shared.fill(0);
  }
}

function sharedSecret(privateKey: KeyObject, publicKey: KeyObject): Uint8Array {
  let secret: Buffer;
  try {
    secret = diffieHellman({ privateKey, publicKey });
  } catch {
    throw new KeyExchangeError('the key exchange failed');
  }
  // A view, not a copy, so the caller's wipe reaches the secret's own memory.
  const shared = new Uint8Array(secret.buffer, secret.byteOffset, secret.length);
  if (shared.every((byte) => byte === 0)) {
    throw new KeyExchangeError('the key exchange gave an all-zero secret');
  }
  return shared;
}

/** `wrapped` is `nonce(12) ‖ ciphertext ‖ tag(16)`, the REST `wrapped_key`. */
export function unwrapSessionKey(wrapping: Uint8Array, keyId: string, wrapped: Uint8Array) {
  return unwrap(wrapping, wrapped, frame('session', keyId));
}

export function unwrapRepositoryKey(
  wrapping: Uint8Array,
  repositoryId: string,
  hmacKeyId: string,
  wrapped: Uint8Array,
): Uint8Array {
  return unwrap(wrapping, wrapped, frame('repo-hmac', repositoryId, hmacKeyId));
}

function unwrap(wrapping: Uint8Array, wrapped: Uint8Array, aad: Uint8Array): Uint8Array {
  if (wrapped.length !== WRAPPED_KEY_BYTES) {
    throw new DecryptionError('a wrapped key has the wrong length');
  }
  const nonce = wrapped.subarray(0, WRAP_NONCE_BYTES);
  return aesGcmOpen(wrapping, nonce, wrapped.subarray(WRAP_NONCE_BYTES), aad);
}
