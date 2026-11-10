"""Network guard: the test process connects only to this machine (rule 06, ADR-0012).

Installed for the whole session by tests/conftest.py, before any fixture runs. It
refuses, before any packet leaves:
- forward name lookups (`getaddrinfo`, `gethostbyname`, `gethostbyname_ex`) of a host
  that is not local, so a paid API host is never resolved;
- connects (`socket.connect`/`connect_ex`, the path of sync clients and of asyncio's
  selector loop) and addressed sends (`sendto`, `sendmsg`: UDP, TCP Fast Open) to an
  address that is not local. A name given at connect time is resolved and every result
  must be local;
- connects and addressed sends on Windows' proactor loop, which go through IOCP
  (`ConnectEx`, `WSASendTo`) and not through the `socket` methods.
Local means loopback, `localhost`, `*.localhost`, Unix sockets (the Docker API) and the
Docker host that testcontainers uses (`DOCKER_HOST` with `tcp://`, `TC_HOST`,
`TESTCONTAINERS_HOST_OVERRIDE`), resolved once at install. Every `*_proxy` variable is
removed for the session, so no client relays through a local proxy.

The error is not an `OSError`, so HTTP clients do not turn it into a retryable connect
error. Every refusal is recorded: a test that swallows the error still fails, and so
does the session (tests/conftest.py).

Limits, the guard covers this Python process only:
- containers started by tests have their own network (test_ollama_embedder.py), and a
  model server already running on this machine is reachable on loopback (run it with
  cloud models off, as compose does);
- native code that opens sockets without the `socket` module (a future grpcio), direct
  use of `_socket`, and subprocesses are not covered;
- reverse lookups (`gethostbyaddr`, `getnameinfo`) are not guarded, and a lookup of a
  `*.localhost` name may reach the system's DNS resolver (the name only);
- inside a container, testcontainers reaches the bridge gateway, which is refused
  unless `TC_HOST` names it; an `ssh://` `DOCKER_HOST` is refused.
"""

import ipaddress
import os
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

DOCKER_HOST_VARIABLES = ("TC_HOST", "TESTCONTAINERS_HOST_OVERRIDE")
# reason: Any - getaddrinfo's own signature, used to resolve the Docker host.
type Resolver = Callable[..., Any]


class OutboundConnectionError(RuntimeError):
    """A test tried to reach a host that is not this machine."""


_violations: list[str] = []
# reason: Any - the patched callables have different, partly private signatures.
_originals: dict[str, Callable[..., Any]] = {}
_saved_environment: dict[str, str] = {}
_allowed_names: set[str] = set()
_allowed_addresses: set[str] = set()


def is_local(host: object) -> bool:
    """Whether `host` names this machine: loopback, `localhost`, or the Docker host."""
    name = _normalise(host)
    if name is None:
        return False
    if name == "localhost" or name.endswith(".localhost") or name in _allowed_names:
        return True
    return _is_local_address(name)


def take_violations() -> list[str]:
    """The refusals recorded so far, clearing them."""
    taken = list(_violations)
    _violations.clear()
    return taken


def allow_docker_host(resolve: Resolver | None = None) -> None:
    """Allow the Docker host from the environment, by name and by its addresses."""
    resolve = resolve or _originals.get("getaddrinfo", socket.getaddrinfo)
    _allowed_names.clear()
    _allowed_addresses.clear()
    for name in _docker_host_names():
        if _is_ip(name):
            _allowed_addresses.add(name)
            continue
        _allowed_names.add(name)
        try:
            infos = resolve(name, None)
        except OSError:
            continue
        _allowed_addresses.update(str(info[4][0]) for info in infos)


def install() -> None:
    if _originals:
        return
    for variable in [v for v in os.environ if v.lower().endswith("_proxy")]:
        _saved_environment[variable] = os.environ.pop(variable)
    allow_docker_host(socket.getaddrinfo)
    for name, guarded in _MODULE_PATCHES.items():
        _originals[name] = getattr(socket, name)
        setattr(socket, name, guarded)
    for name, guarded in _METHOD_PATCHES.items():
        if hasattr(socket.socket, name):  # sendmsg does not exist on Windows
            _originals[name] = getattr(socket.socket, name)
            setattr(socket.socket, name, guarded)
    proactor = _iocp_proactor()
    if proactor is not None:
        _originals["proactor_connect"] = proactor.connect
        _originals["proactor_sendto"] = proactor.sendto
        proactor.connect = _guarded_proactor_connect
        proactor.sendto = _guarded_proactor_sendto


def uninstall() -> None:
    if not _originals:
        return
    for name in _MODULE_PATCHES:
        setattr(socket, name, _originals.pop(name))
    for name in _METHOD_PATCHES.keys() & _originals.keys():
        setattr(socket.socket, name, _originals.pop(name))
    proactor = _iocp_proactor()
    if proactor is not None and "proactor_connect" in _originals:
        proactor.connect = _originals.pop("proactor_connect")
        proactor.sendto = _originals.pop("proactor_sendto")
    os.environ.update(_saved_environment)
    _saved_environment.clear()


def _normalise(host: object) -> str | None:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return None
    return host.lower().rstrip(".").strip("[]").split("%")[0]


def _is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def _is_local_address(name: str) -> bool:
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback or str(address) in _allowed_addresses


def _docker_host_names() -> set[str]:
    names = {os.environ.get(variable, "") for variable in DOCKER_HOST_VARIABLES}
    docker_host = os.environ.get("DOCKER_HOST", "")
    if docker_host.startswith("tcp://"):
        names.add(urlsplit(docker_host).hostname or "")
    return {name for name in (_normalise(n) for n in names) if name}


def _refuse(host: object) -> None:
    message = f"outbound connection to {host!r} refused by the test network guard"
    _violations.append(message)
    raise OutboundConnectionError(message)


def _check_address(family: int, address: object) -> None:
    if family not in {socket.AF_INET, socket.AF_INET6} or not isinstance(address, tuple):
        return
    name = _normalise(address[0])
    if name is None or not is_local(name):
        _refuse(address[0])
    if name is not None and not _is_ip(name):
        # The C resolver behind connect() skips the patched lookups: check its answer.
        infos = _originals["getaddrinfo"](name, None, family)
        if not all(_is_local_address(str(info[4][0])) for info in infos):
            _refuse(address[0])


def _check_name(host: object) -> None:
    # None asks for the wildcard address (binding a server), which is local.
    if host is not None and not is_local(host):
        _refuse(host)


# reason: Any - mirrors socket.getaddrinfo's pass-through arguments and result.
def _guarded_getaddrinfo(host: object, *args: Any, **kwargs: Any) -> Any:
    _check_name(host)
    return _originals["getaddrinfo"](host, *args, **kwargs)


def _guarded_gethostbyname(host: str) -> str:
    _check_name(host)
    result: str = _originals["gethostbyname"](host)
    return result


def _guarded_gethostbyname_ex(host: str) -> tuple[str, list[str], list[str]]:
    _check_name(host)
    result: tuple[str, list[str], list[str]] = _originals["gethostbyname_ex"](host)
    return result


# reason: Any - socket addresses are tuples or str/bytes depending on the family.
def _guarded_connect(self: socket.socket, address: Any) -> None:
    _check_address(self.family, address)
    _originals["connect"](self, address)


# reason: Any - socket addresses are tuples or str/bytes depending on the family.
def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    _check_address(self.family, address)
    result: int = _originals["connect_ex"](self, address)
    return result


# reason: Any - sendto(data[, flags], address) passes its arguments through.
def _guarded_sendto(self: socket.socket, *args: Any) -> int:
    _check_address(self.family, args[-1])
    result: int = _originals["sendto"](self, *args)
    return result


# reason: Any - sendmsg(buffers[, ancdata[, flags[, address]]]) passes them through.
def _guarded_sendmsg(self: socket.socket, *args: Any) -> int:
    if len(args) >= 4:
        _check_address(self.family, args[3])
    result: int = _originals["sendmsg"](self, *args)
    return result


# reason: Any - IocpProactor is private and exists on Windows only.
def _guarded_proactor_connect(self: Any, conn: socket.socket, address: Any) -> Any:
    _check_address(conn.family, address)
    return _originals["proactor_connect"](self, conn, address)


# reason: Any - as above; sendto(conn, buf, flags=0, addr=None).
def _guarded_proactor_sendto(
    self: Any, conn: socket.socket, buf: Any, flags: int = 0, addr: Any = None
) -> Any:
    if addr is not None:
        _check_address(conn.family, addr)
    return _originals["proactor_sendto"](self, conn, buf, flags, addr)


def _iocp_proactor() -> Any:  # reason: Any - private Windows-only class
    try:
        from asyncio import windows_events  # noqa: PLC0415 - exists on Windows only
    except ImportError:
        return None
    return windows_events.IocpProactor  # type: ignore[attr-defined,unused-ignore]  # Windows only


# reason: Any - each guard keeps the signature of the function it replaces.
_MODULE_PATCHES: dict[str, Callable[..., Any]] = {
    "getaddrinfo": _guarded_getaddrinfo,
    "gethostbyname": _guarded_gethostbyname,
    "gethostbyname_ex": _guarded_gethostbyname_ex,
}
# reason: Any - as above, for socket.socket methods.
_METHOD_PATCHES: dict[str, Callable[..., Any]] = {
    "connect": _guarded_connect,
    "connect_ex": _guarded_connect_ex,
    "sendto": _guarded_sendto,
    "sendmsg": _guarded_sendmsg,
}
