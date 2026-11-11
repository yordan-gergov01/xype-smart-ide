"""A throwaway dev CA for the tests, issued by the same generator as the local stack."""

import atexit
import functools
import shutil
import tempfile
from pathlib import Path

from devtools.dev_ca import generate


@functools.cache
def certs_dir() -> Path:
    """deploy/certs-shaped directory: ca.crt plus one directory per TLS service."""
    path = Path(tempfile.mkdtemp(prefix="xype-test-ca-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    generate(path)
    return path


def ca_file() -> Path:
    return certs_dir() / "ca.crt"


@functools.cache
def other_ca_file() -> Path:
    """A CA that issued none of the servers' certificates."""
    path = Path(tempfile.mkdtemp(prefix="xype-other-ca-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    generate(path, services=())
    return path / "ca.crt"


def service_dir(service: str) -> Path:
    """Mounted read-only at /certs, as in deploy/docker-compose.yml."""
    return certs_dir() / service
