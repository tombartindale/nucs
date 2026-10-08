// Backward scheduling for a module's delivery: given a delivery date and current pipeline
// state (from bcn status), when does each unit's prep need to be ready, when does its
// recording session need to happen, and when is each module-wide milestone due. Pure
// functions only — no DB, no HTTP — so the math is directly unit-testable against fixture
// data.
//
// Production happens one unit at a time: every topic's script in a unit must be ready
// before that unit's recording, and every topic in the unit is recorded together, in one
// session, not individually. So the schedule is batched per unit, not per topic — a unit's
// worth of scripts is one task, its recording is another, not N of each.
//
// Nothing here is persisted beyond the module's delivery date and owner (see db.ts). Every
// task/milestone deadline is recomputed from current status on every request: consistent
// with the rest of this UI, where the filesystem (via bcn status) is the only truth and
// nothing pipeline-derived is cached into Postgres.
import type { StatusEnvelope } from '@beacon/shared';

// Assumptions, not measured rates — surfaced here, not buried in the math:
// - Writing one topic's script (topic.md) takes about 20 minutes, a fixed estimate
//   independent of the topic's declared length. A unit's script task is this, summed
//   across however many of its topics aren't drafted yet.
// - Recording (+ review) takes about 1.5x a topic's declared length, summed across a
//   unit's outstanding topics — but a unit's recording always happens in one calendar-day
//   session, however long that session actually runs. It is never spread across days: the
//   total is informational (how long the session will be), not a scheduling cost.
// - A producer has about this many focused hours per day available for script-writing,
//   before other duties. (Recording doesn't use this budget — see above.)
// Dates are calendar days (no weekends/holiday calendar); treat as advisory, not a
// hard commitment. Translation turnaround and packaging are not independently
// estimated per topic in v1 (translation happens off-pipeline, by the partner;
// packaging is fast once recorded) — their milestones report current progress only,
// not a projected date. See computeModulePlan below.
const BRIEF_MINUTES = 20;
const RECORDING_MULTIPLIER = 1.5;
const HOURS_PER_DAY = 3;
const DEFAULT_RECORDING_MINUTES = 40; // used when a topic has no declared `minutes`

/** Minutes of work, as a raw (unrounded) fraction of a day at HOURS_PER_DAY focused
 *  hours/day. Rounding happens only once, when the backward walk steps whole days off
 *  the calendar — never here, or every task under one quarter-day would display
 *  identically regardless of its actual length. */
function rawDaysFor(minutes: number): number {
  return minutes / 60 / HOURS_PER_DAY;
}

/** Steps back by a whole number of days, rounding fractional accumulated offsets up
 *  (never down to 0) so a run of sub-day tasks still separates onto distinct dates. */
function minusDays(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - Math.ceil(days));
  return d.toISOString().slice(0, 10);
}

function today(): string { return new Date().toISOString().slice(0, 10); }

export interface PlannedTopicInput {
  topic: string; unit: string; title: string; minutes: number | null;
  drafted: boolean; recorded: boolean; translated: boolean; packaged: boolean;
}

/** One outstanding batch of work against one unit: writing the scripts still missing in
 *  it, or recording it (every topic in the unit, in a single session). */
export interface UnitTask {
  unit: string; kind: 'scripts' | 'recording';
  topics: string[];           // the outstanding topic ids this batch covers
  estimatedMinutes: number;   // summed across topics; for 'recording' this is informational
                              // only — the task always costs exactly one calendar day (below)
  deadline: string | null;
}

export interface Milestone {
  kind: 'briefs_done' | 'recorded' | 'translated' | 'packaged';
  done: boolean; remaining: number; dueDate: string | null;
}

export interface ModulePlanComputed {
  deliveryDate: string | null;
  topicsTotal: number; topicsRecorded: number; topicsRemaining: number; topicsNotDrafted: number;
  onTrack: boolean | null;
  /** How many days the schedule has already run past "doable": the earliest outstanding
   *  task's deadline is this many days before today. 0 when on track or no delivery date. */
  daysBehind: number;
  milestones: Milestone[];
  tasks: UnitTask[];
}

/** Filters bcn status to one module's topics, in course-map order, mapped to the stage
 *  booleans this module needs. Stage boundaries are read from the stage list bcn ships
 *  (stages/stage_index) rather than hardcoded, so they track tooling/bcn/state.py's
 *  STAGES if it ever changes. */
export function moduleTopicsFor(status: StatusEnvelope, module: string): PlannedTopicInput[] {
  return status.results
    .filter((r) => r.module === module)
    .map((r) => {
      const enAt = (stage: string) => r.en.stage_index >= r.en.stages.indexOf(stage);
      const zhAt = (stage: string) => r.zh.stage_index >= r.zh.stages.indexOf(stage);
      return {
        topic: r.topic,
        unit: r.unit,
        title: r.title,
        minutes: r.minutes,
        drafted: enAt('drafted'),
        recorded: enAt('recorded'),
        translated: zhAt('parity_checked'),
        packaged: enAt('packaged') && zhAt('packaged'),
      };
    });
}

export function computeModulePlan(topics: PlannedTopicInput[], deliveryDate: string | null): ModulePlanComputed {
  // Group into units, preserving the order units first appear in course-map order.
  const unitOrder: string[] = [];
  const byUnit = new Map<string, PlannedTopicInput[]>();
  for (const t of topics) {
    if (!byUnit.has(t.unit)) { byUnit.set(t.unit, []); unitOrder.push(t.unit); }
    byUnit.get(t.unit)!.push(t);
  }

  // One scripts batch and one recording batch per unit, in that order — a unit's prep
  // must finish before its own recording session, matching how production actually
  // happens: one unit at a time, not topic by topic.
  const tasks: Omit<UnitTask, 'deadline'>[] = [];
  for (const unit of unitOrder) {
    const unitTopics = byUnit.get(unit)!;
    const notDrafted = unitTopics.filter((t) => !t.drafted);
    const notRecorded = unitTopics.filter((t) => !t.recorded);
    if (notDrafted.length) {
      tasks.push({ unit, kind: 'scripts', topics: notDrafted.map((t) => t.topic), estimatedMinutes: notDrafted.length * BRIEF_MINUTES });
    }
    if (notRecorded.length) {
      const minutes = notRecorded.reduce((sum, t) => sum + (t.minutes ?? DEFAULT_RECORDING_MINUTES) * RECORDING_MULTIPLIER, 0);
      tasks.push({ unit, kind: 'recording', topics: notRecorded.map((t) => t.topic), estimatedMinutes: minutes });
    }
  }

  // Walk backwards from the delivery date: the last task's deadline sits closest to
  // delivery, each earlier task's deadline is pushed back by its own cost. A recording
  // task always costs exactly one day — the session happens on a single day however long
  // it runs — while a scripts task costs its real proportional share of the daily hours
  // budget. Fractional days accumulate across the whole walk (carried in `accrued`)
  // rather than being rounded away per task, so a run of short scripts batches still
  // separates onto distinct dates instead of collapsing onto the same one.
  const dated: UnitTask[] = [];
  if (deliveryDate) {
    let cursor = deliveryDate;
    let accrued = 0; // fractional days owed but not yet stepped off the calendar
    for (let i = tasks.length - 1; i >= 0; i--) {
      const cost = tasks[i].kind === 'recording' ? 1 : rawDaysFor(tasks[i].estimatedMinutes);
      accrued += cost;
      const wholeDays = Math.floor(accrued);
      if (wholeDays > 0) {
        cursor = minusDays(cursor, wholeDays);
        accrued -= wholeDays;
      }
      dated[i] = { ...tasks[i], deadline: cursor };
    }
  } else {
    for (let i = 0; i < tasks.length; i++) dated[i] = { ...tasks[i], deadline: null };
  }

  const topicsNotDrafted = topics.filter((t) => !t.drafted).length;
  const topicsRecorded = topics.filter((t) => t.recorded).length;
  const topicsRemaining = topics.length - topicsRecorded;
  const topicsNotTranslated = topics.filter((t) => !t.translated).length;
  const topicsNotPackaged = topics.filter((t) => !t.packaged).length;

  const latestDeadline = (kind: UnitTask['kind']): string | null => {
    const ds = dated.filter((t) => t.kind === kind).map((t) => t.deadline).filter((d): d is string => d !== null);
    return ds.length ? ds.reduce((a, b) => (a > b ? a : b)) : null;
  };
  const briefsDueDate = latestDeadline('scripts');
  const recordedDueDate = latestDeadline('recording');

  const milestones: Milestone[] = [
    { kind: 'briefs_done', done: topicsNotDrafted === 0, remaining: topicsNotDrafted, dueDate: briefsDueDate },
    { kind: 'recorded', done: topicsRemaining === 0, remaining: topicsRemaining, dueDate: recordedDueDate },
    { kind: 'translated', done: topicsNotTranslated === 0, remaining: topicsNotTranslated, dueDate: null },
    { kind: 'packaged', done: topicsNotPackaged === 0, remaining: topicsNotPackaged, dueDate: null },
  ];

  // Every task's deadline is constructed backward from deliveryDate, so it can never fall
  // *after* delivery by construction — that can't be what "on track" means. What actually
  // signals trouble is the schedule running out of runway: the earliest outstanding task's
  // deadline already being in the past relative to today, i.e. there is more outstanding
  // work than there is time left before delivery.
  const deadlines = dated.map((t) => t.deadline).filter((d): d is string => d !== null);
  const earliestDeadline = deadlines.length ? deadlines.reduce((a, b) => (a < b ? a : b)) : null;
  const onTrack = deliveryDate === null ? null : (earliestDeadline === null || earliestDeadline >= today());
  const daysBehind = earliestDeadline !== null && earliestDeadline < today()
    ? Math.round((Date.parse(`${today()}T00:00:00Z`) - Date.parse(`${earliestDeadline}T00:00:00Z`)) / 86_400_000)
    : 0;

  return {
    deliveryDate, topicsTotal: topics.length, topicsRecorded, topicsRemaining, topicsNotDrafted,
    onTrack, daysBehind, milestones, tasks: dated,
  };
}
