// Pure math: no DB, no bcn, no App — fixture topic arrays straight into computeModulePlan.
import { describe, expect, it } from 'vitest';
import { computeModulePlan, type PlannedTopicInput } from '../src/planning.js';

function topic(overrides: Partial<PlannedTopicInput> = {}): PlannedTopicInput {
  return {
    topic: 'KV7015-U01-T01', unit: 'U01', title: 'A topic', minutes: 12,
    drafted: true, recorded: false, translated: false, packaged: false,
    ...overrides,
  };
}

describe('computeModulePlan', () => {
  it('returns null deadlines and null onTrack with no delivery date', () => {
    const plan = computeModulePlan([topic()], null);
    expect(plan.onTrack).toBeNull();
    for (const t of plan.tasks) expect(t.deadline).toBeNull();
    for (const m of plan.milestones) expect(m.dueDate).toBeNull();
  });

  it('batches every not-yet-drafted topic in a unit into one scripts task, listing them all', () => {
    const plan = computeModulePlan([
      topic({ topic: 'KV7015-U01-T01', unit: 'U01', drafted: false, recorded: true }),
      topic({ topic: 'KV7015-U01-T02', unit: 'U01', drafted: false, recorded: true }),
      topic({ topic: 'KV7015-U01-T03', unit: 'U01', drafted: true, recorded: true }), // already drafted: excluded
    ], '2026-12-01');
    expect(plan.tasks).toHaveLength(1);
    expect(plan.tasks[0].kind).toBe('scripts');
    expect(plan.tasks[0].topics).toEqual(['KV7015-U01-T01', 'KV7015-U01-T02']);
    expect(plan.tasks[0].estimatedMinutes).toBe(40); // 2 topics x 20 min
  });

  it('batches every not-yet-recorded topic in a unit into one recording task, listing them all', () => {
    const plan = computeModulePlan([
      topic({ topic: 'KV7015-U01-T01', unit: 'U01', drafted: true, recorded: false, minutes: 10 }),
      topic({ topic: 'KV7015-U01-T02', unit: 'U01', drafted: true, recorded: false, minutes: 40 }),
      topic({ topic: 'KV7015-U01-T03', unit: 'U01', drafted: true, recorded: true }), // already recorded: excluded
    ], '2026-12-01');
    expect(plan.tasks).toHaveLength(1);
    expect(plan.tasks[0].kind).toBe('recording');
    expect(plan.tasks[0].topics).toEqual(['KV7015-U01-T01', 'KV7015-U01-T02']);
    expect(plan.tasks[0].estimatedMinutes).toBe(75); // (10 + 40) x 1.5
  });

  it('gives a unit both a scripts and a recording task, scripts first, when neither is done', () => {
    const plan = computeModulePlan([topic({ drafted: false, recorded: false, minutes: 12 })], '2026-12-01');
    expect(plan.tasks.map((t) => t.kind)).toEqual(['scripts', 'recording']);
  });

  it('gives a unit with already-drafted topics only a recording task', () => {
    const plan = computeModulePlan([topic({ drafted: true, recorded: false })], '2026-12-01');
    expect(plan.tasks.map((t) => t.kind)).toEqual(['recording']);
  });

  it('costs a recording session exactly one day regardless of total minutes, unlike scripts\' proportional cost', () => {
    // Scripts: 5 topics not drafted, 20 min each = 100 min, under the 180-minute daily
    // budget, so its deadline is the delivery date itself.
    const scriptsOnly = Array.from({ length: 5 }, (_, i) =>
      topic({ topic: `KV7015-U01-T${i + 1}`, drafted: false, recorded: true }));
    const scriptsPlan = computeModulePlan(scriptsOnly, '2026-12-01');
    expect(scriptsPlan.tasks).toHaveLength(1);
    expect(scriptsPlan.tasks[0].deadline).toBe('2026-12-01');

    // Recording: same five topics, same small total length — but a recording session
    // always reserves a whole day, so its deadline sits one day before delivery even
    // though the work itself would fit in well under a day.
    const recordingOnly = Array.from({ length: 5 }, (_, i) =>
      topic({ topic: `KV7015-U01-T${i + 1}`, drafted: true, recorded: false, minutes: 10 }));
    const recordingPlan = computeModulePlan(recordingOnly, '2026-12-01');
    expect(recordingPlan.tasks).toHaveLength(1);
    expect(recordingPlan.tasks[0].deadline).toBe('2026-11-30');
  });

  it('chains two units backward: the later unit\'s tasks sit closer to delivery than the earlier unit\'s', () => {
    const plan = computeModulePlan([
      topic({ topic: 'KV7015-U01-T01', unit: 'U01', drafted: false, recorded: false, minutes: 10 }),
      topic({ topic: 'KV7015-U02-T01', unit: 'U02', drafted: false, recorded: false, minutes: 10 }),
    ], '2026-12-01');
    expect(plan.tasks.map((t) => `${t.unit}-${t.kind}`)).toEqual(['U01-scripts', 'U01-recording', 'U02-scripts', 'U02-recording']);
    const u01Recording = plan.tasks[1].deadline!;
    const u02Recording = plan.tasks[3].deadline!;
    expect(u01Recording < u02Recording).toBe(true);
    expect(u02Recording).toBe('2026-11-30'); // the very last task still reserves recording's one-day session before delivery
  });

  it('falls back to a default estimate when minutes is null, without throwing', () => {
    expect(() => computeModulePlan([topic({ drafted: true, recorded: false, minutes: null })], '2026-12-01')).not.toThrow();
    const plan = computeModulePlan([topic({ drafted: true, recorded: false, minutes: null })], '2026-12-01');
    expect(plan.tasks[0].estimatedMinutes).toBeGreaterThan(0);
  });

  it('estimates a unit\'s recording total as exactly 1.5x the sum of its topics\' declared minutes', () => {
    const plan = computeModulePlan([
      topic({ topic: 'KV7015-U01-T01', unit: 'U01', drafted: true, recorded: false, minutes: 10 }),
      topic({ topic: 'KV7015-U02-T01', unit: 'U02', drafted: true, recorded: false, minutes: 40 }),
    ], '2026-12-01');
    const byUnit = Object.fromEntries(plan.tasks.map((t) => [t.unit, t]));
    expect(byUnit.U01.estimatedMinutes).toBe(15);   // 10 * 1.5
    expect(byUnit.U02.estimatedMinutes).toBe(60);   // 40 * 1.5
  });

  it('estimates a scripts batch at a fixed 20 minutes per topic, summed across the unit', () => {
    const plan = computeModulePlan([
      topic({ topic: 'KV7015-U01-T01', drafted: false, recorded: true, minutes: 90 }),
      topic({ topic: 'KV7015-U01-T02', drafted: false, recorded: true, minutes: 5 }),
    ], '2026-12-01');
    expect(plan.tasks[0].kind).toBe('scripts');
    expect(plan.tasks[0].estimatedMinutes).toBe(40); // 2 x 20, independent of declared length
  });

  it('reports milestone remaining counts and done flags correctly', () => {
    const plan = computeModulePlan([
      topic({ topic: 'KV7015-U01-T01', drafted: true, recorded: true, translated: true, packaged: true }),
      topic({ topic: 'KV7015-U01-T02', drafted: false, recorded: false, translated: false, packaged: false }),
    ], '2026-12-01');
    const byKind = Object.fromEntries(plan.milestones.map((m) => [m.kind, m]));
    expect(byKind.briefs_done.remaining).toBe(1);
    expect(byKind.briefs_done.done).toBe(false);
    expect(byKind.recorded.remaining).toBe(1);
    expect(byKind.translated.remaining).toBe(1);
    expect(byKind.packaged.remaining).toBe(1);
  });

  it('marks all milestones done when every topic is fully packaged', () => {
    const plan = computeModulePlan(
      [topic({ drafted: true, recorded: true, translated: true, packaged: true })], '2026-12-01');
    expect(plan.milestones.every((m) => m.done)).toBe(true);
    expect(plan.tasks).toHaveLength(0);
  });

  it('only projects dueDate for briefs_done/recorded, never for translated/packaged', () => {
    const plan = computeModulePlan([topic({ drafted: false, recorded: false, minutes: 12 })], '2026-12-01');
    const byKind = Object.fromEntries(plan.milestones.map((m) => [m.kind, m]));
    expect(byKind.briefs_done.dueDate).not.toBeNull();
    expect(byKind.recorded.dueDate).not.toBeNull();
    expect(byKind.translated.dueDate).toBeNull();
    expect(byKind.packaged.dueDate).toBeNull();
  });

  it('is on track when a far-future delivery date leaves plenty of runway', () => {
    const plan = computeModulePlan([topic({ drafted: true, recorded: false, minutes: 12 })], '2099-12-01');
    expect(plan.onTrack).toBe(true);
    expect(plan.daysBehind).toBe(0);
  });

  it('flags off track when there is more outstanding work than time left before delivery', () => {
    // A large backlog batched into one unit, against a delivery date only two days away:
    // the chain (scripts + a one-day recording session) can't possibly fit, so the
    // earliest task's deadline ends up before today even though it's still <= deliveryDate.
    const many = Array.from({ length: 50 }, (_, i) => topic({ topic: `KV7015-U01-T${i + 1}`, drafted: false, recorded: false, minutes: 600 }));
    const soon = new Date(Date.now() + 2 * 86_400_000).toISOString().slice(0, 10); // delivery in 2 days
    const plan = computeModulePlan(many, soon);
    expect(plan.onTrack).toBe(false);
    const earliest = plan.tasks[0].deadline!;
    expect(earliest < new Date().toISOString().slice(0, 10)).toBe(true);
    expect(plan.daysBehind).toBeGreaterThan(0);
  });
});
