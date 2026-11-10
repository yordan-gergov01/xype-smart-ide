"""`SecretBytes`: plaintext code held in memory only, wiped as soon as it is used.

Honest limits (rule 07). Wiping zeroes this buffer only; it cannot reach:
- the caller's original `bytes` (for example the decrypted payload), which callers
  drop right after wrapping;
- every `bytes` returned by `reveal()` and every `str` or JSON body built from it to
  talk to a model server, which are dropped right after the call;
- frame locals that a traceback keeps alive while an exception lives (the logging
  pipeline renders tracebacks without locals, common_lib.logging_config);
- copies the garbage collector has not reclaimed yet.
Queries reach the embedder as `str` and are never wiped. Embedding-only storage is
not a cryptographic guarantee either. Pickling and copying are refused, and a buffer
is wiped when it is garbage-collected as a last resort.
"""

from typing import NoReturn

from common_lib.errors import XypeError


class SecretWipedError(XypeError):
    """The content of a `SecretBytes` was used after it was wiped."""


class SecretBytes:
    """A mutable byte buffer that never shows its content and can be zeroed."""

    __slots__ = ("_buffer", "_wiped")

    def __init__(self, data: bytes | bytearray) -> None:
        self._buffer = bytearray(data)
        self._wiped = False

    def __len__(self) -> int:
        return len(self._buffer)

    def __repr__(self) -> str:
        state = "wiped" if self._wiped else f"len={len(self._buffer)}"
        return f"<SecretBytes {state}>"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("SecretBytes cannot be pickled")

    def __copy__(self) -> NoReturn:
        raise TypeError("SecretBytes cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("SecretBytes cannot be copied")

    def __del__(self) -> None:
        self.wipe()

    @property
    def wiped(self) -> bool:
        return self._wiped

    def is_zeroed(self) -> bool:
        """Whether every byte of the buffer is zero (true after `wipe`)."""
        return not any(self._buffer)

    def reveal(self) -> bytes:
        """The content, for the one call that needs it."""
        if self._wiped:
            raise SecretWipedError("SecretBytes was wiped")
        return bytes(self._buffer)

    def wipe(self) -> None:
        """Zero the buffer in place; idempotent."""
        # getattr: __del__ also runs on an object whose __init__ failed.
        buffer = getattr(self, "_buffer", bytearray())
        for index in range(len(buffer)):
            buffer[index] = 0
        self._wiped = True
