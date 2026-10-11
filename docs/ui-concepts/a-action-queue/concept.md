# Concept A: Next-Action Queue

## One sentence

Open the app and get one ordered to-do list. Every row is a concrete thing to do now: a verb, a scope, why it sits there, a due date, and the button that does it. Rows are batched across topics and sorted by deadline slack.

## Landing screen, and why

The producer does the work themselves, so the morning question is "what do I do next?", not "how are we doing?". Today's Programme page answers the second question and leaves the first to the producer's head. The queue answers the first. From top to bottom the screen has:

1. **Module strip.** One line of eight cells, each showing the code, delivery date, *on track* or *N d behind*, and the EN/ZH packaged count. Clicking a cell filters the queue to that module. It does not open a dashboard. This line is the whole of the overall status, and it stays one line high.
2. **The queue**, in sections: **Today · This week · Later · Waiting on others · Unscheduled**. A typical row reads: `Place 1 cue boundary · KV7015 U01 T02 · blocks EN subtitles→package · due ≈23 Oct · [Open in cue editor]`. A **why?** toggle shows the arithmetic behind the row's position.
3. **Jobs live inside rows.** Clicking Run turns the row into its own progress bar. When the job ends, a one-line "moved on" note says where the topics went.

There is no drag-to-reorder. If a row is in the wrong place, the fix is the data (a date or an estimate), not the list.

## IA / navigation

| Today | Concept A |
| --- | --- |
| Programme (KPIs, schedule, stage ladder) | **Queue** becomes the landing page. The KPIs collapse into the strip, and the ladder moves to the Module page. |
| Verification | Errors become **Fix** rows, grouped by code. The by-code page survives as the drill-down behind a row ("see all 7"). |
| Mis-transcriptions | Reached from a **Proofread N** row. Otherwise unchanged. |
| Jobs | Live progress moves into rows. The page becomes **History** (log, envelopes, cancel). |
| Translation | Unchanged, but opened pre-scoped from **Send N to translation** and **Check returned batch** rows. |
| Module | Gains a milestone header, a per-unit EN/ZH table, and the queue filtered to that module. The topic grid stays as a tab. |
| Topic | Gains a "Next for this topic" bar showing its rows in both languages. |

The nav becomes **Queue · Modules · Translation · History · Transfer**.

## How the order is computed

A pure `queue.ts` sits beside `planning.ts`. It re-sorts what bcn and the plan already say and makes no pipeline decisions.

1. **Candidates come from `bcn status`, per topic × language.** The verb table is static and lives in the UI.
   - `next` of validate, render, cues, subtitles, compose or package → **Run**.
   - `next` of `intake` → **Write scripts**. `await_recording` → **Record session**. `translation_export` → **Send to translation**. `await_translation` → **Waiting**.
   - `blockers[]` and error diagnostics map to a verb by code family:
     - `CUE_*` → **Place cue boundary**
     - `MD_*` → **Fix script**
     - `RENDER_SAFE_AREA` / `RENDER_OVERFLOW` → **Fix slide overflow**
     - `FS_SYNC_CONFLICT` → **Resolve conflict copy**
     - `FS_NOT_HYDRATED` → **Make available offline**
     - an outstanding asset → **Waiting: asset**
   - `unreviewed_mistranscriptions` → **Proofread**.
   - `stale` adds an amber "Re-run" qualifier.
2. **Batch.** Rows are grouped by verb, language, module and unit. Write and Record rows *are* `planning.ts`'s `UnitTask`s, one per unit. Machine runs are merged across units when they fall in the same bucket.
3. **Due date.**
   - Write and Record rows take `UnitTask.deadline`.
   - Anything on a topic whose unit is unrecorded inherits that unit's recording deadline, because it blocks the session.
   - Post-recording EN work inherits the *packaged* date. ZH work inherits the *translated* date (see Data gaps). Until those dates exist, the row shows "≈ delivery date".
4. **Slack** = due − today − effort. Effort is 20 minutes per script and one day per recording, both from `planning.ts`. Fixes count as a nominal 10 minutes. Machine runs count as 0.
5. **Sort.** Slack ascending, then by **unlocks** (how many downstream topic-steps the row frees), then by pipeline order.
6. **Buckets.**
   - **Today:** slack ≤ 1, overdue rows, and *every ready machine run*. A run costs nothing to start, so it is never held back.
   - **This week:** due within 7 days.
   - **Later:** everything else.
   - **Waiting:** `await_translation` and outstanding assets. A waiting row becomes **Chase** once it's overdue.
   - **Unscheduled:** modules with no `delivery_date`, shown as one "Set delivery date" row.

**How rows move along.** After a job, the normal status poll regenerates the queue. Finished topics drop out of the row and reappear in their next row. Failures split the row: "Render 12" becomes 11 done plus "Fix slide overflow · 1". The UI never moves topics itself. It re-reads status.

## Status at each level

- **Programme:** the strip only. It shows on track or behind and the EN/ZH packaged counts. A module that is behind gets a red edge.
- **Module:**
  - a milestone line built from the existing `Milestone[]`: briefs / recorded / translated / packaged, each with its due date and remaining count;
  - then a unit table with an EN lane and a ZH lane of stage pips, and the unit's next action in words;
  - then the filtered queue.
- **Unit:** a row in that table, anchored on its recording date.
- **Topic:** today's page plus the "Next for this topic" bar.

Colour has three meanings only: red for overdue or blocked, amber for stale, grey for waiting.

## EN/ZH

There is one queue, not two tabs. Each row carries an `EN` or `ZH` tag, and ZH rows sort by the same slack rule. A language chip can narrow the list, but by default it shows both. Translation is a set of first-class rows:

- **Send 18 to translation**
- **Waiting: 24 with translator · 14 days**
- **Check returned batch · 12**
- **Return 3 to translator**, for parity and timing failures, gathered into one message

Because ZH work sits in the same list as EN work, it can't quietly fall off the bottom.

## Blocked, stale, human-needed

A blocked item is never a passive badge. It becomes the verb that unblocks it, and it sorts ahead of the runs it gates. A row that depends on another says so ("after ↑ Write 4 scripts") and stays disabled until that row is done.

Stale shows as amber text on a Run row, for example "Re-render 3 · script edited after render". Cloud-only files and conflict copies become Fix rows carrying the spec's instruction text, with no fake button.

## Workflows

**Monday morning.**
1. The strip shows KV7011 is 6 days behind.
2. Today's top row is red: *Write 4 scripts · KV7011 U05 · recording was due 9 Oct*.
3. Click **Run all ready (5)**. The renders and cues start in place.
4. Paste the scripts via **Open intake**. They validate, the Write row vanishes, and *Record KV7011 U05* enables.
5. Meanwhile one render has split off as *Fix slide overflow (ZH)*.

**Recording session for U03.**
1. Open the *Record session · KV7012 U03* row (6 topics, ~2 h 10 m). **Open teleprompter** steps through the unit's scripts.
2. The masters arrive by sync, and the row is replaced by *Run cues + subtitles · 6*. Run it.
3. One topic returns as *Place 1 cue boundary* and one as *Proofread 2*. Both land in Today because they gate the ZH export.

**Translation batch back.**
1. *Check returned batch · 24* appears. Clicking it opens Translation, scoped to that batch, and imports.
2. The queue now shows *Render ZH · 21* and *Return 3 to translator*, with the message pre-filled.
3. Run the 21 and send the 3.

## Data gaps

- **ZH and packaged dates.** The *translated* and *packaged* milestones have `dueDate: null`. The fix is two settings, a translator turnaround (e.g. 21 days) and a packaging buffer (e.g. 14 days), back-scheduled inside `planning.ts`. Until then those dates show as "≈".
- **Effort for fixes and proofreading.** There is no data, so the queue uses a nominal constant and says so in "why?".
- **Export timestamp.** It's in `translation.json`, but `status` doesn't surface it, and "with translator 14 days" needs it.
- **"Returned to translator" state.** Nothing records it yet. It should be written into `translation.json` by bcn, not into the UI database.

## Reused vs new

**Reused:**
- `bcn status` (`next`, `blockers`, `stale`)
- `planning.ts` (`UnitTask`, `Milestone`, `daysBehind`)
- jobs and SSE
- the Diagnostics, Mis-transcriptions, Translation, Intake, Teleprompter and Topic pages, as drill-downs
- the ICS feed
- the email digest, which becomes "your Today"

**New:**
- `queue.ts` and its tests
- `GET /api/queue`
- the Queue page and module strip
- row↔job binding with split and "moved on" handling
- the Module page header and unit table
- the topic "next" bar
- the milestone extension

## Risks

- **The order looks authoritative but rests on guesses** (20 min per script, nominal fix effort). "why?" is a mitigation, not a cure.
- **A list hides the shape of the work.** It can't answer "is the whole runway feasible?". The strip says *behind* but not why. This concept leaves that question to a timeline.
- **Churn.** Rows regenerate on every poll and can jump while you read them. This needs stable row keys and a gentle reflow.
- **A second vocabulary.** The code→verb table can drift from `codes.py`, so it needs a generic "Fix: CODE" fallback.
- **No manual pinning.** Leaving it out is a bet. Producers sometimes batch by circumstance, like a studio day, and this bet may be wrong.

## Build effort

About **2–3 developer-weeks**:

| Work | Time |
| --- | --- |
| `queue.ts`, tests and API | 3–4 d |
| Planning extension | 1 d |
| Queue page, row-job binding, split/moved-on | 4–5 d |
| Strip, Module header, unit table, topic bar | 2 d |
| Nav, History, polish | 2 d |
