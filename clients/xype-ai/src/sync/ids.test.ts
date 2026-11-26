import { describe, expect, it } from 'vitest';

import { uuid7 } from './ids';

describe('uuid7', () => {
  it('is a version 7 UUID that sorts by time', () => {
    const early = uuid7(1_700_000_000_000);
    const late = uuid7(1_700_000_000_001);
    expect(early).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(early < late).toBe(true);
    expect(uuid7()).not.toBe(uuid7());
  });
});
