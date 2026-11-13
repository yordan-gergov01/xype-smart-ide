# Runbook: key rotation

Decisions: ADR-0015 (KEK, session keys), ADR-0016 (repository keys), ADR-0014 (token signing keys and peppers). The KEK settings are wired into the backend (Phase 2 slice 5b); the indexer gets them in slice 8.

Never paste a key into a ticket, chat, commit or log. Keys go only into the environment file on the host (locally) or the secret store (hosted, plan §3a).

## Keys and what rotating them costs

| Key | Where it lives | Rotation | Cost |
|---|---|---|---|
| Session key | `keystore` Redis, wrapped under the KEK | Automatic: every 12 h, on reconnect, after 2^24 encryptions | None |
| KEK (`KEK_CURRENT`, `KEK_PREVIOUS`) | Backend and indexer environment only | Scheduled (yearly) or on suspected exposure | Re-wrap stored keys |
| Repository HMAC key | Postgres `repository_keys`, wrapped under the KEK | Only on compromise or when a developer leaves with a copy | Full re-index of that repository |
| Token signing key, pepper | Backend environment (ADR-0014) | Scheduled or on exposure | Clients log in again after the previous one is removed |

## Rotate the KEK

1. Generate a new KEK with a new id (`<id>:<base64url>`) on a trusted machine:

   ```bash
   python tools/server_tool.py python -m backend.admin_cli new-key kek-2
   ```

2. Move the current value to `KEK_PREVIOUS` and put the new one in `KEK_CURRENT`, for **both** `backend` and `indexer`.
3. Restart `indexer` first, then `backend`. New keys are wrapped under the new KEK; both KEKs still unwrap (`KekRing`).
4. Re-wrap what is stored under the previous KEK:
   - session keys need nothing: they expire within 24 hours;
   - repository keys: there is no re-wrap command yet (a follow-up of slice 7a). Until there is, keep `KEK_PREVIOUS` set: it still unwraps the repository keys stored under it.
   - never re-wrap a session key by writing it again: `put` refuses an existing id, because a re-written key would restart its nonce counters. Let it expire instead.
5. After 24 hours, and after the re-wrap reports zero keys under the previous id, empty `KEK_PREVIOUS` and restart both services. **Until the re-wrap command exists, never empty `KEK_PREVIOUS`, and never rotate the KEK a second time:** the repository keys under the dropped KEK become unreadable, and every `/session-key` request that names one of those repositories fails.
6. Only after step 5 has emptied `KEK_PREVIOUS`: destroy every copy of the old KEK.

## Suspected KEK exposure

Rotate as above, but in step 5 do not wait: flush the session keys instead (`FLUSHDB` on the `keystore` Redis only). Until the repository-key re-wrap exists, keep `KEK_PREVIOUS` set even here, or every registered repository's key becomes unreadable; the exposed KEK then still unwraps them, which is one more reason to rotate them. Clients reconnect and mint new keys; queued indexing jobs fail to decrypt, go to the dead-letter queue and are re-uploaded by the next Merkle sync. Then rotate the repository keys of every affected repository (next section), because the attacker may hold them.

## Rotate a repository key

An admin action, not built yet (a follow-up of slice 7a). The old key is marked retired, the repository's index is dropped, and clients re-index with the new key. Plan it outside working hours for large repositories (the first index can take hours, R7).
