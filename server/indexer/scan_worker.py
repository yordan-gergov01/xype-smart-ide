"""The child process that runs the server's re-check (ADR-0019): `python -m
indexer.scan_worker <rules file>`, started and killed by `indexer.scan.ProcessScanner`.

Once its rules are loaded it writes `READY`. Then it reads frames from stdin (a 4-byte
big-endian length, then the chunk) and answers each with one byte. It never logs and
never writes anything else: stdout is the protocol and stderr goes nowhere.
Its environment holds no credentials.
"""

import sys
from pathlib import Path
from typing import BinaryIO

from common_lib.security import ScrubTimeoutError, SecretScrubber, load_rules

# A frame larger than this is not from the parent (it refuses chunks over 64 KiB).
MAX_FRAME_BYTES = 1024 * 1024
READY, CLEAN, SECRET_FOUND, UNSCANNABLE = b"r", b"c", b"s", b"u"


def verdict(scrubber: SecretScrubber, chunk: bytes) -> bytes:
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return UNSCANNABLE
    try:
        result = scrubber.scrub(text)
    except ScrubTimeoutError:
        return UNSCANNABLE
    return CLEAN if result.clean else SECRET_FOUND


def serve(scrubber: SecretScrubber, source: BinaryIO, sink: BinaryIO) -> None:
    """Answers frames until the parent closes stdin or sends a frame that is not one."""
    while len(header := source.read(4)) == 4:
        size = int.from_bytes(header, "big")
        if size > MAX_FRAME_BYTES:
            return
        chunk = source.read(size)
        if len(chunk) != size:
            return
        sink.write(verdict(scrubber, chunk))
        sink.flush()
        del chunk


def main() -> None:
    scrubber = SecretScrubber(load_rules(Path(sys.argv[1])))
    sys.stdout.buffer.write(READY)
    sys.stdout.buffer.flush()
    serve(scrubber, sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    main()
