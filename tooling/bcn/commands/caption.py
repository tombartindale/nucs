"""bcn caption: fix one or more subtitle cues' text in place, without touching timing.

  bcn caption <topic> --lang en|zh --cue N --caption-text "corrected line(s)"
  bcn caption <topic> --lang en|zh --cues-json '{"12": "corrected line(s)", "47": "..."}'

N is 1-based, the cue's position in the SRT (positions are renumbered 1..N on every
write, so position is the only stable identity to hand). Only the given cue(s)' text
changes; start/end timestamps and every other cue are untouched, so this can never
break the Mandarin SRT's cue-count/timing parity check in bcn subtitles. The previous
version of the file is kept in .history/ (bcn edit's pattern), via
fsutil.save_with_history - one snapshot per invocation, however many cues it touches,
so a whole bulk-edit pass undoes as a single step.
"""

from __future__ import annotations

import argparse
import json

from .. import fsutil, srt
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..tree import Target

HELP = "fix one or more subtitle cues' text in place"
KEEP_VERSIONS = 30


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cue", type=int, metavar="N", help="1-based cue position in the SRT")
    p.add_argument("--caption-text", dest="text", help="the cue's corrected text (may be multi-line)")
    p.add_argument("--cues-json", dest="cues_json", metavar="JSON",
                   help='bulk edit: a JSON object of {"cue": "corrected text", ...} - alternative to --cue/--caption-text')
    p.add_argument("--dry-run", action="store_true", help="validate only; write nothing")


def _clean(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def _edits_from_args(args: argparse.Namespace, t, lang: str) -> dict[int, str]:
    if args.cues_json is not None:
        if args.cue is not None or args.text is not None:
            raise Fail("USAGE", "--cues-json cannot be combined with --cue/--caption-text.")
        try:
            raw = json.loads(args.cues_json)
        except json.JSONDecodeError as e:
            raise Fail("USAGE", f"--cues-json is not valid JSON: {e}")
        if not isinstance(raw, dict) or not raw:
            raise Fail("USAGE", '--cues-json must be a non-empty object of {"cue": "text"}.')
        edits: dict[int, str] = {}
        for k, v in raw.items():
            if not isinstance(k, str) or not k.isdigit():
                raise Fail("USAGE", f"--cues-json key '{k}' is not a cue number.")
            text = _clean(str(v))
            if not text.strip():
                raise Fail("USAGE", f"--cues-json cue {k} has empty text.", topic=t.id, lang=lang)
            edits[int(k)] = text
        return edits
    if args.cue is None or args.text is None:
        raise Fail("USAGE", "caption needs --cue and --caption-text, or --cues-json.")
    text = _clean(args.text)
    if not text.strip():
        raise Fail("USAGE", "--caption-text cannot be empty.", topic=t.id, lang=lang)
    return {args.cue: text}


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if target.level != "topic":
        raise Fail("USAGE", "caption works on one topic directory.")
    t = target.topics[0]
    lang = args.lang
    path = t.srt(lang)

    r = TopicResult(t.id, t.rel)
    env.results.append(r)

    edits = _edits_from_args(args, t, lang)

    cues = srt.parse_file(path)
    bad = sorted(n for n in edits if not 1 <= n <= len(cues))
    if bad:
        r.ok = False
        raise Fail("CAPTION_RANGE", f"Cue(s) {', '.join(map(str, bad))} out of range; {path.name} has {len(cues)} cues.",
                   topic=t.id, lang=lang, file=path.name,
                   hint="Reload the cue list — the file may have changed since it was fetched.")

    old_bytes = path.read_bytes()
    changed = []
    for n, text in edits.items():
        cue = cues[n - 1]
        if cue.text != text:
            cue.text = text
            changed.append(n)

    if not changed:
        r.extra.update({"written": False, "cues": []})
        r.ok = True
        return

    new_bytes = srt.to_srt(cues).encode("utf-8")

    if args.dry_run:
        r.extra.update({"written": False, "cues": sorted(changed)})
        r.ok = True
        return

    fsutil.save_with_history(path, old_bytes, keep=KEEP_VERSIONS)
    fsutil.write_bytes(path, new_bytes)
    r.extra.update({"written": True, "cues": sorted(changed)})
    r.diagnostics.append(Diagnostic("CAPTION_SAVED",
                                     f"Saved cue {changed[0]} of {path.name}." if len(changed) == 1
                                     else f"Saved {len(changed)} cues of {path.name}.",
                                     topic=t.id, lang=lang, file=path.name))
    r.ok = True
