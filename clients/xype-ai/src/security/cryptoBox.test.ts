import { describe, expect, it } from 'vitest';

import { DecryptionError } from './aead';
import {
  ClientCryptoBox,
  ContextError,
  counterOf,
  MAX_ENCRYPTIONS_PER_DIRECTION,
  NonceError,
  nonceFor,
  openPayload,
  sealPayload,
} from './cryptoBox';
import { directionKey } from './keys';

const SESSION_KEY = Uint8Array.from({ length: 32 }, (_, i) => i + 100);
const CTX = { messageId: 'm-1', repositoryId: 'repo-1' };
const text = (data: Uint8Array) => new TextDecoder().decode(data);
const utf8 = (value: string) => new TextEncoder().encode(value);

/** The server's side: it seals server-to-client under its own counters. */
function fromServer(counter: number, plaintext: string, messageId = 'm-1') {
  const key = directionKey(SESSION_KEY, 's2c');
  const context = { ...CTX, messageId, keyId: 'key-1', direction: 's2c' as const };
  return {
    keyId: 'key-1',
    nonce: nonceFor(counter),
    ciphertext: sealPayload(key, counter, utf8(plaintext), context),
  };
}

describe('ClientCryptoBox', () => {
  it('spends a new counter on every seal, from 1, under the client-to-server key', () => {
    const box = new ClientCryptoBox('key-1', SESSION_KEY.slice());
    const first = box.seal(utf8('a'), CTX);
    const second = box.seal(utf8('a'), CTX);
    expect([counterOf(first.nonce), counterOf(second.nonce)]).toEqual([1, 2]);
    expect(first.ciphertext).not.toEqual(second.ciphertext);
    const key = directionKey(SESSION_KEY, 'c2s');
    const context = { ...CTX, keyId: 'key-1', direction: 'c2s' as const };
    expect(text(openPayload(key, second.nonce, second.ciphertext, context))).toBe('a');
  });

  it('opens server payloads only with rising counters', () => {
    const box = new ClientCryptoBox('key-1', SESSION_KEY.slice());
    expect(text(box.open(fromServer(3, 'hi'), CTX))).toBe('hi');
    expect(() => box.open(fromServer(3, 'again'), CTX)).toThrow(NonceError);
    expect(() => box.open(fromServer(2, 'older'), CTX)).toThrow(NonceError);
    expect(text(box.open(fromServer(4, 'next'), CTX))).toBe('next');
  });

  it('refuses its own direction, another key and a forged payload', () => {
    const box = new ClientCryptoBox('key-1', SESSION_KEY.slice());
    const own = box.seal(utf8('a'), CTX);
    expect(() => box.open(own, CTX)).toThrow(DecryptionError);
    expect(() => box.open({ ...fromServer(1, 'x'), keyId: 'key-2' }, CTX)).toThrow(DecryptionError);
    expect(() => box.open(fromServer(1, 'x', 'm-2'), CTX)).toThrow(DecryptionError);
    // A failed open spends no counter.
    expect(text(box.open(fromServer(1, 'ok'), CTX))).toBe('ok');
  });

  it('refuses counters outside the budget and nonces of another shape', () => {
    expect(() => nonceFor(0)).toThrow(NonceError);
    expect(() => nonceFor(MAX_ENCRYPTIONS_PER_DIRECTION + 1)).toThrow(NonceError);
    expect(counterOf(nonceFor(MAX_ENCRYPTIONS_PER_DIRECTION))).toBe(MAX_ENCRYPTIONS_PER_DIRECTION);
    expect(() => counterOf(Uint8Array.of(1, ...new Uint8Array(11)))).toThrow(NonceError);
    expect(() => counterOf(new Uint8Array(12))).toThrow(NonceError);
  });

  it('takes the session key over and refuses to work once wiped', () => {
    const key = SESSION_KEY.slice();
    const box = new ClientCryptoBox('key-1', key);
    expect(key.every((byte) => byte === 0)).toBe(true);
    expect(() => new ClientCryptoBox('key-1', key)).toThrow(ContextError);
    box.wipe();
    expect(() => box.seal(utf8('a'), CTX)).toThrow(ContextError);
    expect(() => box.open(fromServer(1, 'x'), CTX)).toThrow(ContextError);
  });

  it('needs both the chunk hash and the leaf id of a chunk', () => {
    const box = new ClientCryptoBox('key-1', SESSION_KEY.slice());
    expect(() => box.seal(utf8('a'), { ...CTX, chunkHash: 'c'.repeat(64) })).toThrow(ContextError);
  });
});
