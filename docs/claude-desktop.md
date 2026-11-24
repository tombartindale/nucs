# NUCS in Claude Desktop

NUCS can be used from the Claude Desktop app. Claude can then read NUCS, review Mandarin QA items, leave comments, and check and import module content that developers send you. It does this through the **NUCS extension**, which you install yourself. It doesn't need an organisation-wide connector, so no Claude administrator has to approve anything.

```
Claude Desktop ──stdio──▶ NUCS extension (on your computer) ──HTTPS + your token──▶ NUCS /mcp
                                  │
                                  └── import_from_disk: reads allowed folders, uploads to /api/intake/upload
```

- **What the server provides.** `/mcp` on the NUCS server ([ui/server/src/mcp.ts](../ui/server/src/mcp.ts)) offers tools that call NUCS's own API, so every existing rule applies. For example, intake never overwrites a topic that differs unless you ask it to, and only the author of a comment can delete it.
- **Who it acts as.** Everything Claude writes is recorded as you. QA comments show a **via Claude** chip, and intake jobs are labelled `· via Claude` on the Jobs page.
- **What the extension does.** The extension ([ui/desktop-extension/](../ui/desktop-extension/)) passes every server tool through to Claude unchanged, and adds one tool of its own, `import_from_disk`, because only something running on your computer can read your files. File contents go straight to NUCS and never pass through Claude.

## Setting it up

1. In NUCS, click the **robot icon** in the toolbar to open **Connect Claude Desktop**.
2. Download **nucs.mcpb**, then click **Create token**. Copy the token straight away, because it's only shown once.
3. Double-click `nucs.mcpb`, or in Claude Desktop go to **Settings → Extensions → Install extension**. Fill in:
   - **NUCS address**: as shown in the dialog, for example `https://nucs.example.org`.
   - **API token**: the token you just created. It starts with `nucs_`.
   - **Folders NUCS may import from** (optional): `import_from_disk` can only read inside these folders. If you leave it empty, it uses your Downloads folder.
4. Start a new chat and ask *"What modules are in NUCS?"*

**If extensions are turned off for your Claude account**, the dialog's *If extensions are turned off* section gives you a configuration snippet to paste into Claude Desktop's config file instead. It needs Node.js installed on your computer, and you can't use `import_from_disk` that way, so use the Intake page to import files.

**To disconnect**, revoke the token in the same dialog. Claude loses access straight away. You also lose access automatically if your email address is removed from the sign-in allowlist.

## What you can ask it

| You say | What Claude uses |
|---|---|
| "What's left to do on KV7016?" | `programme_overview`, `get_diagnostics` |
| "Import ~/Downloads/KV7016-U04 from Jane" | the `import_tutor_content` prompt: `import_from_disk` with check_only first, a summary and the diffs, and then the real import only once you agree |
| "Review video:KV7015-U01-T03" | the `review_qa_item` prompt: the English and Mandarin scripts, the Mandarin subtitles, and the existing comments, then a proposed list of anchored comments. It posts only the ones you choose. |
| "Which QA items need attention in KV7015?" | `list_qa_items` |
| "Correct the subtitles for KV7015-U01-T03" (with the edit's SRT attached) | your `srt-script-align` skill. With NUCS connected, it gets the script from NUCS (`get_topic_source`). Once you have saved the result, `upload_subtitles_from_disk` makes it the recorded SRT. |
| "Reconcile KV7015" | the `reconcile_module` prompt: the course map against the topics that were built (titles, order, minutes, outcome coverage), and assessment alignment. It changes nothing. |

The prompts are listed in Claude Desktop's **+** menu under NUCS.

### Tools

| Tool | Writes? | What it does |
|---|---|---|
| `programme_overview(module?)` | no | Modules, units, stage counts and documents. With a module, also every topic's English and Mandarin state. |
| `get_topic(topic, lang)` | no | The parsed script for each slide (title, narration, word counts), plus the cues and subtitle files |
| `get_topic_source(topic, lang)` | no | The raw `topic.md` or `topic.zh.md` |
| `read_document(path)` | no | The course map, asset requests, reading list, an assignment or an activity (English or Mandarin) |
| `get_subtitles(topic, lang)` | no | The SRT text: the aligned version if one exists, otherwise the editor's |
| `get_diagnostics(path?)` | no | The outstanding diagnostics |
| `list_qa_items(...)`, `get_qa_item(item)` | no | Mandarin QA items, their verdicts and comments |
| `add_qa_comment`, `resolve_qa_comment`, `set_qa_status` | yes | QA actions, recorded as you and marked via Claude |
| `intake_check(text)`, `intake_submit(text, replace?)` | `intake_submit` does | Intake for pasted markdown, the same as the Intake page |
| `import_from_disk(path, check_only, replace, scope?)` | when `check_only` is false | Intake for a folder, `.zip` or `.md` on your computer. This tool runs in the extension. |
| `get_job_result(job_id)` | no | Waits for a NUCS job to finish and returns its outcome |
| `upload_subtitles_from_disk(topic, path, lang, replace?)` | yes | Uploads an `.srt` from your computer as the topic's recorded SRT, byte for byte. It refuses to replace a different SRT unless you agree. This tool runs in the extension. |

Images embedded in markdown as `data:` URIs are extracted by `bcn intake` into `assets/fig-N.<ext>`, whichever route the content came in by. They're matched to existing images by content, not by position.

## For maintainers

- **Building the extension:** run `npm run build -w desktop-extension` in `ui/`. It writes `ui/desktop-extension/dist/nucs.mcpb`, which the Docker image includes and the server serves at `/api/mcp/extension`. The full `npm run build` builds it too.
- **Adding a tool:** add it in [mcp.ts](../ui/server/src/mcp.ts) only. The extension passes the server's tools through without listing them itself, so it doesn't need a new release. Keep each tool a thin wrapper over an existing API route.
- **Tokens** ([api-tokens.ts](../ui/server/src/api-tokens.ts)):
  - only the sha256 hash is stored;
  - they're accepted only on `/mcp` and `/api/intake/upload`;
  - they can't create other tokens;
  - the owner is re-checked against the allowlist each time a token is used.
- **How tools call the API:** in-process, with `fastify.inject`. Each call carries a secret generated when the process starts. A request from outside that presents that header with any other value is refused.
- **Tests:** `ui/server/test/mcp.test.ts` and `ui/server/test/desktop-extension.test.ts`. The second runs the extension over stdio, as Claude Desktop does. Both need `DATABASE_URL`; see `ui/server/test/harness.ts`.

## Later: module developers using the extension

This is designed but not built. The module developers' interview Projects run in claude.ai, and connecting those to NUCS live would need a claude.ai connector, which only an Owner of the Enterprise organisation can add. Alternatively, each developer could install this same extension in their own Claude Desktop. Local extension tools are available inside Projects in the Desktop app, so the module's interview assistant could then:
- fetch the current templates;
- check a draft against `bcn validate` before the developer approves it;
- confirm a gate was cleared, without taking the developer's word for it;
- submit an approved unit with no copy and paste.

What would need building:

1. **Token roles.** Add `role` (`producer` | `author`) and `modules[]` columns to `api_tokens`. The guard on `/mcp` (in `mcp.ts`) would filter both the tool list and the module argument by role. An author would get read access to their own modules, plus `intake_check` and a new `submit_unit`. They would get no QA writes and no `replace`.
2. **A submissions inbox.** A `submissions` table holding the submitter, the text, the intake dry-run envelope and a status, plus a tab on the Intake page where the producer accepts a submission (which runs the existing intake job) or rejects it with a note. `submit_unit` would only ever write to the inbox, never into the programme root.
3. **Gates.** Gate 1 and gate 2 records for each module, set by the producer, and a `get_gate(module)` tool.
4. **Templates as MCP resources,** served from the canonical templates in the repository, so every module's Project reads the same version.
5. **Instructions that work both ways.** The Project instructions would say: if the NUCS tools are present, check and submit through them; if not, output the unit block as today for the producer to paste. The web and mobile apps have no extensions, so that fallback is always needed.

Costs and risks to weigh before doing this:
- each developer installs the extension and sets up a token once;
- Claude Desktop asks "Allow tool?" the first time each tool is used;
- it only works in the Desktop app;
- your organisation's extension policy, or university-managed machines, may block installation.

Pilot it with one developer on one module before asking others to install anything.
