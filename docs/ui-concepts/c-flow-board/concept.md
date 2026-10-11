# Concept C: Pipeline Flow Board

**One sentence:** The landing screen is a flow board in which work moves left to right toward delivery. EN and ZH are swimlanes, each column boundary is a *gate* that carries the action that moves work across it, and anything stuck is pulled up onto a red "needs you" shelf, so the producer can see where work is piling up, what one click will move, and what only they can unblock.

Mockup: `mockup.html` has two screens, the programme board and a module-filtered board with a unit trace and a cell drawer.

## Landing screen and why

The board has one row per lane and one column per stage, with Packaged on the right, which is delivery.

- **Cells** show a count, an amber "↻ n stale · rebuild" bar when some are stale, and a stack of **cards**. With no filter a card is a *module* (KV7015 · 14). With a module filter it is a *unit* (U07 · 4), because units are what planning.ts schedules and what gets recorded in one session.
- **Cards are sorted by slack**, least first. Slack is the planning.ts deadline minus today: a unit's scripts or recording deadline for EN up to Rendered, and the module `delivery_date` after that. When slack is negative the card gets a red edge. A ⏱ badge appears when the oldest item has sat in that column for 14 days or more.
- **Gates** sit in the gutter between columns and come in three kinds. A ⚙ machine gate shows "38 ready · [Render 38]". A ✋ human gate shows "96 await camera · next: KV7015 U07 −1d · [Sessions…]". A ⇢ external gate shows "88 out · oldest 26d · [Import return…]". The ready count is the number of topics in the left-hand column whose `lang.next` equals the gate's step and which are neither blocked nor stale.
- **WIP limits** are a per-column UI preference. A column over its limit gets a hatched header ("96 ▲ over 60"), mainly so the recording bottleneck can't be ignored.
- **"Needs you" shelf**: a red row above each lane. Blocked topics, unplaceable cues and unproofread mis-transcriptions are taken *out* of the flow and placed above the column where they're stuck, with a verb on each item (place, review, fix, message).

Why a board? The stage-by-lane matrix shows *where* the work is, the gates give *the next action*, and slack sorting plus the red shelves give *the order*. The board also exposes a bottleneck that the numbers hide: in the fake data, 96 topics are Rendered and waiting for the camera, so the constraint is recording sessions and not tooling.

### Solving the EN/ZH stage mismatch

EN has 7 stages and ZH has 6. I **right-align the ZH lane** so that both lanes end at the same delivery edge, and I **split ZH "Not sent" into two columns** using bcn's own `zh.next`: `null` means *waiting on EN* (greyed, with a dotted dependency gate, "unlocks itself"), and `translation_export` means *ready to send*. Both lanes then have 7 columns at the same distance from delivery, so "rightmost first" works for both languages at once. The split comes straight from `next`, so the UI computes no eligibility.

## Information architecture

| Today | Flow Board |
|---|---|
| Programme | **Board** (landing). KPIs become five numbers (need you / ready at gates / awaiting camera / with translator / packaged). The schedule card becomes module filter chips ("KV7015 14 Oct · 1d behind"). |
| Module page | The **board filtered to the module**, with "Grid" (today's grid) and "Plan" (delivery date, owner, ICS) tabs. Bulk actions move onto gates and the cell drawer. |
| Translation | **Removed.** Export and import become ZH gates, the translator message goes on the shelf item, and export history moves to Transfer. |
| Jobs | A **bottom tray** (running jobs, queue order), with the full page kept behind "all jobs". |
| Verification | Kept as the fix-by-code workshop, which shelf items deep-link into. |
| Topic | Unchanged, with a breadcrumb back to the board cell. |

Nav: **Board · Verification · Transfer · Jobs**, plus Settings.

## How "what next, in what order" is computed

There is no new scheduling model, just a rule printed on the board:

1. **Shelf first, right to left.** Shelf items are closest to delivery and nothing moves until a person acts on them.
2. **Fire every ⚙ gate that has ready work, rightmost first.** Each gate enqueues `bcn <next>` on its ready set, sorted least-slack-first, and the jobs run while the producer does step 3.
3. **Spend your own hours on ✋ gates.** The top card in the column before a gate is the next session or the next script batch to write.

Inputs: `bcn status` (`stage`, `next`, `blocked`, `blockers`, `stale`, `unreviewed_mistranscriptions`, `hydration`) and planning.ts (`tasks[].deadline`, `deliveryDate`, `daysBehind`). The UI groups, counts and sorts, and makes no pipeline decisions.

## Status at each level

- **Programme:** the board with module cards, plus the KPI strip and module chips showing on-track or days behind.
- **Module:** the same board filtered, with unit cards that each show their planning task ("rec 10 Oct −1d"), and the Grid tab.
- **Unit:** **unit trace.** Clicking a unit card outlines it in every cell of both lanes, with a caption ("session held, 4 of 8 masters in, ZH locked"). Units are normally spread across columns, and the trace makes that visible.
- **Topic:** clicking a cell's count opens the **cell drawer**: its topics grouped by unit, with EN/ZH pips, age in column, and next step or reason for being stuck, plus the gate action scoped to the cell. A row opens the Topic page.

## How blocked, stale and human-needed items surface

- **Blocked** (error diagnostics, assets.md requests, cloud-only, sync conflicts), plus **unplaceable cues** and **unreviewed mis-transcriptions** go on the shelf, shown in red. These are the only red elements on the board apart from negative slack.
- **Stale** is shown in amber inside the cell, with its own rebuild action. It isn't waiting on a person, but it must never look done.
- **Ageing** (⏱) is shown in text without colour, because age alone isn't a failure. External gates show their oldest age ("out 26d").

## Workflows

**Monday morning.** Open the Board. The KV7015 chip is red (1d behind). Reading the shelves right to left:
1. ZH Returned has "KV7015 ×4 parity fails". Click *message* and copy the text to the translator.
2. EN Cued has "KV7013 ×2 to proofread". Click *review* to open the Mis-transcriptions tab filtered to those items, and spend ten minutes on them. This also unlocks their ZH export.
3. EN Recorded has 2 unplaceable cues. Open each at its boundary and use *set to playhead*.

Then fire the ⚙ gates, rightmost first: Package ZH 12, Render ZH 32, Check parity 15, Export 56, Package 61, Run cues 43, Render 38, Validate 54. Each one confirms its count and targets first, and the tray shows the queue in slack order. Finally, the ✋ Record gate says the next session is KV7015 U07 (−1d), then U08. Book them.

**Recording session for KV7015 U08.** Filter to KV7015 and click the U08 card. The trace shows all 8 topics in Rendered and nothing on the shelf. From the drawer, run *Recording scripts* (`bcn script KV7015/U08`) and open the teleprompter. After the session the editor's masters arrive by Sync and the cards move into Recorded topic by topic, until the gate reads "8 ready · Run cues 8". Any cue that can't be placed lands on the shelf above Recorded.

**A translation batch came back.** Click *Import return…* on the ZH lane's ⇢ Translator gate and drop the zip. Cards move into Returned, and failures go straight onto the shelf above it ("U03 ×4 slide count ≠ EN") with *message*, which produces the one combined note for the translator. The clean topics show at the next gate as "17 ready · Check parity".

## Data gaps

1. **Time in column.** Deriving it from step `time` in the UI would be pipeline logic, so `bcn status` should emit `stage_since` per language, plus `exported_at` from `translation.json`.
2. **ZH deadlines.** planning.ts gives no due date for `translated` or `packaged`, so ZH cards fall back to module delivery for slack, which is optimistic. A translator-turnaround setting (e.g. 10 days) would let the export deadline be scheduled backwards from delivery.
3. **Unit tasks across modules.** `/api/plans` returns only milestones and needs to return `tasks[]` as well, which is a small change.
4. **Post-recording estimates** don't exist (cues, packaging, editor turnaround), so slack after Recorded is coarse.
5. **WIP limits** are new UI preferences, which the spec allows.
6. **Flow history** (throughput, cumulative flow) would need status snapshots, which the spec discourages, so it is left out.

## Reused vs new

**Reused:** the status store, planning.ts and the plan routes, `runJob` with confirm, StateChip and StagePips, the Topic, Diagnostics, Intake and Transfer pages, and the translation endpoints.
**New:** a `FlowBoard` page with a custom CSS grid (the spec allows custom CSS for grid cells), `BoardCell`, `Gate`, `HumanShelf`, `CellDrawer`, a `useFlowAggregate` composable, the unit trace, the jobs tray, the `/api/plans` extension, WIP preferences, and bcn `stage_since`.

## Risks and weaknesses

- **Aggregation hides individual urgency.** One critical topic in a module card of 20 becomes a minimum-slack number, and the drawer only helps once clicked.
- **Units don't respect stage columns.** Recording is per unit but columns are per stage, so a unit spread across 3 columns is hard to read without the trace.
- **Right-aligned columns imply a false equivalence.** EN Drafted sits directly above ZH Ready-to-send, though they have nothing in common.
- **Width.** At 14 grid tracks and about 1,300 px, the board scrolls horizontally on a laptop.
- **Programme-wide gate buttons are powerful.** "Render 38" across five modules is a big job, and confirm dialogs get clicked through.
- **Kanban habits suit teams more than one doer.** WIP limits mean less to one person, and the board answers "where" better than "exactly which step next", which concept A's queue answers more directly.
- **Slack after recording is weak** until data gap 2 is closed.

## Build effort

About 3 to 3.5 weeks for one developer who knows the codebase: board, cells and cards 5 days; gates and scoped confirm 2 days; shelves 2 days; drawer and unit trace 3 days; translation folded into gates 2 days; jobs tray 1 day; plans endpoint and preferences 1 day; bcn `stage_since` 1 to 2 days; tests and contract re-records 2 days.
