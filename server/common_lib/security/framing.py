"""Unambiguous concatenation for every keyed hash, key derivation and AAD (ADR-0016).

`frame(a, b)` is the 4-byte big-endian length of each field's bytes followed by those
bytes, so ("ab", "c") and ("a", "bc") never encode alike. The TypeScript client uses
the same encoding; `packages/crypto-vectors/vectors.json` pins it for both.
"""

from common_lib.errors import InvalidValueError

_MAX_FIELD = 2**32 - 1


def frame(*fields: bytes | str | int) -> bytes:
    """Encodes `fields` in order; `str` as UTF-8, `int` as its decimal digits."""
    out = bytearray()
    for field in fields:
        data = _to_bytes(field)
        if len(data) > _MAX_FIELD:
            raise InvalidValueError("a framed field is too long")
        out += len(data).to_bytes(4, "big")
        out += data
    return bytes(out)


def _to_bytes(field: bytes | str | int) -> bytes:
    if isinstance(field, bytes):
        return field
    if isinstance(field, bool):
        raise InvalidValueError("booleans are not framed")
    if isinstance(field, int):
        return str(field).encode("ascii")
    return field.encode("utf-8")
