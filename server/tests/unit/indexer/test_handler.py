"""The indexer's handler (ADR-0015, ADR-0019) with fakes: a sealed chunk becomes a vector;
one moved in any bound field, under another user's key or without a key is
dead-lettered; a planted secret is rejected and never reaches the embedder; an embedder
outage is retried. Nothing it emits holds the plaintext."""

import base64
import json
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from support.bus import FakeBus
from support.clock import FakeClock
from support.embeddings import FakeEmbedder
from support.keystore import FakeKeyStore

from common_lib.domain import ChunkRef
from common_lib.embeddings import EmbeddingError, EmbeddingRefusedError
from common_lib.index_messages import ChunkJob, WireChunkRef, decode_result, encode
from common_lib.messaging import VECTORS, Delivery, RetryableError
from common_lib.secret_bytes import SecretBytes
from common_lib.security import (
    CryptoBox,
    Direction,
    PayloadContext,
    SecretScrubber,
    SessionKeyRecord,
    load_rules,
    new_key,
)
from indexer.handler import MAX_CHUNK_BYTES, SCRUB_WORKERS, ChunkIndexer
from indexer.health import Readiness
from indexer.metrics import IndexerMetrics

RULES_DIR = Path(__file__).resolve().parents[4] / "packages" / "scrub-rules"
NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
MARKER = "def secret_sauce_7f3a():\n    return 42\n"


def _planted_secret() -> str:
    """A corpus input the rules must change; stored there as fragments."""
    corpus = json.loads((RULES_DIR / "corpus.json").read_text("utf-8"))
    for case in corpus["cases"]:
        text, expected = "".join(case["input"]), "".join(case["expected"])
        if text != expected:
            return text
    raise AssertionError("the corpus has no case with a finding")


class World:
    def __init__(self, *, embedder: FakeEmbedder | None = None) -> None:
        self.keys = FakeKeyStore(FakeClock(NOW))
        self.bus = FakeBus()
        self.embedder = embedder or FakeEmbedder()
        self.registry = CollectorRegistry()
        self.readiness = Readiness(consuming=True)
        self.indexer = ChunkIndexer(
            self.keys,
            SecretScrubber(load_rules(RULES_DIR / "rules.json")),
            self.embedder,
            self.bus,
            metrics=IndexerMetrics.create(self.registry),
            tracer=TracerProvider().get_tracer("test"),
            readiness=self.readiness,
        )
        self.user = uuid.uuid4()
        self.key_id = str(uuid.uuid4())
        self.key = new_key()

    async def store_key(self, user: uuid.UUID | None = None) -> None:
        record = SessionKeyRecord(self.key_id, str(user or self.user), "family-1", NOW)
        await self.keys.put(
            record,
            SecretBytes(self.key.reveal()),
            ttl_seconds=3600,
            max_active=8,
            credentials_issued_at=NOW,
        )

    def job(self, text: str | bytes, **moved: object) -> bytes:
        """A chunk sealed as the client seals it; `moved` changes the job after sealing."""
        ref = ChunkRef(uuid.uuid4(), "f" * 64, 3, 9, "c" * 64, "e" * 64)
        context = PayloadContext(
            key_id=self.key_id,
            direction=Direction.CLIENT_TO_SERVER,
            message_id="m-1",
            request_id="r-1",
            field_number=3,
            repository_id=str(ref.repository_id),
            obfuscated_file_id=ref.obfuscated_file_id,
            chunk_hash=ref.chunk_hash,
            line_start=ref.line_start,
            line_end=ref.line_end,
            leaf_node_id=ref.leaf_node_id,
        )
        sealed = CryptoBox(sends=Direction.CLIENT_TO_SERVER).seal(
            self.key, 1, SecretBytes(text if isinstance(text, bytes) else text.encode()), context
        )
        job = ChunkJob(
            user_id=self.user,
            team_id=uuid.uuid4(),
            key_id=uuid.UUID(self.key_id),
            message_id="m-1",
            request_id="r-1",
            field_number=3,
            chunk=WireChunkRef.of(ref),
            nonce=sealed.nonce,
            ciphertext=sealed.ciphertext,
        )
        chunk = {k: v for k, v in moved.items() if k in WireChunkRef.model_fields}
        rest = {k: v for k, v in moved.items() if k not in chunk}
        if chunk:
            rest["chunk"] = job.chunk.model_copy(update=chunk)
        return encode(job.model_copy(update=rest))

    async def handle(self, body: bytes) -> str | None:
        return await self.indexer.handle(Delivery(body, "m-1", "corr-1", 0))

    def outcome(self, name: str) -> float:
        value = self.registry.get_sample_value("xype_indexer_chunks_total", {"outcome": name})
        return value or 0.0


async def test_a_sealed_chunk_becomes_a_vector_and_nothing_leaks() -> None:
    world = World()
    await world.store_key()
    with structlog.testing.capture_logs() as logs:
        assert await world.handle(world.job(MARKER)) is None
    [message] = world.bus.queues[VECTORS]
    result = decode_result(message.body)
    assert result.outcome == "embedded"
    assert result.model_id == world.embedder.model.model_id
    assert message.correlation_id == "corr-1"
    assert world.outcome("embedded") == 1
    for emitted in (repr(logs), message.body.decode()):
        assert "secret_sauce" not in emitted


@pytest.mark.parametrize(
    "moved",
    [
        {"leaf_node_id": "d" * 64},
        {"chunk_hash": "d" * 64},
        {"line_end": 10},
        {"repository_id": uuid.uuid4()},
        {"request_id": "r-2"},
        {"field_number": 4},
    ],
    ids=["leaf", "hash", "lines", "repository", "request", "field"],
)
async def test_a_chunk_moved_in_any_bound_field_is_dead_lettered(moved: dict[str, object]) -> None:
    world = World()
    await world.store_key()
    assert await world.handle(world.job(MARKER, **moved)) == "decryption_failed"
    assert world.bus.depth(VECTORS) == 0


async def test_another_users_or_a_missing_key_is_unusable() -> None:
    world = World()
    await world.store_key(user=uuid.uuid4())
    assert await world.handle(world.job(MARKER)) == "key_unusable"
    other = World()
    assert await other.handle(other.job(MARKER)) == "key_unusable"
    assert await other.handle(b'{"not": "a job"}') == "malformed"
    assert world.bus.depth(VECTORS) == 0


async def test_a_planted_secret_is_rejected_before_the_embedder() -> None:
    calls: list[int] = []

    class CountingEmbedder(FakeEmbedder):
        async def embed_documents(self, texts):  # type: ignore[no-untyped-def]
            calls.append(len(texts))
            return await super().embed_documents(texts)

    world = World(embedder=CountingEmbedder())
    await world.store_key()
    assert await world.handle(world.job(_planted_secret())) is None
    result = decode_result(world.bus.queues[VECTORS][0].body)
    assert (result.outcome, getattr(result, "reason", None)) == ("rejected", "secret_found")
    assert calls == []


async def test_large_or_unreadable_chunks_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The schema already caps the ciphertext at 64 KiB; the indexer checks again."""
    world = World()
    await world.store_key()
    monkeypatch.setattr("indexer.handler.MAX_CHUNK_BYTES", 8)
    await world.handle(world.job("more than eight bytes"))
    monkeypatch.setattr("indexer.handler.MAX_CHUNK_BYTES", MAX_CHUNK_BYTES)
    await world.handle(world.job(bytes([0xFF, 0xFE]) + b" not utf-8"))
    reasons = [getattr(decode_result(m.body), "reason", None) for m in world.bus.queues[VECTORS]]
    assert reasons == ["too_large", "unscannable"]


async def test_an_embedder_outage_is_retried_and_reported_not_ready() -> None:
    world = World(embedder=FakeEmbedder(available=False))
    await world.store_key()
    with pytest.raises(RetryableError):
        await world.handle(world.job(MARKER))
    assert world.bus.depth(VECTORS) == 0
    assert not world.readiness.ready()
    world.embedder._available = True
    await world.handle(world.job(MARKER))
    assert world.readiness.ready()


async def test_a_refusal_by_the_model_is_a_rejection() -> None:
    class Refusing(FakeEmbedder):
        async def embed_documents(self, texts):  # type: ignore[no-untyped-def]
            for text in texts:
                text.wipe()
            raise EmbeddingRefusedError("refused")

    world = World(embedder=Refusing())
    await world.store_key()
    assert await world.handle(world.job(MARKER)) is None
    result = decode_result(world.bus.queues[VECTORS][0].body)
    assert getattr(result, "reason", None) == "unembeddable"


@pytest.mark.parametrize(
    "answer",
    [EmbeddingError("unusable"), [[0.0] * 1024], [[float("nan")] * 1024], [[1.0] * 3], []],
    ids=["unusable", "zero vector", "nan", "short vector", "no vector"],
)
async def test_an_unusable_answer_is_retried_not_settled(answer: object) -> None:
    class Broken(FakeEmbedder):
        async def embed_documents(self, texts):  # type: ignore[no-untyped-def]
            for text in texts:
                text.wipe()
            if isinstance(answer, Exception):
                raise answer
            return answer

    world = World(embedder=Broken())
    await world.store_key()
    with pytest.raises(RetryableError):
        await world.handle(world.job(MARKER))
    assert world.bus.depth(VECTORS) == 0
    assert not world.readiness.ready()


def test_the_job_carries_only_ciphertext() -> None:
    world = World()
    body = json.loads(world.job(MARKER))
    assert "secret_sauce" not in base64.urlsafe_b64decode(body["ciphertext"]).decode("latin-1")


async def test_a_scrub_that_does_not_finish_is_unscannable(monkeypatch: pytest.MonkeyPatch) -> None:
    world = World()
    await world.store_key()
    monkeypatch.setattr("indexer.handler.SCRUB_TIMEOUT_S", 0.05)

    def stuck(_: str) -> object:
        time.sleep(0.5)  # a scan that runs far past its budget
        raise AssertionError("not reached in time")

    monkeypatch.setattr(world.indexer._scrubber, "scrub", stuck)
    assert await world.handle(world.job(MARKER)) is None
    result = decode_result(world.bus.queues[VECTORS][0].body)
    assert getattr(result, "reason", None) == "unscannable"


async def test_busy_scan_threads_mean_a_retry_not_a_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chunks stuck past their time hold their threads; the next chunk is not blamed."""
    world = World()
    await world.store_key()
    monkeypatch.setattr("indexer.handler.SCRUB_TIMEOUT_S", 0.05)
    release = threading.Event()

    def stuck(_: str) -> object:
        release.wait(5)
        raise AssertionError("released")

    monkeypatch.setattr(world.indexer._scrubber, "scrub", stuck)
    for _ in range(SCRUB_WORKERS):
        await world.handle(world.job(MARKER))  # each runs past its time: unscannable
    with pytest.raises(RetryableError):
        await world.handle(world.job(MARKER))
    assert world.readiness.healthy()  # not yet stuck for long
    since = world.readiness.scans_full_since
    assert since is not None
    assert not world.readiness.healthy(now=since + 31)
    release.set()
    reasons = [getattr(decode_result(m.body), "reason", None) for m in world.bus.queues[VECTORS]]
    assert reasons == ["unscannable"] * SCRUB_WORKERS
