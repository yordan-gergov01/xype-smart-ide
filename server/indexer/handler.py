"""One encrypted chunk in, one result out (ADR-0015, ADR-0019): find the session key,
decrypt in memory, check again for secrets, embed, wipe, publish.

A chunk the check rejects is reported as `RejectedChunk` (it settles the sync); a job
that cannot be opened (an unknown, expired or foreign key, a failed tag) is
dead-lettered with a fixed reason. Plaintext lives only in `SecretBytes`, wiped on every
path. The scan gets one copy (`reveal`), which cannot be wiped, and its child
process another (`indexer.scan`).
Logs, spans and metrics carry ids, counts and fixed outcomes only.
"""

import time
from typing import Literal

import structlog
from opentelemetry.trace import Status, StatusCode, Tracer

from common_lib.embeddings import (
    Embedder,
    EmbeddingError,
    EmbeddingRefusedError,
    EmbeddingUnavailableError,
)
from common_lib.errors import DependencyUnavailableError, InvalidValueError
from common_lib.index_messages import (
    ChunkJob,
    EmbeddedChunk,
    RejectedChunk,
    decode_job,
    encode,
)
from common_lib.messaging import MAX_RETRIES, VECTORS, Delivery, MessageBus, RetryableError
from common_lib.secret_bytes import SecretBytes
from common_lib.security import (
    CryptoBox,
    DecryptionError,
    Direction,
    EncryptedPayload,
    KeyNotFoundError,
    KeyStore,
    NonceError,
    PayloadContext,
)
from common_lib.vectors import check_vector
from indexer.health import Readiness
from indexer.metrics import IndexerMetrics
from indexer.scan import ScanInterruptedError, Scanner

logger = structlog.get_logger(__name__)

# A chunk is at most 64 KiB of scrubbed text (ADR-0019); larger is refused unchecked.
MAX_CHUNK_BYTES = 64 * 1024
type Rejection = Literal["secret_found", "too_large", "unscannable", "unembeddable"]
# The indexer receives what clients send; it never encrypts.
_BOX = CryptoBox(sends=Direction.SERVER_TO_CLIENT)


class Unopened(Exception):  # noqa: N818 - a control-flow signal, not an error
    """The job cannot be opened; `reason` is the fixed dead-letter reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ChunkIndexer:
    def __init__(
        self,
        keys: KeyStore,
        scanner: Scanner,
        embedder: Embedder,
        bus: MessageBus,
        *,
        metrics: IndexerMetrics,
        tracer: Tracer,
        readiness: Readiness,
    ) -> None:
        self._readiness = readiness
        self._keys = keys
        self._scanner = scanner
        self._embedder = embedder
        self._bus = bus
        self._metrics = metrics
        self._tracer = tracer

    async def handle(self, delivery: Delivery) -> str | None:
        """The bus's handler: None acknowledges, a reason dead-letters (`MessageBus`)."""
        started = time.perf_counter()
        # Exceptions are not recorded on the span: their text never reaches a trace.
        with self._tracer.start_as_current_span(
            "indexer.chunk", record_exception=False, set_status_on_exception=False
        ) as span:
            try:
                result = await self._result(delivery)
            except (RetryableError, DependencyUnavailableError) as down:
                self._readiness.dependencies_ok = False
                span.set_attribute("xype.outcome", "retry")
                span.set_status(Status(StatusCode.ERROR, type(down).__name__))
                raise
            except Unopened as unopened:
                self._metrics.chunks.labels(unopened.reason).inc()
                span.set_attribute("xype.outcome", unopened.reason)
                logger.info("indexer.chunk.dead_lettered", reason=unopened.reason)
                return unopened.reason
            self._readiness.dependencies_ok = True
            outcome = result.outcome if isinstance(result, EmbeddedChunk) else result.reason
            await self._bus.publish(
                VECTORS,
                encode(result),
                message_id=delivery.message_id,
                correlation_id=delivery.correlation_id,
            )
            self._metrics.chunks.labels(outcome).inc()
            self._metrics.duration.observe(time.perf_counter() - started)
            span.set_attribute("xype.outcome", outcome)
            logger.info("indexer.chunk.done", outcome=outcome, attempt=delivery.attempt)
            return None

    async def _result(self, delivery: Delivery) -> EmbeddedChunk | RejectedChunk:
        try:
            job = decode_job(delivery.body)
        except InvalidValueError:
            raise Unopened("malformed") from None
        key = await self._key(job)
        try:
            plaintext = _open(job, key)
        finally:
            key.wipe()
        try:
            return await self._index(job, plaintext, final=delivery.attempt >= MAX_RETRIES)
        finally:
            plaintext.wipe()

    async def _key(self, job: ChunkJob) -> SecretBytes:
        try:
            record, key = await self._keys.get(str(job.key_id))
        except (KeyNotFoundError, DecryptionError):
            raise Unopened("key_unusable") from None
        if record.user_id != str(job.user_id):
            key.wipe()
            raise Unopened("key_unusable")
        return key

    async def _index(
        self, job: ChunkJob, plaintext: SecretBytes, *, final: bool
    ) -> EmbeddedChunk | RejectedChunk:
        rejection = await self._check(plaintext, final=final)
        if rejection is not None:
            return RejectedChunk(chunk=job.chunk, reason=rejection)
        try:
            vectors = await self._embedder.embed_documents([plaintext])
        except EmbeddingUnavailableError:
            raise RetryableError("the embedder is unavailable") from None
        except EmbeddingRefusedError:
            # The model refused this text: trying again will not help, and a rejection
            # settles the sync.
            return RejectedChunk(chunk=job.chunk, reason="unembeddable")
        except EmbeddingError:
            # An unusable answer is the model server's or the configuration's fault, not
            # this chunk's: retried (then dead-lettered), never settled as a rejection.
            logger.warning("indexer.embedder.unusable_answer")
            raise RetryableError("the embedder answered something unusable") from None
        try:
            [vector] = vectors
            check_vector(vector)
        except (InvalidValueError, ValueError):
            logger.warning("indexer.embedder.unusable_answer")
            raise RetryableError("the embedder answered something unusable") from None
        model = self._embedder.model
        return EmbeddedChunk(
            chunk=job.chunk,
            model_id=model.model_id,
            model_version=model.model_version,
            vector=list(vector),
        )

    async def _check(self, plaintext: SecretBytes, *, final: bool) -> Rejection | None:
        """The server's re-check (ADR-0019): None when clean, else the fixed reason. A
        check that did not finish is retried; only the last delivery settles it."""
        if len(plaintext) > MAX_CHUNK_BYTES:
            return "too_large"
        try:
            verdict = await self._scanner.scan(plaintext.reveal())
        except ScanInterruptedError:
            if final:
                return "unscannable"
            raise RetryableError("the re-check did not finish") from None
        return None if verdict == "clean" else verdict


def _open(job: ChunkJob, key: SecretBytes) -> SecretBytes:
    """Rebuilds the AAD from the job's plain fields (never from bytes the client sent)."""
    chunk = job.chunk
    context = PayloadContext(
        key_id=str(job.key_id),
        direction=Direction.CLIENT_TO_SERVER,
        message_id=job.message_id,
        request_id=job.request_id,
        field_number=job.field_number,
        repository_id=str(chunk.repository_id),
        obfuscated_file_id=chunk.obfuscated_file_id,
        chunk_hash=chunk.chunk_hash,
        line_start=chunk.line_start,
        line_end=chunk.line_end,
        leaf_node_id=chunk.leaf_node_id,
    )
    payload = EncryptedPayload(str(job.key_id), job.nonce, job.ciphertext)
    try:
        return _BOX.open(key, payload, context)
    except (DecryptionError, NonceError):
        raise Unopened("decryption_failed") from None
