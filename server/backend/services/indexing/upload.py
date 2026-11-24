"""Accepting a `ChunkUpload` (ADR-0015 steps 1 and 2, ADR-0018 amendment, slice 8c3b).

The gateway hands a chunk to the indexer only for a leaf the store holds pending with
the chunk's hash and file. So nothing is published before the `apply` that asked for it
committed, and a client cannot spend scan processes on chunks nobody asked for; a chunk
no longer pending (settled, replaced, deleted) is left out without an error.

The nonce counters of an upload strictly rise in message order, and every one of them,
pending chunk or not, is advanced in the key store before anything is published: the
store refuses a counter not above the highest seen, so a replayed payload is never
published (and two plaintexts never travel under one GCM nonce), and a refused upload
publishes nothing. A spent counter is gone; the client re-encrypts under a new one. If
the broker or a store fails mid-upload, the upload is refused (`UPLOAD_UNAVAILABLE`) and
the stream stays; chunks already published settle once, so the repeat costs only a
scan. The job lives no longer than its session key (`ttl_ms`).

Ids and counts only are logged; ciphertext never leaves this module except in the job.
"""

import itertools
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

import structlog
from pydantic import ValidationError

from backend.services.indexing.ports import MerkleIndex
from backend.services.indexing.steps import UPLOAD_NONCE, UPLOAD_UNAVAILABLE
from backend.services.session import KEY_UNUSABLE, MALFORMED, NO_ACCESS, OpenSession, RefusedError
from common_lib.clock import Clock
from common_lib.domain import ChunkRef
from common_lib.errors import DependencyUnavailableError
from common_lib.index_messages import ChunkJob, WireChunkRef
from common_lib.merkle import MAX_CHUNKS_PER_UPLOAD
from common_lib.security import (
    SESSION_KEY_TTL_SECONDS,
    Direction,
    KeyNotFoundError,
    KeyStore,
    NonceError,
    SessionKeyRecord,
    counter_of,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class UploadedChunk:
    ref: ChunkRef
    key_id: str
    nonce: bytes
    ciphertext: bytes


@dataclass(frozen=True, slots=True)
class Upload:
    """A `ChunkUpload` with the AAD inputs the transport read: the envelope's message id
    and the field number of `chunks`."""

    message_id: str
    request_id: str
    repository_id: uuid.UUID
    field_number: int
    chunks: tuple[UploadedChunk, ...]


@dataclass(frozen=True, slots=True)
class UploadAccepted:
    """The chunks went to the indexer; they settle later (`IndexAck` means accepted)."""

    request_id: str
    repository_id: uuid.UUID
    root: str | None
    published: int


class ChunkPublisher(Protocol):
    async def publish(self, job: ChunkJob, *, correlation_id: str, ttl_ms: int) -> None:
        """On `index.chunks`, confirmed; `DependencyUnavailableError` otherwise."""
        ...


class ChunkUploads:
    """Shared by every stream; holds nothing between uploads."""

    def __init__(
        self, index: MerkleIndex, keys: KeyStore, publisher: ChunkPublisher, *, clock: Clock
    ) -> None:
        self._index = index
        self._keys = keys
        self._publisher = publisher
        self._clock = clock

    async def accept(
        self, session: OpenSession, upload: Upload, correlation_id: str
    ) -> UploadAccepted:
        counters = _check(session, upload)
        ttl_ms = self._ttl_ms(session.key)
        try:
            # Read first: once anything is published, no store failure may refuse the
            # upload, or the client would send it all again.
            root = await self._index.root(upload.repository_id)
            published = await self._publish(session, upload, counters, ttl_ms, correlation_id)
        except DependencyUnavailableError:
            raise RefusedError(UPLOAD_UNAVAILABLE) from None
        logger.info(
            "upload.accepted",
            repository_id=str(upload.repository_id),
            chunks=len(upload.chunks),
            published=published,
        )
        return UploadAccepted(upload.request_id, upload.repository_id, root, published)

    async def _publish(
        self,
        session: OpenSession,
        upload: Upload,
        counters: list[int],
        ttl_ms: int,
        correlation_id: str,
    ) -> int:
        pending = await self._index.pending(upload.repository_id, [c.ref for c in upload.chunks])
        jobs = [
            _job(session, upload, chunk)
            for chunk in upload.chunks
            if chunk.ref.leaf_node_id in pending
        ]
        for counter in counters:
            try:
                await self._keys.advance_nonce(
                    session.key.key_id, Direction.CLIENT_TO_SERVER, counter
                )
            except NonceError:
                raise RefusedError(UPLOAD_NONCE) from None
            except KeyNotFoundError:
                raise RefusedError(KEY_UNUSABLE) from None
        for job in jobs:
            await self._publisher.publish(job, correlation_id=correlation_id, ttl_ms=ttl_ms)
        return len(jobs)

    def _ttl_ms(self, key: SessionKeyRecord) -> int:
        """The key's remaining life in the key store: a job never outlives its key."""
        left = key.created_at + timedelta(seconds=SESSION_KEY_TTL_SECONDS) - self._clock.now()
        ttl_ms = int(left.total_seconds() * 1000)
        if ttl_ms <= 0:
            raise RefusedError(KEY_UNUSABLE)
        return ttl_ms


def _check(session: OpenSession, upload: Upload) -> list[int]:
    """Every chunk names the upload's repository, under this stream's key, once, with a
    counter above the one before it; the counters are returned in message order."""
    if upload.repository_id not in session.repository_ids:
        raise RefusedError(NO_ACCESS)
    chunks = upload.chunks
    if not 1 <= len(chunks) <= MAX_CHUNKS_PER_UPLOAD:
        raise RefusedError(MALFORMED)
    if any(chunk.ref.repository_id != upload.repository_id for chunk in chunks):
        raise RefusedError(NO_ACCESS)
    if any(chunk.key_id != session.key.key_id for chunk in chunks):
        raise RefusedError(KEY_UNUSABLE)
    if len({chunk.ref.leaf_node_id for chunk in chunks}) != len(chunks):
        raise RefusedError(MALFORMED)
    counters = [_counter(chunk) for chunk in chunks]
    if any(low >= high for low, high in itertools.pairwise(counters)):
        raise RefusedError(MALFORMED)
    return counters


def _counter(chunk: UploadedChunk) -> int:
    try:
        return counter_of(chunk.nonce)
    except NonceError:
        raise RefusedError(MALFORMED) from None


def _job(session: OpenSession, upload: Upload, chunk: UploadedChunk) -> ChunkJob:
    principal = session.claims.principal
    try:
        return ChunkJob(
            user_id=principal.user_id,
            team_id=principal.team_id,
            key_id=uuid.UUID(session.key.key_id),
            message_id=upload.message_id,
            request_id=upload.request_id,
            field_number=upload.field_number,
            chunk=WireChunkRef.of(chunk.ref),
            nonce=chunk.nonce,
            ciphertext=chunk.ciphertext,
        )
    except (ValidationError, ValueError):
        raise RefusedError(MALFORMED) from None
