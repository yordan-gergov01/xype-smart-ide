// A session key for one stream (ADR-0015, ADR-0016): an ephemeral X25519 pair, the
// server's wrapped key and the repositories' HMAC keys, unwrapped in memory. The session
// key goes straight into a new `ClientCryptoBox`, which wipes it; it is never stored.

import { ClientCryptoBox } from '@/security/cryptoBox';
import { clientKeyPair, unwrapRepositoryKey, unwrapSessionKey, wrappingKey } from '@/security/keys';

import type { KeyApi } from './api';

export interface SessionKeys {
  readonly box: ClientCryptoBox;
  /** Repository id → its HMAC key; the caller owns and wipes them. */
  readonly repositoryKeys: ReadonlyMap<string, Uint8Array>;
}

export async function mintSessionKey(
  api: KeyApi,
  accessToken: string,
  userId: string,
  repositoryIds: readonly string[],
): Promise<SessionKeys> {
  const pair = clientKeyPair();
  const minted = await api.sessionKey(accessToken, pair.publicKey, repositoryIds);
  const wrapping = wrappingKey(pair, minted.serverPublicKey, minted.keyId, userId);
  const repositoryKeys = new Map<string, Uint8Array>();
  let box: ClientCryptoBox | null = null;
  try {
    box = new ClientCryptoBox(
      minted.keyId,
      unwrapSessionKey(wrapping, minted.keyId, minted.wrappedKey),
    );
    for (const key of minted.repositoryKeys) {
      repositoryKeys.set(
        key.repositoryId,
        unwrapRepositoryKey(wrapping, key.repositoryId, key.hmacKeyId, key.wrappedKey),
      );
    }
    return { box, repositoryKeys };
  } catch (error) {
    box?.wipe();
    for (const key of repositoryKeys.values()) key.fill(0);
    throw error;
  } finally {
    wrapping.fill(0);
  }
}
