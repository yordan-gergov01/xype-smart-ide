// AES-256-GCM with a 16-byte tag appended to the ciphertext, as the server's
// `cryptography` AESGCM produces it. Keys and plaintext stay `Uint8Array`s so callers
// can wipe them; Node and OpenSSL keep internal copies that cannot be zeroed.

import { createCipheriv, createDecipheriv } from 'node:crypto';

export class DecryptionError extends Error {
  override name = 'DecryptionError';
}

const TAG_BYTES = 16;

export function aesGcmSeal(
  key: Uint8Array,
  nonce: Uint8Array,
  plaintext: Uint8Array,
  aad: Uint8Array,
): Uint8Array {
  const cipher = createCipheriv('aes-256-gcm', key, nonce, { authTagLength: TAG_BYTES });
  cipher.setAAD(aad);
  const head = cipher.update(plaintext);
  const tail = cipher.final();
  const sealed = new Uint8Array(head.length + tail.length + TAG_BYTES);
  sealed.set(head);
  sealed.set(tail, head.length);
  sealed.set(cipher.getAuthTag(), head.length + tail.length);
  return sealed;
}

/** Throws `DecryptionError` when the ciphertext, nonce or AAD was changed. */
export function aesGcmOpen(
  key: Uint8Array,
  nonce: Uint8Array,
  sealed: Uint8Array,
  aad: Uint8Array,
): Uint8Array {
  if (sealed.length < TAG_BYTES) throw new DecryptionError('the ciphertext is too short');
  const body = sealed.subarray(0, sealed.length - TAG_BYTES);
  const decipher = createDecipheriv('aes-256-gcm', key, nonce, { authTagLength: TAG_BYTES });
  decipher.setAAD(aad);
  decipher.setAuthTag(sealed.subarray(sealed.length - TAG_BYTES));
  const head = decipher.update(body);
  try {
    const tail = decipher.final();
    const plaintext = new Uint8Array(head.length + tail.length);
    plaintext.set(head);
    plaintext.set(tail, head.length);
    return plaintext;
  } catch {
    throw new DecryptionError('the payload failed authentication');
  } finally {
    head.fill(0);
  }
}
