import { describe, expect, it } from 'vitest';

import { ManualClock } from '@/session/testing/fakes';

import { CHECK_INTERVAL_MS, CPU_SHARE, DutyCycle, IDLE_MS, IdleScheduler } from './scheduler';

function scheduler(clock: ManualClock, work: () => Promise<void> = () => Promise.resolve()) {
  const runs: number[] = [];
  const errors: unknown[] = [];
  const s = new IdleScheduler(
    clock,
    async () => {
      runs.push(clock.time);
      await work();
    },
    (error) => errors.push(error),
  );
  return { s, runs, errors };
}

describe('IdleScheduler', () => {
  it('runs once at start, then only after the workspace was quiet for the idle delay', async () => {
    const clock = new ManualClock();
    const { s, runs } = scheduler(clock);
    s.start();
    await clock.advance(0);
    expect(runs).toEqual([0]);
    s.changed();
    await clock.advance(IDLE_MS - 500);
    s.changed(); // a new change restarts the quiet period
    await clock.advance(IDLE_MS - 1);
    expect(runs).toEqual([0]);
    await clock.advance(1);
    expect(runs).toEqual([0, 2 * IDLE_MS - 500]);
    await s.stop();
  });

  it('runs the consistency check every 10 minutes without a change', async () => {
    const clock = new ManualClock();
    const { s, runs } = scheduler(clock);
    s.start();
    await clock.advance(CHECK_INTERVAL_MS);
    await clock.advance(CHECK_INTERVAL_MS);
    expect(runs).toEqual([0, CHECK_INTERVAL_MS, 2 * CHECK_INTERVAL_MS]);
    await s.stop();
  });

  it('never runs twice at once and coalesces requests into one more run', async () => {
    const clock = new ManualClock();
    let release = () => undefined as unknown;
    const { s, runs } = scheduler(clock, () => new Promise((resolve) => (release = resolve)));
    s.start();
    await clock.advance(0);
    s.changed();
    s.changed();
    await clock.advance(IDLE_MS);
    await clock.advance(CHECK_INTERVAL_MS);
    expect(runs).toHaveLength(1);
    release();
    await clock.advance(0);
    expect(runs).toHaveLength(2);
    release();
    await s.stop();
  });

  it('reports a failed run and keeps scheduling', async () => {
    const clock = new ManualClock();
    const { s, runs, errors } = scheduler(clock, () => Promise.reject(new Error('walk failed')));
    s.start();
    await clock.advance(0);
    s.changed();
    await clock.advance(IDLE_MS);
    expect([runs.length, errors.length]).toEqual([2, 2]);
    await s.stop();
  });
});

describe('DutyCycle', () => {
  it('waits so that work stays within its share of one core', async () => {
    const clock = new ManualClock();
    const cycle = new DutyCycle(clock);
    const paced = cycle.pace(12, new AbortController().signal);
    const wait = (12 * (1 - CPU_SHARE)) / CPU_SHARE;
    await clock.advance(wait - 1);
    let done = false;
    void paced.then(() => (done = true));
    await clock.advance(0);
    expect(done).toBe(false);
    await clock.advance(1);
    expect(done).toBe(true);
    expect(12 / (12 + wait)).toBeCloseTo(CPU_SHARE);
  });
});
