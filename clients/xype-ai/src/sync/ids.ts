// Message ids: UUIDv7 (RFC 9562), as `Envelope.message_id` asks, so ids sort by time.

import { getRandomValues } from 'node:crypto';

const hex = (bytes: Uint8Array) => Buffer.from(bytes).toString('hex');

export function uuid7(now = Date.now()): string {
  const bytes = getRandomValues(new Uint8Array(16));
  new DataView(bytes.buffer).setUint16(0, Math.floor(now / 2 ** 32));
  new DataView(bytes.buffer).setUint32(2, now % 2 ** 32);
  bytes[6] = 0x70 | ((bytes[6] ?? 0) & 0x0f); // version 7
  bytes[8] = 0x80 | ((bytes[8] ?? 0) & 0x3f); // variant 10
  const h = hex(bytes);
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
