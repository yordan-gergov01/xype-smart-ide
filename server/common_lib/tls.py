"""The TLS client policy (ADR-0036): the configured CA only, host-name check, TLS 1.2+.

asyncpg and aio-pika take this context; redis-py builds its own from the same settings
(`backend.core.connections`). Nothing connects in plaintext.
"""

import ssl
from pathlib import Path

MINIMUM_TLS = ssl.TLSVersion.TLSv1_2


def client_context(ca_file: Path) -> ssl.SSLContext:
    """Verify the server's chain against `ca_file` only, and its host name."""
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ca_file))
    context.minimum_version = MINIMUM_TLS
    return context
