// Pure math: no DB, no bcn, no App — fixture topic arrays straight into computeModulePlan.
import { describe, expect, it } from 'vitest';
import { computeModulePlan, type PlannedTopicInput } from '../src/planning.js';

function topic(overrides: Partial<PlannedTopicInput> = {}): PlannedTopicInput {
  return {
    topic: 'KV7015-U01-T01', title: 'A topic', minutes: 12,
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

  it('gives a not-yet-drafted topic both a brief and a recording task, brief first', () => {
    const plan = computeModulePlan([topic({ topic: 'KV7015-U01-T01', drafted: false, minutes: 12 })], '2026-12-01');
    expect(plan.tasks.map((t) => t.kind)).toEqual(['brief', 'recording']);
    // brief's deadline must be no later than the recording deadline that follows it
    expect(plan.tasks[0].deadline! <= plan.tasks[1].deadline!).toBe(true);
  });

  it('gives an already-drafted topic only a recording task', () => {
    const plan = computeModulePlan([topic({ drafted: true })], '2026-12-01');
    expect(plan.tasks.map((t) => t.kind)).toEqual(['recording']);
  });

  it('chains deadlines backward: later course-map topics get deadlines closer to delivery', () => {
    // Each recording here is 1.5x 100 = 150 min: under the 180-minute daily budget (3h/day)
    // alone, so the last task's deadline is still the delivery date itself, but the two
    // together (300 min) exceed one day, so the earlier task must land a day before it.
    const plan = computeModulePlan([
      topic({ topic: 'KV7015-U01-T01', drafted: true, minutes: 100 }),
      topic({ topic: 'KV7015-U01-T02', drafted: true, minutes: 100 }),
    ], '2026-12-01');
    const [first, second] = plan.tasks;
    expect(first.deadline! < second.deadline!).toBe(true);
    expect(second.deadline).toBe('2026-12-01');
  });

  it('lets two short tasks share the same deadline when both fit inside one day\'s budget', () => {
    // Each is only 1.5x 40 = 60 min of work; two of them (120 min) fit inside the 180-minute
    // daily budget, so the accurate estimate (unlike the old, over-quantized one) correctly
    // gives them the same deadline rather than inventing a day of separation that isn't real.
    const plan = computeModulePlan([
      topic({ topic: 'KV7015-U01-T01', drafted: true, minutes: 40 }),
      topic({ topic: 'KV7015-U01-T02', drafted: true, minutes: 40 }),
    ], '2026-12-01');
    const [first, second] = plan.tasks;
    expect(first.deadline).toBe(second.deadline);
    expect(second.deadline).toBe('2026-12-01');
  });

  it('falls back to a default estimate when minutes is null, without throwing', () => {
    expect(() => computeModulePlan([topic({ drafted: true, minutes: null })], '2026-12-01')).not.toThrow();
    const plan = computeModulePlan([topic({ drafted: true, minutes: null })], '2026-12-01');
    expect(plan.tasks[0].estimatedMinutes).toBeGreaterThan(0);
  });

  it('estimates recording time as exactly 1.5x the declared minutes, distinct per topic', () => {
    const plan = computeModulePlan([
      topic({ topic: 'KV7015-U01-T01', drafted: true, minutes: 10 }),
      topic({ topic: 'KV7015-U01-T02', drafted: true, minutes: 40 }),
    ], '2026-12-01');
    const [short, long] = plan.tasks;
    expect(short.estimatedMinutes).toBe(15);   // 10 * 1.5
    expect(long.estimatedMinutes).toBe(60);    // 40 * 1.5
    expect(short.estimatedMinutes).not.toBe(long.estimatedMinutes);
  });

  it('estimates a brief at a fixed 20 minutes regardless of the topic\'s declared length', () => {
    const plan = computeModulePlan([topic({ drafted: false, minutes: 90 })], '2026-12-01');
    expect(plan.tasks[0].kind).toBe('brief');
    expect(plan.tasks[0].estimatedMinutes).toBe(20);
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
    const plan = computeModulePlan([topic({ drafted: false, minutes: 12 })], '2026-12-01');
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
    // Every task's deadline is chained backward from deliveryDate, so no deadline can ever
    // land after it — "on track" instead means the earliest deadline hasn't already been
    // pushed into the past relative to today. A huge backlog against a near-term delivery
    // date does exactly that: the chain runs out of room and the earliest task's deadline
    // ends up before today, even though it's still <= deliveryDate.
    const many = Array.from({ length: 50 }, (_, i) => topic({ topic: `KV7015-U01-T${i + 1}`, drafted: false, minutes: 600 }));
    const soon = new Date(Date.now() + 2 * 86_400_000).toISOString().slice(0, 10); // delivery in 2 days
    const plan = computeModulePlan(many, soon);
    expect(plan.onTrack).toBe(false);
    const earliest = plan.tasks[0].deadline!;
    expect(earliest < new Date().toISOString().slice(0, 10)).toBe(true);
    expect(plan.daysBehind).toBeGreaterThan(0);
  });
});
