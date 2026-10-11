# UI redesign brief (shared by all concept agents)

App: NUCS / Beacon UI, a Vue 3 + Quasar front end (`ui/app/src`) over the `bcn` CLI. It produces ~640 video-lecture topics across 8 modules, in English (EN) and Mandarin (ZH).
Read: `spec/ui-spec.md`, `README.md`, `ui/app/src/pages/*.vue` (especially ProgrammePage, ModulePage, TopicPage, DiagnosticsPage, JobsPage, TranslationPage), `ui/app/src/layouts/MainLayout.vue`, `ui/shared/index.d.ts`, and `ui/server/src/planning.ts`.

## Goals (from the product owner)
1. Better visibility of the **overall status of work**.
2. Better clarity on **what actions need to be taken, when, and in what order**.

## Decisions already made by the owner. Design to these and don't reopen them.
- **Primary user: the producer/PM, who mostly does the work themselves**: running bcn jobs, fixing diagnostics, placing cues, accepting mis-transcriptions, writing scripts, recording. Not mainly a "chase other people" or reporting tool.
- **Order is driven by deadlines.** Deadlines already exist: each module has a `delivery_date` + owner (DB table `module_plans`, set in the Module page Plan tab). `planning.ts` back-schedules per-unit tasks (write scripts → record, one recording session per unit) and module milestones (briefs_done, recorded, translated, packaged), with `onTrack` / `daysBehind`. There's also an .ics feed and an email digest. Build on this real data and don't invent a parallel scheduling model. If your concept needs data that doesn't exist (e.g. ZH dates, per-stage estimates), say so explicitly as a "data gap".
- **Mandarin is equal to English**: a parallel track that's just as important to see, not a footnote.
- Hard constraint from the spec: the UI holds no pipeline logic. State comes from `bcn status` envelopes. Stage model: EN Planned→Drafted→Validated→Rendered→Recorded→Cued→Packaged; ZH Not sent→Out→Returned→Parity checked→Rendered→Packaged; plus **Stale** and **Blocked** overlays (blocked = needs a human).
- Stack stays Quasar/Vue. No Tailwind.

## Deliverables (write only inside your folder, `docs/ui-concepts/<your-concept>/`; never edit app code)
1. `concept.md` covering:
   - the one-sentence idea
   - the landing screen and why
   - an information architecture/navigation change (what pages merge, split or disappear vs today's nav)
   - how "what next, in what order" is computed from existing data
   - how status is shown at programme → module → unit → topic levels
   - EN/ZH treatment
   - how blocked/stale/human-needed items surface
   - 2–3 key workflows walked step by step (e.g. "Monday morning, what do I do", "a recording session for unit U03", "a translation batch came back")
   - data gaps
   - what's reused vs new
   - risks and weaknesses of your own idea (be honest)
   - a rough build-effort estimate
2. `mockup.html`: a single self-contained static HTML file (inline CSS, no external deps, plausible fake data drawn from `example/`) showing the landing screen and at least one drill-down screen. Lo-fi to mid-fi is fine, but it must make the idea legible at a glance. Use light-neutral styling. Colour carries meaning only for stale/blocked/overdue.

Keep concept.md under ~1500 words. Be opinionated. Your concept should be clearly distinct from the other two directions (listed in your prompt).
