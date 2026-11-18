"""An in-process `Scanner` for unit tests: the same verdicts as the scan processes,
without a child process (the real one runs the same contract in `test_scan.py`)."""

from common_lib.security import ScrubTimeoutError, SecretScrubber
from indexer.scan import ScanVerdict


class InlineScanner:
    def __init__(self, scrubber: SecretScrubber) -> None:
        self.scrubber = scrubber

    async def scan(self, chunk: bytes) -> ScanVerdict:
        try:
            result = self.scrubber.scrub(chunk.decode("utf-8"))
        except (UnicodeDecodeError, ScrubTimeoutError):
            return "unscannable"
        return "clean" if result.clean else "secret_found"
