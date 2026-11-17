"""`/healthz`, `/readyz` and `/metrics` (rule 08) on a small HTTP server from the
standard library: the indexer needs no web framework. They carry no payload, so they are
HTTP on the internal networks only (ADR-0036, like the OTel collector's metrics)."""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

# A request line and its headers; anything longer is not one of ours.
_MAX_REQUEST_BYTES = 8192
_READ_TIMEOUT_S = 5.0


def _response(status: str, body: bytes, content_type: str = "text/plain") -> bytes:
    head = (
        f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode("ascii") + body


# Every scan thread stuck for this long: unhealthy, and the watchdog in `indexer.main`
# ends the process so the container restarts (until scans can be killed; ADR-0015).
STUCK_SCANS_S = 30.0


@dataclass
class Readiness:
    """Ready while consuming and while the last chunk did not find a store or the model
    server down (a retry clears it on the next success). Healthy unless every scan
    thread has been stuck for `STUCK_SCANS_S`."""

    consuming: bool = False
    dependencies_ok: bool = True
    scans_full_since: float | None = None  # monotonic seconds

    def ready(self) -> bool:
        return self.consuming and self.dependencies_ok

    def healthy(self, now: float | None = None) -> bool:
        since = self.scans_full_since
        return since is None or (now or time.monotonic()) - since < STUCK_SCANS_S


class HealthServer:
    def __init__(
        self,
        registry: CollectorRegistry,
        ready: Callable[[], bool],
        healthy: Callable[[], bool] = lambda: True,
    ) -> None:
        self._registry = registry
        self._ready = ready
        self._healthy = healthy

    async def start(self, address: str) -> asyncio.Server:
        host, _, port = address.rpartition(":")
        return await asyncio.start_server(self._serve, host.strip("[]"), int(port))

    def answer(self, path: str) -> bytes:
        if path == "/healthz":
            if self._healthy():
                return _response("200 OK", b"ok\n")
            return _response("503 Service Unavailable", b"stuck\n")
        if path == "/readyz":
            if self._ready():
                return _response("200 OK", b"ready\n")
            return _response("503 Service Unavailable", b"not ready\n")
        if path == "/metrics":
            return _response("200 OK", generate_latest(self._registry), CONTENT_TYPE_LATEST)
        return _response("404 Not Found", b"not found\n")

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), _READ_TIMEOUT_S)
            parts = line[:_MAX_REQUEST_BYTES].decode("ascii", "replace").split()
            path = parts[1] if len(parts) >= 2 and parts[0] == "GET" else ""
            writer.write(self.answer(path))
            await writer.drain()
        except (TimeoutError, OSError):
            pass  # a client that hangs up or stays silent gets nothing
        finally:
            writer.close()
