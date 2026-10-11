# Concept B: Delivery Runway

**The idea in one sentence:** the landing screen is a timeline. Each module is a lane that runs to its delivery date, built from the tasks `planning.ts` already back-schedules, so lateness, float and clashes show up as shapes on a calendar, and what to do now is whatever the today-line crosses.

Mockup: `mockup.html`, a single file with three screens. The checkbox in its top bar previews the proposed ZH schedule.

## Landing screen: Programme runway

From top to bottom:

1. **Headline row**: modules behind, modules with ≤3 days of float, days double-booked across modules, ZH lanes still undated, and modules with no delivery date. These replace today's four numbers. Topics complete and jobs running move down a level or into the jobs tray.
2. **"Your days" load strip**: script hours per day summed across modules against the 3 h/day budget, plus one ● per recording session. `planning.ts` plans each module as if it were the only one, so this is where two "on track" modules turn out to need the same day (13 and 22 Oct in the mockup).
3. **Module lanes, least float first.** Each lane has an EN sub-lane and a ZH sub-lane:
   - Unit **scripts** batches are light bars. Unit **recording** sessions are dark one-day blocks. Both come straight from `UnitTask.deadline`.
   - A solid ◆ marks delivery. A hollow ◇ marks the projected finish, which is delivery minus float. The bracket between them is the float ("float 11d"), or a red "3d late".
   - Units that are already recorded show as small ticks on the date `edit/master.mp4` was written.
   - A blocked (▲) or stale (◆) flag sits on the batch that contains the affected topic. That shows when the problem will start to hurt, not only that it exists.
4. **Undated modules** sit in their own group, drawn as "if started today": the same estimates walked forward from today. The result is "~15 days of work, earliest delivery Mon 26 Oct", with a **set date** link next to it. They are not counted in the load strip, and the strip says so.
5. **Under the today-line**: a short list of what the line crosses, plus any lane whose window has already closed, kept in lane order. It is there to help read the chart, not to replace it.

**Why a timeline:** the owner chose deadlines to drive the order, and that data already exists, but today it is hidden in a collapsed panel on each module page. A timeline also shows what a list can't: no time left for post-production, two modules wanting the same day, and an "on track" module whose ZH batch has been out for 26 days.

## Information architecture

| Today | Runway |
|---|---|
| Programme (stage ladder + KPIs) | **Runway** (landing). The stage ladder moves into the module Grid tab |
| Module page (planning panel + grid + documents) | Module page with tabs **Runway** (default) · **Grid** (today's grid and bulk actions, unchanged) · **Documents**. Delivery date and owner are edited in the header; the .ics link and email digest become header buttons |
| (none) | **Unit runway** `/module/:m/unit/:u`: new |
| Topic | Unchanged. TopicHead gains one line: "in U05 session Fri 23 Oct · needs validate" |
| Verification, Translation | Stay. Flags on the runway deep-link into them with the scope filled in |
| Jobs (top-level tab) | Becomes a tray in the header, since jobs are feedback on actions taken from the runway. `/jobs` still exists |
| Transfer, Settings, Admin | Overflow menu |

Top nav: **Runway · Verification · Translation · Intake**.

## How "what next, in what order" is computed

All dates come from `computeModulePlan`; the UI only places them on a calendar. The useful property is that **the chain is strictly serial**: one producer, one unit at a time, walked backwards from delivery. So:

- **Every outstanding task is on the critical path**, and all of them share one float: `earliestDeadline − today`. `daysBehind` is just the negative of that number. I'd add `floatDays` to `ModulePlanComputed` (a one-line change) so the UI doesn't have to work it out.
- **Order across modules**: lanes sorted by float, smallest first. **Order within a module**: the leftmost outstanding bar, which is course-map order.
- **Now** = bars whose window the today-line crosses, plus bars to the left of the line (overdue, in red). Rule of thumb: *work the leftmost bar in the top lane, unless a ▲ sits on a bar within two weeks*. A blocked topic can't be estimated, so it comes first.
- A date the producer drags (the delivery ◆) is previewed (the float re-draws) before it is saved to `module_plans`.

## Status at each level

- **Programme**: lane shape (float or lateness), ▲ and ◆ counts, and the ZH sub-lane.
- **Module**: one row per unit, with pips for written and recorded topics. `briefs_done` and `recorded` are drawn as lines at their `dueDate`. `translated` and `packaged` show as counts, because they have no date. A "whole module" row carries the float bracket.
- **Unit**: one row per topic with what it still needs before the session (**write → validate → recording script**), taken from `bcn status`. Remaining effort is docked against the scripts deadline, with a dotted float band to its left. A **session sheet** holds the date, minutes, ready count and unit-scope actions.
- **Topic**: today's page.

Colour only means overdue, blocked (red) or stale (amber). Everything else is grey, and stage is shown by position.

## EN / ZH

ZH has a sub-lane at every level, with the same space as EN. Today it can only show **actuals**: batches out with the translator (bars from export date to today, "U01–U04 out 26d"), returns with parity failures marked ▲, units ready to send, and a dashed "?" where the return date should be. Each lane label carries a **ZH undated** badge, which is the honest state of the data. Gap 1 below makes ZH a scheduled lane.

## Blocked, stale, needs a human

Each is pinned to a point in time rather than listed separately. Pre-recording problems (validate errors, assets) sit on the unit's recording bar. Post-recording ones (cues, mis-transcriptions, stale packages) sit on its post-production tail. ZH parity failures sit on the ZH sub-lane at today, and cloud-only files hatch the lane label. Every flag links to Verification with the scope set.

## Workflows

**Monday morning.** The headline reads "1 behind, 3 days double-booked". The top lane, KV7016, is 3 days late, with red bars left of the line. Either drag its ◆ (the float redraws as a preview) or start on the leftmost bar. The load strip shows two sessions on Tue 13 Oct (KV7016 U08, KV7018 U03), so move one. Open KV7016 U06, where ▲2 sits in an overdue session, fix it in the editor and validate. The flag clears.

**Preparing KV7015 U05's session (Fri 23 Oct).** 2 of 10 topics are ready. T04 is red (1,180 words, max 960): open the editor. T03's recording script is amber (older than `topic.md`): run Recording script for U05. T07–T10 need writing: use Intake. On the day, start the Teleprompter from the session sheet. When `edit/master.mp4` arrives, the block becomes a recorded tick and a post-production tail appears.

**A translation batch comes back.** KV7020's ZH lane shows "U01–U04 out 26d". Import the zip on Translation (the lane links there, already scoped). The out-bar stops at the return date, and parity failures appear as ▲ at today, one click from the existing "back to the translator" message.

## Data gaps (and proposed fixes)

1. **ZH has no dates.** `translated` and `packaged` have `dueDate: null`, and the last EN recording is scheduled for *delivery − 1*, leaving **0 days** for edit, cues, packaging and all ZH work. KV7020 reports on track (no EN tasks) while four batches have been out 26 days.
   **Fix:** add `edit_days` (default 3) and `zh_turnaround_days` (default 10) to `module_plans`. `computeModulePlan` then anchors the EN chain at `delivery − tail` (edit + turnaround + 1 day fix + 1 day package) and derives per unit: export by recording + edit, return due at export + turnaround, then ZH packaged. That fills in the `translated` and `packaged` due dates and adds ZH events to the .ics feed. It extends the existing model rather than adding a parallel one. The mockup's toggle previews it: KV7015 goes from 11 days of float to 4 days late, and three modules' ZH lands after delivery.
2. **Export date per topic.** `translation.json` records exports, but `bcn status` only reports the stage. Add `zh.exported_at` (and `returned_at`). The mockup falls back to export zip modification times.
3. **Cross-module capacity.** `/api/plans` drops `tasks`. Add them so the load strip can be computed on the server.
4. **No working calendar.** Sessions land on Sundays (KV7015 U06, Sun 25 Oct). An optional working-days mask would fix this.
5. **"Ready to record" ≠ "drafted".** The plan counts a drafted script as done. Recording really needs it validated, not stale, and with a current recording script. Ask `bcn status` for a `ready_to_record` flag rather than deriving it in the UI.
6. **Booked sessions and actual dates.** There is nowhere to store a booked session date (later: a pinned date the walk treats as fixed). Actual recording dates come from file modification times, which sync can rewrite: fine for ticks, not for audit.

## Reused vs new

**Reused:** `planning.ts` (all the date maths), `module_plans`, the .ics feed and digest, `bcn status` data, and the Grid, Topic, Verification, Translation, Intake and editor pages with their job and confirm flows.

**New:** one CSS-positioned timeline renderer used at three zoom levels (spec §2.1 allows custom CSS for timelines), the Runway landing, the module Runway tab, the unit route and session sheet, the jobs tray, and in `planning.ts`: `floatDays`, tasks in `/api/plans`, and the ZH tail.

## Risks and weaknesses

- **False precision.** The plan is crude: 20 minutes per script, 3 h/day, no weekends, one producer. A chart makes those guesses look authoritative. The assumptions have to stay visible.
- **ALAP drawing invites procrastination.** Everything is drawn as late as possible, so float looks like spare time. The projected ◇ helps, but only partly.
- **Weak below unit level.** Topic work isn't really scheduled, so the unit runway is effectively a checklist pinned to a date. For actually doing the work, a queue (Concept A) is better.
- **Undated problems feel secondary.** Document errors, QA findings and theme problems have no natural place on a timeline.
- **Density.** At 12 weeks, scripts batches are slivers, so labels move into tooltips, and the chart needs horizontal scrolling on small screens.
- **The ZH fix changes the headline numbers.** Modules will flip from on track to behind the day it ships. It's accurate, but it should be announced first.

## Build estimate (one developer)

| Work | Days |
|---|---|
| `planning.ts`: `floatDays`, tasks in `/api/plans`, tests | 2 |
| Timeline renderer (axis, bars, flags, today-line, zoom) | 6–8 |
| Programme runway, load strip, today slice | 3 |
| Module tab reorganisation, header plan editing | 3 |
| Unit route and session sheet | 3–4 |
| Nav changes and jobs tray | 2 |
| ZH tail (migration, planning, .ics), and re-recording contract fixtures | 4–5 |

That is about **3 weeks without the ZH fix and 4–5 weeks with it**.
