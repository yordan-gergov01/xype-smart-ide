"""The server's re-check in killable child processes (ADR-0019, slice 8c1): the same
verdicts as in process; a scan past its time is killed; a child that died while idle
or failed to start is replaced with the next chunk; a cancelled scan or a closed
scanner leaves no child behind; the child gets no credentials."""

import asyncio
import io
import os
import signal
from collections.abc import AsyncIterator
from typing import Any

import pytest
from support.contracts import scanner as contract
from support.scanner import InlineScanner

from common_lib.errors import DependencyUnavailableError
from common_lib.security import SecretScrubber, load_rules
from indexer.scan import ProcessScanner
from indexer.scan_worker import MAX_FRAME_BYTES, serve

# About 1 MiB that takes the scrubber some 0.4 s (linear: the ReDoS check's worst shape).
SLOW = (b"a.b@c" + b"A" * MAX_FRAME_BYTES)[:MAX_FRAME_BYTES]


@pytest.fixture
async def scanner() -> AsyncIterator[ProcessScanner]:
    started = ProcessScanner(contract.RULES_FILE, workers=1, timeout_s=0.5)
    await started.start()
    yield started
    await started.close()


async def test_the_fake_meets_the_contract() -> None:
    await contract.check_verdicts(InlineScanner(SecretScrubber(load_rules(contract.RULES_FILE))))


async def test_the_scan_processes_meet_the_contract(scanner: ProcessScanner) -> None:
    await contract.check_verdicts(scanner)


async def test_a_scan_past_its_time_is_killed_and_the_next_one_runs() -> None:
    scanner = ProcessScanner(contract.RULES_FILE, workers=1, timeout_s=0.1)
    await scanner.start()
    try:
        busy = scanner.pids()
        assert await scanner.scan(SLOW) == "unscannable"
        assert not scanner.pids() & busy  # killed, not left scanning
        assert await scanner.scan(contract.CLEAN) == "clean"
    finally:
        await scanner.close()


async def test_a_child_that_died_while_idle_does_not_settle_the_next_chunk(
    scanner: ProcessScanner,
) -> None:
    [pid] = scanner.pids()
    os.kill(pid, signal.SIGTERM)  # from outside, between two scans
    await asyncio.sleep(0.3)
    assert await scanner.scan(contract.CLEAN) == "clean"
    assert pid not in scanner.pids()


async def test_a_failed_start_is_retried_with_the_next_chunk(
    scanner: ProcessScanner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A start that fails must not shrink the pool for good, nor make a scan hang."""
    [pid] = scanner.pids()
    real = asyncio.create_subprocess_exec

    # reason: Any - the arguments of `create_subprocess_exec`, never used.
    async def fail(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        raise OSError("no more processes")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    os.kill(pid, signal.SIGTERM)
    await asyncio.sleep(0.3)
    for _ in range(2):
        with pytest.raises(DependencyUnavailableError):
            await asyncio.wait_for(scanner.scan(contract.CLEAN), timeout=5)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", real)
    assert await scanner.scan(contract.CLEAN) == "clean"


async def test_a_cancelled_scan_leaves_no_busy_child(scanner: ProcessScanner) -> None:
    task = asyncio.create_task(scanner.scan(SLOW))
    await asyncio.sleep(0.1)
    busy = scanner.pids()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not scanner.pids() & busy
    assert await asyncio.wait_for(scanner.scan(contract.CLEAN), timeout=5) == "clean"


async def test_a_closed_scanner_starts_nothing(scanner: ProcessScanner) -> None:
    await scanner.close()
    assert scanner.pids() == frozenset()
    with pytest.raises(DependencyUnavailableError):
        await scanner.scan(contract.CLEAN)
    assert scanner.pids() == frozenset()


async def test_the_child_gets_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEK_CURRENT", "not-for-the-child")
    monkeypatch.setenv("PYTHONPATH", "elsewhere")
    seen: dict[str, str] = {}
    real = asyncio.create_subprocess_exec

    # reason: Any - the keyword arguments of `create_subprocess_exec`, passed through.
    async def spawn(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        seen.update(kwargs["env"])
        return await real(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    scanner = ProcessScanner(contract.RULES_FILE, workers=1, timeout_s=1.0)
    await scanner.start()
    await scanner.close()
    assert set(seen) <= {"PATH", "SYSTEMROOT", "LANG", "LC_ALL"}


def test_the_child_stops_on_a_frame_that_is_not_one() -> None:
    scrubber = SecretScrubber(load_rules(contract.RULES_FILE))
    frame = len(contract.CLEAN).to_bytes(4, "big") + contract.CLEAN
    for stream, answers in [
        (frame + frame, b"cc"),
        (frame + (MAX_FRAME_BYTES + 1).to_bytes(4, "big") + frame, b"c"),  # too large
        (frame + (100).to_bytes(4, "big") + b"short", b"c"),  # cut off
        (frame + b"\x00\x00", b"c"),  # half a header
    ]:
        sink = io.BytesIO()
        serve(scrubber, io.BytesIO(stream), sink)
        assert sink.getvalue() == answers
