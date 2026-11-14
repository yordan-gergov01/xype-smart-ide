"""The Merkle sync over the `Session` stream (ADR-0018), gRPC with TLS on loopback: Hello
starts a sync for each differing root after `HelloAck`, the client's level reports lead
to a `MerkleDelta` or an `IndexAck`, the poll asks again, and refusals keep the stream
open and echo nothing."""

import uuid
from datetime import timedelta

from grpc import aio
from support.merkle_client import ClientTree, Spec
from support.streams import Call, ShiftedClock, StreamWorld, heartbeat, hello, stream_server
from xype.v1 import common_pb2, index_pb2, session_pb2

from backend.services.indexing import SYNC_MISMATCH, SyncRequest, TreeReport


def h(n: int) -> str:
    return f"{n:064x}"


D, F, G, L1, L2, L3 = (h(n) for n in (0xD, 0xF, 0x6, 0x11, 0x12, 0x13))
BASE: Spec = {
    D: {F: [(L1, h(0xC1), (1, 10)), (L2, h(0xC2), (12, 20))]},
    G: [(L3, h(0xC3), (1, 5))],
}


async def _ready(world: StreamWorld) -> tuple[str, str, uuid.UUID]:
    token, claims = await world.login(await world.client())
    return token, await world.session_key(claims), world.repository(world.team_of(claims))


async def _read(call: Call) -> session_pb2.SessionResponse:
    out = await call.read()
    assert out is not aio.EOF
    assert isinstance(out, session_pb2.SessionResponse)
    return out


def _report(report: TreeReport) -> session_pb2.SessionRequest:
    nodes = [
        index_pb2.MerkleNode(
            node_id=n.node_id,
            hash=n.hash,
            is_leaf=n.is_leaf,
            parent_id=n.parent_id or "",
            lines=common_pb2.LineRange(start=n.lines[0], end=n.lines[1]) if n.lines else None,
        )
        for n in report.nodes
    ]
    message = index_pb2.MerkleTreeReport(
        request_id=report.request_id,
        repository_id=str(report.repository_id),
        root_hash=report.root_hash,
        nodes=nodes,
        last_page=report.last_page,
    )
    return session_pb2.SessionRequest(merkle_tree_report=message)


def _request(out: session_pb2.SessionResponse, repository: uuid.UUID) -> SyncRequest:
    assert out.WhichOneof("body") == "merkle_sync_request", out
    asked = out.merkle_sync_request
    root = asked.server_root_hash or None
    return SyncRequest(asked.request_id, repository, root, tuple(asked.node_ids))


async def _answer(
    call: Call, tree: ClientTree, out: session_pb2.SessionResponse, repository: uuid.UUID
) -> session_pb2.SessionResponse:
    """Answers requests until the server says something else."""
    while out.WhichOneof("body") == "merkle_sync_request":
        for report in tree.answer(_request(out, repository)):
            await call.write(_report(report))
        out = await _read(call)
    return out


async def test_hello_acks_first_then_syncs_to_a_delta() -> None:
    async with stream_server() as world:
        token, key_id, repository = await _ready(world)
        tree = ClientTree(BASE)
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository, **{str(repository): tree.root}))
            assert (await _read(call)).WhichOneof("body") == "hello_ack"
            done = await _answer(call, tree, await _read(call), repository)
    assert done.WhichOneof("body") == "merkle_delta"
    uploads = {(c.leaf_node_id, c.obfuscated_file_id) for c in done.merkle_delta.upload}
    assert uploads == {(L1, F), (L2, F), (L3, G)}


async def test_a_root_report_applies_a_deletion_and_acks_the_new_root() -> None:
    async with stream_server() as world:
        token, key_id, repository = await _ready(world)
        await world.merkle.apply(repository, None, ClientTree(BASE).as_update())
        without_g = ClientTree({D: BASE[D]})
        report = index_pb2.MerkleRootReport(repository_id=str(repository), root_hash=without_g.root)
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository))
            await _read(call)
            await call.write(session_pb2.SessionRequest(merkle_root_report=report))
            done = await _answer(call, without_g, await _read(call), repository)
    assert done.index_ack.server_root_hash == without_g.root
    assert world.merkle.roots[repository] == without_g.root
    assert G not in world.merkle.nodes[repository]


async def test_a_mismatched_level_is_refused_and_the_stream_goes_on() -> None:
    async with stream_server() as world:
        token, key_id, repository = await _ready(world)
        tree = ClientTree(BASE)
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository, **{str(repository): tree.root}))
            await _read(call)
            [page] = tree.answer(_request(await _read(call), repository))
            # The top level does not hash to the root the report claims.
            bad = TreeReport(page.request_id, repository, h(0xBAD), page.nodes, last_page=True)
            await call.write(_report(bad))
            refused = await _read(call)
            await call.write(heartbeat(7))
            beat = await _read(call)
    assert refused.error.message == SYNC_MISMATCH
    assert h(0xBAD) not in refused.SerializeToString().decode("latin-1")
    assert beat.heartbeat.seq == 7


async def test_the_poll_asks_for_the_root_again() -> None:
    clock = ShiftedClock()
    async with stream_server(clock, merkle_poll_interval_s=30) as world:
        token, key_id, repository = await _ready(world)
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository))
            await _read(call)
            clock.offset = timedelta(seconds=31)
            await call.write(heartbeat(1))
            beat, poll = await _read(call), await _read(call)
    assert beat.heartbeat.seq == 1
    assert poll.merkle_sync_request.node_ids == []
    assert poll.merkle_sync_request.repository_id == str(repository)


async def test_a_rate_limited_page_keeps_the_sync() -> None:
    """A page refused by the rate limit is sent again a minute later, and the sync goes
    on where it was."""
    clock = ShiftedClock()
    async with stream_server(clock, rate_limit_per_minute=3) as world:
        token, key_id, repository = await _ready(world)
        tree = ClientTree(BASE)
        limited = 0
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository, **{str(repository): tree.root}))
            await _read(call)
            out = await _read(call)
            while out.WhichOneof("body") == "merkle_sync_request":
                [report] = tree.answer(_request(out, repository))  # one page per level
                await call.write(_report(report))
                out = await _read(call)
                while out.error.code == session_pb2.StreamError.CODE_RATE_LIMITED:
                    limited += 1
                    clock.offset += timedelta(minutes=1)
                    await call.write(_report(report))
                    out = await _read(call)
    assert limited > 0
    assert out.WhichOneof("body") == "merkle_delta"
