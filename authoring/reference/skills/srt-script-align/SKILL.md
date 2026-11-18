---
name: "srt-script-align"
description: Correct an auto-generated SRT subtitle file so its text matches the original script, keeping all timestamps. Use whenever the user uploads an .srt (or mentions captions, subtitles, transcript clean-up, ASR errors, Whisper/Teams/Stream/video-editor sidecar SRT) together with a script, markdown lesson file, or narration text and wants the subtitles fixed, aligned, matched to the script, or cleaned up. Also use for KV70xx topic files with "Say:" blockquotes plus a *_mp4.srt. Do not use for translating subtitles or re-timing them.
---

# SRT → script alignment

Fixes speech-to-text mistakes in an SRT by replacing cue text with the script's wording while leaving every cue number and timestamp untouched.

## With NUCS connected (Claude Desktop with the NUCS extension)

If the NUCS tools are available, use NUCS as the source and the destination, so the user handles only the Premiere SRT:
- **Script:** get it with `get_topic_source(topic)` instead of asking for the markdown. It is the validated script exactly as NUCS holds it, including edits made after filming. Write it to a file and run the script on it as below.
- **Edit SRT:** the user attaches the Premiere export as usual.
- **When the user is happy with the corrected SRT:** ask them to save it (to Downloads, or another folder the extension may read), then call `upload_subtitles_from_disk(topic, path)`. It uploads the file byte for byte (BOM and CRLF kept) as the topic's recorded SRT. If NUCS already holds a different SRT for the topic, nothing is replaced. Say so, and only call it again with `replace: true` once the user has agreed.

Everything below is unchanged.

## Inputs
1. The script. For topic markdown files, only the `> **Say:** ...` blockquotes are the spoken script (headings, bullets and image lines are slide content, not narration — ignore them). Blockquotes may wrap across several `>` lines; words hyphenated at a line end ("rule-\nbased") are rejoined. For anything else, use `--plain`.
2. The SRT (typically `<topic>_mp4.srt`).

If a file is uploaded but its content isn't in context, read it from `/mnt/user-data/uploads/` first.

## Run
```bash
python /mnt/skills/user/srt-script-align/scripts/align_srt.py SCRIPT.md IN.srt /mnt/user-data/outputs/OUT.srt --report /home/claude/report.json
```
(Use the skill's actual install path if different.) Keep the original SRT filename for the output unless asked otherwise. Options: `--plain`, `--say-prefix`, `--max-line 42`, `--max-drop-run 5`.

## What the script does
- Word-level fuzzy alignment of SRT words to script words; each cue gets the script words that align to it.
- Spelling, punctuation, capitalisation and numbers come from the script (e.g. "organizations"→"organisations", "200km"→"two hundred kilometres").
- Re-wraps to two balanced lines of ≤42 characters; preserves BOM and CRLF.
- Script text with no audio match (run longer than 5 words) is **dropped, not inserted**, because cramming it into a short cue makes unreadable subtitles that don't match the audio.

## Retakes
Recordings often contain false starts or a sentence recorded twice. The script drops cues that match no script wording and prints `REVIEW` lines for orphaned 1–2 word cues beside timing gaps. Where a sentence was recorded twice, word-level alignment can stitch pieces of both takes together; the fix is a cleaned SRT/re-export from the editor, not more aligning. Always list REVIEW lines and removed cues with timecodes in your reply.

## After running — always
1. Verify: cue count, and that joined cue text equals the script minus reported dropped passages.
2. Present the SRT with `present_files`.
3. Tell the user, briefly: how many cues changed; any **script text not spoken** (quote it — it means a re-take or edit is needed); and that wording where the speaker may have deliberately deviated from the script will now disagree with the audio (list a few likely candidates from the report, e.g. tense or singular/plural changes).
4. Do not alter timestamps. If a dropped passage leaves a long gap, mention it rather than retiming.
