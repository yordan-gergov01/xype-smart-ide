"""The server's re-check in child processes that are killed when a scan runs too long
(ADR-0019, slice 8c1). A thread cannot be stopped, so a pathological input could keep
one busy for good; a process can.

Each child (`indexer.scan_worker`) scans one chunk at a time. The chunk travels over its
stdin pipe, in memory only; it gets a small environment without the worker's
credentials and has no logging. A scan past its time, or a child that dies, settles the
chunk as `unscannable` (fail closed), and the child is replaced.
"""

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import Literal, Protocol

import structlog

from common_lib.errors import DependencyUnavailableError, XypeError

type ScanVerdict = Literal["clean", "secret_found", "unscannable"]

_VERDICTS: dict[bytes, ScanVerdict] = {
    b"c": "clean",
    b"s": "secret_found",
    b"u": "unscannable",
}
# Only what the interpreter needs to start; never the KEK or a password.
_ENVIRONMENT = ("PATH", "SYSTEMROOT", "LANG", "LC_ALL")
_SERVER_DIR = Path(__file__).resolve().parents[1]
# A child's start is not part of a scan's time: it must load its rules within this.
_START_TIMEOUT_S = 30.0


class ScanInterruptedError(XypeError):
    """The check did not finish: past its wall-clock time or the child died. That may
    be the host's load as much as the chunk, so the caller retries before it settles
    the chunk as `unscannable` (slice 12a)."""


class Scanner(Protocol):
    async def scan(self, chunk: bytes) -> ScanVerdict:
        """Whether a chunk is clean; `unscannable` when the check itself refused it (not
        UTF-8, past the scrubber's CPU budget, or a reply that is not one). Raises
        `ScanInterruptedError` when the check did not finish."""
        ...


logger = structlog.get_logger(__name__)
# Why a chunk could not be checked, as a fixed word (never the exception's message).
_CAUSES: tuple[tuple[type[BaseException], str], ...] = (
    (TimeoutError, "timeout"),
    (asyncio.IncompleteReadError, "child_died"),
    (ConnectionError, "child_died"),
    (KeyError, "bad_reply"),
)


def _cause(exc: BaseException) -> str:
    return next((cause for kind, cause in _CAUSES if isinstance(exc, kind)), "other")


class _Child:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        stdin: asyncio.StreamWriter,
        stdout: asyncio.StreamReader,
    ) -> None:
        self.process = process
        self._stdin = stdin
        self._stdout = stdout

    @classmethod
    async def start(cls, rules_file: Path) -> "_Child":
        environment = {k: os.environ[k] for k in _ENVIRONMENT if k in os.environ}
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-E",  # no PYTHON* variables from the parent
                "-s",  # no user site directory
                "-B",  # no bytecode written (the file system is read-only)
                "-m",
                "indexer.scan_worker",
                str(rules_file),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=_SERVER_DIR,
                env=environment,
            )
        except OSError:
            raise DependencyUnavailableError("the scan process did not start") from None
        if process.stdin is None or process.stdout is None:  # both are pipes above
            raise DependencyUnavailableError("the scan process has no pipes")
        child = cls(process, process.stdin, process.stdout)
        try:
            ready = await asyncio.wait_for(child._stdout.readexactly(1), _START_TIMEOUT_S)
        except (TimeoutError, asyncio.IncompleteReadError):
            ready = b""
        except BaseException:
            await child.kill()  # cancelled while starting: no process is left behind
            raise
        if ready != b"r":
            await child.kill()
            raise DependencyUnavailableError("the scan process did not start")
        return child

    async def ask(self, chunk: bytes) -> ScanVerdict:
        self._stdin.write(len(chunk).to_bytes(4, "big"))
        self._stdin.write(chunk)
        await self._stdin.drain()
        return _VERDICTS[await self._stdout.readexactly(1)]

    async def kill(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self.process.kill()
        await self.process.wait()


class ProcessScanner:
    """At most `workers` children. A child that is killed or dies is not replaced at
    once: the next scan starts one when none is free, so a failed start is retried
    with the next chunk (and that chunk is retried by the bus) instead of shrinking
    the pool for good. A scan waits for a free child, then raises
    `DependencyUnavailableError` (a retry)."""

    def __init__(self, rules_file: Path, *, workers: int, timeout_s: float) -> None:
        self._rules_file = rules_file
        self._workers = workers
        self._timeout_s = timeout_s
        self._idle: asyncio.Queue[_Child] = asyncio.Queue()
        self._children: set[_Child] = set()
        self._starting = 0
        self._closed = False

    async def start(self) -> None:
        for _ in range(self._workers):
            await self._spawn()

    def pids(self) -> frozenset[int]:
        """The live children's process ids."""
        return frozenset(child.process.pid for child in self._children)

    async def scan(self, chunk: bytes) -> ScanVerdict:
        child = await self._checkout()
        try:
            verdict = await asyncio.wait_for(child.ask(chunk), self._timeout_s)
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError, KeyError) as exc:
            # Past its time, dead or talking nonsense: this chunk cannot be checked.
            await self._discard(child)
            cause = _cause(exc)
            logger.warning("indexer.scan.unscannable", cause=cause)
            if cause == "bad_reply":
                return "unscannable"
            raise ScanInterruptedError(cause) from None
        except BaseException:
            # Cancelled mid-scan: the child's state is unknown. No replacement now; the
            # next scan starts one if it needs it.
            await self._discard(child)
            raise
        self._idle.put_nowait(child)
        if verdict == "unscannable":
            # The child itself could not check it: not UTF-8, or past the scrub budget.
            logger.warning("indexer.scan.unscannable", cause="child_verdict")
        return verdict

    async def close(self) -> None:
        self._closed = True
        while not self._idle.empty():
            self._idle.get_nowait()
        for child in list(self._children):
            await self._discard(child)

    async def _checkout(self) -> _Child:
        if self._closed:
            raise DependencyUnavailableError("the scanner is closed")
        if self._idle.empty() and len(self._children) + self._starting < self._workers:
            await self._spawn()
        while True:
            try:
                # Every child is busy for at most one scan, or starting.
                wait_s = self._timeout_s + _START_TIMEOUT_S
                child = await asyncio.wait_for(self._idle.get(), wait_s)
            except TimeoutError:
                raise DependencyUnavailableError("no scan process is free") from None
            if child.process.returncode is None:
                return child
            # Died while idle (killed from outside): not this chunk's fault.
            await self._discard(child)
            if self._idle.empty() and len(self._children) + self._starting < self._workers:
                await self._spawn()

    async def _spawn(self) -> None:
        self._starting += 1
        try:
            child = await _Child.start(self._rules_file)
        finally:
            self._starting -= 1
        if self._closed:
            await child.kill()
            raise DependencyUnavailableError("the scanner is closed")
        self._children.add(child)
        self._idle.put_nowait(child)

    async def _discard(self, child: _Child) -> None:
        self._children.discard(child)
        await child.kill()
