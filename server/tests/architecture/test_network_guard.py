"""The test network guard refuses every host but this machine (ADR-0012)."""

import asyncio
import contextlib
import os
import socket
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from support import network_guard
from support.network_guard import OutboundConnectionError

PAID_HOSTS_FILE = Path(__file__).resolve().parents[3] / "tools" / "paid_api_hosts.txt"
# Reserved for documentation (RFC 5737) and never resolvable (RFC 6761): a broken guard
# still reaches nobody.
DOC_ADDRESS = "192.0.2.1"
INVALID_NAME = "xype-guard.invalid"


def paid_hosts() -> list[str]:
    """Parsed like tools/check_paid_api.py `load_hosts()` (tests cannot import tools/)."""
    lines = PAID_HOSTS_FILE.read_text(encoding="utf-8").splitlines()
    entries = [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    return sorted({entry.split("/")[0] for entry in entries})


@pytest.fixture
def listener() -> Iterator[int]:
    server = socket.create_server(("127.0.0.1", 0))
    try:
        yield server.getsockname()[1]
    finally:
        server.close()


def assert_refused(host: str) -> None:
    refused = network_guard.take_violations()
    assert refused, "the guard recorded nothing"
    assert all(host in message for message in refused)


@pytest.mark.parametrize("host", paid_hosts())
def test_paid_api_hosts_are_never_resolved(host: str) -> None:
    with pytest.raises(OutboundConnectionError):
        httpx.get(f"https://{host}/", timeout=1)
    assert_refused(host)


def test_a_socket_connect_to_a_remote_address_is_refused() -> None:
    with socket.socket() as sock, pytest.raises(OutboundConnectionError):
        sock.connect((DOC_ADDRESS, 80))
    assert_refused(DOC_ADDRESS)


def test_addressed_udp_sends_are_refused() -> None:
    with socket.socket(type=socket.SOCK_DGRAM) as sock, pytest.raises(OutboundConnectionError):
        sock.sendto(b"x", (DOC_ADDRESS, 53))
    assert_refused(DOC_ADDRESS)


@pytest.mark.parametrize("lookup", [socket.gethostbyname, socket.gethostbyname_ex])
def test_legacy_lookups_are_refused(lookup: Callable[[str], object]) -> None:
    with pytest.raises(OutboundConnectionError):
        lookup(INVALID_NAME)
    assert_refused(INVALID_NAME)


@pytest.mark.skipif(not hasattr(socket.socket, "sendmsg"), reason="no sendmsg on Windows")
def test_addressed_sendmsg_is_refused() -> None:
    with socket.socket(type=socket.SOCK_DGRAM) as sock, pytest.raises(OutboundConnectionError):
        sock.sendmsg([b"x"], [], 0, (DOC_ADDRESS, 53))  # type: ignore[attr-defined,unused-ignore]  # Unix only
    assert_refused(DOC_ADDRESS)


async def test_asyncio_datagrams_to_an_address_are_refused() -> None:
    """An unconnected datagram endpoint sends through the loop (WSASendTo on Windows)."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
    )
    try:
        # Selector loops raise here; the proactor raises later, inside the loop.
        with contextlib.suppress(OutboundConnectionError):
            transport.sendto(b"x", (DOC_ADDRESS, 53))
        await asyncio.sleep(0.05)
    finally:
        transport.close()
    assert_refused(DOC_ADDRESS)


def test_proxy_variables_are_removed_for_the_session() -> None:
    assert not [v for v in os.environ if v.lower().endswith("_proxy")]


def test_connect_ex_is_refused_too() -> None:
    with socket.socket() as sock, pytest.raises(OutboundConnectionError):
        sock.connect_ex((DOC_ADDRESS, 80))
    assert_refused(DOC_ADDRESS)


def is_refusal(error: BaseException) -> bool:
    """anyio's happy-eyeballs connect wraps the refusal in an exception group."""
    if isinstance(error, BaseExceptionGroup):
        return all(is_refusal(inner) for inner in error.exceptions)
    return isinstance(error, OutboundConnectionError)


@pytest.mark.parametrize("url", [f"http://{DOC_ADDRESS}/", f"http://{INVALID_NAME}/"])
async def test_async_clients_are_refused_by_address_and_by_name(url: str) -> None:
    async with httpx.AsyncClient(timeout=1) as client:
        with pytest.raises(BaseException) as raised:  # noqa: PT011 - narrowed below
            await client.get(url)
    assert is_refusal(raised.value)
    assert_refused(DOC_ADDRESS if DOC_ADDRESS in url else INVALID_NAME)


async def test_an_asyncio_connection_to_an_address_is_refused() -> None:
    """The event loop's own connect path (IOCP on Windows, selectors elsewhere)."""
    with pytest.raises(OutboundConnectionError):
        await asyncio.open_connection(DOC_ADDRESS, 80)
    assert_refused(DOC_ADDRESS)


def test_loopback_is_allowed(listener: int) -> None:
    with socket.create_connection(("127.0.0.1", listener), timeout=1):
        pass
    with socket.create_connection(("localhost", listener), timeout=1):
        pass


async def test_asyncio_loopback_is_allowed(listener: int) -> None:
    _, writer = await asyncio.open_connection("127.0.0.1", listener)
    writer.close()
    await writer.wait_closed()


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("localhost", True),
        ("LOCALHOST.", True),
        ("db.localhost", True),
        ("127.0.0.2", True),
        ("::1", True),
        ("[::1]", True),
        (b"127.0.0.1", True),
        ("0.0.0.0", False),  # noqa: S104 - checked, not bound
        ("10.0.0.1", False),
        ("localhost.example.com", False),
        (None, False),
    ],
)
def test_is_local(host: object, local: bool) -> None:
    assert network_guard.is_local(host) is local


@pytest.fixture
def docker_host_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    for variable in ("DOCKER_HOST", "TC_HOST", "TESTCONTAINERS_HOST_OVERRIDE"):
        monkeypatch.delenv(variable, raising=False)
    yield monkeypatch
    monkeypatch.undo()
    network_guard.allow_docker_host()


def resolves_to(address: str) -> Callable[..., object]:
    def resolve(name: str, port: object, *args: object) -> object:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    return resolve


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("DOCKER_HOST", "tcp://docker-vm:2375"),
        ("TC_HOST", "docker-vm"),
        ("TESTCONTAINERS_HOST_OVERRIDE", "docker-vm"),
    ],
)
def test_the_docker_host_is_local_by_name_and_address(
    docker_host_env: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    docker_host_env.setenv(variable, value)
    network_guard.allow_docker_host(resolves_to("198.51.100.7"))
    assert network_guard.is_local("docker-vm")
    assert network_guard.is_local("198.51.100.7")


def test_a_connect_by_an_allowed_name_checks_where_it_resolves(
    docker_host_env: pytest.MonkeyPatch,
) -> None:
    """connect() resolves names in C, past the patched lookup: the guard resolves first."""
    docker_host_env.setenv("TC_HOST", "docker-vm")
    network_guard.allow_docker_host(resolves_to("198.51.100.7"))
    docker_host_env.setitem(
        network_guard._originals,
        "getaddrinfo",
        resolves_to("198.51.100.8"),
    )
    with socket.socket() as sock, pytest.raises(OutboundConnectionError):
        sock.connect(("docker-vm", 1))
    assert_refused("docker-vm")


def test_a_unix_docker_host_allows_nothing_remote(docker_host_env: pytest.MonkeyPatch) -> None:
    docker_host_env.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    network_guard.allow_docker_host(resolves_to("198.51.100.7"))
    assert not network_guard.is_local("docker-vm")
    assert not network_guard.is_local("198.51.100.7")
