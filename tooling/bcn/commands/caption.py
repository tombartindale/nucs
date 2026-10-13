"""bcn caption: fix one subtitle cue's text in place, without touching timing.

  bcn caption <topic> --lang en|zh --cue N --caption-text "corrected line(s)"

N is 1-based, the cue's position in the SRT (positions are renumbered 1..N on every
write, so position is the only stable identity to hand). Only that cue's text changes;
start/end timestamps and every other cue are untouched, so this can never break the
Mandarin SRT's cue-count/timing parity check in bcn subtitles. The previous version of
the file is kept in .history/ (bcn edit's pattern), via fsutil.save_with_history.
"""

from __future__ import annotations

import argparse

from .. import fsutil, srt
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..tree import Target

HELP = "fix one subtitle cue's text in place"
KEEP_VERSIONS = 30


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cue", type=int, required=True, metavar="N", help="1-based cue position in the SRT")
    p.add_argument("--caption-text", dest="text", required=True, help="the cue's corrected text (may be multi-line)")
    p.add_argument("--dry-run", action="store_true", help="validate only; write nothing")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if target.level != "topic":
        raise Fail("USAGE", "caption works on one topic directory.")
    t = target.topics[0]
    lang = args.lang
    path = t.srt(lang)

    r = TopicResult(t.id, t.rel)
    env.results.append(r)

    text = args.text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    if not text.strip():
        raise Fail("USAGE", "--caption-text cannot be empty.", topic=t.id, lang=lang)

    cues = srt.parse_file(path)
    if not 1 <= args.cue <= len(cues):
        r.ok = False
        raise Fail("CAPTION_RANGE", f"Cue {args.cue} is out of range; {path.name} has {len(cues)} cues.",
                   topic=t.id, lang=lang, file=path.name,
                   hint="Reload the cue list — the file may have changed since it was fetched.")

    old_bytes = path.read_bytes()
    target_cue = cues[args.cue - 1]
    if target_cue.text == text:
        r.extra.update({"written": False, "cue": args.cue})
        r.ok = True
        return

    target_cue.text = text
    new_bytes = srt.to_srt(cues).encode("utf-8")

    if args.dry_run:
        r.extra.update({"written": False, "cue": args.cue, "preview": text})
        r.ok = True
        return

    fsutil.save_with_history(path, old_bytes, keep=KEEP_VERSIONS)
    fsutil.write_bytes(path, new_bytes)
    r.extra.update({"written": True, "cue": args.cue})
    r.diagnostics.append(Diagnostic("CAPTION_SAVED", f"Saved cue {args.cue} of {path.name}.",
                                     topic=t.id, lang=lang, file=path.name))
    r.ok = True
