// Unambiguous concatenation for every keyed hash, key derivation and AAD (ADR-0016),
// the same encoding as the server's `common_lib.security.framing`: each field is its
// 4-byte big-endian length followed by its bytes, so ("ab", "c") and ("a", "bc") never
// encode alike. `packages/crypto-vectors/vectors.json` pins it for both sides.

export type FrameField = Uint8Array | string | number;

export class FramingError extends Error {
  override name = 'FramingError';
}

const MAX_FIELD = 0xffff_ffff;
const encoder = new TextEncoder();

/** Encodes `fields` in order: strings as UTF-8, integers as their decimal digits. */
export function frame(...fields: readonly FrameField[]): Uint8Array {
  const parts = fields.map(toBytes);
  const out = new Uint8Array(parts.reduce((total, part) => total + 4 + part.length, 0));
  const view = new DataView(out.buffer);
  let at = 0;
  for (const part of parts) {
    view.setUint32(at, part.length);
    out.set(part, at + 4);
    at += 4 + part.length;
  }
  return out;
}

function toBytes(field: FrameField): Uint8Array {
  if (typeof field === 'number') {
    if (!Number.isSafeInteger(field)) throw new FramingError('only integers are framed');
    return encoder.encode(String(field));
  }
  const bytes = typeof field === 'string' ? encoder.encode(field) : field;
  if (bytes.length > MAX_FIELD) throw new FramingError('a framed field is too long');
  return bytes;
}
