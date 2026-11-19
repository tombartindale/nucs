# Plan: module Claude Projects under version control, and NUCS in Claude Desktop

Branch `mcp-desktop`. It is not merged or deployed. Status was last updated on 2026-11-19.

## Status

| # | Item | State | Where |
|---|---|---|---|
| 0 | Mandarin QA review, moved here from main (untested at the time) | Done; the tests pass | `df693cb` |
| 1 | Personal API tokens and the `/mcp` endpoint with read tools | Done | `00fc9c4` |
| 1 | Claude Desktop extension (`.mcpb`), a generic proxy | Done | `e3cf271` |
| 1 | "Connect Claude Desktop" dialog | Done | `6609101` |
| 2 | QA write tools, the `via Claude` chip, the `review_qa_item` and `reconcile_module` prompts | Done | `00fc9c4`, `6609101` |
| 3 | Images embedded as `data:` URIs extracted in `bcn intake` | Done, and hardened (PNG integrity, matching by content) | `d8107f3`, `184f21e` |
| 3 | `intake_check`/`intake_submit`, `import_from_disk`, the `import_tutor_content` prompt | Done | `00fc9c4`, `e3cf271` |
| 3+ | `upload_subtitles_from_disk` (the result of the `srt-script-align` skill becomes the recorded SRT) | Done | `9cd6407` |
| 4 | `docs/claude-desktop.md`: setup, tools, the developer-extension design | Done | `34ed63d` |
| 5a | Current Project instructions, templates and skills brought into `authoring/` | Done (KV7015 is the reference copy) | `41b6bf4`, `184f21e` |
| 5a | `bcn` aligned with the prompt: brief headings, course-map table, timings, image paths, no unit block | Done | `dec0806`…`aa7e406` |
| 5b | `bcn pack` (Project pack plus `pack_version`), the stamp check in intake, `pack` in status | **Not started** | |
| 6 | `bcn module --new`, `SETUP.md`, the Add-module UI, the Claude Project card, the stale badge | **Not started** | |
| – | Module developers using the extension | Designed, not built (see `docs/claude-desktop.md`) | |

### Not yet verified
- Installing the `.mcpb` in Claude Desktop end to end.
- The Docker image build in CI, and the contract tests against main.
- On the live server, `programme.toml` may override the new defaults: `[documents].assignment_headings`, `[qa]` minutes, and `[validate].narration_max_words`.

### Open questions
- **Asset requests.** Should NUCS track them itself, as a per-topic tick list or as something that blocks render? The template is kept, but "we should not need it".
- **Topic template.** Its example narration says "here on the left", which fails the deictic check, so it needs rewording.
- **Other modules.** Another module's instructions are needed to confirm that only the module code differs between Projects. Until then a new module's Project is made by copying the KV7015 reference and replacing the module code.

### Running it locally
See the comment in `docker-compose.mcp-test.yml`, which is local only and not committed. It runs the branch as compose project `nucs-mcp` on :8421, beside the main dev stack.

## Context

The module developers write content by being interviewed by a Claude Project, one per module. Each Project holds a module-specific prompt and copies of the output templates. These live only in claude.ai, so:
- they aren't versioned;
- they drift between modules;
- nothing ties a pasted unit back to the prompt that produced it.

Constraints:
- The producer uses **Claude Desktop on an Enterprise account**. There is no Claude Code.
- The producer can't add or approve org connectors.
- The producer does control Projects' instructions and knowledge files.
- The developers' experience must not change.
- Existing `bcn` envelopes, diagnostic codes and `build/` outputs must not break.

Decisions made along the way:
- **MCP through a local extension, not a skill calling the API.** A skill runs in Claude's cloud sandbox, which needs admin-controlled egress to reach NUCS. It would also put tokens in the chat and couldn't read local files.
- **Subtitle correction stays in Claude.** It is a back-and-forth with the `srt-script-align` skill, so NUCS only supplies the script (`get_topic_source`) and accepts the result (`upload_subtitles_from_disk`).
- **The validator follows the prompt in use.** Assessments haven't been designed yet. The course map keeps its tables, because `bcn` reads them.
- **Topics arrive as separate markdown files,** never as one unit block.

## A. Project spec in git

### Layout
- **`authoring/reference/KV7015-project-instructions.md`** is the prompt in use, kept as the reference copy.
  - The generic `authoring/instructions.md` with placeholders (`{{module}}`, `{{module_title}}`, `{{pack_version}}`, `{{rules}}`, `{{module_notes}}`) comes with `bcn pack`.
  - `{{rules}}` would be rendered from the effective config: forbidden words, the deictic list, word-count tolerance, required headings, activity types.
- **`authoring/templates/`** holds the canonical templates. `tooling/tests/test_templates.py` keeps them passing `bcn`'s checks.
- **`authoring/reference/skills/`** holds `srt-script-align` and `md-embedded-image-extract`.
- **Per-module extras** live in the programme root, not git: `KVxxxx/authoring.md`, for the paired module, assessment components and deadlines, and notes.

### `bcn pack <module|root>` (to do)
- Writes `transfer/exports/<stamp>-<module>-project-pack.zip`, containing `instructions.md`, `knowledge/*.md` and `SETUP.md`.
- `pack_version` is a short hash of the rendered instructions plus the templates.
- Records what was issued in `KVxxxx/build/pack.json`.
- The instructions tell the assistant to start every output with `<!-- nucs-pack: {{pack_version}} -->`.
- Reuses `load`/`DEFAULTS` from `config.py`, and the batch naming and atomic writes from `transfer.py`.

### "Add new module" (to do)
- **`bcn module --new KV7023 --title "…"`**:
  - validates the code and refuses one that exists;
  - creates the folder and `authoring.md`;
  - renders the pack.
  - It creates no `course-map.md`, because that is the output of stage 1. `bcn status` must handle an empty module.
- **`SETUP.md`**:
  1. Create the Project.
  2. Paste the instructions.
  3. Upload the templates, plus three documents from the producer: the module descriptor, the validated learning outcomes, and the indicative reading list.
  4. Share the Project with the developer.
  5. Click "Project set up" in NUCS.
- **UI**: an admin-only "Add module" button on the Programme page, backed by `POST /api/modules`. It shows the checklist with copy buttons and the knowledge zip.

### Stamp check, status and UI (to do)
- Intake reads and strips the `nucs-pack` comment.
  - New codes: `INTAKE_PACK_STALE` (warn) and `INTAKE_PACK_MISSING` (info).
- `bcn status` gains `pack: {issued, current, stale}` for each module. It's a new key; existing keys don't change.
- **UI**:
  - a "Claude Project" card on the module page: version, stale badge, download, and an `authoring.md` editor;
  - a "Project out of date" badge on the programme grid.

### Embedded images in intake (done)
- `bcn intake` rewrites `data:` image URIs to `assets/fig-N.<ext>`.
- Images are matched by content hash, not position.
- Codes: `INTAKE_IMAGE_EXTRACTED` (info) and `INTAKE_IMAGE_CORRUPT` (error; that topic is refused).

## B. NUCS in Claude Desktop (done)

### Server: `ui/server/src/mcp.ts` and `api-tokens.ts`
- **Tokens**:
  - `nucs_…`, stored as sha256;
  - cookie-only routes to manage them (`/api/tokens`);
  - the allowlist is re-checked on every use;
  - accepted only on `/mcp`, `/api/intake/upload` and subtitle uploads to `/api/topic/:id/media`.
- **`/mcp`** is stateless Streamable HTTP. Its tools call the existing routes in-process through `fastify.inject`, with a per-process secret header. Writes are attributed to the token owner and marked `via: 'claude'`.
- **Tools**:
  - read: `programme_overview`, `get_topic`, `get_topic_source`, `read_document`, `get_subtitles`, `get_diagnostics`, `list_qa_items`, `get_qa_item`;
  - write: `add_qa_comment`, `resolve_qa_comment`, `set_qa_status`, `intake_check`, `intake_submit`, `get_job_result`.
- **Prompts**: `review_qa_item`, `reconcile_module`, `import_tutor_content`.

### Extension: `ui/desktop-extension/`
- **What it is**: a `.mcpb` bundle (manifest 0.3), built with esbuild and served at `/api/mcp/extension`.
- **What it forwards**: every remote tool and prompt, generically, so new server tools need no new extension release.
- **What it adds**: two local tools, `import_from_disk` and `upload_subtitles_from_disk`. They are confined to `import_folders`, which defaults to `~/Downloads`.
- **`user_config`**: `nucs_url`, `token` (sensitive), `import_folders`.

### Connect dialog
- **Opening it**: the robot icon in the toolbar.
- **What it offers**: the extension download, token creation (shown once) and the token list with revoke.
- **If extensions are blocked**, it offers an `mcp-remote` config snippet instead. That route has no local import tools.

## Later: module developers using the extension (designed, not built)
`docs/claude-desktop.md` has the full design:
- token roles (`producer` | `author`, plus `modules[]`);
- a submissions inbox;
- gate records;
- templates as MCP resources;
- instructions that work with or without the extension.

Pilot it with one developer on one module first.

## Verification still to write (for 5b and 6)
- **`test_pack.py`**:
  - the rules are rendered from config;
  - the version changes with templates or rules.
- **`test_module_new.py`**:
  - the folder and `authoring.md` are created;
  - a bad or existing code is refused;
  - `SETUP.md` lists the three producer documents;
  - status handles an empty module.
- **`test_intake.py`**:
  - stale and missing stamps give the right codes;
  - the stamp is stripped;
  - an unstamped paste behaves as today.
