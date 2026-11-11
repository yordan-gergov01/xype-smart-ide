"""`client_context` really verifies: the chain, the host name and the CA's name limits.

A TLS server on loopback serves dev CA certificates. This is the one unit-tier exception
to "no I/O": a handshake cannot be faked, and loopback is in-process, fast and allowed by
the network guard.
"""

import asyncio
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from pydantic import ValidationError
from support.tls import ca_file, certs_dir, other_ca_file, service_dir

from common_lib.settings import PostgresSettings
from common_lib.tls import MINIMUM_TLS, client_context
from devtools.dev_ca import create_ca, issue_leaf


def _server_context(cert: Path, key: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


@asynccontextmanager
async def serving(context: ssl.SSLContext) -> AsyncIterator[int]:
    async def answer(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"ok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer, "127.0.0.1", 0, ssl=context)
    async with server:
        yield server.sockets[0].getsockname()[1]


async def handshake(port: int, ca: Path, server_hostname: str) -> bytes:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            "127.0.0.1", port, ssl=client_context(ca), server_hostname=server_hostname
        ),
        timeout=5,
    )
    data = await reader.read()
    writer.close()
    return data


def redis_server() -> ssl.SSLContext:
    return _server_context(service_dir("redis") / "tls.crt", service_dir("redis") / "tls.key")


async def test_accepts_the_server_the_ca_issued() -> None:
    async with serving(redis_server()) as port:
        assert await handshake(port, ca_file(), "redis") == b"ok"
        assert await handshake(port, ca_file(), "127.0.0.1") == b"ok"


async def test_refuses_another_host_name() -> None:
    async with serving(redis_server()) as port:
        with pytest.raises(ssl.SSLCertVerificationError, match="match"):
            await handshake(port, ca_file(), "postgres")


async def test_refuses_a_server_from_another_ca() -> None:
    async with serving(redis_server()) as port:
        with pytest.raises(ssl.SSLCertVerificationError):
            await handshake(port, other_ca_file(), "redis")


async def test_refuses_a_name_outside_the_ca_constraints(tmp_path: Path) -> None:
    """Even a leaked CA key cannot impersonate a host outside the stack."""
    ca = create_ca(("redis",), datetime.now(UTC))
    cert, key = issue_leaf(ca, "example.com", datetime.now(UTC))
    (tmp_path / "ca.crt").write_bytes(ca.cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "tls.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "tls.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    async with serving(_server_context(tmp_path / "tls.crt", tmp_path / "tls.key")) as port:
        with pytest.raises(ssl.SSLCertVerificationError, match="permitted subtree violation"):
            await handshake(port, tmp_path / "ca.crt", "example.com")


def test_requires_verification_and_tls_1_2() -> None:
    context = client_context(ca_file())
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.minimum_version is MINIMUM_TLS


def test_a_missing_ca_file_fails_at_start() -> None:
    with pytest.raises(FileNotFoundError):
        client_context(certs_dir() / "no-such-ca.crt")


@pytest.mark.parametrize("value", [None, "no-such-ca.crt"], ids=["unset", "missing-file"])
def test_settings_refuse_to_start_without_a_ca(value: str | None) -> None:
    """No plaintext fallback: a service without a CA file does not start."""
    overrides = {"tls_ca_file": value} if value else {}
    values = {"db_host": "db", "db_name": "x", "db_user": "u", "db_password": "p", **overrides}
    with pytest.raises(ValidationError, match="tls_ca_file"):
        PostgresSettings.model_validate(values)
