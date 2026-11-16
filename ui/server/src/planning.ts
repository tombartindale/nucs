// Backward scheduling for a module's delivery: each unit has its own agreed date (set by the
// producer, not inferred) by which its English subtitles must be in the system, ready for
// translation: through bcn subtitles with every suspected mishearing reviewed (or already
// sent). Prep, recording and the editor's day for that unit are scheduled backward from its
// own date alone — independently of every other unit's. Pure functions only — no DB, no
// HTTP — so the math is directly unit-testable against fixture data.
//
// Production happens one unit at a time: every topic's script in a unit must be ready
// before that unit's recording, and every topic in the unit is recorded together, in one
// session, not individually. So the schedule is batched per unit, not per topic — a unit's
// worth of scripts is one task, its recording is another, not N of each.
//
// Nothing here is persisted beyond each unit's delivery date and the module's owner (see
// db.ts). Every task/milestone deadline is recomputed from current status on every
// request: consistent with the rest of this UI, where the filesystem (via bcn status) is
// the only truth and nothing pipeline-derived is cached into Postgres.
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
// - The editor needs the one working day before the deadline: edit, export the subtitles,
//   and their proofreading (reviewing mishearings) all fit in it. Like recording, it is a
//   fixed day, not a per-topic cost.
// Dates are working days (Monday to Friday; no holiday calendar); treat as advisory, not a
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

const isWeekend = (d: Date): boolean => d.getUTCDay() === 0 || d.getUTCDay() === 6;

/** Steps back by a whole number of working days (Saturdays and Sundays are skipped), rounding
 *  fractional accumulated offsets up (never down to 0) so a run of sub-day tasks still
 *  separates onto distinct dates. */
export function minusWorkingDays(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  for (let n = Math.ceil(days); n > 0; n--) {
    do d.setUTCDate(d.getUTCDate() - 1); while (isWeekend(d));
  }
  return d.toISOString().slice(0, 10);
}

/** Working days after `from` up to and including `to` (0 if `to` is not later). */
export function workingDaysBetween(from: string, to: string): number {
  const d = new Date(`${from}T00:00:00Z`);
  const end = new Date(`${to}T00:00:00Z`);
  let n = 0;
  while (d < end) {
    d.setUTCDate(d.getUTCDate() + 1);
    if (!isWeekend(d)) n++;
  }
  return n;
}

function today(): string { return new Date().toISOString().slice(0, 10); }

export interface PlannedTopicInput {
  topic: string; unit: string; title: string; minutes: number | null;
  drafted: boolean; recorded: boolean;
  /** English subtitles ready to hand to the translator (or already sent): what a unit's date is for. */
  ready: boolean;
  translated: boolean; published: boolean;
}

/** One outstanding batch of work against one unit: writing the scripts still missing in
 *  it, recording it (every topic in the unit, in a single session), or the editor's day
 *  (edit, subtitles and their proofreading) that gets it ready for translation. */
export interface UnitTask {
  unit: string; kind: 'scripts' | 'recording' | 'editing';
  topics: string[];           // the outstanding topic ids this batch covers
  estimatedMinutes: number;   // summed across topics; for 'recording' this is informational
                              // only — the task always costs exactly one working day (below);
                              // 0 for 'editing', which is the editor's fixed day
  deadline: string | null;
}

/** One unit's own schedule: its agreed date (if set) and whether its own tasks fit
 *  before it, computed independently of every other unit's date. */
export interface UnitPlan {
  unit: string; deliveryDate: string | null;
  onTrack: boolean | null;   // null if this unit has no agreed date yet
  daysBehind: number;        // 0 if on track or no date set
}

export interface Milestone {
  kind: 'briefs_done' | 'recorded' | 'ready' | 'translated' | 'published';
  done: boolean; remaining: number; dueDate: string | null;
}

export interface ModulePlanComputed {
  topicsTotal: number; topicsRecorded: number; topicsRemaining: number; topicsNotDrafted: number; topicsReady: number;
  /** Worst-unit-wins: true only if every unit with an agreed date is itself on track; null
   *  if no unit has a date set yet, so there's nothing to judge against. */
  onTrack: boolean | null;
  daysBehind: number;        // the worst (largest) daysBehind across all units
  units: UnitPlan[];         // one per unit, in course-map order
  milestones: Milestone[];
  tasks: UnitTask[];         // every unit's outstanding tasks, concatenated in course-map order
}

/** Filters bcn status to one module's topics, in course-map order, mapped to the stage
 *  booleans this module needs. Stage boundaries are read from the stage list bcn ships
 *  (stages/stage_index) rather than hardcoded, so they track tooling/bcn/state.py's
 *  STAGES if it ever changes. */
/** Published means composed: a full (non-draft) compose has run and passed. The same rule
 *  as the front end's progress.ts `published`, which this must keep in step with. */
function composed(l: { steps?: Record<string, { exists?: boolean; ok?: boolean | null; draft?: boolean | null } | undefined> }): boolean {
  const c = l.steps?.compose;
  return !!c?.exists && c.ok !== false && !c.draft;
}

export function moduleTopicsFor(status: StatusEnvelope, module: string): PlannedTopicInput[] {
  return status.results
    .filter((r) => r.module === module)
    .map((r) => {
      const enAt = (stage: string) => r.en.stage_index >= r.en.stages.indexOf(stage);
      const zhAt = (stage: string) => r.zh.stage_index >= r.zh.stages.indexOf(stage);
      // The same test as the topic page's "Ready for translation" tile: bcn says the next
      // Mandarin step is sending it (subtitles done, nothing unreviewed), or it has gone.
      const ready = r.zh.next === 'translation_export' || zhAt('out_for_translation');
      return {
        topic: r.topic,
        unit: r.unit,
        title: r.title,
        minutes: r.minutes,
        drafted: enAt('drafted'),
        // Filmed or edited, ticked off by hand, counts: what's left after it is the editor's, not ours.
        // So do subtitles that have arrived (cues has run on them) or are ready: the English
        // stage needs the video as well, which can come after the subtitles.
        recorded: enAt('recorded') || !!r.marks.filmed || !!r.marks.edited || ready || !!r.en.steps?.cues?.exists,
        ready,
        translated: zhAt('parity_checked'),
        published: composed(r.en) && composed(r.zh),
      };
    });
}

/** `unitDates` holds each unit's own agreed delivery date (or null/missing if not yet
 *  agreed) — keyed by unit code, e.g. "U02". A unit with no date gets undated (null)
 *  tasks, same as the module used to when it had no delivery date at all. */
export function computeModulePlan(topics: PlannedTopicInput[], unitDates: Record<string, string | null>): ModulePlanComputed {
  // Group into units, preserving the order units first appear in course-map order.
  const unitOrder: string[] = [];
  const byUnit = new Map<string, PlannedTopicInput[]>();
  for (const t of topics) {
    if (!byUnit.has(t.unit)) { byUnit.set(t.unit, []); unitOrder.push(t.unit); }
    byUnit.get(t.unit)!.push(t);
  }

  const allTasks: UnitTask[] = [];
  const units: UnitPlan[] = [];
  for (const unit of unitOrder) {
    const unitTopics = byUnit.get(unit)!;
    const deliveryDate = unitDates[unit] ?? null;
    const notDrafted = unitTopics.filter((t) => !t.drafted);
    const notRecorded = unitTopics.filter((t) => !t.recorded);
    const notReady = unitTopics.filter((t) => !t.ready);

    // One scripts batch, one recording batch and the editor's day for this unit, in that
    // order — its prep must finish before its own recording session, and the editor works
    // from the recording, matching how production actually happens: one unit at a time,
    // not topic by topic.
    const rawTasks: Omit<UnitTask, 'deadline'>[] = [];
    if (notDrafted.length) {
      rawTasks.push({ unit, kind: 'scripts', topics: notDrafted.map((t) => t.topic), estimatedMinutes: notDrafted.length * BRIEF_MINUTES });
    }
    if (notRecorded.length) {
      const minutes = notRecorded.reduce((sum, t) => sum + (t.minutes ?? DEFAULT_RECORDING_MINUTES) * RECORDING_MULTIPLIER, 0);
      rawTasks.push({ unit, kind: 'recording', topics: notRecorded.map((t) => t.topic), estimatedMinutes: minutes });
    }
    if (notReady.length) {
      rawTasks.push({ unit, kind: 'editing', topics: notReady.map((t) => t.topic), estimatedMinutes: 0 });
    }

    // Walk this unit's own tasks backward from its own agreed date, independently of
    // every other unit: each unit's date is now externally agreed, not inferred relative
    // to the others, so there is no cross-unit chain any more — U01 and U05 are each
    // scheduled purely against their own deadline. A recording task and the editor's day
    // each cost exactly one working day — the session happens on a single day however long
    // it runs — while a scripts task costs its real proportional share of the daily hours
    // budget.
    // Fractional days accumulate across the walk rather than being rounded away per task,
    // so a run of short scripts batches still separates onto distinct dates.
    const dated: UnitTask[] = [];
    if (deliveryDate) {
      let cursor = deliveryDate;
      let accrued = 0; // fractional days owed but not yet stepped off the calendar
      for (let i = rawTasks.length - 1; i >= 0; i--) {
        const cost = rawTasks[i].kind === 'scripts' ? rawDaysFor(rawTasks[i].estimatedMinutes) : 1;
        accrued += cost;
        const wholeDays = Math.floor(accrued);
        if (wholeDays > 0) {
          cursor = minusWorkingDays(cursor, wholeDays);
          accrued -= wholeDays;
        }
        dated[i] = { ...rawTasks[i], deadline: cursor };
      }
    } else {
      for (let i = 0; i < rawTasks.length; i++) dated[i] = { ...rawTasks[i], deadline: null };
    }

    // Every task's deadline is constructed backward from this unit's own deliveryDate, so
    // it can never fall *after* it by construction — that can't be what "on track" means.
    // What actually signals trouble is this unit's schedule running out of runway: its
    // earliest outstanding task's deadline already being in the past relative to today.
    const deadlines = dated.map((t) => t.deadline).filter((d): d is string => d !== null);
    const earliestDeadline = deadlines.length ? deadlines.reduce((a, b) => (a < b ? a : b)) : null;
    const onTrack = deliveryDate === null ? null : (earliestDeadline === null || earliestDeadline >= today());
    // Counted in working days, like the schedule itself.
    const daysBehind = earliestDeadline !== null && earliestDeadline < today()
      ? Math.max(1, workingDaysBetween(earliestDeadline, today()))
      : 0;

    units.push({ unit, deliveryDate, onTrack, daysBehind });
    allTasks.push(...dated);
  }

  const topicsNotDrafted = topics.filter((t) => !t.drafted).length;
  const topicsRecorded = topics.filter((t) => t.recorded).length;
  const topicsRemaining = topics.length - topicsRecorded;
  const topicsReady = topics.filter((t) => t.ready).length;
  const topicsNotTranslated = topics.filter((t) => !t.translated).length;
  const topicsNotPublished = topics.filter((t) => !t.published).length;

  const latestDeadline = (kind: UnitTask['kind']): string | null => {
    const ds = allTasks.filter((t) => t.kind === kind).map((t) => t.deadline).filter((d): d is string => d !== null);
    return ds.length ? ds.reduce((a, b) => (a > b ? a : b)) : null;
  };

  const milestones: Milestone[] = [
    { kind: 'briefs_done', done: topicsNotDrafted === 0, remaining: topicsNotDrafted, dueDate: latestDeadline('scripts') },
    { kind: 'recorded', done: topicsRemaining === 0, remaining: topicsRemaining, dueDate: latestDeadline('recording') },
    // Due on the latest unit date still outstanding: the date itself is this milestone.
    { kind: 'ready', done: topicsReady === topics.length, remaining: topics.length - topicsReady,
      dueDate: units.filter((u) => u.deliveryDate && allTasks.some((t) => t.unit === u.unit && t.kind === 'editing'))
        .map((u) => u.deliveryDate as string).reduce<string | null>((a, b) => (a === null || b > a ? b : a), null) },
    { kind: 'translated', done: topicsNotTranslated === 0, remaining: topicsNotTranslated, dueDate: null },
    { kind: 'published', done: topicsNotPublished === 0, remaining: topicsNotPublished, dueDate: null },
  ];

  // Worst-unit-wins: the module is on track only if every unit with an agreed date is
  // itself on track; null if no unit has a date set at all, so there's nothing to judge.
  const datedUnits = units.filter((u) => u.deliveryDate !== null);
  const onTrack = datedUnits.length === 0 ? null : datedUnits.every((u) => u.onTrack !== false);
  const daysBehind = units.reduce((worst, u) => Math.max(worst, u.daysBehind), 0);

  return {
    topicsTotal: topics.length, topicsRecorded, topicsRemaining, topicsNotDrafted, topicsReady,
    onTrack, daysBehind, units, milestones, tasks: allTasks,
  };
}
