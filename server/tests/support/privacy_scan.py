"""The end-to-end privacy scan (Phase 2 slice 12, rule 07): after the sample repository was
indexed, look for its canaries in every place the server keeps or emits data.

Canaries come in two kinds:
- words: the sample repository's distinctive `zqv` words (identifiers, comments and file
  names, seven characters or more, so random ciphertext cannot match one by chance) and
  any value the test plants, such as a secret. Each is searched as is, lowercased, in hex
  and in base64;
- hashes: the unkeyed SHA-256 of every path, every file and every line range of every
  file (any chunk the client could cut). Only keyed HMACs may reach the server, so any
  64-digit hex token in the data that is one of these is a leak.

A finding names the place and the canary, never the surrounding bytes."""

import base64
import hashlib
import re
import shlex
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from support.e2e_stack import PG_AUTH, PG_TARGET, SERVICES, Stack

CANARY = re.compile(rb"[A-Za-z0-9_-]*zqv[A-Za-z0-9_-]*", re.IGNORECASE)
MIN_CANARY = 7
HEX_TOKEN = re.compile(rb"[0-9a-f]{64}")
# Files on disk keep bytes that no query returns any more: write-ahead logs, queue
# segments, Redis append-only files and snapshots, and the services' temporary files.
DISK = {
    "postgres": "/var/lib/postgresql/data",
    "rabbitmq": "/var/lib/rabbitmq",
    "redis": "/data",
    "keystore": "/data",
    "backend": "/tmp",  # noqa: S108 - a path inside the container
    "indexer": "/tmp",  # noqa: S108 - a path inside the container
}
REDIS_SERVICES = ("redis", "keystore")
REDIS_CLI = "redis-cli --tls --cacert /certs/ca.crt -h 127.0.0.1 --no-auth-warning"
REDIS_DB = re.compile(rb"^db(\d+):", re.MULTILINE)
# How each Redis type is read in full.
REDIS_READ = {
    "string": "GET {key}",
    "hash": "HGETALL {key}",
    "list": "LRANGE {key} 0 -1",
    "set": "SMEMBERS {key}",
    "zset": "ZRANGE {key} 0 -1 WITHSCORES",
    "stream": "XRANGE {key} - +",
}


@dataclass(frozen=True, slots=True)
class Canaries:
    words: frozenset[bytes]
    hashes: frozenset[bytes]

    def __or__(self, words: Iterable[str]) -> "Canaries":
        return Canaries(self.words | {word.encode() for word in words}, self.hashes)


@dataclass(frozen=True, slots=True)
class Finding:
    place: str
    canary: str


def canaries(root: Path, extra: Iterable[str] = ()) -> Canaries:
    """The repository's words and unkeyed hashes (see the module docstring)."""
    words: set[bytes] = {value.encode() for value in extra}
    hashes: set[bytes] = set()
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix().encode()
        content = path.read_bytes() if path.is_file() else b""
        words.update(CANARY.findall(name + b"\n" + content))
        hashes.add(_sha256(name))
        hashes.update(_sha256(piece) for piece in _pieces(content))
    return Canaries(frozenset(w for w in words if len(w) >= MIN_CANARY), frozenset(hashes))


def scan(stack: Stack, found: Canaries) -> list[Finding]:
    """Findings in Postgres, both Redis stores (every database), RabbitMQ, the files on
    disk listed in `DISK`, every service's log and the collector's spans (its log)."""
    findings: list[Finding] = []
    for place, data in places(stack):
        lowered = data.lower()
        findings += [
            Finding(place, word.decode())
            for word in sorted(found.words)
            if _has(data, lowered, word)
        ]
        leaked = set(HEX_TOKEN.findall(lowered)) & found.hashes
        findings += [Finding(place, f"sha256:{digest.decode()[:12]}") for digest in sorted(leaked)]
    return findings


def places(stack: Stack) -> Iterator[tuple[str, bytes]]:
    yield "postgres dump", stack.exec("postgres", f"{PG_AUTH} pg_dump {PG_TARGET} --data-only")
    for service in REDIS_SERVICES:
        yield f"{service} keys", _redis_dump(stack, service)
    for service, path in DISK.items():
        yield f"{service} disk", _disk(stack, service, path)
    for service in SERVICES:
        yield f"{service} log", stack.logs(service)


CONTROL_KEY = "xype-e2e:control"
CONTROL_VALUE = "e2e-positive-control"


def plant_controls(stack: Stack) -> None:
    """A short-lived key in each Redis store, so the positive control can show that the
    scan reads them (both may otherwise hold nothing the test knows)."""
    for service in REDIS_SERVICES:
        stack.exec(service, f"{REDIS_CLI} SET {CONTROL_KEY} {CONTROL_VALUE} EX 600")


def queued_messages(stack: Stack) -> dict[str, int]:
    """Queues that still hold messages (the dead-letter queues included)."""
    out = stack.exec("rabbitmq", "rabbitmqctl -q list_queues -p xype name messages")
    rows = (line.split("\t") for line in out.decode().splitlines() if "\t" in line)
    return {name: int(count) for name, count in rows if count.isdigit() and int(count) > 0}


def _has(data: bytes, lowered: bytes, word: bytes) -> bool:
    # bytea columns are dumped as hex; payloads travel as base64 inside JSON.
    return (
        word.lower() in lowered
        or word.hex().encode() in lowered
        or any(form in data for form in _base64_forms(word))
    )


def _base64_forms(word: bytes) -> list[bytes]:
    """The part of `word`'s base64 that does not depend on the bytes around it, for each
    of the three alignments it can start at (standard and URL-safe alphabets)."""
    forms = []
    for offset in range(3):
        encoded = base64.b64encode(b"\0" * offset + word)
        stable = encoded[4 if offset else 0 : len(encoded) - 4]
        forms += [stable, stable.replace(b"+", b"-").replace(b"/", b"_")]
    return [form for form in forms if len(form) >= MIN_CANARY]


def _pieces(content: bytes) -> Iterator[bytes]:
    """The whole file and every run of whole lines, with and without the last newline."""
    if not content:
        return
    yield content
    lines = content.split(b"\n")
    for start in range(len(lines)):
        for end in range(start + 1, len(lines) + 1):
            piece = b"\n".join(lines[start:end])
            yield piece
            yield piece + b"\n"


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).hexdigest().encode()


def _redis_dump(stack: Stack, service: str) -> bytes:
    """Every key name and value, in every database that holds keys."""
    databases = REDIS_DB.findall(stack.exec(service, f"{REDIS_CLI} INFO keyspace"))
    out: list[bytes] = []
    for db in (number.decode() for number in databases):
        keys = stack.exec(service, f"{REDIS_CLI} -n {db} --scan").split()
        out += keys
        for key in keys:
            name = shlex.quote(key.decode())
            kind = stack.exec(service, f"{REDIS_CLI} -n {db} TYPE {name}").strip().decode()
            command = REDIS_READ[kind].format(key=name)
            out.append(stack.exec(service, f"{REDIS_CLI} -n {db} --raw {command}"))
    return b"\n".join(out)


def _disk(stack: Stack, service: str, path: str) -> bytes:
    """Every file under `path`, concatenated (read inside the container)."""
    return stack.exec(service, f"find {path} -type f -exec cat {{}} + 2>/dev/null || true")
