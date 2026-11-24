---
name: "md-embedded-image-extract"
description: Remove base64-embedded images from Markdown files and replace them with a relative file path, saving the decoded image to disk. Use whenever the user pastes or uploads a .md file (lecture topic files, KV70xx topic scripts with "Say:" blockquotes, slide notes) containing `![...](data:image/png;base64,...)`, or says "replace the embedded image with a path", "strip the inline images", "the markdown is huge because of the image", or "extract the image from this md". Use it even if they only say "swap the image for a path" or the file is too long to read because of a giant base64 string.
---

# Markdown embedded-image extraction

Turns `![alt](data:image/png;base64,AAAA...)` into `![alt](assets/fig-<num>.png)` and packs the cleaned Markdown plus the decoded images into a zip. Nothing else in the document changes: alt text, "Say:" blocks, headings, front matter and line endings are preserved byte for byte.

## Step 0: where is the Markdown?

Check `/mnt/user-data/uploads` first. Large pastes are sometimes attached as a file; if the `.md` is there, use the script on it directly (below).

If it is only pasted text (the usual case), follow **Pasted text** further down.

## Running the script (file on disk)

```bash
python scripts/extract_images.py INPUT.md --zip /mnt/user-data/outputs/KV7015-U03-T06.zip
```

- **Deliverable is a zip**, laid out `KV####/U##/T##/` with the cleaned `.md` (named `<topic_id>.md`, because uploads arrive with mangled names like `topic__1_.md`; `--keep-name` keeps the original) in the `T##` folder and the images in `T##/assets/`:

  ```
  KV7015/U03/T06/KV7015-U03-T06.md
  KV7015/U03/T06/assets/fig-1.png
  ```
- Folder names come from the front-matter `topic_id` (`KV7015-U03-T06`). If it is missing, pass `--topic-id`; the script stops rather than guessing.
- Links are **relative to the Markdown file**, written as `assets/fig-<num>.<ext>` (no leading slash), numbered from 1 in document order (even for one image); `--link-prefix` changes the prefix.
- Every PNG is integrity-checked (chunk CRCs, IEND, inflate) before anything is written. A corrupt image aborts the run with the image number.
- The input file is never modified. `--dry-run` lists the zip contents without writing.
- Present the zip with `present_files`. It holds both the Markdown and the images.

## Pasted text (the usual case)

The pasted Markdown exists only in the conversation, so it would have to be copied to disk before the script can run. That means reproducing every base64 character exactly, and a single wrong character corrupts a PNG. In practice this is only reliable for **tiny** images, because long base64 strings cannot be copied faithfully (a real test with chart-sized images, 1600 px wide, was cut off by the output limit). Do not pretend otherwise.

1. **Size it up.** Count the `data:image` links and judge each base64 string's length. Chart, screenshot and puzzle images are far too big. Only small icons or diagrams (each under roughly 8,000 base64 characters) are worth copying.
2. **Small images:** write the full pasted Markdown to `/home/claude/<topic_id>.md` verbatim, base64 included, in as few tool calls as fit, then run the script with `--zip /mnt/user-data/outputs/<topic_id>.zip`. The built-in integrity check will refuse a corrupted PNG and name it. Re-copy only that image and retry, at most twice per image. Present the zip, and say the check proves the PNGs are intact, not that they match the originals (in practice a copy that passes every CRC is exact).
3. **Anything bigger (the normal case here): do not attempt the copy.** Instead:
   - Write the cleaned Markdown yourself: the pasted text exactly as given, with each `data:` link replaced by `assets/fig-<num>.png` (alt text untouched, numbered in document order).
   - Build the folder skeleton and zip it, with an empty `assets/` folder ready for the images:
     ```bash
     mkdir -p KV7015/U04/T03/assets && cp cleaned.md KV7015/U04/T03/<topic_id>.md
     zip -r <topic_id>.zip KV7015
     ```
   - Present the zip and say plainly: the Markdown is cleaned and the folders are in place, but the images could not be extracted from pasted text because they are too large to copy reliably. List the files the user needs to drop into `assets/` (`fig-1.png`, ...) with a one-line description of each from its alt text. If they upload the original `.md` (attach it instead of pasting), the script will produce the full zip, images included.

## Rules

- Keep alt text exactly as written. Never shorten or rewrite it.
- Never guess a project's path convention silently: if the user has stated one, use it; otherwise use the `assets/fig-<num>` default and say so.
- Only `data:image/...;base64` links are touched. Ordinary image links are left alone.
- If decoding fails the script aborts without modifying the file; report that rather than patching around it.
- Do not widen scope (reformatting, "fixing" the text) unless asked.
