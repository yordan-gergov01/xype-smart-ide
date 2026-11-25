"""`ChunkUploads` with fakes (ADR-0015 steps 1 and 2, slice 8c3b): a chunk goes to the
indexer only for a leaf stored pending with its hash and file, once per nonce counter,
under this stream's key and repository, and never outlives the key. Every counter of an
upload is spent before anything is published, so a refused upload publishes nothing;
anything malformed spends nothing."""

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import structlog.testing
from support.clock import FakeClock
from support.contracts import merkle as contract
from support.keystore import FakeKeyStore
from support.merkle import FakeMerkleIndex
from support.uploads import RecordingBus
from xype.v1 import index_pb2

from backend.grpc import upload as transport
from backend.services.indexing import (
    UPLOAD_NONCE,
    UPLOAD_UNAVAILABLE,
    ChunkUploads,
    Upload,
    UploadedChunk,
)
from backend.services.session import KEY_UNUSABLE, MALFORMED, NO_ACCESS, OpenSession, RefusedError
from common_lib.auth import AccessClaims, Principal
from common_lib.errors import DependencyUnavailableError
from common_lib.merkle import (
    MAX_CHUNKS_PER_UPLOAD,
    REJECTION_REASONS,
    MerkleNodeUpdate,
    MerkleUpdate,
)
from common_lib.secret_bytes import SecretBytes
from common_lib.security import SESSION_KEY_TTL_SECONDS, SessionKeyRecord, nonce_for

NOW = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
FIELD = 3  # `ChunkUpload.chunks`


class World:
    def __init__(self) -> None:
        self.clock = FakeClock(NOW)
        self.index, self.keys = FakeMerkleIndex(self.clock), FakeKeyStore(self.clock)
        self.bus = RecordingBus()
        self.uploads = ChunkUploads(
            self.index, self.keys, self.bus.publisher(), settle_window_s=900, clock=self.clock
        )
        self.repo, self.other = uuid.uuid4(), uuid.uuid4()
        user, team = uuid.uuid4(), uuid.uuid4()
        self.key = SessionKeyRecord(str(uuid.uuid4()), str(user), str(uuid.uuid4()), NOW)
        claims = AccessClaims(
            Principal(user, team, "developer"), uuid.UUID(self.key.family_id), NOW, NOW
        )
        self.session = OpenSession("s-1", claims, self.key, frozenset({self.repo, self.other}))

    async def ready(self) -> "World":
        await self.keys.put(
            self.key,
            SecretBytes(bytes(32)),
            ttl_seconds=SESSION_KEY_TTL_SECONDS,
            max_active=8,
            credentials_issued_at=NOW,
        )
        for repository_id in (self.repo, self.other):
            self.index.register(repository_id)
            await self.index.apply(
                repository_id, None, MerkleUpdate(contract.ROOT_1, contract.TREE)
            )
        return self

    def upload(
        self, *chunks: tuple[MerkleNodeUpdate, int], repository_id: uuid.UUID | None = None
    ) -> Upload:
        repo = repository_id or self.repo
        return Upload(
            "m-1",
            "r-1",
            repo,
            FIELD,
            tuple(
                UploadedChunk(
                    contract.ref(repo, node), self.key.key_id, nonce_for(counter), b"c" * 32
                )
                for node, counter in chunks
            ),
        )

    async def accept(self, upload: Upload) -> int:
        accepted = await self.uploads.accept(self.session, upload, "corr-1")
        return accepted.published


FIRST, SECOND, THIRD = contract.TREE[2], contract.TREE[3], contract.TREE[5]


async def test_a_pending_chunk_is_published_with_its_aad_inputs_and_key_life() -> None:
    world = await World().ready()
    world.clock.advance(timedelta(hours=2))
    accepted = await world.uploads.accept(world.session, world.upload((FIRST, 1)), "corr-1")
    assert (accepted.published, accepted.root) == (1, contract.ROOT_1)
    [(job, ttl_ms)] = world.bus.published
    assert (job.key_id, job.message_id, job.request_id) == (
        uuid.UUID(world.key.key_id),
        "m-1",
        "r-1",
    )
    assert (job.field_number, job.chunk.leaf_node_id, job.nonce) == (
        FIELD,
        contract.L1,
        nonce_for(1),
    )
    assert job.user_id == world.session.claims.principal.user_id
    assert ttl_ms == (SESSION_KEY_TTL_SECONDS - 2 * 3600) * 1000


async def test_a_replayed_nonce_is_refused_and_never_published() -> None:
    world = await World().ready()
    assert await world.accept(world.upload((FIRST, 5))) == 1
    with pytest.raises(RefusedError) as refused:
        await world.accept(world.upload((SECOND, 5)))  # the same counter again
    assert refused.value.reason == UPLOAD_NONCE
    with pytest.raises(RefusedError):
        await world.accept(world.upload((SECOND, 4)))  # a lower one
    with pytest.raises(RefusedError):
        await world.accept(world.upload((SECOND, 5), (THIRD, 6)))  # no prefix is published
    assert [job.chunk.leaf_node_id for job, _ in world.bus.published] == [contract.L1]
    assert await world.accept(world.upload((THIRD, 7))) == 1


async def test_a_chunk_no_longer_pending_is_left_out_but_its_counter_is_spent() -> None:
    world = await World().ready()
    assert await world.index.settle(contract.result(world.repo, THIRD)) == "settled"
    stale = dataclasses.replace(SECOND, hash=contract.hexid(0x99))  # not the stored hash
    assert await world.accept(world.upload((FIRST, 1), (stale, 2), (THIRD, 3))) == 1
    assert len(world.bus.published) == 1
    # A left-out chunk's counter is spent too: its payload can never be replayed later.
    with pytest.raises(RefusedError):
        await world.accept(world.upload((SECOND, 3)))
    assert await world.accept(world.upload((SECOND, 4))) == 1


async def test_a_chunk_of_another_repository_is_refused_whole() -> None:
    """The cross-team case: a nested ref naming another repository, even one this
    stream may use, publishes nothing and spends no counter."""
    world = await World().ready()
    mixed = world.upload((FIRST, 1))
    foreign = dataclasses.replace(mixed.chunks[0], ref=contract.ref(world.other, SECOND))
    for upload in (
        dataclasses.replace(mixed, chunks=(*mixed.chunks, foreign)),
        world.upload((FIRST, 1), repository_id=uuid.uuid4()),
    ):
        with pytest.raises(RefusedError) as refused:
            await world.accept(upload)
        assert refused.value.reason == NO_ACCESS
    assert world.bus.published == []
    assert await world.accept(world.upload((FIRST, 1))) == 1


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("other key", KEY_UNUSABLE),
        ("same leaf twice", MALFORMED),
        ("too many", MALFORMED),
        ("none", MALFORMED),
        ("not a counter nonce", MALFORMED),
        ("counters out of order", MALFORMED),
    ],
)
async def test_malformed_uploads_spend_nothing(change: str, reason: str) -> None:
    world = await World().ready()
    upload = world.upload((FIRST, 1), (SECOND, 2))
    first = upload.chunks[0]
    chunks = {
        "other key": (dataclasses.replace(first, key_id=str(uuid.uuid4())),),
        "same leaf twice": (first, dataclasses.replace(first, nonce=nonce_for(3))),
        "too many": (first,) * (MAX_CHUNKS_PER_UPLOAD + 1),
        "none": (),
        "not a counter nonce": (dataclasses.replace(first, nonce=b"\x01" * 12),),
        "counters out of order": tuple(reversed(upload.chunks)),
    }[change]
    with pytest.raises(RefusedError) as refused:
        await world.accept(dataclasses.replace(upload, chunks=chunks))
    assert refused.value.reason == reason
    assert world.bus.published == []
    assert await world.accept(world.upload((FIRST, 1))) == 1


async def test_a_key_at_the_end_of_its_life_publishes_nothing() -> None:
    world = await World().ready()
    world.clock.advance(timedelta(seconds=SESSION_KEY_TTL_SECONDS))
    with pytest.raises(RefusedError) as refused:
        await world.accept(world.upload((FIRST, 1)))
    assert refused.value.reason == KEY_UNUSABLE
    assert world.bus.published == []


async def test_a_broker_failure_refuses_the_upload_and_its_counters_are_gone() -> None:
    """The stream stays; the client re-encrypts under new counters, so a ciphertext is
    never published twice under one nonce (the chunk already published settles once)."""
    world = await World().ready()
    world.bus.fail_after = 1
    with pytest.raises(RefusedError) as refused:
        await world.accept(world.upload((FIRST, 1), (SECOND, 2)))
    assert refused.value.reason == UPLOAD_UNAVAILABLE
    world.bus.fail_after = None
    with pytest.raises(RefusedError):
        await world.accept(world.upload((SECOND, 2)))
    assert await world.accept(world.upload((SECOND, 3))) == 1


async def test_a_key_deleted_since_hello_publishes_nothing() -> None:
    """Evicted by the key cap or deleted by a revocation after the gate checked it."""
    world = await World().ready()
    await world.keys.revoke_family(world.key.family_id)
    with pytest.raises(RefusedError) as refused:
        await world.accept(world.upload((FIRST, 1)))
    assert refused.value.reason == KEY_UNUSABLE
    assert world.bus.published == []


@pytest.mark.parametrize("failing", ["upload_states", "advance_nonce", "root"])
async def test_a_store_outage_refuses_the_upload_before_anything_is_published(
    failing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await World().ready()

    async def unavailable(*args: object, **kwargs: object) -> None:
        raise DependencyUnavailableError("store")

    owner = world.keys if failing == "advance_nonce" else world.index
    monkeypatch.setattr(owner, failing, unavailable)
    with pytest.raises(RefusedError) as refused:
        await world.accept(world.upload((FIRST, 1)))
    assert refused.value.reason == UPLOAD_UNAVAILABLE
    assert world.bus.published == []


async def test_a_chunk_published_within_the_window_is_acked_without_a_second_publish() -> None:
    """The indexer has it: a repeat costs no second scan; after the window it may go
    again (slice 8c3c)."""
    world = await World().ready()
    assert await world.accept(world.upload((FIRST, 1))) == 1
    assert await world.accept(world.upload((FIRST, 2))) == 0
    world.clock.advance(timedelta(seconds=901))
    assert await world.accept(world.upload((FIRST, 3))) == 1
    assert len(world.bus.published) == 2


async def test_a_chunk_rejected_before_is_reported_with_its_reason() -> None:
    world = await World().ready()
    rejected = contract.result(world.repo, THIRD, reason="secret_found")
    assert await world.index.settle(rejected) == "settled"
    accepted = await world.uploads.accept(
        world.session, world.upload((FIRST, 1), (THIRD, 2)), "corr-1"
    )
    assert (accepted.published, accepted.rejected) == (1, ((rejected.chunk, "secret_found"),))
    ack = transport.ack(NOW, "corr-1", accepted).index_ack
    [chunk] = ack.rejected
    assert (chunk.chunk.leaf_node_id, chunk.reason) == (
        contract.L3,
        index_pb2.REJECTION_REASON_SECRET_FOUND,
    )


def test_every_rejection_reason_has_a_proto_value() -> None:
    for reason in REJECTION_REASONS:
        assert index_pb2.RejectionReason.Value(transport.reason_name(reason)) != 0


async def test_a_failed_publish_mark_still_accepts_the_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The job is on the broker; unmarked, a repeat within the window goes again."""
    world = await World().ready()

    async def unavailable(*args: object, **kwargs: object) -> None:
        raise DependencyUnavailableError("store")

    monkeypatch.setattr(world.index, "mark_published", unavailable)
    with structlog.testing.capture_logs() as logs:
        assert await world.accept(world.upload((FIRST, 1))) == 1
    assert "upload.mark_failed" in [entry["event"] for entry in logs]
    assert await world.accept(world.upload((FIRST, 2))) == 1
