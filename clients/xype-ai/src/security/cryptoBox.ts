// Payload encryption: AES-256-GCM with per-direction keys and counter nonces (ADR-0015),
// matching the server's `common_lib.security.crypto_box`. The nonce is 4 zero bytes and
// a 64-bit big-endian counter. The client seals only client-to-server payloads and opens
// only server-to-client ones, so the two sides never share a nonce under one key.

import { aesGcmOpen, aesGcmSeal, DecryptionError } from './aead';
import { frame } from './framing';
import { type Direction, directionKey } from './keys';

export const AAD_VERSION = '2';
// Far below GCM's limit; the server refuses anything above it.
export const MAX_ENCRYPTIONS_PER_DIRECTION = 2 ** 24;
export const NONCE_BYTES = 12;

export class NonceError extends Error {
  override name = 'NonceError';
}

export class ContextError extends Error {
  override name = 'ContextError';
}

/** What a payload is bound to. Fields a message has no use for stay empty. */
export interface PayloadContext {
  readonly keyId: string;
  readonly direction: Direction;
  readonly messageId: string;
  readonly requestId?: string;
  readonly fieldNumber?: number;
  readonly repositoryId?: string;
  readonly obfuscatedFileId?: string;
  readonly chunkHash?: string;
  readonly lineStart?: number;
  readonly lineEnd?: number;
  readonly leafNodeId?: string;
}

/** Mirrors `xype.v1.EncryptedPayload`; `ciphertext` includes the 16-byte tag. */
export interface EncryptedPayload {
  readonly keyId: string;
  readonly nonce: Uint8Array;
  readonly ciphertext: Uint8Array;
}

/** The canonical AAD; the receiver rebuilds it and never trusts sent bytes. */
export function payloadAad(context: PayloadContext): Uint8Array {
  const chunkHash = context.chunkHash ?? '';
  const leafNodeId = context.leafNodeId ?? '';
  if ((chunkHash === '') !== (leafNodeId === '')) {
    // A chunk payload without its leaf id would silently lose the leaf binding.
    throw new ContextError('a chunk context needs both chunkHash and leafNodeId');
  }
  return frame(
    AAD_VERSION,
    context.direction,
    context.keyId,
    context.messageId,
    context.requestId ?? '',
    context.fieldNumber ?? 0,
    context.repositoryId ?? '',
    context.obfuscatedFileId ?? '',
    chunkHash,
    context.lineStart ?? 0,
    context.lineEnd ?? 0,
    leafNodeId,
  );
}

export function nonceFor(counter: number): Uint8Array {
  if (!Number.isInteger(counter) || counter < 1 || counter > MAX_ENCRYPTIONS_PER_DIRECTION) {
    throw new NonceError("the nonce counter is outside the key's budget");
  }
  const nonce = new Uint8Array(NONCE_BYTES);
  new DataView(nonce.buffer).setBigUint64(4, BigInt(counter));
  return nonce;
}

/** The counter inside a received nonce; refuses any other nonce shape. */
export function counterOf(nonce: Uint8Array): number {
  if (nonce.length !== NONCE_BYTES || nonce.subarray(0, 4).some((byte) => byte !== 0)) {
    throw new NonceError('the nonce is not a counter nonce');
  }
  const counter = new DataView(nonce.buffer, nonce.byteOffset, NONCE_BYTES).getBigUint64(4);
  if (counter < 1n || counter > BigInt(MAX_ENCRYPTIONS_PER_DIRECTION)) {
    throw new NonceError("the nonce counter is outside the key's budget");
  }
  return Number(counter);
}

export type SealContext = Omit<PayloadContext, 'keyId' | 'direction'>;

/**
 * One session key on the client and its counters. It lives only in memory: counters
 * start at 1 for every new key, so a key must never outlive its `ClientCryptoBox`.
 */
export class ClientCryptoBox {
  readonly #send: Uint8Array;
  readonly #receive: Uint8Array;
  #sent = 0;
  #received = 0;
  #wiped = false;

  readonly keyId: string;

  /**
   * Takes the session key over: it is wiped once the direction keys are derived, so no
   * second box can start the same counters under it.
   */
  constructor(keyId: string, sessionKey: Uint8Array) {
    if (sessionKey.every((byte) => byte === 0)) throw new ContextError('the session key is empty');
    this.keyId = keyId;
    this.#send = directionKey(sessionKey, 'c2s');
    this.#receive = directionKey(sessionKey, 's2c');
    sessionKey.fill(0);
  }

  /** Each call spends the next counter, also when the caller drops the result. */
  seal(plaintext: Uint8Array, context: SealContext): EncryptedPayload {
    this.#usable();
    const full: PayloadContext = { ...context, keyId: this.keyId, direction: 'c2s' };
    const nonce = nonceFor(this.#sent + 1);
    this.#sent += 1;
    return {
      keyId: this.keyId,
      nonce,
      ciphertext: aesGcmSeal(this.#send, nonce, plaintext, payloadAad(full)),
    };
  }

  /** Refuses a counter not above the last one opened, so a replay never opens twice. */
  open(payload: EncryptedPayload, context: SealContext): Uint8Array {
    this.#usable();
    if (payload.keyId !== this.keyId) throw new DecryptionError('the payload names another key');
    const counter = counterOf(payload.nonce);
    if (counter <= this.#received) throw new NonceError('the nonce counter was already used');
    const full: PayloadContext = { ...context, keyId: this.keyId, direction: 's2c' };
    const plaintext = aesGcmOpen(
      this.#receive,
      payload.nonce,
      payload.ciphertext,
      payloadAad(full),
    );
    this.#received = counter;
    return plaintext;
  }

  /** After this, `seal` and `open` throw: a wiped key must never encrypt. */
  wipe(): void {
    this.#wiped = true;
    this.#send.fill(0);
    this.#receive.fill(0);
  }

  #usable(): void {
    if (this.#wiped) throw new ContextError('the session key was wiped');
  }
}

/** One payload under an explicit direction key and counter (test vectors, the server's view). */
export function sealPayload(
  key: Uint8Array,
  counter: number,
  plaintext: Uint8Array,
  context: PayloadContext,
): Uint8Array {
  return aesGcmSeal(key, nonceFor(counter), plaintext, payloadAad(context));
}

export function openPayload(
  key: Uint8Array,
  nonce: Uint8Array,
  ciphertext: Uint8Array,
  context: PayloadContext,
): Uint8Array {
  counterOf(nonce);
  return aesGcmOpen(key, nonce, ciphertext, payloadAad(context));
}
