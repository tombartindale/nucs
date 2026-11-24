# Plan: module Claude Projects under version control, and NUCS in Claude Desktop

Branch `mcp-desktop`. It is not merged or deployed. Status was last updated on 2026-11-20.

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
| 5a | Current Project instructions, templates and skills brought into `authoring/` | Done; `authoring/instructions.md` is now generic (`{{module}}`, `{{module_name}}`, `{{units}}`) | `41b6bf4`, `184f21e` |
| 5a | `bcn` aligned with the prompt: brief headings, course-map table, timings, image paths, no unit block | Done | `dec0806`…`aa7e406` |
| 5b/6 | `bcn project` (pack plus version, `--new` creates a module), `SETUP.md`, `claude_project` in status, empty modules listed, the Add-module dialog, the Claude Project dialog, the stale badges | Done | this commit |
| 5b | The `nucs-pack` stamp in the assistant's output, and the stamp check in intake | **Not started** | |
| – | Module developers using the extension | Designed, not built (see `docs/claude-desktop.md`) | |

### Not yet verified
- Installing the `.mcpb` in Claude Desktop end to end.
- The Docker image build in CI, and the contract tests against main.
- On the live server, `programme.toml` may override the new defaults: `[documents].assignment_headings`, `[qa]` minutes, and `[validate].narration_max_words`.

### Open questions
- **Asset requests.** Should NUCS track them itself, as a per-topic tick list or as something that blocks render? The template is kept, but "we should not need it".
- **Topic template.** Its example narration says "here on the left", which fails the deictic check, so it needs rewording.
- **Other modules.** Another module's instructions are needed to confirm that only the module code (and unit count) differs between Projects. `bcn project` assumes so.

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
- **`authoring/instructions.md`** is the interview prompt, made generic from the KV7015 Project's. Placeholders:
  - `{{module}}`: the module code;
  - `{{module_name}}`: the code, plus the title in brackets when one is given;
  - `{{units}}`: the number of units.
- **`authoring/templates/`** holds the canonical templates. Their worked examples are written for KV7015, and that code is replaced with the module's own code. `tooling/tests/test_templates.py` keeps them passing `bcn`'s checks.
- **`authoring/reference/skills/`** holds `srt-script-align` and `md-embedded-image-extract`.
- **The Docker image** carries `authoring/` at `/app/authoring`. `BCN_AUTHORING` overrides the location.
- **Not done:** a `{{rules}}` placeholder rendered from config (forbidden words, the deictic list, word counts, headings), and a per-module `authoring.md` for notes.

### `bcn project <module|root>` (done)
- **Output.** It writes `<module>/build/claude-project/`, containing:
  - `instructions.md`;
  - `knowledge/*.md`;
  - `SETUP.md`, the producer's checklist;
  - `pack.json`, which records the version, units, title, files and producer documents.

  It also writes `<module>/build/claude-project.zip`, which holds the templates.
- **Version.** `version` is a short hash of the rendered instructions and templates.
- **Unit count and title** come from, in order:
  1. the arguments;
  2. the last pack;
  3. the course map;
  4. a default of 10 units.
- **`--new KV7023 [--title …] [--units N]`**, run against the programme root:
  - creates the module folder;
  - refuses a code that exists, comparing case-insensitively (`MODULE_EXISTS`), or a malformed code (`MODULE_CODE_INVALID`);
  - creates no `course-map.md`.
- **`bcn status`**:
  - now lists every module folder, including an empty one;
  - adds `claude_project: {exists, version, current, stale, …}` to each module.
- **Server:**
  - `POST /api/modules` (admin only) runs `project --new`;
  - `new` is stripped from plain `/api/jobs` requests;
  - the job is labelled `add module KV7023`.
- **UI:**
  - **Add module** on the Programme page (admin only) asks for the code, title and units, then opens the new module with the Claude Project dialog showing;
  - the module page's **Claude Project** button opens a checklist for the Project. It gives the name to copy, the instructions to copy or download, the templates zip, the producer's three documents, and a share step. It also has an update button;
  - "Claude Project out of date" badges appear on the module page and the Programme page.

### Stamp check (to do)
- The instructions would tell the assistant to start every output with `<!-- nucs-pack: <version> -->`.
- Intake would read and strip that comment.
  - New codes: `INTAKE_PACK_STALE` (warn) and `INTAKE_PACK_MISSING` (info).

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

## Verification
- **Written:** `tooling/tests/test_project.py` and `ui/server/test/modules.test.ts`.
- **Still to write, with the stamp check (`test_intake.py`):**
  - stale and missing stamps give the right codes;
  - the stamp is stripped;
  - an unstamped paste behaves as today.
