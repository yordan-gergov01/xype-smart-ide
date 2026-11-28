"""The extension's sync worker driven without VS Code (`clients/xype-ai/scripts/e2e-client.mjs`):
a child process that reads `changed` and `stop` on stdin and writes the engine's messages
(ids, counts, fixed reasons) as JSON lines."""

import json
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Self

REPO = Path(__file__).resolve().parents[3]
CLIENT = REPO / "clients" / "xype-ai"
SCRIPT = CLIENT / "scripts" / "e2e-client.mjs"
WORKER = CLIENT / "dist" / "syncWorker.cjs"
CA = REPO / "deploy" / "certs" / "ca.crt"


class ClientError(Exception):
    """The client stopped or did not send the expected message in time."""


type Message = dict[str, Any]  # reason: free-form engine messages, checked by the test


class SyncClient:
    def __init__(self, *, server_url: str, credentials: Path, repository: str, root: Path) -> None:
        node = shutil.which("node")
        if node is None or not WORKER.exists():
            raise ClientError("needs node and the built extension (npm run build)")
        args = [node, str(SCRIPT), "--server", server_url, "--ca", str(CA)]
        args += ["--credentials", str(credentials), "--repository", repository]
        args += ["--root", str(root)]
        self._process = subprocess.Popen(  # noqa: S603 - fixed script, test arguments
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        self._messages: queue.Queue[Message | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.send("stop")
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()

    def send(self, command: str) -> None:
        stdin = self._process.stdin
        if stdin is not None and self._process.poll() is None:
            stdin.write(command + "\n")
            stdin.flush()

    def wait_for(self, kind: str, timeout_s: float) -> Message:
        """The next message of this kind; a `stopped` or `worker_error` first fails."""
        deadline = time.monotonic() + timeout_s
        while (left := deadline - time.monotonic()) > 0:
            try:
                message = self._messages.get(timeout=left)
            except queue.Empty:
                break
            if message is None or message["kind"] in {"stopped", "worker_error"}:
                raise ClientError(f"the client ended while waiting for {kind}: {message}")
            if message["kind"] == kind:
                return message
        raise ClientError(f"no {kind} within {timeout_s} s")

    def _read(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            self._messages.put(json.loads(line))
        self._messages.put(None)
